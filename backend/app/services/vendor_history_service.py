"""Price history per vendor (and per marketplace seller): stock, delivery-adjusted price, how each vendor's price
relates to the main price-history series, and a forecast per vendor when its history is long enough.

Reads the database only - no vendor page is fetched here. Rows come from vendor_price_points (one per successful
vendor check) and vendor_seller_offers (marketplace sellers, written by seller discovery).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.database.repositories import Repository
from app.forecasting.forecasting import build_forecast
from app.services.price_service import IST, daily_series, points_frame

FRESH_HOURS = 72                  # a vendor price older than this is not used to name the cheapest vendor now
MIN_OVERLAP_DAYS = 10             # days both series have a price before a relation is reported
MAX_POINTS = 400                  # points returned per vendor series (most recent)


def _f(v) -> float | None:
    return None if v is None else float(v)


def _pct(new: float | None, old: float | None) -> float | None:
    return None if new is None or not old else round((new - old) / old * 100, 2)


def _series(points: list[dict], col: str, only_in_stock: bool = True) -> pd.Series:
    rows = [(p["observed_at"], float(p[col])) for p in points
            if p.get(col) is not None and (p["in_stock"] or not only_in_stock)]
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series([v for _, v in rows], index=pd.to_datetime([t for t, _ in rows], utc=True)).sort_index()
    return s


def _value_days_ago(s: pd.Series, now: datetime, days: int) -> float | None:
    """Last recorded value at or before now - days (needs a record that old, otherwise unknown)."""
    if s.empty:
        return None
    cutoff = pd.Timestamp(now - timedelta(days=days))
    older = s[s.index <= cutoff]
    return float(older.iloc[-1]) if len(older) else None


def vendor_stats(points: list[dict], now: datetime) -> dict:
    """Latest state, extremes, changes and stock record of one vendor listing."""
    last = points[-1]
    priced = _series(points, "price")
    eff = _series(points, "effective_price")
    changes = int((priced.diff().dropna() != 0).sum()) if len(priced) > 1 else 0
    in_stock_pts = [p for p in points if p["in_stock"]]
    cur = _f(last["price"]) if last["in_stock"] else None
    cur_eff = _f(last["effective_price"]) if last["in_stock"] else None
    last30 = priced[priced.index >= pd.Timestamp(now - timedelta(days=30))]
    return {
        "checks": len(points), "first_check": points[0]["observed_at"].isoformat(),
        "last_check": last["observed_at"].isoformat(),
        "age_hours": round((now - last["observed_at"]).total_seconds() / 3600, 1),
        "in_stock": bool(last["in_stock"]), "availability": last["availability"],
        "price": _f(last["price"]), "mrp": _f(last["mrp"]), "effective_price": _f(last["effective_price"]),
        "delivery_known": bool(last["delivery_known"]), "member_price": _f(last["member_price"]),
        "lowest": float(priced.min()) if len(priced) else None,
        "highest": float(priced.max()) if len(priced) else None,
        "median_30d": float(last30.median()) if len(last30) else None,
        "change_7d_pct": _pct(cur, _value_days_ago(priced, now, 7)),
        "change_30d_pct": _pct(cur, _value_days_ago(priced, now, 30)),
        "effective_change_7d_pct": _pct(cur_eff, _value_days_ago(eff, now, 7)),
        "price_changes": changes,
        "in_stock_share": round(len(in_stock_pts) / len(points), 3),
        "last_in_stock_at": in_stock_pts[-1]["observed_at"].isoformat() if in_stock_pts else None,
    }


def relation_to_reference(points: list[dict], ref_daily: pd.DataFrame) -> dict:
    """How this vendor's price relates to the main price-history series on the days both have a price."""
    s = _series(points, "price")
    if s.empty or ref_daily.empty:
        return {"overlap_days": 0, "note": "No overlapping prices."}
    v = s.groupby(s.index.tz_convert(IST).date).last()
    v.index = pd.to_datetime(v.index)
    both = pd.concat([v.rename("vendor"), ref_daily["price"].rename("ref")], axis=1, join="inner").dropna()
    n = len(both)
    if n < 1:
        return {"overlap_days": 0, "note": "No overlapping prices."}
    ratio = (both["vendor"] / both["ref"])
    out = {"overlap_days": n, "median_ratio": round(float(ratio.median()), 4),
           "latest_ratio": round(float(ratio.iloc[-1]), 4),
           "median_gap": round(float((both["vendor"] - both["ref"]).median()), 2), "correlation": None,
           "note": None}
    if n < MIN_OVERLAP_DAYS:
        out["note"] = f"Only {n} overlapping day(s); at least {MIN_OVERLAP_DAYS} are needed to compare movements."
    elif both["vendor"].nunique() < 2 or both["ref"].nunique() < 2:
        out["note"] = "One of the two prices did not change in the overlapping period, so movements cannot be compared."
    else:
        out["correlation"] = round(float(np.corrcoef(both["vendor"], both["ref"])[0, 1]), 3)
    return out


