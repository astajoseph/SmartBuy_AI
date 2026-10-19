"""TTL policy for on-demand vendor data. TTLs are configurable starting points, not a promise
that the underlying offer stays valid that long; offer_valid_until is checked separately."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import get_settings

KINDS = ("vendor_price", "emi", "review_aggregate", "review_samples")
NEGATIVE_CACHE_MINUTES = 15          # after a failed fetch, wait before hitting the vendor again
MAX_STALE_HOURS = {"vendor_price": 24, "emi": 72, "review_aggregate": 24 * 14, "review_samples": 24 * 30}


def ttl(kind: str) -> timedelta:
    s = get_settings()
    hours = {"vendor_price": s.ttl_vendor_price_hours, "emi": s.ttl_emi_hours,
             "review_aggregate": s.ttl_review_aggregate_hours, "review_samples": s.ttl_review_samples_hours}[kind]
    return timedelta(hours=hours)


def expiry_map(fetched_at: datetime) -> dict[str, str]:
    return {k: (fetched_at + ttl(k)).isoformat() for k in KINDS}


def is_fresh(snapshot: dict | None, kinds: tuple[str, ...], now: datetime | None = None) -> bool:
    if not snapshot or snapshot["fetch_status"] not in ("ok", "partial"):
        return False
    now = now or datetime.now(timezone.utc)
    exp = (snapshot.get("extraction_report") or {}).get("expires") or {}
    return all(k in exp and datetime.fromisoformat(exp[k]) > now for k in kinds)


def usable_when_stale(snapshot: dict | None, kinds: tuple[str, ...], now: datetime | None = None) -> bool:
    if not snapshot or snapshot["fetch_status"] not in ("ok", "partial"):
        return False
    now = now or datetime.now(timezone.utc)
    age = now - snapshot["fetched_at"]
    return all(age <= timedelta(hours=MAX_STALE_HOURS[k]) for k in kinds)


def freshness_label(fetched_at: datetime, origin: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    age_h = round((now - fetched_at).total_seconds() / 3600, 1)
    text = {
        "live": f"Fetched just now ({fetched_at:%d %b %Y %H:%M} UTC).",
        "cache": f"From cache, fetched {age_h} h ago ({fetched_at:%d %b %Y %H:%M} UTC); within its refresh window.",
        "stale_cache": f"STALE: vendor could not be reached; showing data fetched {age_h} h ago "
                       f"({fetched_at:%d %b %Y %H:%M} UTC). It may no longer be accurate.",
    }[origin]
    return {"origin": origin, "fetched_at": fetched_at.isoformat(), "age_hours": age_h, "label": text}
