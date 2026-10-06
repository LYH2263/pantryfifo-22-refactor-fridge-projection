"""货架投影模块：全仓唯一的 lots × items JOIN 归属。

真源是 lots 表；本模块不落任何投影表 / 缓存行，只在被读取时由真源现算
（read-through projection）。全层竖列与层页必须走同一入口 shelf_rows，
任何路由都不得再手写第二套 JOIN 拼装。顶条紧急集合也在投影结果上现算。

因为投影只读不写，入库 / 扣减 / 下架不存在"投影刷新失败留下半份行"的可能：
写事务失败时真源整体回滚，随后任何一次读取重算出的投影都是完整一致的。
"""
from datetime import date

# 全仓唯一允许出现的 lots JOIN items，读时现算。
_SHELF_SQL = """
SELECT lots.id, lots.item_id, lots.qty_in, lots.qty_remain,
       lots.expiry, lots.status, lots.data_quality,
       items.name, items.layer, items.unit
FROM lots JOIN items ON items.id = lots.item_id
WHERE lots.status = 'on_shelf'
"""

def shelf_rows(c, layer: str | None = None) -> list[dict]:
    """货架投影。layer=None 为全层；传 layer 为单层过滤。

    两条路径共用同一段 SQL 与同一份谓词，因此
    全层余量合计 == 各层页过滤后余量合计之和 是结构性保证。
    """
    sql, args = _SHELF_SQL, []
    if layer is not None:
        sql += " AND items.layer = ?"
        args.append(layer)
    sql += " ORDER BY items.layer, lots.expiry, lots.id"
    return [dict(r) for r in c.execute(sql, args)]

def shelf_qty_total(rows: list[dict]) -> float:
    return round(sum(float(r["qty_remain"]) for r in rows), 3)

def urgent(c, today: date | None = None) -> list[dict]:
    """顶条紧急集合：直接在货架投影结果上判定，绝不另写 JOIN。

    只认投影里仍在架 (on_shelf) 且 qty_remain>0 的批，所以消费写回 lots
    成功后，即便没有任何额外"投影刷新"步骤，已扣光 / consumed 的批也不会
    再被报为紧急。
    """
    today = today or date.today()
    warn = int(c.execute("SELECT value FROM settings WHERE key='warn_days'").fetchone()["value"])
    out = []
    for r in shelf_rows(c):
        exp = r.get("expiry")
        if not exp or float(r.get("qty_remain", 0)) <= 0:
            continue
        exp_day = date.fromisoformat(exp)
        if exp_day <= today:
            r = {**r, "level": "expired"}
            out.append(r)
        else:
            days_left = (exp_day - today).days
            if days_left <= warn:
                out.append({**r, "level": "soon", "days_left": days_left})
    return out
