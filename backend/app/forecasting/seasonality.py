"""Indian sale-event and calendar-pattern analysis.

Event windows: [start - pre_days, start) | [start, end] | (end, end + post_days].
Every figure comes with its sample size; a pattern is only called "supported" when the sample and
a statistical test justify it. Co-occurrence of an event and a lower price is described as an
association, never as the event causing the drop.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy import stats

from app.extraction.validation import IST
from app.services.price_service import daily_series

DEFAULT_PRE, DEFAULT_POST = 14, 14
MIN_EVENTS_FOR_PATTERN = 3
MIN_RECORDED_IN_WINDOW = 2


def event_window_stats(daily: pd.DataFrame, start: date, end: date, pre: int = DEFAULT_PRE,
                       post: int = DEFAULT_POST) -> dict | None:
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    if daily.empty or s - pd.Timedelta(days=pre) < daily.index.min() or s > daily.index.max():
        return None
    pre_w = daily.loc[s - pd.Timedelta(days=pre): s - pd.Timedelta(days=1)]
    dur_w = daily.loc[s: e]
    post_w = daily.loc[e + pd.Timedelta(days=1): e + pd.Timedelta(days=post)]
    prior90 = daily.loc[s - pd.Timedelta(days=90): s - pd.Timedelta(days=1)].dropna(subset=["price"])
    pre_w, dur_w, post_w = (w.dropna(subset=["price"]) for w in (pre_w, dur_w, post_w))
    if pre_w.empty or dur_w.empty:
        return None
    med_pre = float(pre_w["price"].median())
    min_dur = float(dur_w["price"].min())
    med_dur = float(dur_w["price"].median())
    recorded_in_window = int(pre_w["is_recorded"].sum() + dur_w["is_recorded"].sum() + post_w["is_recorded"].sum())
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "median_before": med_pre, "min_during": min_dur, "median_during": med_dur,
        "median_after": float(post_w["price"].median()) if len(post_w) else None,
        "pct_change_median": round((med_dur - med_pre) / med_pre * 100, 2),
        "pct_change_min": round((min_dur - med_pre) / med_pre * 100, 2),
        "abs_saving_at_min": round(med_pre - min_dur, 2),
        "price_dropped": min_dur < med_pre * 0.98,
        "recovered_after": (None if not len(post_w) else bool(float(post_w["price"].median()) >= med_pre * 0.98)),
        "low_vs_prior_90d_percentile": round(float((prior90["price"] < min_dur).mean() * 100), 1) if len(prior90) else None,
        "discount_days": int((dur_w["price"] < med_pre * 0.98).sum()),
        "recorded_points_in_windows": recorded_in_window,
        "data_quality": "ok" if recorded_in_window >= MIN_RECORDED_IN_WINDOW else "weak (mostly carried-forward values)",
    }


def non_event_baseline(daily: pd.DataFrame, event_days: set[pd.Timestamp], length: int, pre: int = DEFAULT_PRE,
                       step: int = 7) -> np.ndarray:
    """Same-shaped windows that do not overlap any event: median(window)/median(prior pre days) - 1."""
    out = []
    idx = daily.index
    for i in range(pre, len(idx) - length, step):
        s = idx[i]
        window = pd.date_range(s - pd.Timedelta(days=pre), s + pd.Timedelta(days=length + DEFAULT_POST))
        if any(d in event_days for d in window):
            continue
        if daily["is_recorded"].iloc[i - pre:i + length].sum() < MIN_RECORDED_IN_WINDOW:
            continue                                 # same data-quality bar as event windows
        before_w, during_w = daily["price"].iloc[i - pre:i], daily["price"].iloc[i:i + length]
        if before_w.isna().all() or during_w.isna().all():
            continue                                 # window lies in an unknown period
        before, during = before_w.median(), during_w.median()
        out.append(during / before - 1)
    return np.array(out)


_STATUS_RANK = {"observed_on_source_chart": 0, "confirmed_official": 1, "reported_by_media": 2, "calendar_date": 3}


def merge_event_records(events: list[dict]) -> list[dict]:
    """The same sale can be registered twice (e.g. a chart band and a media report). Records with the same
    name and retailer whose dates overlap or start within 3 days are merged; the record with an end date
    and the strongest verification wins."""
    dated = sorted((e for e in events if e.get("start_date")),
                   key=lambda e: (e["event_name"].lower(), e.get("retailer") or "any", e["start_date"]))
    merged: list[dict] = []
    for e in dated:
        if merged:
            m = merged[-1]
            same = (m["event_name"].lower() == e["event_name"].lower()
                    and (m.get("retailer") or "any") == (e.get("retailer") or "any"))
            m_end = m.get("end_date") or m["start_date"]
            if same and e["start_date"] <= m_end + timedelta(days=3):
                cands = sorted([m, e], key=lambda x: (x.get("end_date") is None,
                                                      _STATUS_RANK.get(x.get("verification_status"), 9)))
                merged[-1] = {**cands[0], "merged_records": m.get("merged_records", 1) + 1}
                continue
        merged.append(e)
    return merged


def analyse_events(points_df: pd.DataFrame, events: list[dict], *, today: date) -> dict:
    daily = daily_series(points_df)
    if daily.empty:
        return {"events": [], "by_event": [], "limitations": ["No price history."]}
    per_event, event_days, skipped = [], set(), []
    for ev in merge_event_records(events):
        if not ev.get("end_date"):
            skipped.append({"event_name": ev["event_name"], "start": ev["start_date"].isoformat(),
                            "reason": "end date unknown"})
            continue
        end = ev["end_date"]
        event_days.update(pd.date_range(ev["start_date"], end))
        if end >= today:
            continue                                     # future/ongoing events are not history
        st = event_window_stats(daily, ev["start_date"], end)
        if st is None:
            skipped.append({"event_name": ev["event_name"], "start": ev["start_date"].isoformat(),
                            "reason": "outside the recorded history or no known prices in the window"})
            continue
        per_event.append({"event_name": ev["event_name"], "retailer": ev.get("retailer"),
                          "verification_status": ev.get("verification_status"),
                          "source_url": ev.get("source_url"), **st})

    by_name: dict[str, list[dict]] = {}
    for r in per_event:
        if r["recorded_points_in_windows"] >= MIN_RECORDED_IN_WINDOW:
            by_name.setdefault(r["event_name"], []).append(r)
    summary = []
    for name, rows in by_name.items():
        changes = np.array([r["pct_change_median"] for r in rows])
        length = int(np.median([(date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days + 1 for r in rows]))
        baseline = non_event_baseline(daily, event_days, length)
        unusual = None
        if len(rows) >= MIN_EVENTS_FOR_PATTERN and len(baseline) >= 20:
            typical = float(np.median(changes))       # median: robust to one odd sale window
            pct = float((baseline * 100 < typical).mean() * 100)
            unusual = {"event_median_change_pct": round(typical, 2),
                       "share_of_non_event_windows_with_bigger_drop_pct": round(pct, 1),
                       "non_event_windows": int(len(baseline)),
                       "unusually_large": pct < 10}
        summary.append({
            "event_name": name, "comparable_events": len(rows),
            "drop_frequency": f"{sum(r['price_dropped'] for r in rows)}/{len(rows)}",
            "median_pct_change": round(float(np.median(changes)), 2),
            "median_saving_at_min": round(float(np.median([r["abs_saving_at_min"] for r in rows])), 2),
            "typical_discount_days": int(np.median([r["discount_days"] for r in rows])),
            "vs_non_event_periods": unusual,
            "sufficient": len(rows) >= MIN_EVENTS_FOR_PATTERN,
            "note": None if len(rows) >= MIN_EVENTS_FOR_PATTERN else
                    f"Only {len(rows)} comparable event(s); not enough to call this a pattern.",
        })
    weak = [r for r in per_event if r["recorded_points_in_windows"] < MIN_RECORDED_IN_WINDOW]
    limitations = ["Event effects are associations with price changes, not proof that the event caused them.",
                   "Unrecorded days carry the last recorded price forward for up to 30 days; longer gaps are unknown."]
    if weak:
        limitations.append(f"{len(weak)} event window(s) had fewer than {MIN_RECORDED_IN_WINDOW} recorded prices and "
                           "are shown but excluded from the summary.")
    return {"events": per_event, "by_event": summary, "skipped_events": skipped, "limitations": limitations}


def calendar_patterns(points_df: pd.DataFrame) -> dict:
    """Day-of-week and month effects on *recorded* prices relative to their local level."""
    if points_df.empty:
        return {"day_of_week": None, "month": None, "month_end": None}
    df = points_df.copy()
    df["local"] = df["ts"].dt.tz_convert(IST)
    daily = daily_series(points_df)
    level = daily["price"].rolling(61, center=True, min_periods=15).median()   # NaN where history is unknown
    df["day"] = pd.to_datetime(df["local"].dt.date)
    df["rel"] = df["price"].to_numpy() / level.reindex(df["day"]).to_numpy() - 1
    df = df.dropna(subset=["rel"])
    span = (points_df["ts"].max() - points_df["ts"].min()).days

    def test(groups: dict, label: str, min_per_group: int) -> dict:
        valid = {k: v for k, v in groups.items() if len(v) >= min_per_group}
        res = {"groups": {str(k): {"n": len(v), "median_rel_pct": round(float(np.median(v)) * 100, 2)}
                          for k, v in groups.items() if len(v)}}
        if len(valid) < 3:
            return {**res, "supported": False, "reason": f"Too few recorded prices per {label} group."}
        h, p = stats.kruskal(*valid.values())
        return {**res, "test": "Kruskal-Wallis", "p_value": round(float(p), 4),
                "supported": bool(p < 0.05), "reason": "p < 0.05" if p < 0.05 else "No significant difference."}

    dow = test({d: df.loc[df["local"].dt.dayofweek == d, "rel"].to_numpy() for d in range(7)}, "weekday", 5)
    dow["note"] = "Recording days depend on when the tracker checked prices, which can bias weekday comparisons."
    years_per_month = df.groupby(df["local"].dt.month)["local"].apply(lambda x: x.dt.year.nunique())
    multi_year_months = [int(m) for m, n in years_per_month.items() if n >= 2]
    if span < 730:
        month = {"supported": False, "reason": f"History spans {span} days; at least two years are needed for monthly seasonality."}
    elif len(multi_year_months) < 6:
        month = {"supported": False,
                 "reason": f"Only {len(multi_year_months)} calendar month(s) have recorded prices from two or more years; "
                           "a month effect cannot be separated from a one-off price change.",
                 "groups": {str(m): {"n": int((df["local"].dt.month == m).sum())} for m in range(1, 13)
                            if (df["local"].dt.month == m).any()}}
    else:
        month = test({m: df.loc[df["local"].dt.month == m, "rel"].to_numpy() for m in multi_year_months}, "month", 3)
    me = df["local"].dt.day >= (df["local"].dt.days_in_month - 4)
    a, b = df.loc[me, "rel"].to_numpy(), df.loc[~me, "rel"].to_numpy()
    if len(a) >= 5 and len(b) >= 5:
        u, p = stats.mannwhitneyu(a, b)
        month_end = {"n_month_end": int(len(a)), "n_other": int(len(b)),
                     "median_rel_pct_month_end": round(float(np.median(a)) * 100, 2),
                     "median_rel_pct_other": round(float(np.median(b)) * 100, 2),
                     "p_value": round(float(p), 4), "supported": bool(p < 0.05)}
    else:
        month_end = {"supported": False, "reason": "Too few recorded prices at month-end."}
    return {"day_of_week": dow, "month": month, "month_end": month_end,
            "method": "Recorded prices divided by a 61-day centred rolling median of the daily series."}


def upcoming_events(events: list[dict], today: date, horizon_days: int = 120) -> list[dict]:
    out = []
    for e in events:
        sd = e.get("start_date")
        if sd and today <= (e.get("end_date") or sd) and sd <= today + timedelta(days=horizon_days):
            out.append({**e, "days_until": (sd - today).days, "status_label": status_label(e["verification_status"])})
        elif not sd and e.get("verification_status") == "recurring_unconfirmed":
            out.append({**e, "days_until": None, "status_label": status_label(e["verification_status"])})
    return sorted(out, key=lambda e: (e["days_until"] is None, e["days_until"] or 0))


def status_label(status: str) -> str:
    return {
        "confirmed_official": "Confirmed by the retailer",
        "reported_by_media": "Dates reported by media (not an official announcement)",
        "observed_on_source_chart": "Past sale window shown on the price-history source",
        "calendar_date": "Festival calendar date",
        "recurring_unconfirmed": "Recurring event; next dates not confirmed",
        "hypothetical": "Hypothetical window based on past patterns",
    }.get(status, status)
