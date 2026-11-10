"""PyMySQL + DBUtils connection pool with a commit/rollback context manager."""
from contextlib import contextmanager

import pymysql
from dbutils.pooled_db import PooledDB
from pymysql.cursors import DictCursor

_pool = None


def init_db(app):
    """Create the pool. mincached=0 so the app boots even if MySQL is briefly down."""
    global _pool
    c = app.config
    _pool = PooledDB(
        creator=pymysql,
        maxconnections=c["DB_POOL_MAX"],
        mincached=0,
        blocking=True,
        ping=1,
        host=c["DB_HOST"],
        port=c["DB_PORT"],
        user=c["DB_USER"],
        password=c["DB_PASSWORD"],
        database=c["DB_NAME"],
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
    )


@contextmanager
def get_cursor():
    """Yield a DictCursor inside a transaction: commit on success, roll back on any exception."""
    if _pool is None:
        raise RuntimeError("Database pool not initialised (call init_db first).")
    conn = _pool.connection()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
