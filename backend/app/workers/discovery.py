"""Seller discovery worker (see services/discovery_service.py).

    python -m app.workers.discovery                      # all active products, then deactivate stale sellers
    python -m app.workers.discovery --product <id>       # one product
    python -m app.workers.discovery --no-marketplace     # cloud: web search + discovered shops only
    python -m app.workers.discovery --no-search          # this machine: marketplace sellers, no Brave queries

Runs every 6 hours in GitHub Actions with --no-marketplace (.github/workflows/smartbuy-discover.yml; Amazon and
Myntra block GitHub's data-centre addresses) and from this machine's crontab with --no-search
(scripts/run_discovery_local.sh). Needs DATABASE_URL and
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
from app.services.discovery_service import DiscoveryDeps, run_discovery
from app.services.web_search import search_client

log = logging.getLogger("discovery")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--product", help="only this product_id")
    ap.add_argument("--no-search", action="store_true", help="skip web search (no Brave queries)")
    ap.add_argument("--no-marketplace", action="store_true", help="skip Amazon / Myntra seller lists")
    a = ap.parse_args()
    configure_logging()
    deps = DiscoveryDeps(marketplace=not a.no_marketplace)
    if a.no_search:
        deps.search_factory = lambda: None
    elif search_client() is None:
        log.info("BRAVE_SEARCH_API_KEY not set: web search skipped")
    res = asyncio.run(run_discovery(Repository(get_database()), product_id=a.product, deps=deps))
    log.info("discovery finished: %s", json.dumps(res, default=str)[:20000])
    if res["products"] and all(p.get("status") == "failed" for p in res["products"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
