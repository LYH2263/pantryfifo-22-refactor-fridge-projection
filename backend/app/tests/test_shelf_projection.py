"""Projection consistency + write atomicity.

The projection is read-time derived from lots (truth). These tests pin the
invariants the refactor guarantees:

* full-shelf view == concatenation/filter of per-layer views (same numbers),
* consumed-to-zero / swept / non-positive lots never appear on the shelf,
* the urgent alert bar drops a lot the instant it is deducted to zero,
* a failure mid-write rolls the truth back — no half-deducted lots.
"""

import os
from datetime import date

import pytest

from app.db import connect
from app.modules.shelf_projection import shelf_rows, urgent_alerts


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app import seed
    seed.init_db()
    c = connect()
    yield c
    c.close()


def _add_lot(c, item_id, qty, expiry, status="on_shelf", dq="clean"):
    cur = c.execute(
        "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality) VALUES (?,?,?,?,?,?)",
        (item_id, qty, qty, expiry, status, dq))
    c.commit()
    return cur.lastrowid


def test_full_equals_sum_of_layer_filters(db):
    """全层数字 = 层页过滤之和（按层逐条对齐，总量也相等）。"""
    full = shelf_rows(db)
    by_layer = {L: shelf_rows(db, L) for L in ("upper", "mid", "lower")}

    flat = [r for L in ("upper", "mid", "lower") for r in by_layer[L]]
    assert sorted(r["id"] for r in full) == sorted(r["id"] for r in flat)

    def qty(rows): return round(sum(float(r["qty_remain"]) for r in rows), 6)
    assert qty(full) == qty(flat)
    for L in ("upper", "mid", "lower"):
        assert qty([r for r in full if r["layer"] == L]) == qty(by_layer[L])


def test_layer_page_is_a_subset_of_full(db):
    full = {r["id"]: r for r in shelf_rows(db)}
    for L in ("upper", "mid", "lower"):
        for r in shelf_rows(db, L):
            assert r["layer"] == L
            assert full[r["id"]] == r  # identical projection row, not a second JOIN shape


def test_consumed_to_zero_lot_vanishes(db):
    lid = _add_lot(db, 1, 2, "2026-12-01")
    assert any(r["id"] == lid for r in shelf_rows(db))
    db.execute("UPDATE lots SET status='consumed', qty_remain=0 WHERE id=?", (lid,)); db.commit()
    rows = shelf_rows(db)
    assert all(r["id"] != lid for r in rows)
    assert all(r["status"] == "on_shelf" and float(r["qty_remain"]) > 0 for r in rows)


def test_swept_expired_leaves_no_on_shelf_row(db):
    lid = _add_lot(db, 2, 5, "2025-01-01")
    db.execute("UPDATE lots SET status='expired' WHERE id=?", (lid,)); db.commit()
    assert all(r["id"] != lid for r in shelf_rows(db))
    assert all(r["status"] == "on_shelf" for r in shelf_rows(db))


def test_non_positive_lots_never_projected(db):
    # seed already inserts a negative dirty lot; add a zero one too
    _add_lot(db, 1, 0, "2026-12-01")
    for r in shelf_rows(db):
        assert float(r["qty_remain"]) > 0


def test_urgent_drops_lot_deducted_to_zero(db):
    """扣光的批次不能仍按旧 JOIN 留在顶条紧急集合里。"""
    lid = _add_lot(db, 1, 1, "2026-10-06")  # within warn window below
    today = date(2026, 10, 5)
    assert [r["id"] for r in urgent_alerts(db, 3, today) if r["id"] == lid]
    db.execute("UPDATE lots SET status='consumed', qty_remain=0 WHERE id=?", (lid,)); db.commit()
    assert all(r["id"] != lid for r in urgent_alerts(db, 3, today))


def test_urgent_levels(db):
    soon_id = _add_lot(db, 2, 1, "2026-10-07")
    expired_id = _add_lot(db, 2, 1, "2026-10-01")
    healthy_id = _add_lot(db, 2, 1, "2026-12-01")
    alerts = {r["id"]: r for r in urgent_alerts(db, 3, date(2026, 10, 5))}
    assert alerts[expired_id]["level"] == "expired"
    assert alerts[soon_id]["level"] == "soon" and alerts[soon_id]["days_left"] == 2
    assert healthy_id not in alerts


def test_consume_write_rolls_back_on_later_failure(db):
    """写回 lots 后若同事务内再失败，扣减必须整体回滚，不留半份。"""
    lid = _add_lot(db, 1, 5, "2027-01-01")
    with pytest.raises(RuntimeError):
        with db:  # exact transaction boundary the routes use
            db.execute("UPDATE lots SET qty_remain = qty_remain - 2 WHERE id=?", (lid,))
            raise RuntimeError("simulated refresh/audit failure")
    rem = db.execute("SELECT qty_remain FROM lots WHERE id=?", (lid,)).fetchone()["qty_remain"]
    assert rem == 5


def _client_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import app.seed as seed
    importlib.reload(seed)
    seed.init_db()
    import app.main as main
    importlib.reload(main)
    return main


def test_route_consume_rollback_when_audit_insert_fails(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="route-level check needs fastapi")
    main = _client_env(tmp_path, monkeypatch)

    real_connect = main.connect

    # Seed a fresh on_shelf lot through a normal connection.
    c = real_connect()
    lot_id = c.execute(
        "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality) VALUES (?,?,?,?,?,?)",
        (1, 3, 3, "2027-01-01", "on_shelf", "clean")).lastrowid
    c.commit(); c.close()

    class ExplodingConn:
        """Fails the audit insert after the lot UPDATEs already ran."""
        def __init__(self, inner): self.inner = inner
        def execute(self, sql, *a):
            if sql.lstrip().startswith("INSERT INTO consumptions"):
                raise RuntimeError("audit write failed")
            return self.inner.execute(sql, *a)
        def commit(self): self.inner.commit()
        def rollback(self): self.inner.rollback()
        def close(self): self.inner.close()
        def __enter__(self): return self
        def __exit__(self, et, ev, tb): return self.inner.__exit__(et, ev, tb)

    monkeypatch.setattr(main, "connect", lambda: ExplodingConn(real_connect()))

    with pytest.raises(RuntimeError):
        main.consume(main.ConsumeIn(item_id=1, qty=2))

    c = real_connect()
    rem = c.execute("SELECT qty_remain,status FROM lots WHERE id=?", (lot_id,)).fetchone()
    assert rem["qty_remain"] == 3 and rem["status"] == "on_shelf"  # fully rolled back
    c.close()


def test_route_inbound_invalid_item_leaves_no_row(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="route-level check needs fastapi")
    from fastapi import HTTPException
    main = _client_env(tmp_path, monkeypatch)
    c = main.connect()
    before = c.execute("SELECT COUNT(*) n FROM lots").fetchone()["n"]; c.close()
    with pytest.raises(HTTPException) as ei:
        main.inbound(main.LotIn(item_id=999, qty=1, expiry="2027-01-01"))
    assert ei.value.status_code == 404
    c = main.connect()
    after = c.execute("SELECT COUNT(*) n FROM lots").fetchone()["n"]; c.close()
    assert before == after
