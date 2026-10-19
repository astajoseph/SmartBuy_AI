from app.recommendations.policies import EventSignal, PolicyInputs, decide

BASE = dict(current_price=75000, price_age_days=0.2, stats_sufficient=True, percentile_365=50, historical_low=70000,
            forecast_ok=True, forecast_horizon=14, forecast_point=75000, forecast_lower=72000, forecast_upper=76000,
            interval_coverage=0.8, effective_samples=30, probability_skill=True)


def test_price_below_target_buy_now():
    d = decide(PolicyInputs(**{**BASE, "target_price": 76000}))
    assert d.action == "BUY_NOW"


def test_price_above_target_with_likely_drop_wait():
    d = decide(PolicyInputs(**{**BASE, "target_price": 72000, "probability": 0.7, "probability_band": "likely (60-80%)"}))
    assert d.action == "WAIT" and d.wait_days == 14


def test_price_above_target_unlikely_and_not_low_monitor():
    d = decide(PolicyInputs(**{**BASE, "target_price": 68000, "probability": 0.05, "probability_band": "very unlikely (<5%)"}))
    assert d.action == "MONITOR"


def test_insufficient_history_with_target_matches_spec_example():
    d = decide(PolicyInputs(current_price=75000, target_price=68000, deadline_days=14, stats_sufficient=False))
    assert d.action == "MONITOR" and d.confidence == "LOW"
    assert any("₹7,000 above" in r for r in d.reasoning)


def test_no_price_is_insufficient_data():
    assert decide(PolicyInputs(current_price=None)).action == "INSUFFICIENT_DATA"


def test_imminent_deadline_buys_now_within_budget():
    d = decide(PolicyInputs(**{**BASE, "target_price": 70000, "deadline_days": 2, "max_budget": 80000}))
    assert d.action == "BUY_NOW" and any("deadline" in r for r in d.reasoning)


def test_imminent_deadline_over_budget_monitors():
    d = decide(PolicyInputs(**{**BASE, "deadline_days": 1, "max_budget": 70000}))
    assert d.action == "MONITOR"


def test_high_uncertainty_lowers_confidence():
    d = decide(PolicyInputs(**{**BASE, "target_price": 72000, "probability": 0.7, "probability_band": "likely",
                               "probability_skill": False}))
    assert d.confidence == "LOW" and any("base-rate" in r for r in d.risks)


def test_max_emi_exceeded_is_a_risk():
    d = decide(PolicyInputs(**{**BASE, "vendor_requested": True, "all_emi_over_limit": True}))
    assert any("maximum monthly payment" in r for r in d.risks)


def test_unconfirmed_event_does_not_trigger_wait():
    ev = EventSignal("Diwali Sale", 10, "recurring_unconfirmed", 3, 3, -10.0)
    d = decide(PolicyInputs(**{**BASE, "percentile_365": 80, "upcoming_events": [ev]}))
    assert d.action != "WAIT"


def test_supported_media_reported_event_can_justify_wait():
    ev = EventSignal("Big Billion Days", 10, "reported_by_media", 3, 3, -10.0)
    d = decide(PolicyInputs(**{**BASE, "percentile_365": 80, "upcoming_events": [ev]}))
    assert d.action == "WAIT" and d.wait_days == 10


def test_vendor_price_spread_suggests_comparing():
    d = decide(PolicyInputs(**{**BASE, "target_price": 76000, "vendor_requested": True, "vendor_price_spread_pct": 5}))
    assert d.action == "COMPARE_VENDORS"