def vendor_forecast(points: list[dict], horizon: int, now: datetime) -> dict:
    """The same validated forecaster as the main price, on this vendor's in-stock price series."""
    rows = [{"ts": p["observed_at"], "price": p["price"], "point_kind": "vendor_check"}
            for p in points if p["in_stock"] and p["price"] is not None]
    f = build_forecast(points_frame(rows), horizon, now=now)
    keep = ("status", "message", "horizon_days", "forecast_method", "predicted_price", "lower_price_bound",
            "upper_price_bound", "data_points_used", "limitations")
    out = {k: f.get(k) for k in keep}
    q = f.get("data_quality") or {}
    out["history"] = {"recorded_days": q.get("recorded_days", 0), "span_days": q.get("span_days", 0)}
    return out


def seller_series(rows: list[dict], now: datetime) -> list[dict]:
    by: dict[int, list[dict]] = {}
    for r in rows:
        by.setdefault(r["seller_id"], []).append(r)
    out = []
    for sid, rs in by.items():
        priced = [float(r["price"]) for r in rs if r["price"] is not None and r["in_stock"]]
        last = rs[-1]
        out.append({
            "seller_id": sid, "seller": last["seller_name"], "marketplace": last["marketplace"],
            "is_active": last["is_active"], "offers": len(rs), "in_stock": bool(last["in_stock"]),
            "price": _f(last["price"]), "lowest": min(priced) if priced else None,
            "highest": max(priced) if priced else None, "last_check": last["observed_at"].isoformat(),
            "age_hours": round((now - last["observed_at"]).total_seconds() / 3600, 1),
            "points": [{"t": r["observed_at"].isoformat(), "price": _f(r["price"]), "in_stock": bool(r["in_stock"])}
                       for r in rs[-MAX_POINTS:]]})
    out.sort(key=lambda x: (not x["in_stock"], x["price"] if x["price"] is not None else 1e18))
    return out


def vendor_history(repo: Repository, product_id: str, *, days: int = 180, horizon: int = 14,
                   now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    rows = repo.vendor_price_points(product_id, since=since)
    by: dict[int, list[dict]] = {}
    for r in rows:
        by.setdefault(r["vendor_source_id"], []).append(r)

    ref_daily = daily_series(points_frame(repo.price_points(product_id, "selling_price")))
    vendors = []
    for sid, pts in by.items():
        st = vendor_stats(pts, now)
        vendors.append({
            "vendor_source_id": sid, "vendor_id": pts[-1]["vendor_id"], "vendor": pts[-1]["vendor_name"],
            "url": pts[-1]["source_url"], "is_active": pts[-1]["is_active"], "stats": st,
            "relation_to_main_price": relation_to_reference(pts, ref_daily),
            "forecast": vendor_forecast(pts, horizon, now),
            "points": [{"t": p["observed_at"].isoformat(), "price": _f(p["price"]),
                        "effective_price": _f(p["effective_price"]), "in_stock": bool(p["in_stock"])}
                       for p in pts[-MAX_POINTS:]]})
    vendors.sort(key=lambda v: (not v["stats"]["in_stock"], v["stats"]["effective_price"] or 1e18))

    live = [v for v in vendors if v["is_active"] and v["stats"]["in_stock"] and v["stats"]["effective_price"] is not None
            and v["stats"]["age_hours"] <= FRESH_HOURS]
    comparison = {"as_of": now.isoformat(), "compared": len(live), "cheapest_now": None, "spread_pct": None,
                  "excluded": [{"vendor": v["vendor"], "reason": "out of stock" if not v["stats"]["in_stock"] else
                                f"last checked {v['stats']['age_hours']:.0f} h ago"}
                               for v in vendors if v["is_active"] and v not in live]}
    if live:
        cheapest = live[0]
        comparison["cheapest_now"] = {"vendor": cheapest["vendor"], "effective_price": cheapest["stats"]["effective_price"],
                                      "price": cheapest["stats"]["price"],
                                      "delivery_included": cheapest["stats"]["delivery_known"]}
        if len(live) > 1:
            lo, hi = live[0]["stats"]["effective_price"], live[-1]["stats"]["effective_price"]
            comparison["spread_pct"] = round((hi - lo) / lo * 100, 2)
    return {"product_id": product_id, "days": days, "vendors": vendors, "comparison": comparison,
            "sellers": seller_series(repo.seller_price_series(product_id, since=since), now),
            "notes": ["effective_price = displayed price + delivery when the page states the delivery charge; "
                      "otherwise the displayed price.",
                      "A vendor's forecast needs the same history as the main price (>= 20 recorded days over >= 120 days); "
                      "until then it is withheld."]}
