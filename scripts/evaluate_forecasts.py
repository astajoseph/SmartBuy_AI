#!/usr/bin/env python3
"""Score matured forecasts against later recorded prices and print accuracy by horizon/method.

    python scripts/evaluate_forecasts.py [--backtest boj-sunscreen-50ml]
--backtest also prints a chronological backtest of every baseline for the product (no data stored).
These are backtests on historical data, not real-world user savings.
"""
import argparse
import json

import _bootstrap  # noqa: F401

from app.database.client import get_database
from app.database.repositories import Repository
from app.forecasting.evaluation import brier_backtest, compare_baselines
from app.forecasting.forecasting import HORIZONS, evaluate_due_forecasts
from app.services.price_service import daily_series, points_frame

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backtest", metavar="PRODUCT_ID")
    a = ap.parse_args()
    repo = Repository(get_database())
    print(f"newly evaluated forecasts: {evaluate_due_forecasts(repo)}")
    print(json.dumps(repo.outcome_summary(), indent=2, default=str))
    if a.backtest:
        daily = daily_series(points_frame(repo.price_points(a.backtest, "selling_price")))["price"]
        for h in HORIZONS:
            print(f"\n=== horizon {h} days ===")
            print(json.dumps(compare_baselines(daily, h), indent=2))
            print("probability check (5% drop):", json.dumps(brier_backtest(daily, h, 5.0)))
