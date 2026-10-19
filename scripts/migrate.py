#!/usr/bin/env python3
"""Apply supabase/migrations/*.sql in order, recording each in smartbuy.schema_migrations.

    python scripts/migrate.py            # uses DATABASE_URL from .env
    python scripts/migrate.py --status   # list applied / pending, change nothing

Equivalent to `supabase db push` for projects not using the Supabase CLI.
"""
import argparse
from pathlib import Path

import _bootstrap  # noqa: F401
import psycopg

from app.config import get_settings

MIGRATIONS = Path(__file__).resolve().parents[1] / "supabase" / "migrations"


def applied_versions(conn) -> set[str]:
    if conn.execute("select to_regclass('smartbuy.schema_migrations') as t").fetchone()[0] is None:
        return set()
    return {r[0] for r in conn.execute("select version from smartbuy.schema_migrations")}


def migrate(dsn: str, status_only: bool = False) -> list[str]:
    done = []
    with psycopg.connect(dsn, prepare_threshold=None) as conn:
        have = applied_versions(conn)
        for f in sorted(MIGRATIONS.glob("*.sql")):
            version = f.stem
            if version in have:
                print(f"  applied  {version}")
                continue
            if status_only:
                print(f"  pending  {version}")
                continue
            with conn.transaction():
                conn.execute(f.read_text(encoding="utf-8"))
                conn.execute("insert into smartbuy.schema_migrations (version) values (%s)", (version,))
            print(f"  applied  {version} (now)")
            done.append(version)
    return done


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--dsn", help="override DATABASE_URL (e.g. a local test database)")
    a = ap.parse_args()
    s = get_settings()
    dsn = a.dsn or (s.database_url.get_secret_value() if s.database_url else None)
    if not dsn:
        raise SystemExit("DATABASE_URL is not set (see .env.example)")
    migrate(dsn, a.status)
