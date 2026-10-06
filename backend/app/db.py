import os, sqlite3
from contextlib import contextmanager
from pathlib import Path

def db_path() -> Path:
    d = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "pantryfifo.db"

def connect():
    c = sqlite3.connect(db_path())
    c.row_factory = sqlite3.Row
    return c

@contextmanager
def transaction():
    """单个写事务：真源 lots 的所有改动只在提交后生效。

    入库 / 扣减 / 下架中途失败一律 rollback，不会留下改了一半的真源；
    投影是读时现算、从不落盘，因此也不可能出现半份投影行。
    BEGIN IMMEDIATE 让并发写在真源上串行化，读到的投影始终对应某个已提交快照。
    """
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
