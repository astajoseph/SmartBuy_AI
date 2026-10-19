#!/usr/bin/env python3
"""Collect price-history sources now.

    python scripts/run_collection.py              # all due sources (same as the scheduler's --once)
    python scripts/run_collection.py --product boj-sunscreen-50ml   # this product, even if not due
"""
import argparse
import asyncio
import json

import _bootstrap  # noqa: F401

from app.database.client import get_database
from app.database.repositories import Repository
from app.logging_config import configure_logging
from app.services.alert_service import evaluate_product_alerts
from app.workers.price_collector import CollectorDeps, collect_source
from app.workers.scheduler import run_once

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--product")
    a = ap.parse_args()
    configure_logging("WARNING")
    repo = Repository(get_database())
    if a.product:
        async def go():
            return [await collect_source(repo, s, job_type="manual_price",
                                         deps=CollectorDeps(after_collect=evaluate_product_alerts))
                    for s in repo.list_price_sources(a.product) if s["is_active"]]
        res = asyncio.run(go())
    else:
        res = asyncio.run(run_once(repo))
    print(json.dumps(res, indent=2, default=str))
