"""How often each price-history source should be checked.

A source's configured `collection_interval_minutes` is the baseline. It is adapted to the product's situation, within
the limits of the retailer (never below ADAPTIVE_MIN_INTERVAL_MINUTES, never above ADAPTIVE_MAX_INTERVAL_MINUTES unless
the configured interval is already longer):

  price alert set by a user ........ x 0.5   (the user is waiting for a drop)
  a sale event on / starting soon .. x 0.5   (prices move most then)
  volatile (>= 3 price changes / 14 d) x 0.5
  flat for 30 days (>= 6 points) ... x 2     (flat for 90 days: x 3)
  out of stock ..................... x 3     (until restocking becomes likely)
  repeated failures ................ x 2 from 3 failures, x 4 from 6   (be gentle with a source that blocks or errors)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.database.repositories import Repository

MIN_POINTS_FOR_FLAT = 6
VOLATILE_CHANGES_14D = 3


def adaptive_interval(base_minutes: int, signals: dict, consecutive_failures: int = 0) -> tuple[int, list[str]]:
    s = get_settings()
    if not s.adaptive_schedule:
        return base_minutes, []
    mult, why = 1.0, []
    if signals.get("has_alert"):
        mult *= 0.5
        why.append("price alert set (x0.5)")
    if signals.get("sale_on"):
        mult *= 0.5
        why.append("sale event on or starting within 2 days (x0.5)")
    if signals.get("changes_14d", 0) >= VOLATILE_CHANGES_14D:
        mult *= 0.5
        why.append(f"volatile: {signals['changes_14d']} price changes in 14 days (x0.5)")
    elif signals.get("points_90d", 0) >= 2 * MIN_POINTS_FOR_FLAT and signals.get("changes_90d", 1) == 0:
        mult *= 3
        why.append("price unchanged for 90 days (x3)")
    elif signals.get("points_30d", 0) >= MIN_POINTS_FOR_FLAT and signals.get("changes_30d", 1) == 0:
        mult *= 2
        why.append("price unchanged for 30 days (x2)")
    if signals.get("out_of_stock"):
        mult *= 3
        why.append("out of stock (x3)")
    if consecutive_failures >= 6:
        mult *= 4
        why.append(f"{consecutive_failures} failures in a row (x4)")
    elif consecutive_failures >= 3:
        mult *= 2
        why.append(f"{consecutive_failures} failures in a row (x2)")
    lo, hi = s.adaptive_min_interval_minutes, max(s.adaptive_max_interval_minutes, base_minutes)
    minutes = int(round(min(hi, max(lo, base_minutes * mult))))
    return minutes, why


def due_sources(repo: Repository, now: datetime | None = None) -> list[dict]:
    """Price-history sources that are due under their adaptive interval (oldest success first)."""
    now = now or datetime.now(timezone.utc)
    floor = get_settings().adaptive_min_interval_minutes if get_settings().adaptive_schedule else None
    candidates = repo.due_price_sources(now, interval_minutes=floor)
    if floor is None:
        return candidates
    signals = repo.schedule_signals(sorted({c["product_id"] for c in candidates}), now)
    due = []
    for c in candidates:
        minutes, why = adaptive_interval(c["collection_interval_minutes"], signals[c["product_id"]], c["consecutive_failures"])
        if c["last_success_at"] is None or c["last_success_at"] + timedelta(minutes=minutes) <= now:
            due.append({**c, "effective_interval_minutes": minutes, "schedule_reasons": why})
    return due


def schedule_overview(repo: Repository, now: datetime | None = None) -> list[dict]:
    """Every active source with its baseline and adaptive interval and when it is next due (for the admin view)."""
    now = now or datetime.now(timezone.utc)
    sources = [s for s in repo.list_price_sources() if s["is_active"]]
    signals = repo.schedule_signals(sorted({s["product_id"] for s in sources}), now)
    out = []
    for s in sources:
        minutes, why = adaptive_interval(s["collection_interval_minutes"], signals[s["product_id"]], s["consecutive_failures"])
        last = s["last_success_at"]
        out.append({"source_id": s["id"], "product_id": s["product_id"], "url": s["source_url"],
                    "base_interval_minutes": s["collection_interval_minutes"], "effective_interval_minutes": minutes,
                    "reasons": why, "signals": signals[s["product_id"]],
                    "last_success_at": last.isoformat() if last else None,
                    "next_due_at": (last + timedelta(minutes=minutes)).isoformat() if last else "now"})
    return out
