"""Deterministic BUY / WAIT / MONITOR policy. Pure function of PolicyInputs -> PolicyDecision, unit tested
in tests/test_recommendations.py. Thresholds are named constants so they can be reviewed and tuned."""
from __future__ import annotations

from dataclasses import dataclass, field

POLICY_VERSION = "policy-1.0"
IMMINENT_DEADLINE_DAYS = 3
STALE_PRICE_DAYS = 7
LIKELY_P = 0.6
UNLIKELY_P = 0.2
ATTRACTIVE_PERCENTILE = 20
HIGH_PERCENTILE = 70


@dataclass
class EventSignal:
    name: str
    days_until: int
    verification_status: str
    past_events: int
    past_drops: int
    median_pct_change: float | None


@dataclass
class PolicyInputs:
    current_price: float | None
    price_age_days: float | None = None
    target_price: float | None = None
    max_budget: float | None = None
    deadline_days: int | None = None
    stats_sufficient: bool = False
    percentile_365: float | None = None
    historical_low: float | None = None
    forecast_ok: bool = False
    forecast_horizon: int | None = None
    forecast_point: float | None = None
    forecast_lower: float | None = None
    forecast_upper: float | None = None
    interval_coverage: float | None = None
    probability: float | None = None
    probability_band: str | None = None
    effective_samples: float | None = None
    probability_skill: bool | None = None
    upcoming_events: list[EventSignal] = field(default_factory=list)
    available: bool | None = None
    vendor_requested: bool = False
    all_emi_over_limit: bool = False
    vendor_price_spread_pct: float | None = None


@dataclass
class PolicyDecision:
    action: str
    confidence: str
    reasoning: list[str]
    risks: list[str]
    suggested_action: str
    wait_days: int | None = None
    expected_saving: float | None = None
    saving_range: list[float] | None = None
    savings_explanation: str | None = None


def _inr(x: float) -> str:
    return f"₹{x:,.0f}"


def _confidence(i: PolicyInputs) -> str:
    if not i.forecast_ok or (i.price_age_days is not None and i.price_age_days > 2):
        return "LOW" if not i.stats_sufficient or (i.price_age_days or 0) > STALE_PRICE_DAYS else "MEDIUM"
    if i.probability is not None and i.probability_skill is False:
        return "LOW"
    if (i.interval_coverage or 0) >= 0.7 and (i.effective_samples or 0) >= 20 and i.probability_skill is not False:
        return "HIGH"
    return "MEDIUM"


def _supportive_event(i: PolicyInputs, within: int | None) -> EventSignal | None:
    for e in i.upcoming_events:
        if e.verification_status not in ("confirmed_official", "reported_by_media"):
            continue
        if within is not None and e.days_until > within:
            continue
        if e.past_events >= 3 and e.past_drops / e.past_events >= 0.6:
            return e
    return None


