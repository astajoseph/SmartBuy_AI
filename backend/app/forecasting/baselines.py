"""Interpretable baseline forecasters on a daily (carry-forward) price series.

Each takes the history up to and including the forecast origin (NaN = unknown day, ignored) and returns the point forecast
for `horizon` days later. They deliberately avoid heavy models: price-tracker histories are
short and step-shaped, and any extra complexity must beat `naive_last` on chronological backtests
(see evaluation.py) before it is used.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

Forecaster = Callable[[pd.Series, int], float]


def naive_last(history: pd.Series, horizon: int) -> float:
    return float(history.dropna().iloc[-1])


def median_window(days: int) -> Forecaster:
    def f(history: pd.Series, horizon: int) -> float:
        return float(history.iloc[-days:].dropna().median())
    f.__name__ = f"median_{days}d"
    return f


def ewma(halflife_days: float) -> Forecaster:
    def f(history: pd.Series, horizon: int) -> float:
        return float(history.dropna().ewm(halflife=halflife_days).mean().iloc[-1])
    f.__name__ = f"ewma_hl{int(halflife_days)}d"
    return f


def damped_trend(window: int = 60, damping: float = 0.5) -> Forecaster:
    """Last value plus a damped linear trend fitted to the last `window` days."""
    def f(history: pd.Series, horizon: int) -> float:
        w = history.iloc[-window:]
        mask = w.notna().to_numpy()
        if mask.sum() < 10:
            return float(history.dropna().iloc[-1])
        slope = np.polyfit(np.arange(len(w))[mask], w.to_numpy(dtype=float)[mask], 1)[0]
        return float(history.dropna().iloc[-1] + damping * slope * horizon)
    f.__name__ = f"damped_trend_{window}d"
    return f


BASELINES: dict[str, Forecaster] = {
    "naive_last": naive_last,
    "median_30d": median_window(30),
    "median_90d": median_window(90),
    "ewma_hl14d": ewma(14),
    "damped_trend_60d": damped_trend(60),
}
