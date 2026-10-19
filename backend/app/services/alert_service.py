"""Price alerts and notifications (stored in-app; no e-mail/SMS provider is wired up).

Each notification states its category (observed / forecast / vendor_offer / recommendation / event)
and the observation timestamp. A unique dedupe_key plus a cooldown prevents repeats.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config import get_settings
from app.database.repositories import Repository


def _notify(repo: Repository, *, product_id: str, kind: str, category: str, message: str, observed_at: datetime,
            dedupe_key: str, user_id=None, payload: dict | None = None) -> bool:
    cooldown = timedelta(hours=get_settings().alert_cooldown_hours)
    if repo.recent_notification_exists(product_id, kind, user_id, datetime.now(timezone.utc) - cooldown):
        return False
    return repo.insert_notification({"user_id": user_id, "product_id": product_id, "kind": kind, "category": category,
                                     "message": message, "observed_at": observed_at, "dedupe_key": dedupe_key,
                                     "payload": payload or {}})


def evaluate_product_alerts(repo: Repository, product_id: str) -> int:
    latest = repo.latest_price(product_id, "selling_price")
    if not latest:
        return 0
    sent = 0
    price: Decimal = latest["price"]
    ts: datetime = latest["ts"]
    ts_txt = f"{ts:%d %b %Y %H:%M} UTC"

    for t in repo.targets_for_product(product_id):
        if t["target_price"] is not None and price <= t["target_price"]:
            sent += _notify(repo, product_id=product_id, kind="target_reached", category="observed", user_id=t["user_id"],
                            message=f"Observed price ₹{price} is at or below your target ₹{t['target_price']} (seen {ts_txt}).",
                            observed_at=ts, dedupe_key=f"target:{t['user_id']}:{product_id}:{t['target_price']}:{ts.isoformat()}")

    pts = repo.price_points(product_id, "selling_price", since=ts - timedelta(days=30))
    previous = [p for p in pts if p["ts"] < ts]
    if previous:
        prev = previous[-1]["price"]
        drop = (prev - price) / prev * 100
        if drop >= Decimal(str(get_settings().significant_drop_percent)):
            sent += _notify(repo, product_id=product_id, kind="price_drop", category="observed",
                            message=f"Observed price fell {drop:.1f}% from ₹{prev} to ₹{price} (seen {ts_txt}).",
                            observed_at=ts, dedupe_key=f"drop:{product_id}:{ts.isoformat()}")

    obs = repo.latest_observation(product_id)
    if obs and obs["availability"] == "InStock":
        prev_obs = repo._one(  # noqa: SLF001 - small one-off query
            """select availability from price_observations where product_id = %s and price_type = 'selling_price'
               and observed_at < %s order by observed_at desc limit 1""", (product_id, obs["observed_at"]))
        if prev_obs and prev_obs["availability"] and prev_obs["availability"] != "InStock":
            sent += _notify(repo, product_id=product_id, kind="back_in_stock", category="observed",
                            message=f"Product is shown as in stock again (seen {obs['observed_at']:%d %b %Y %H:%M} UTC).",
                            observed_at=obs["observed_at"], dedupe_key=f"stock:{product_id}:{obs['observed_at'].isoformat()}")

    today = datetime.now(timezone.utc).date()
    for e in repo.list_sale_events(from_date=today):
        if e["start_date"] and 0 <= (e["start_date"] - today).days <= 7:
            sent += _notify(repo, product_id=product_id, kind="sale_upcoming", category="event",
                            message=f"{e['event_name']} ({e['retailer']}) starts {e['start_date']:%d %b %Y} "
                                    f"[{e['verification_status'].replace('_', ' ')}].",
                            observed_at=datetime.now(timezone.utc),
                            dedupe_key=f"event:{product_id}:{e['id']}")
    return sent


def notify_recommendation_change(repo: Repository, product_id: str, user_id, old: str | None, new: str) -> None:
    if old and old != new:
        now = datetime.now(timezone.utc)
        _notify(repo, product_id=product_id, kind="recommendation_changed", category="recommendation", user_id=user_id,
                message=f"Recommendation changed from {old} to {new}.", observed_at=now,
                dedupe_key=f"rec:{user_id}:{product_id}:{now.isoformat()}")
