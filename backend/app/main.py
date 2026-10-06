import json
from datetime import date, datetime, timezone
from contextlib import closing
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect
from app.engines.fefo import consume_fefo, expire_lots
from app.modules.shelf_projection import shelf_rows, urgent_alerts

app = FastAPI(title="Pantryfifo", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

@app.get("/api/health")
def health(): return {"ok": True, "project": "pantryfifo"}

@app.get("/api/items")
def items():
    with closing(connect()) as c:
        return [dict(r) for r in c.execute("SELECT * FROM items")]

# --- projection (read model) ------------------------------------------------
# The full-shelf columns and the per-layer page both read this one endpoint.
# It derives from lots (the truth) at read time; there is no materialized
# projection table, so no write can ever leave a half-refreshed view.
@app.get("/api/projection/shelf")
def projection_shelf(layer: str | None = None):
    with closing(connect()) as c:
        return shelf_rows(c, layer)

@app.get("/api/alerts")
def alerts():
    with closing(connect()) as c:
        warn = int(c.execute("SELECT value FROM settings WHERE key='warn_days'").fetchone()["value"])
        return urgent_alerts(c, warn, date.today())

# --- writes (truth only; single transaction each) ---------------------------
class LotIn(BaseModel):
    item_id: int
    qty: float
    expiry: str

@app.post("/api/lots")
def inbound(body: LotIn):
    with closing(connect()) as c, c:  # commit on success / roll back on error
        item = c.execute("SELECT id FROM items WHERE id=?", (body.item_id,)).fetchone()
        if not item:
            raise HTTPException(404, "item")
        cur = c.execute(
            "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality) VALUES (?,?,?,?,?,?)",
            (body.item_id, body.qty, body.qty, body.expiry, "on_shelf", "clean"))
        lid = cur.lastrowid
    return {"id": lid}

class ConsumeIn(BaseModel):
    item_id: int
    qty: float
    note: str = ""

@app.post("/api/consume")
def consume(body: ConsumeIn):
    with closing(connect()) as c, c:
        lots = [dict(r) for r in c.execute(
            "SELECT * FROM lots WHERE item_id=? AND status='on_shelf' AND qty_remain>0", (body.item_id,))]
        result = consume_fefo(lots, body.qty)
        if not result["ok"] and result["reason"] == "qty_non_positive":
            raise HTTPException(400, result["reason"])
        if not result["ok"]:
            raise HTTPException(409, result)
        # All deductions + the audit row commit together. Any failure rolls
        # every UPDATE back, so lots can never be left half-deducted.
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
    with closing(connect()) as c, c:
        lots = [dict(r) for r in c.execute("SELECT * FROM lots WHERE status='on_shelf'")]
        ids = expire_lots(lots, date.today().isoformat())
        for i in ids:
            c.execute("UPDATE lots SET status='expired' WHERE id=?", (i,))
    return {"expired_ids": ids}

@app.get("/api/settings")
def settings():
    with closing(connect()) as c:
        return {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}
