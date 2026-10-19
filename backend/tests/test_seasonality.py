from datetime import date, datetime, timedelta, timezone

from app.forecasting.seasonality import analyse_events, calendar_patterns, merge_event_records, upcoming_events
from app.services.price_service import points_frame


def _frame(start: date, values):
    return points_frame([{"ts": datetime(start.year, start.month, start.day, tzinfo=timezone.utc) + timedelta(days=i),
                          "price": v, "point_kind": "historical"} for i, v in enumerate(values)])


def _ev(name, s, e, status="observed_on_source_chart", retailer="flipkart"):
    return {"event_name": name, "start_date": s, "end_date": e, "verification_status": status, "retailer": retailer}


def test_incomplete_history_skips_events_outside_data():
    df = _frame(date(2026, 1, 1), [1000] * 60)
    res = analyse_events(df, [_ev("Big Billion Days", date(2025, 9, 22), date(2025, 10, 2))], today=date(2026, 10, 1))
    assert res["events"] == [] and res["skipped_events"][0]["reason"].startswith("outside")


def test_missing_end_date_is_not_analysed():
    df = _frame(date(2025, 1, 1), [1000] * 400)
    res = analyse_events(df, [_ev("Big Billion Days", date(2025, 9, 23), None, "reported_by_media")], today=date(2026, 10, 1))
    assert res["events"] == [] and res["skipped_events"][0]["reason"] == "end date unknown"


def test_duplicate_records_of_same_event_are_merged():
    evs = [_ev("Big Billion Days", date(2025, 9, 22), date(2025, 10, 2)),
           _ev("Big Billion Days", date(2025, 9, 23), None, "reported_by_media")]
    merged = merge_event_records(evs)
    assert len(merged) == 1 and merged[0]["end_date"] == date(2025, 10, 2)


def test_repeated_event_windows_and_drop_frequency():
    # SYNTHETIC: three yearly sales, each with a 10% dip during the event
    values = []
    start = date(2023, 1, 1)
    sales = [(date(2023, 10, 1), date(2023, 10, 7)), (date(2024, 10, 1), date(2024, 10, 7)), (date(2025, 10, 1), date(2025, 10, 7))]
    d = start
    while d <= date(2025, 12, 31):
        values.append(900 if any(s <= d <= e for s, e in sales) else 1000)
        d += timedelta(days=1)
    df = _frame(start, values)
    res = analyse_events(df, [_ev("BBD", s, e) for s, e in sales], today=date(2026, 6, 1))
    summary = res["by_event"][0]
    assert summary["comparable_events"] == 3 and summary["drop_frequency"] == "3/3"
    assert summary["median_pct_change"] == -10.0 and summary["sufficient"] is True
    assert summary["vs_non_event_periods"]["unusually_large"] is True


def test_insufficient_comparable_events_is_flagged():
    df = _frame(date(2025, 1, 1), [1000] * 300 + [900] * 7 + [1000] * 30)
    res = analyse_events(df, [_ev("BBD", date(2025, 10, 28), date(2025, 11, 3))], today=date(2027, 1, 1))
    for s in res["by_event"]:
        assert s["sufficient"] is False and "not enough" in s["note"]


def test_unconfirmed_events_are_labelled_and_never_dated():
    evs = [{"event_name": "Diwali Sale", "start_date": None, "end_date": None, "verification_status": "recurring_unconfirmed"},
           {"event_name": "BBD", "start_date": date(2026, 10, 20), "end_date": None, "verification_status": "reported_by_media"}]
    up = upcoming_events(evs, date(2026, 10, 16))
    assert up[0]["days_until"] == 4 and "not an official" in up[0]["status_label"]
    assert up[1]["days_until"] is None and "not confirmed" in up[1]["status_label"]


def test_short_history_does_not_claim_monthly_seasonality():
    cp = calendar_patterns(_frame(date(2026, 1, 1), [1000 + (i % 7) for i in range(120)]))
    assert cp["month"]["supported"] is False
