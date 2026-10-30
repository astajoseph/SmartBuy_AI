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


# ---- classical benchmark methods (implemented directly: no heavy dependency for statsforecast / sktime). They are
# candidates only: chosen_method still has to beat naive_last on the rolling backtest (evaluation.compare_baselines).
FIT_WINDOW = 180                     # most recent known days used to fit smoothing parameters
ALPHAS = (0.05, 0.1, 0.2, 0.35, 0.5, 0.7, 0.9)


def seasonal_naive(period: int = 7) -> Forecaster:
    """The value one full cycle before the target day (same weekday for period=7)."""
    def f(history: pd.Series, horizon: int) -> float:
        vals = history.to_numpy(dtype=float)
        i = len(vals) - period + ((horizon - 1) % period)
        if i < 0 or np.isnan(vals[i]):
            return float(history.dropna().iloc[-1])
        return float(vals[i])
    f.__name__ = f"seasonal_naive_{period}d"
    return f


def _ses(y: np.ndarray, alpha: float) -> tuple[float, float]:
    """(final level, one-step-ahead SSE) of simple exponential smoothing."""
    level, sse = y[0], 0.0
    for v in y[1:]:
        sse += (v - level) ** 2
        level = alpha * v + (1 - alpha) * level
    return level, sse


def _best_ses(y: np.ndarray) -> tuple[float, float]:
    """(alpha, level) with the lowest one-step SSE on y."""
    fits = [(a, *_ses(y, a)) for a in ALPHAS]
    a, level, _ = min(fits, key=lambda t: t[2])
    return a, level


def ses_auto(history: pd.Series, horizon: int) -> float:
    """ETS(A,N,N): simple exponential smoothing with alpha chosen on the recent history."""
    y = history.dropna().to_numpy(dtype=float)[-FIT_WINDOW:]
    return float(_best_ses(y)[1]) if len(y) >= 10 else float(y[-1])


def theta(history: pd.Series, horizon: int) -> float:
    """Standard Theta method: SES level plus half the linear-trend slope (Assimakopoulos & Nikolopoulos)."""
    y = history.dropna().to_numpy(dtype=float)[-FIT_WINDOW:]
    n = len(y)
    if n < 10:
        return float(y[-1])
    alpha, level = _best_ses(y)
    slope = np.polyfit(np.arange(n), y, 1)[0]
    return float(level + 0.5 * slope * ((horizon - 1) + 1 / alpha - ((1 - alpha) ** n) / alpha))


def holt_damped(phi: float = 0.9) -> Forecaster:
    """Holt's linear method with a damped trend (ETS(A,Ad,N)); alpha and beta chosen on the recent history."""
    def f(history: pd.Series, horizon: int) -> float:
        y = history.dropna().to_numpy(dtype=float)[-FIT_WINDOW:]
        if len(y) < 15:
            return float(y[-1])
        best = None
        for a in (0.1, 0.3, 0.6, 0.9):
            for b in (0.05, 0.2):
                level, trend, sse = y[0], 0.0, 0.0
                for v in y[1:]:
                    pred = level + phi * trend
                    sse += (v - pred) ** 2
                    new_level = a * v + (1 - a) * pred
                    trend = b * (new_level - level) + (1 - b) * phi * trend
                    level = new_level
                if best is None or sse < best[0]:
                    best = (sse, level, trend)
        _, level, trend = best
        return float(level + trend * sum(phi ** k for k in range(1, horizon + 1)))
    f.__name__ = f"holt_damped_phi{int(phi * 100)}"
    return f


BASELINES: dict[str, Forecaster] = {
    "naive_last": naive_last,
    "median_30d": median_window(30),
    "median_90d": median_window(90),
    "ewma_hl14d": ewma(14),
    "damped_trend_60d": damped_trend(60),
    "seasonal_naive_7d": seasonal_naive(7),
    "ses_auto": ses_auto,
    "theta": theta,
    "holt_damped": holt_damped(0.9),
}
