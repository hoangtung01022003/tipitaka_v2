from contextlib import contextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
from psycopg.rows import dict_row

from .config import settings


ALLOWED_POSTGRES_QUERY_PARAMS = {
    "application_name",
    "connect_timeout",
    "gssencmode",
    "keepalives",
    "keepalives_count",
    "keepalives_idle",
    "keepalives_interval",
    "options",
    "sslcert",
    "sslcompression",
    "sslcrl",
    "sslkey",
    "sslmode",
    "sslrootcert",
    "target_session_attrs",
}


def normalize_database_url(database_url: str) -> str:
    parsed = urlsplit(database_url)
    if not parsed.query:
        return database_url

    clean_query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key in ALLOWED_POSTGRES_QUERY_PARAMS
        ]
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, clean_query, parsed.fragment))


@contextmanager
def get_conn():
    conn = psycopg.connect(
        normalize_database_url(str(settings()["database_url"])),
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
