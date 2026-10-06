import json
from datetime import date, datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect, transaction
from app.engines.fefo import consume_fefo, expire_lots
from app.modules import projection

app = FastAPI(title="Pantryfifo", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

@app.get("/api/health")
def health(): return {"ok": True, "project": "pantryfifo"}

@app.get("/api/items")
def items():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM items")]; c.close(); return rows

# 全层竖列与层页的唯一读口：前端两页都只打这里，路由层不出现任何 JOIN。
@app.get("/api/projection")
def shelf(layer: str | None = None):
    c = connect()
    rows = projection.shelf_rows(c, layer)
    c.close()
    return rows

# 顶条紧急集合同样只认投影，不再自写第二套 JOIN。
@app.get("/api/alerts")
def alerts():
    c = connect()
    rows = projection.urgent(c)
    c.close()
    return rows

class LotIn(BaseModel):
    item_id: int
    qty: float
    expiry: str

@app.post("/api/lots")
def inbound(body: LotIn):
    with transaction() as c:
        if not c.execute("SELECT id FROM items WHERE id=?", (body.item_id,)).fetchone():
            raise HTTPException(404, "item")
        cur = c.execute(
            "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality) VALUES (?,?,?,?,?,?)",
            (body.item_id, body.qty, body.qty, body.expiry, "on_shelf", "clean"))
        lid = cur.lastrowid
    # 提交成功后再由真源现算投影，不存在投影刷新失败这一步。
    return {"id": lid}

class ConsumeIn(BaseModel):
    item_id: int
    qty: float
    note: str = ""

@app.post("/api/consume")
def consume(body: ConsumeIn):
    with transaction() as c:
        lots = [dict(r) for r in c.execute(
            "SELECT * FROM lots WHERE item_id=? AND status='on_shelf' AND qty_remain>0", (body.item_id,))]
        result = consume_fefo(lots, body.qty)
        if not result["ok"] and result["reason"] == "qty_non_positive":
            raise HTTPException(400, result["reason"])
        if not result["ok"]:
            raise HTTPException(409, result)
        # 全部扣减与状态翻转都在同一事务内：任何一步失败整体回滚，
        # lots 真源不会被扣掉一半，下一次读投影即为完整结果。
        for d in result["deductions"]:
            c.execute("UPDATE lots SET qty_remain = qty_remain - ? WHERE id=?", (d["take"], d["lot_id"]))
            rem = c.execute("SELECT qty_remain FROM lots WHERE id=?", (d["lot_id"],)).fetchone()["qty_remain"]
            if rem <= 0:
                c.execute("UPDATE lots SET status='consumed', qty_remain=0 WHERE id=?", (d["lot_id"],))
        c.execute("INSERT INTO consumptions(note,result_json,created_at) VALUES (?,?,?)",
                  (body.note, json.dumps(result), datetime.now(timezone.utc).isoformat()))
    return result

@app.post("/api/expire-sweep")
def expire_sweep():
    with transaction() as c:
        lots = [dict(r) for r in c.execute("SELECT * FROM lots WHERE status='on_shelf'")]
        ids = expire_lots(lots, date.today().isoformat())
        for i in ids:
            c.execute("UPDATE lots SET status='expired' WHERE id=?", (i,))
    # 下架已提交；投影读时现算，on_shelf 行不可能残留。
    return {"expired_ids": ids}

@app.get("/api/settings")
def settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows
