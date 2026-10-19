#!/usr/bin/env python3
"""Write one row per IST calendar day (from smartbuy.v_daily_price) to CSV, flagging each row as
'recorded' or 'carried forward' - the SmartBuy equivalent of extract_dashboard_timeseries.py --export-daily.

    python scripts/export_daily.py --product boj-sunscreen-50ml --out exports/daily_prices.csv
"""
import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from app.database.client import get_database
from app.database.repositories import Repository
from app.services.import_service import export_daily_csv

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--product", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--price-type", default="selling_price", choices=["selling_price", "offer_price"])
    a = ap.parse_args()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    n = export_daily_csv(Repository(get_database()), a.product, a.out, a.price_type)
    print(f"wrote {n} daily rows to {a.out}")
