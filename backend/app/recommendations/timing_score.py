"""Explainable buy-timing score (0-100): how favourable is buying NOW, judged only from the price history.

It is descriptive, not a prediction: each component compares today's price with its own history (or uses a
validated forecast / supported upcoming sale when there is one). Components without enough data are left out and the
remaining weights are renormalised; `coverage` says how much of the score rests on data.
"""
from __future__ import annotations

from dataclasses import dataclass

# component weights (sum 100)
WEIGHTS = {"percentile_365d": 35, "vs_30d_median": 20, "vs_historical_low": 15, "forecast": 15, "upcoming_sale": 15}
LABELS = [(75, "Good time to buy"), (55, "Fair price"), (35, "Neutral"), (0, "Better to wait")]
MIN_COVERAGE = 0.5


@dataclass
class TimingInputs:
    current: float | None
    percentile_365: float | None = None          # share of the last year's known days that were cheaper (0-100)
    median_30d: float | None = None
    historical_low: float | None = None
    forecast_point: float | None = None          # only when the forecast passed its backtest
    supported_sale_days: int | None = None       # a confirmed sale with a supporting history starting in N days
    volatility_pct: float | None = None          # (p90 - p10) / median of the last year, in %
    price_age_days: float | None = None


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def buy_timing_score(i: TimingInputs) -> dict:
    if i.current is None:
        return {"score": None, "label": "No price", "coverage": 0.0, "components": [],
                "explanation": "No price has been recorded yet."}
    comps = []

    def add(name: str, favour: float, note: str) -> None:
        comps.append({"name": name, "weight": WEIGHTS[name], "favourable": round(favour, 3), "note": note})

    if i.percentile_365 is not None:
        add("percentile_365d", _clip(1 - i.percentile_365 / 100),
            f"Cheaper than {100 - i.percentile_365:.0f}% of the last year's days.")
    if i.median_30d:
        r = i.current / i.median_30d
        add("vs_30d_median", _clip((1.05 - r) / 0.15),
            f"{(r - 1) * 100:+.1f}% against the 30-day median of ₹{i.median_30d:,.0f}.")
    if i.historical_low:
        gap = (i.current - i.historical_low) / i.historical_low
        add("vs_historical_low", _clip(1 - gap / 0.20),
            "At the lowest price on record." if gap <= 0.001 else f"{gap * 100:.1f}% above the lowest recorded price.")
    if i.forecast_point:
        chg = i.forecast_point / i.current - 1
        add("forecast", _clip(0.5 + chg * 5),
            f"Validated forecast: {chg * 100:+.1f}% from today" + (" - a drop is expected." if chg < -0.01 else
                                                                   " - no drop expected." if chg > -0.01 else "."))
    if i.supported_sale_days is not None:
        add("upcoming_sale", 0.2, f"A sale that has lowered this price before starts in {i.supported_sale_days} day(s).")

    total_w = sum(c["weight"] for c in comps)
    coverage = round(total_w / sum(WEIGHTS.values()), 2)
    if not comps:
        return {"score": None, "label": "Not enough history", "coverage": 0.0, "components": [],
                "explanation": "Not enough price history to judge the timing."}
    score = round(sum(c["weight"] * c["favourable"] for c in comps) / total_w * 100)
    label = next(lab for floor, lab in LABELS if score >= floor)
    caveats = []
    if coverage < MIN_COVERAGE:
        caveats.append(f"Only {coverage:.0%} of the score's inputs are available; treat it as rough.")
    if i.price_age_days is not None and i.price_age_days > 7:
        caveats.append(f"The latest price is {i.price_age_days:.0f} days old.")
    if i.volatility_pct is not None and i.volatility_pct > 25:
        caveats.append(f"This price is volatile ({i.volatility_pct:.0f}% spread over the last year).")
    return {"score": score, "label": label, "coverage": coverage, "components": comps, "caveats": caveats,
            "explanation": "Score = weighted share of signals favouring a purchase today (100 = most favourable). "
                           "It describes where today's price sits in its own history; it does not predict the future."}
