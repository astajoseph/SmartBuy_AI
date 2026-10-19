"""PostgreSQL (Supabase) connection pool.

Uses psycopg 3 directly. Connect with the Supabase *session* pooler (port 5432) or a direct
connection; prepared statements are disabled so PgBouncer/Supavisor poolers work.
"""
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import DB_SCHEMA, get_settings


class DatabaseNotConfigured(RuntimeError):
    pass


def _configure(conn: psycopg.Connection) -> None:
    conn.execute(f"set search_path to {DB_SCHEMA}, public")
    conn.execute("set timezone to 'UTC'")


class Database:
    def __init__(self, dsn: str, min_size: int = 4, max_size: int = 10):
        self.pool = ConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            # autocommit: single statements need no extra COMMIT/ROLLBACK round trip (Supabase can be far away);
            # multi-statement writes use explicit `with conn.transaction()` blocks in the repository.
            kwargs={"prepare_threshold": None, "row_factory": dict_row, "connect_timeout": 15, "autocommit": True},
            configure=_configure,
            open=True,
        )

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        with self.pool.connection() as conn:
            yield conn

    def close(self) -> None:
        self.pool.close()


_db: Database | None = None


def get_database() -> Database:
    global _db
    if _db is None:
        dsn = get_settings().database_url
        if dsn is None:
            raise DatabaseNotConfigured("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
        _db = Database(dsn.get_secret_value())
    return _db


def set_database(db: Database | None) -> None:
    """Used by tests and scripts to inject a specific database."""
    global _db
    _db = db
