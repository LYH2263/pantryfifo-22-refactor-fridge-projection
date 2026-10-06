import os
import tempfile
import unittest
from datetime import date

# 在导入 app.db 之前把真源指到临时库，隔离种子数据。
_tmp = tempfile.mkdtemp(prefix="pantryfifo-test-")
os.environ["DATA_DIR"] = _tmp

from app.db import connect, transaction
from app import seed
from app.modules import projection


class ProjectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.init_db()

    def setUp(self):
        """每个用例都从同一份种子真源出发，避免用例间互相污染。"""
        with transaction() as c:
            c.execute("DELETE FROM lots")
            c.execute("DELETE FROM items")
            c.execute("DELETE FROM settings")
            c.executemany("INSERT INTO items(id,name,layer,unit) VALUES (?,?,?,?)", [
                (1, "牛奶", "upper", "盒"), (2, "鸡蛋", "mid", "个"), (3, "冻饺", "lower", "袋"),
            ])
            c.executemany(
                "INSERT INTO lots(id,item_id,qty_in,qty_remain,expiry,status,data_quality) "
                "VALUES (?,?,?,?,?,?,?)",
                [
                    (1, 1, 2, 2, "2026-10-01", "on_shelf", "clean"),
                    (2, 1, 1, 1, "2026-09-28", "on_shelf", "clean"),
                    (3, 2, 12, 12, "2026-11-01", "on_shelf", "clean"),
                    (4, 3, 1, 1, "2025-01-01", "on_shelf", "dirty"),
                    (5, 2, -3, -3, "2026-12-01", "on_shelf", "dirty"),
                ],
            )
            c.execute("INSERT INTO settings(key,value) VALUES ('warn_days','3')")

    def test_whole_equals_sum_of_layer_filters(self):
        """全层余量合计必须恒等于各层页过滤后合计之和。"""
        with connect() as c:
            whole = projection.shelf_rows(c)
            per_layer = {L: projection.shelf_rows(c, L) for L in ("upper", "mid", "lower")}
        # 行集合被层过滤无损切分
        self.assertEqual(
            sorted(r["id"] for r in whole),
            sorted(i for rows in per_layer.values() for i in (r["id"] for r in rows)),
        )
        self.assertAlmostEqual(
            projection.shelf_qty_total(whole),
            sum(projection.shelf_qty_total(rows) for rows in per_layer.values()),
            places=6,
        )

    def test_layer_filter_uses_same_predicate_as_whole(self):
        with connect() as c:
            upper = projection.shelf_rows(c, "upper")
        self.assertTrue(upper)
        self.assertTrue(all(r["layer"] == "upper" for r in upper))

    def test_consume_drained_lot_leaves_no_row(self):
        """扣光的批翻成 consumed 后，投影与紧急集合都不得残留。"""
        # 种子里 item1 两批：2026-10-01 x2、2026-09-28 x1，相对 2026-10-05 都已过期。
        with transaction() as c:
            c.execute("UPDATE lots SET status='consumed', qty_remain=0 "
                      "WHERE item_id=1 AND expiry='2026-09-28'")
            c.execute("UPDATE lots SET status='consumed', qty_remain=0 "
                      "WHERE item_id=1 AND expiry='2026-10-01'")
        with connect() as c:
            rows = projection.shelf_rows(c)
            urgent = projection.urgent(c, today=date(2026, 10, 5))
        self.assertFalse(any(r["item_id"] == 1 for r in rows))
        self.assertFalse(any(r["item_id"] == 1 for r in urgent))

    def test_urgent_never_reports_drained_or_partial_consumed(self):
        """消费写回 lots 成功后，顶条不能再按旧 JOIN 报已扣光的批；
        部分扣减的批按真源剩余量出现。"""
        # item1/2026-09-28 这批扣到 0，2026-10-01 这批从 2 扣到 1。
        with transaction() as c:
            c.execute("UPDATE lots SET status='consumed', qty_remain=0 "
                      "WHERE item_id=1 AND expiry='2026-09-28'")
            c.execute("UPDATE lots SET qty_remain=1 WHERE item_id=1 AND expiry='2026-10-01'")
        with connect() as c:
            urgent = projection.urgent(c, today=date(2026, 10, 5))
        ids = {r["id"] for r in urgent if r["item_id"] == 1}
        with connect() as c:
            drained = c.execute("SELECT id FROM lots WHERE item_id=1 AND expiry='2026-09-28'").fetchone()["id"]
            remain = c.execute("SELECT id FROM lots WHERE item_id=1 AND expiry='2026-10-01'").fetchone()["id"]
        self.assertNotIn(drained, ids)
        self.assertIn(remain, ids)
        self.assertEqual(next(r["qty_remain"] for r in urgent if r["id"] == remain), 1)

    def test_txn_failure_leaves_no_half_projection(self):
        """写事务中途失败：真源整体回滚，投影读时现算必然仍是完整旧貌，
        结构上不可能留下半份投影行。"""
        class Boom(Exception): ...
        with connect() as c:
            before = projection.shelf_rows(c)
        with self.assertRaises(Boom):
            with transaction() as c:
                c.execute(
                    "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality) "
                    "VALUES (?,?,?,?,?,?)", (2, 9, 9, "2030-01-01", "on_shelf", "clean"))
                raise Boom()
        with connect() as c:
            after = projection.shelf_rows(c)
        self.assertEqual(
            [(r["id"], r["qty_remain"], r["status"]) for r in before],
            [(r["id"], r["qty_remain"], r["status"]) for r in after],
        )

    def test_partial_txn_failure_atomic(self):
        """同一事务里先改一批、后一步炸掉：第一批的改动也必须回滚。"""
        with self.assertRaises(RuntimeError):
            with transaction() as c:
                c.execute("UPDATE lots SET qty_remain=qty_remain-1 WHERE item_id=2")
                raise RuntimeError("sweep failed")
        with connect() as c:
            rows = projection.shelf_rows(c)
        mid = [r for r in rows if r["item_id"] == 2]
        # 种子 item2：12 + (-3)，回滚后保持
        self.assertAlmostEqual(sum(r["qty_remain"] for r in mid), 9, places=6)

    def test_off_shelf_leaves_no_on_shelf_row(self):
        """下架（expired）后投影不得残留该 on_shelf 行。"""
        with transaction() as c:
            c.execute("UPDATE lots SET status='expired' WHERE item_id=3")
        with connect() as c:
            rows = projection.shelf_rows(c)
        self.assertFalse(any(r["item_id"] == 3 for r in rows))


if __name__ == "__main__":
    unittest.main()
