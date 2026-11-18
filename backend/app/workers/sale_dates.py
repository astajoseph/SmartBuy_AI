"""Weekly sale-date finder run (see services/sale_date_finder.py).

    python -m app.workers.sale_dates            # runs only if the last run was 6+ days ago
    python -m app.workers.sale_dates --force    # run now

Called from the daily Windows task (scripts/run_daily_local.ps1); the 6-day guard makes it weekly. Uses about one
Brave query per upcoming sale (shared monthly budget BRAVE_MONTHLY_QUERY_LIMIT). Finds wait for admin approval.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from app.database.client import get_database
from app.database.repositories import Repository
from app.logging_config import configure_logging
from app.services import sale_date_finder

log = logging.getLogger("sale_dates")
MIN_DAYS_BETWEEN_RUNS = 6


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="run even if the last run was recent")
    a = ap.parse_args()
    configure_logging()
    repo = Repository(get_database())
    last = repo.last_sale_date_run()
    if not a.force and last and datetime.now(timezone.utc) - last < timedelta(days=MIN_DAYS_BETWEEN_RUNS):
        log.info("sale-date finder ran %s; next run after %s days", last.isoformat(), MIN_DAYS_BETWEEN_RUNS)
        return
    res = asyncio.run(sale_date_finder.run(repo))
    log.info("sale-date finder finished: %s", json.dumps({k: v for k, v in res.items() if k != "dropped"}))
    for d in res["dropped"][:50]:
        log.info("dropped: %s", json.dumps(d))


if __name__ == "__main__":
    main()
