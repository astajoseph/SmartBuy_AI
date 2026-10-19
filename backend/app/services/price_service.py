"""Historical price statistics and data-quality indicators.

Two views of the same data are used and always labelled:
  * recorded points  - prices the source / collector actually recorded (facts)
  * daily series     - one value per IST day; days without a record carry the previous recorded
                       price forward (a step-function assumption typical for price trackers) for at
                       most MAX_CARRY_FORWARD_DAYS. Longer gaps are left unknown (NaN) instead of
                       inventing a flat price. Carried-forward days are flagged, never shown as observations.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pandas as pd

from app.database.repositories import Repository
from app.extraction.validation import IST

PERIODS = {"7d": 7, "30d": 30, "90d": 90, "365d": 365}
MAX_CARRY_FORWARD_DAYS = 30


def points_frame(points: list[dict]) -> pd.DataFrame:
    if not points:
        return pd.DataFrame(columns=["ts", "price", "point_kind"])
    df = pd.DataFrame(points)[["ts", "price", "point_kind"]].copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df["price"] = df["price"].astype(float)
    return df.sort_values("ts").reset_index(drop=True)


def daily_series(df: pd.DataFrame, until: date | None = None,
                 max_carry_days: int = MAX_CARRY_FORWARD_DAYS) -> pd.DataFrame:
    """Columns: price (NaN = unknown), is_recorded, days_since_record. Index = IST calendar date.
    Extends to `until` (default: last record)."""
    if df.empty:
        return pd.DataFrame(columns=["price", "is_recorded", "days_since_record"])
    d = df.assign(day=df["ts"].dt.tz_convert(IST).dt.date).groupby("day")["price"].last()
    d.index = pd.to_datetime(d.index)
    end = pd.Timestamp(until) if until else d.index.max()
    full = pd.date_range(d.index.min(), max(end, d.index.max()), freq="D")
    out = pd.DataFrame(index=full)
    out["is_recorded"] = out.index.isin(d.index)
    last_day = pd.Series(full.where(out["is_recorded"]), index=full).ffill()
    out["days_since_record"] = (full - pd.DatetimeIndex(last_day)).days
    out["price"] = d.reindex(full).ffill().where(out["days_since_record"] <= max_carry_days)
    return out


def data_quality(df: pd.DataFrame, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if df.empty:
        return {"recorded_points": 0, "sufficient_for_stats": False, "sufficient_for_forecast": False}
    days = df["ts"].dt.tz_convert(IST).dt.normalize().drop_duplicates()
    gaps = days.diff().dt.days.dropna()
    span = (df["ts"].max() - df["ts"].min()).days
    daily = daily_series(df)
    return {
        "recorded_points": int(len(df)),
        "recorded_days": int(len(days)),
        "first_record": df["ts"].min().isoformat(),
        "last_record": df["ts"].max().isoformat(),
        "span_days": int(span),
        "median_gap_days": float(gaps.median()) if len(gaps) else None,
        "max_gap_days": int(gaps.max()) if len(gaps) else None,
        "days_since_last_record": round((now - df["ts"].max().to_pydatetime()).total_seconds() / 86400, 1),
        "carried_forward_share": round(float((~daily["is_recorded"] & daily["price"].notna()).mean()), 3) if len(daily) else None,
        "unknown_day_share": round(float(daily["price"].isna().mean()), 3) if len(daily) else None,
        "known_days": int(daily["price"].notna().sum()),
        "distinct_prices": int(df["price"].nunique()),
        "sufficient_for_stats": len(days) >= 5,
        "sufficient_for_forecast": len(days) >= 20 and int(daily["price"].notna().sum()) >= 120,
        "sufficient_for_seasonality": span >= 365 and len(days) >= 40,
    }


def history_summary(repo: Repository, product_id: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    df = points_frame(repo.price_points(product_id, "selling_price"))
    quality = data_quality(df, now)
    stated = {k: {"value": float(v["value"]), "observed_at": v["observed_at"].isoformat()}
              for k, v in repo.latest_source_stats(product_id).items()}
    latest_offer = repo.latest_price(product_id, "offer_price")
    last_sell = repo.latest_price(product_id, "selling_price")
    stale_offer = None
    if latest_offer and last_sell and (last_sell["ts"] - latest_offer["ts"]).days > 2:
        # an offer price recorded weeks ago is not a current offer; report it separately
        stale_offer, latest_offer = latest_offer, None
    if df.empty:
        return {"product_id": product_id, "current": None, "data_quality": quality, "source_stated_stats": stated,
                "limitations": ["No price data has been collected for this product yet."]}

    last = df.iloc[-1]
    current = float(last["price"])
    daily = daily_series(df, until=now.astimezone(IST).date())
    year = daily[daily.index >= daily.index.max() - pd.Timedelta(days=365)]
    changes = {}
    for label, n in PERIODS.items():
        ref_day = daily.index.max() - pd.Timedelta(days=n)
        if ref_day < daily.index.min():
            changes[label] = None
            continue
        if pd.isna(daily.loc[ref_day, "price"]):
            changes[label] = {"unknown": True, "reason": "No recorded price within 30 days of that date."}
            continue
        ref = float(daily.loc[ref_day, "price"])
        changes[label] = {"from": ref, "to": current, "abs": round(current - ref, 2),
                          "pct": round((current - ref) / ref * 100, 2), "reference_was_recorded": bool(daily.loc[ref_day, "is_recorded"])}
    year_known = year["price"].dropna()
    pct_rank = float((year_known < current).mean() * 100) if len(year_known) >= 60 else None

    limitations = []
    if quality["days_since_last_record"] > 2:
        limitations.append(f"Latest recorded price is {quality['days_since_last_record']} days old.")
    if quality.get("max_gap_days") and quality["max_gap_days"] > 14:
        limitations.append(f"Largest gap between recorded prices is {quality['max_gap_days']} days. Prices are carried "
                           f"forward for at most {MAX_CARRY_FORWARD_DAYS} days; beyond that they are treated as unknown.")
    if quality.get("unknown_day_share"):
        limitations.append(f"{quality['unknown_day_share']:.0%} of days in the history have no usable price.")
    limitations.append("Daily statistics assume the price stayed at the last recorded value until the next record "
                       f"(up to {MAX_CARRY_FORWARD_DAYS} days).")

    return {
        "product_id": product_id,
        "currency": "INR",
        "current": {"price": current, "as_of": last["ts"].isoformat(), "kind": last["point_kind"]},
        "current_offer_price": ({"price": float(latest_offer["price"]), "as_of": latest_offer["ts"].isoformat()}
                                if latest_offer else None),
        "last_offer_price_seen": ({"price": float(stale_offer["price"]), "as_of": stale_offer["ts"].isoformat()}
                                  if stale_offer else None),
        "recorded_points_stats": {"min": float(df["price"].min()), "max": float(df["price"].max()),
                                  "median": float(df["price"].median()), "n": int(len(df))},
        "daily_stats_all_time": _stats(daily["price"]),
        "daily_stats_last_365d": _stats(year["price"]),
        "current_percentile_last_365d": None if pct_rank is None else round(pct_rank, 1),
        "percentile_basis_days": int(len(year_known)),
        "historical_low": {"price": float(df["price"].min()),
                           "first_seen": df.loc[df["price"].idxmin(), "ts"].isoformat()},
        "changes": changes,
        "source_stated_stats": stated,
        "data_quality": quality,
        "limitations": limitations,
    }


def _stats(s: pd.Series) -> dict | None:
    s = s.dropna()
    if s.empty:
        return None
    return {"min": float(s.min()), "max": float(s.max()), "median": float(s.median()),
            "p10": float(np.percentile(s, 10)), "p90": float(np.percentile(s, 90)), "days": int(len(s))}


def chart_series(repo: Repository, product_id: str, days: int | None = None) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    out = {}
    for pt in ("selling_price", "offer_price"):
        out[pt] = [{"ts": r["ts"].isoformat(), "price": float(r["price"]), "kind": r["point_kind"],
                    "origin": r["data_origin"]} for r in repo.price_points(product_id, pt, since)]
    return out


def to_decimal(x: float | None) -> Decimal | None:
    return None if x is None else Decimal(str(round(x, 2)))