def decide(i: PolicyInputs) -> PolicyDecision:
    reasons, risks = [], []
    conf = _confidence(i)

    if i.current_price is None:
        return PolicyDecision("INSUFFICIENT_DATA", "LOW", ["No current price has been collected for this product."],
                              [], "Wait for the first successful price collection, then ask again.")
    cur = i.current_price
    if i.price_age_days is not None and i.price_age_days > STALE_PRICE_DAYS:
        risks.append(f"The latest recorded price is {i.price_age_days:.0f} days old; the live price may differ.")
    if i.available is False:
        risks.append("The source last showed the product as unavailable.")
    if i.vendor_requested and i.all_emi_over_limit:
        risks.append("None of the EMI plans found fits your maximum monthly payment.")
    target = i.target_price
    if i.max_budget is not None and (target is None or i.max_budget < target):
        target = i.max_budget
        if i.target_price is None:
            reasons.append(f"Your maximum budget {_inr(i.max_budget)} is used as the target price.")

    if i.forecast_ok and i.forecast_point is not None and i.forecast_lower is not None:
        exp = round(cur - i.forecast_point, 2)
        rng = [round(cur - i.forecast_upper, 2), round(cur - i.forecast_lower, 2)]
    else:
        exp, rng = None, None

    def done(action, suggested, wait=None, saving=None, srange=None, confidence=None, explanation=None):
        if saving is not None and explanation is None:
            explanation = (f"Forecast-based estimate: today's price minus the {i.forecast_horizon}-day point forecast; "
                           "the range uses the forecast interval. Not a guaranteed saving.")
        if i.vendor_requested and action == "BUY_NOW" and (i.vendor_price_spread_pct or 0) > 2:
            action = "COMPARE_VENDORS"
            reasons.append(f"Vendor prices differ by {i.vendor_price_spread_pct:.1f}%; choose the vendor carefully.")
            suggested = "Buy now, but use the vendor comparison to pick the cheapest suitable option."
        return PolicyDecision(action, confidence or conf, reasons, risks, suggested, wait, saving, srange,
                              explanation if saving is not None else None)

    # 1. at or below target
    if target is not None and cur <= target:
        reasons.append(f"The current price {_inr(cur)} is at or below your target {_inr(target)}.")
        return done("BUY_NOW", "Buy now if the product suits you; the price meets your target.",
                    confidence="HIGH" if (i.price_age_days or 0) <= 2 else "MEDIUM")
    if target is not None:
        reasons.append(f"The current price {_inr(cur)} is {_inr(cur - target)} above your target {_inr(target)}.")

    # 2. imminent deadline
    if i.deadline_days is not None and i.deadline_days <= IMMINENT_DEADLINE_DAYS:
        reasons.append(f"Your purchase deadline is {i.deadline_days} day(s) away, leaving little room to wait.")
        if i.max_budget is None or cur <= i.max_budget:
            risks.append("A lower price may appear after you buy.")
            return done("BUY_NOW", "Buy before your deadline; waiting a few days is unlikely to be worth the risk.")
        risks.append("The current price exceeds your budget.")
        return done("MONITOR", f"The price is above your budget. Buy only if it falls to {_inr(i.max_budget)} before your deadline.")

    # 3. not enough data
    if not i.stats_sufficient:
        reasons.append("There is not enough price history to judge whether this price is good.")
        if target is not None:
            return done("MONITOR", f"Keep tracking and reconsider if the price reaches {_inr(target)}.", confidence="LOW")
        return done("INSUFFICIENT_DATA", "Keep tracking the price; ask again once more history is available.", confidence="LOW")

    if i.percentile_365 is not None:
        reasons.append(f"Over the last year, the price was lower than today on {i.percentile_365:.0f}% of days.")
    if i.historical_low is not None:
        reasons.append(f"The lowest recorded price is {_inr(i.historical_low)}.")

    within = i.deadline_days if i.deadline_days is not None else (i.forecast_horizon or 30)
    event = _supportive_event(i, within)

    # 4. target with probability
    if target is not None:
        if i.probability is None:
            reasons.append("Historical data is insufficient to estimate how likely the target is within the period.")
            if event:
                reasons.append(f"{event.name} is expected in {event.days_until} days; the price dropped in "
                               f"{event.past_drops} of {event.past_events} past occurrences.")
                return done("WAIT", f"Wait for {event.name} and buy if the price reaches {_inr(target)}.",
                            wait=event.days_until, saving=exp, srange=rng)
            return done("BUY_IF_TARGET_REACHED", f"Set an alert at {_inr(target)} and buy if it is reached.",
                        wait=within, saving=exp, srange=rng)
        reasons.append(f"Based on past price movements, reaching {_inr(target)} within {i.forecast_horizon} days is "
                       f"{i.probability_band}.")
        if i.probability_skill is False:
            risks.append("This probability method has not beaten a simple base-rate guess in backtests.")
        p_saving = round(i.probability * (cur - target), 2)
        p_expl = (f"Probability-weighted expected saving: chance of reaching the target × the {_inr(cur - target)} gap. "
                  f"If the target is reached you save {_inr(cur - target)}; if not, nothing. Not a guaranteed saving.")
        if i.probability >= LIKELY_P:
            risks.append("Past patterns may not repeat; the price could stay high or the product could sell out.")
            return done("WAIT", f"Wait up to {i.forecast_horizon} days with an alert at {_inr(target)}.",
                        wait=i.forecast_horizon, saving=p_saving, srange=[0.0, round(cur - target, 2)], explanation=p_expl)
        if i.probability >= UNLIKELY_P or event:
            if event:
                reasons.append(f"{event.name} is expected in {event.days_until} days; the price dropped in "
                               f"{event.past_drops} of {event.past_events} past occurrences.")
            risks.append("The target may not be reached.")
            return done("BUY_IF_TARGET_REACHED", f"Monitor for up to {within} days and buy if it reaches {_inr(target)}.",
                        wait=within, saving=p_saving, srange=[0.0, round(cur - target, 2)], explanation=p_expl)
        risks.append("The target is unlikely to be reached in this period based on history.")
        if i.percentile_365 is not None and i.percentile_365 <= 30:
            reasons.append("Today's price is already low compared with the past year.")
            return done("BUY_NOW", "Buy now if you need it; consider whether your target is realistic.")
        return done("MONITOR", "Keep monitoring; if you need the product soon, reconsider your target price.")

    # 5. no target: judge by history and events
    if i.percentile_365 is not None and i.percentile_365 <= ATTRACTIVE_PERCENTILE:
        reasons.append("Today's price is among the lowest seen over the past year.")
        risks.append("A lower price may still appear during a future sale.")
        return done("BUY_NOW", "Today's price is historically attractive; buying now is reasonable.")
    if event and (i.percentile_365 is None or i.percentile_365 >= HIGH_PERCENTILE):
        reasons.append(f"{event.name} is expected in {event.days_until} days; the price dropped in "
                       f"{event.past_drops} of {event.past_events} past occurrences.")
        risks.append("Sale dates or discounts may differ from previous years.")
        return done("WAIT", f"Consider waiting for {event.name} if you can.", wait=event.days_until, saving=exp, srange=rng)
    if i.forecast_ok and i.forecast_upper is not None and i.forecast_upper < cur:
        reasons.append(f"Even the upper end of the {i.forecast_horizon}-day forecast range is below today's price.")
        return done("WAIT", f"Waiting about {i.forecast_horizon} days may lower the price.", wait=i.forecast_horizon,
                    saving=exp, srange=rng)
    reasons.append("Today's price is neither unusually low nor unusually high.")
    return done("MONITOR", "Set a target price and an alert; buy when it is reached or when you need the product.",
                saving=exp, srange=rng)
