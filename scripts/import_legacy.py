#!/usr/bin/env python3
"""Import history collected earlier by data/extract_dashboard_timeseries.py.

    python scripts/import_legacy.py dashboard-ts --product boj-sunscreen-50ml
    python scripts/import_legacy.py csv --product boj-sunscreen-50ml --file ../data/daily_prices.csv
Only 'recorded' rows of a daily CSV are imported; carried-forward rows are skipped.
"""
import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from app.database.client import get_database
from app.database.repositories import Repository
from app.services.import_service import import_daily_csv, import_dashboard_ts

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["dashboard-ts", "csv"])
    ap.add_argument("--product", required=True)
    ap.add_argument("--file", type=Path)
    ap.add_argument("--price-type", default="selling_price", choices=["selling_price", "offer_price"])
    a = ap.parse_args()
    repo = Repository(get_database())
    sources = repo.list_price_sources(a.product)
    if not sources:
        raise SystemExit(f"{a.product} has no registered price-history source; run register_products.py first")
    for s in sources:
        if a.mode == "dashboard-ts":
            print(json.dumps(import_dashboard_ts(repo, a.product, s["source_url"]), indent=2, default=str))
        else:
            if not a.file:
                raise SystemExit("--file is required for csv mode")
            print(json.dumps(import_daily_csv(repo, a.product, s["source_url"], a.file, a.price_type), indent=2))
