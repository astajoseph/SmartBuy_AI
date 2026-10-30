from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.forecasting import evaluation
from app.forecasting.evaluation import backtest, compare_baselines, probability_reaching
from app.forecasting.forecasting import INSUFFICIENT, build_forecast, evaluate_due_forecasts, store_forecast
from app.services.price_service import daily_series, points_frame
from conftest import add_history, register_product

START = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _frame(values, start=START, step_days=1):
    return points_frame([{"ts": start + timedelta(days=i * step_days), "price": v, "point_kind": "historical"}
                         for i, v in enumerate(values)])


def _steps(n=400, seed=1):
    """SYNTHETIC step-shaped price path."""
    rng = np.random.default_rng(seed)
    p, out = 1000.0, []
    for _ in range(n):
        if rng.random() < 0.05:
            p = max(500, p * (1 + rng.normal(0, 0.08)))
        out.append(round(p, 2))
    return out


def test_backtest_never_sees_the_future():
    series = daily_series(_frame(_steps(200)))["price"]
    seen = []

    def spy(hist, horizon):
        seen.append(hist.index[-1])
        return float(hist.iloc[-1])

    bt = backtest(series, 7, spy, step=5)
    assert len(bt) > 10
    for origin, last_seen in zip(bt["origin"], seen):
        assert last_seen == origin                       # history ends exactly at the origin
    # and the scored value is 7 days after the origin
    assert all(series.loc[o + pd.Timedelta(days=7)] == a for o, a in zip(bt["origin"], bt["actual"]))


def test_complex_method_must_beat_naive(monkeypatch):
    series = daily_series(_frame([1000.0] * 300))["price"]      # flat: nothing beats naive
    res = compare_baselines(series, 7)
    assert res["chosen"] == "naive_last"


def test_insufficient_history_returns_message():
    f = build_forecast(_frame([1000, 990, 980]), 14)
    assert f["status"] == "insufficient_data" and f["message"] == INSUFFICIENT
    assert f["predicted_price"] is None and f["probability_below_target"] is None


def test_forecast_has_interval_and_rounded_probability():
    f = build_forecast(_frame(_steps(500)), 14, target=900, now=START + timedelta(days=500))
    assert f["status"] == "ok"
    assert f["lower_price_bound"] <= f["predicted_price"] <= f["upper_price_bound"]
    p = f["probability_below_target"]
    assert p is None or abs(p * 20 - round(p * 20)) < 1e-9          # 5 % steps, no false precision


def test_probability_when_already_at_target():
    s = pd.Series([1000.0] * 100)
    assert probability_reaching(s, 14, 1200, 1000)["probability"] == 1.0


def test_probability_needs_enough_samples():
    s = pd.Series([1000.0] * 40)
    assert probability_reaching(s, 14, 900, 1000)["probability"] is None


def test_long_gaps_are_unknown_not_carried_forward():
    df = points_frame([{"ts": START, "price": 1000, "point_kind": "historical"},
                       {"ts": START + timedelta(days=200), "price": 900, "point_kind": "historical"}])
    d = daily_series(df)
    assert d["price"].iloc[:31].notna().all()
    assert d["price"].iloc[31:200].isna().all()


@pytest.mark.db
def test_prediction_outcome_logging(repo):
    sid, _ = register_product(repo)
    base = datetime.now(timezone.utc) - timedelta(days=40)
    add_history(repo, "test-product", sid, [(base, 1000), (base + timedelta(days=10), 950)])
    fid = store_forecast(repo, "test-product", {
        "horizon_days": 7, "forecast_method": "naive_last", "predicted_price": 1000, "lower_price_bound": 980,
        "upper_price_bound": 1020, "probability_below_target": None, "target_price": 960, "data_points_used": 1,
        "model_version": "test", "base_price": 1000, "base_timestamp": base})
    assert evaluate_due_forecasts(repo) == 1
    with repo.db.connection() as c:
        o = c.execute("select * from prediction_outcomes where forecast_id = %s", (fid,)).fetchone()
    assert float(o["actual_price"]) == 1000.0 and o["within_interval"] is True and o["target_reached"] is False
    assert evaluate_due_forecasts(repo) == 0          # each forecast scored once


def test_forecast_is_withheld_when_the_latest_price_is_stale():
    frame = _frame(_steps(500))
    fresh = build_forecast(frame, 14, now=START + timedelta(days=500))
    stale = build_forecast(frame, 14, now=START + timedelta(days=500 + 20))
    assert fresh["status"] == "ok"
    assert stale["status"] == "stale" and stale["predicted_price"] is None
    assert "20 days old" in stale["message"] or "days old" in stale["message"]
