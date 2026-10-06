"""Read-time projection over the single source of truth (lots).

`lots` is the only writable truth. The full-shelf view and the per-layer
page are *not* materialized anywhere; both are derived here on every read
from the same query, so they can never disagree and no write can leave a
half-written projection row behind. The JOIN between lots and items lives
in this module only — routes must never hand-assemble a second one.
"""

from datetime import date

# Only on_shelf, positive-remainder lots are "on the shelf". Negative-qty
# dirty seed lots and fully consumed/expired lots are excluded at the source.
_SQL = """
SELECT lots.id           AS id,
       lots.item_id      AS item_id,
       lots.qty_in       AS qty_in,
       lots.qty_remain   AS qty_remain,
       lots.expiry       AS expiry,
       lots.status       AS status,
       lots.data_quality AS data_quality,
       items.name        AS name,
       items.layer       AS layer,
       items.unit        AS unit
FROM lots
JOIN items ON items.id = lots.item_id
WHERE lots.status = 'on_shelf'
  AND lots.qty_remain > 0
"""


def shelf_rows(c, layer: str | None = None) -> list[dict]:
    """Full-shelf column view when layer is None, else one layer's rows.

    Same function, same query — the layer page is just a filtered read of
    the full projection, so sum(per-layer) == full totals by construction.
    """
    sql = _SQL
    args: list = []
    if layer is not None:
        sql += " AND items.layer = ?"
        args.append(layer)
    sql += "  ORDER BY items.layer, items.name, lots.expiry, lots.id"
    return [dict(r) for r in c.execute(sql, args)]


def urgent_alerts(c, warn_days: int, today: date) -> list[dict]:
    """Top alert bar: expired now or expiring within warn_days, derived
    directly from the same projection rows, so a lot deducted to zero can
    never linger in the urgent set.
    """
    rows = shelf_rows(c)
    today_s = today.isoformat()
    out: list[dict] = []
    for r in rows:
        exp = r.get("expiry")
        if not exp:
            continue
        if exp <= today_s:
            r["level"] = "expired"
            out.append(r)
        else:
            delta = (date.fromisoformat(exp) - today).days
            if delta <= warn_days:
                r["level"] = "soon"
                r["days_left"] = delta
                out.append(r)
    return out
