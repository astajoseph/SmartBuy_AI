"""Forecast orchestration: pick a validated baseline per horizon, attach an empirical interval and
(optionally) a target-price probability, store it, and later score it against what happened."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from app.database.repositories import Repository
from app.forecasting.baselines import BASELINES
from app.forecasting.evaluation import brier_backtest, compare_baselines, probability_reaching
from app.services.price_service import MAX_CARRY_FORWARD_DAYS, daily_series, data_quality, points_frame, to_decimal

MODEL_VERSION = "baselines-1.0"
HORIZONS = (7, 14, 30)
INSUFFICIENT = "Insufficient historical data for a reliable forecast."


def build_forecast(df: pd.DataFrame, horizon: int, target: float | None = None,
                   now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    quality = data_quality(df, now)
    base = {"horizon_days": horizon, "model_version": MODEL_VERSION, "data_points_used": quality.get("recorded_points", 0),
            "target_price": target, "data_quality": quality}
    if not quality.get("sufficient_for_forecast"):
        return {**base, "status": "insufficient_data", "message": INSUFFICIENT, "forecast_method": "none",
                "predicted_price": None, "lower_price_bound": None, "upper_price_bound": None,
                "probability_below_target": None, "validation_metrics": {},
                "limitations": [INSUFFICIENT, "Needs >= 20 recorded days spanning >= 120 days."]}

    daily = daily_series(df)["price"]
    cmp = compare_baselines(daily, horizon)
    if cmp["chosen"] is None:
        return {**base, "status": "insufficient_data", "message": INSUFFICIENT, "forecast_method": "none",
                "predicted_price": None, "lower_price_bound": None, "upper_price_bound": None,
                "probability_below_target": None, "validation_metrics": cmp, "limitations": [INSUFFICIENT]}

    current = float(daily.dropna().iloc[-1])
    point = BASELINES[cmp["chosen"]](daily, horizon)
    lo_r, hi_r = cmp["interval_ratio"]
    prob = probability_reaching(daily, horizon, target, current) if target else None
    brier = brier_backtest(daily, horizon, 5.0)
    validation = {"backtest": cmp, "probability_brier_5pct_drop": brier}
    limitations = [
        "Forecast scored on a daily series where unrecorded days carry the last recorded price forward.",
        "Retail prices change in steps driven by sales and seller decisions; baselines cannot anticipate announcements.",
    ]
    if quality.get("days_since_last_record", 0) > 3:
        limitations.append(f"Last recorded price is {quality['days_since_last_record']} days old.")
    if prob and prob.get("probability") is not None and brier.get("skill") is not None:
        prob["skill_demonstrated"] = bool(brier["skill"])
        if not brier["skill"]:
            limitations.append("In backtests the target-probability method did not beat a constant base-rate guess "
                               "(Brier score); treat the probability as a rough indication only.")
    if lo_r == hi_r == 1.0:
        limitations.append(f"In most past {horizon}-day periods the price did not change, so the interval has zero width; "
                           "this reflects infrequent price changes, not certainty.")
    cov = cmp.get("interval_holdout_coverage")
    if cov is not None and cov < 0.65:
        limitations.append(f"The 80% interval covered only {cov:.0%} of hold-out cases; treat it as optimistic.")
    return {
        **base, "status": "ok", "forecast_method": cmp["chosen"],
        "base_price": current, "base_timestamp": df["ts"].max().to_pydatetime(),
        "predicted_price": round(point, 2),
        "lower_price_bound": round(point * lo_r, 2), "upper_price_bound": round(point * hi_r, 2),
        "probability_below_target": prob["probability"] if prob else None,
        "probability_detail": prob,
        "validation_metrics": validation, "limitations": limitations,
    }


def store_forecast(repo: Repository, product_id: str, f: dict) -> int:
    return repo.insert_forecast({
        "product_id": product_id, "horizon_days": f["horizon_days"], "forecast_method": f["forecast_method"],
        "predicted_price": to_decimal(f.get("predicted_price")), "lower_price_bound": to_decimal(f.get("lower_price_bound")),
        "upper_price_bound": to_decimal(f.get("upper_price_bound")),
        "probability_below_target": f.get("probability_below_target"), "target_price": to_decimal(f.get("target_price")),
        "data_points_used": f["data_points_used"], "model_version": f["model_version"],
        "validation_metrics": f.get("validation_metrics"), "limitations": f.get("limitations"),
        "base_price": to_decimal(f.get("base_price")), "base_timestamp": f.get("base_timestamp"),
    })


def refresh_forecasts(repo: Repository, product_id: str) -> list[dict]:
    df = points_frame(repo.price_points(product_id, "selling_price"))
    out = []
    for h in HORIZONS:
        f = build_forecast(df, h)
        if f["status"] == "ok":
            f["id"] = store_forecast(repo, product_id, f)
        out.append(f)
    return out


def evaluate_due_forecasts(repo: Repository, now: datetime | None = None) -> int:
    """Score matured forecasts. The actual price at base_timestamp + horizon is the last recorded price at or
    before that moment, accepted only if a later record exists (so the price state at that date is known)
    and the record is at most MAX_CARRY_FORWARD_DAYS old. Otherwise the forecast waits for more data."""
    n = 0
    for f in repo.forecasts_due_for_evaluation(now):
        base_ts = f["base_timestamp"] or f["generated_at"]
        end = base_ts + timedelta(days=f["horizon_days"])
        newest = repo.latest_price(f["product_id"])
        if newest is None or newest["ts"] < end:
            continue                                  # no data beyond the horizon yet; try again later
        actual = repo.price_at_or_before(f["product_id"], end)
        if actual is None or (end - actual["ts"]).days > MAX_CARRY_FORWARD_DAYS:
            repo.insert_outcome({"forecast_id": f["id"], "actual_price": None, "forecast_error": None,
                                 "within_interval": None, "target_reached": None,
                                 "outcome_notes": "No recorded price close enough to the horizon date; not scored."})
            continue
        price = actual["price"]
        within = (f["lower_price_bound"] is not None and f["lower_price_bound"] <= price <= f["upper_price_bound"])
        reached = None
        if f["target_price"] is not None:
            mn = repo.min_price_between(f["product_id"], base_ts, end)
            reached = mn is not None and mn <= f["target_price"]
        repo.insert_outcome({"forecast_id": f["id"], "actual_price": price,
                             "forecast_error": f["predicted_price"] - price, "within_interval": within,
                             "target_reached": reached,
                             "outcome_notes": f"Actual = last recorded price at or before {end.date()} ({actual['ts'].date()})."})
        n += 1
    return n
