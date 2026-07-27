from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row

from .config import settings


@contextmanager
def get_conn():
    conn = psycopg.connect(
        str(settings()["database_url"]),
        row_factory=dict_row,
        autocommit=True,
    )
    try:
        yield conn
    finally:
        conn.close()


def fetch_all(sql: str, params: tuple | list = ()) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def fetch_one(sql: str, params: tuple | list = ()) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def execute(sql: str, params: tuple | list = ()) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
