"""Chronological (rolling-origin) backtesting. No future data is ever visible to a forecast:
each origin uses history[:origin] only and is scored against the value `horizon` days later.

Note: the scoring target is the daily carry-forward series, so a "day" without a record is
scored against the last recorded price before it. This is reported as a limitation.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.forecasting.baselines import BASELINES, Forecaster

MIN_TRAIN_DAYS = 60


def origins(n: int, horizon: int, step: int = 7, min_train: int = MIN_TRAIN_DAYS) -> list[int]:
    """Indices i such that history = series[:i+1] and target = series[i+horizon]."""
    return list(range(min_train - 1, n - horizon, step))


def backtest(series: pd.Series, horizon: int, forecaster: Forecaster, step: int = 7) -> pd.DataFrame:
    rows = []
    vals = series.to_numpy(dtype=float)
    for i in origins(len(series), horizon, step):
        if np.isnan(vals[i]) or np.isnan(vals[i + horizon]):
            continue                              # unknown day at origin or target: not scorable
        hist = series.iloc[: i + 1]               # strictly past and present, never the future
        if hist.notna().sum() < 30:
            continue
        pred = forecaster(hist, horizon)
        rows.append({"origin": series.index[i], "pred": pred, "actual": vals[i + horizon], "last": vals[i]})
    return pd.DataFrame(rows)


def metrics(bt: pd.DataFrame) -> dict:
    if bt.empty:
        return {"n": 0}
    err = bt["pred"] - bt["actual"]
    return {"n": int(len(bt)), "mae": float(err.abs().mean()), "rmse": float(math.sqrt((err ** 2).mean())),
            "mape_pct": float((err.abs() / bt["actual"]).mean() * 100)}


def compare_baselines(series: pd.Series, horizon: int, step: int = 7) -> dict:
    """Backtest all baselines. The chosen method must beat naive_last's MAE by >= 2 %, otherwise
    naive_last is used. Returns per-method metrics, choice, and empirical interval quantiles."""
    results = {name: backtest(series, horizon, f, step) for name, f in BASELINES.items()}
    table = {name: metrics(bt) for name, bt in results.items()}
    if table["naive_last"].get("n", 0) < 8:
        return {"methods": table, "chosen": None, "reason": "fewer than 8 backtest origins"}
    naive_mae = table["naive_last"]["mae"]
    best = min(table, key=lambda k: table[k]["mae"])
    chosen = best if table[best]["mae"] <= naive_mae * 0.98 else "naive_last"

    bt = results[chosen]
    ratio = (bt["actual"] / bt["pred"]).to_numpy()
    half = len(ratio) // 2
    lo_q, hi_q = (np.nanquantile(ratio[:half], [0.1, 0.9]) if half >= 4 else (np.nan, np.nan))
    holdout = ratio[half:]
    coverage = float(np.mean((holdout >= lo_q) & (holdout <= hi_q))) if half >= 4 and len(holdout) else None
    lo_all, hi_all = np.nanquantile(ratio, [0.1, 0.9])
    return {
        "methods": table,
        "chosen": chosen,
        "reason": "lowest backtest MAE" if chosen == best else "no method beat naive_last by >= 2% MAE",
        "interval_ratio": [float(lo_all), float(hi_all)],
        "interval_nominal": 0.8,
        "interval_holdout_coverage": coverage,
    }


# ----------------------------------------------------------------------------- target probability
def min_ratio_samples(series: pd.Series, horizon: int, lookback_days: int = 730) -> np.ndarray:
    """For each past day t: min(price[t+1 .. t+horizon]) / price[t]."""
    s = series.iloc[-(lookback_days + horizon):].to_numpy(dtype=float)
    if len(s) <= horizon + 1:
        return np.array([])
    out = []
    for t in range(len(s) - horizon):
        window = s[t + 1: t + horizon + 1]
        if np.isnan(s[t]) or np.isnan(window).any():
            continue                              # only fully known windows
        out.append(window.min() / s[t])
    return np.array(out)


def probability_reaching(series: pd.Series, horizon: int, target: float, current: float,
                         lookback_days: int = 730) -> dict:
    """Empirical probability that the price falls to <= target within `horizon` days, from how often
    comparable relative drops happened historically. Overlapping windows are not independent, so the
    effective sample size is ~ n / horizon; the result is reported as a rounded band."""
    if target >= current:
        return {"probability": 1.0, "band": "already at or below target", "method": "current_price_check",
                "effective_samples": None}
    samples = min_ratio_samples(series, horizon, lookback_days)
    eff = len(samples) / max(horizon, 1)
    if len(samples) < 60 or eff < 8:
        return {"probability": None, "band": None, "method": "empirical_min_ratio",
                "effective_samples": round(eff, 1), "reason": "Not enough history for a reliable probability."}
    needed = target / current
    p = float(np.mean(samples <= needed))
    return {"probability": round(p / 0.05) * 0.05, "band": probability_band(p), "method": "empirical_min_ratio",
            "effective_samples": round(eff, 1), "needed_ratio": round(needed, 4)}


def probability_band(p: float) -> str:
    if p < 0.05:
        return "very unlikely (<5%)"
    if p < 0.2:
        return "unlikely (5-20%)"
    if p < 0.4:
        return "possible (20-40%)"
    if p < 0.6:
        return "roughly even (40-60%)"
    if p < 0.8:
        return "likely (60-80%)"
    return "very likely (>80%)"


def brier_backtest(series: pd.Series, horizon: int, drop_pct: float, step: int = 7) -> dict:
    """Validate probability_reaching: at each origin, predict P(drop of drop_pct) from past data only
    and compare with what happened."""
    preds, outcomes = [], []
    vals = series.to_numpy(dtype=float)
    for i in origins(len(series), horizon, step, min_train=365):
        future = vals[i + 1: i + horizon + 1]
        if np.isnan(vals[i]) or np.isnan(future).any():
            continue
        hist = series.iloc[: i + 1]
        cur = vals[i]
        target = cur * (1 - drop_pct / 100)
        r = probability_reaching(hist, horizon, target, cur)
        if r["probability"] is None:
            continue
        preds.append(r["probability"])
        outcomes.append(float(future.min() <= target))
    if len(preds) < 8:
        return {"n": len(preds), "brier": None}
    p, o = np.array(preds), np.array(outcomes)
    base = float(np.mean(o))
    brier, clim = float(np.mean((p - o) ** 2)), float(np.mean((base - o) ** 2))
    return {"n": len(preds), "brier": brier, "brier_climatology": clim, "event_rate": base,
            "skill": brier < clim,
            "note": "Brier below the climatology reference means the method beat a constant base-rate guess."}
