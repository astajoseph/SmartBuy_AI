"""Seller discovery worker (see services/discovery_service.py).

    python -m app.workers.discovery                      # all active products, then deactivate stale sellers
    python -m app.workers.discovery --product <id>       # one product

Runs in GitHub Actions every 6 hours (.github/workflows/smartbuy-discover.yml). Needs DATABASE_URL and
Playwright Chromium (Amazon's seller list is rendered); BRAVE_SEARCH_API_KEY enables web search.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging

from app.database.client import get_database
from app.database.repositories import Repository
from app.logging_config import configure_logging
from app.services.discovery_service import run_discovery
from app.services.web_search import search_client

log = logging.getLogger("discovery")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--product", help="only this product_id")
    a = ap.parse_args()
    configure_logging()
    if search_client() is None:
        log.info("BRAVE_SEARCH_API_KEY not set: web search skipped, marketplace sellers still checked")
    res = asyncio.run(run_discovery(Repository(get_database()), product_id=a.product))
    log.info("discovery finished: %s", json.dumps(res, default=str)[:20000])
    if res["products"] and all(p.get("status") == "failed" for p in res["products"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
