from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest

from app.extraction.base import CurrentPrice
from conftest import add_history, register_product

pytestmark = pytest.mark.db
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_insert_observation_and_duplicate_prevention(repo):
    sid, _ = register_product(repo)
    cp = [CurrentPrice(price=Decimal("1050"), price_type="selling_price", currency="INR", availability="InStock")]
    assert repo.insert_observations("test-product", sid, NOW, cp, "t@1") == 1
    assert repo.insert_observations("test-product", sid, NOW, cp, "t@1") == 0      # same observed_at -> no duplicate
    assert repo.latest_observation("test-product")["price"] == Decimal("1050.00")


def test_historical_points_idempotent_and_queryable(repo):
    sid, _ = register_product(repo)
    pts = [(NOW - timedelta(days=i), 1000 + i) for i in range(5)]
    assert add_history(repo, "test-product", sid, pts) == 5
    assert add_history(repo, "test-product", sid, pts) == 0
    rows = repo.price_points("test-product")
    assert [float(r["price"]) for r in rows] == [1004, 1003, 1002, 1001, 1000]
    assert all(r["point_kind"] == "historical" for r in rows)


def test_offer_and_selling_price_do_not_collide(repo):
    sid, _ = register_product(repo)
    add_history(repo, "test-product", sid, [(NOW, 1000)], "selling_price")
    assert add_history(repo, "test-product", sid, [(NOW, 900)], "offer_price") == 1


def test_zero_or_null_price_cannot_be_stored(repo):
    sid, _ = register_product(repo)
    with pytest.raises(psycopg.errors.CheckViolation):
        repo.insert_observations("test-product", sid, NOW, [CurrentPrice(price=Decimal("0"), price_type="selling_price",
                                                                        currency="INR")], "t")


def test_product_source_relationships_and_vendor_separation(repo):
    sid, vids = register_product(repo, vendors=[("vendor-a", "https://vendor-a.example/p/1")])
    assert [s["id"] for s in repo.list_price_sources("test-product")] == [sid]
    assert [v["id"] for v in repo.list_vendor_sources("test-product")] == vids
    due = repo.due_price_sources(NOW)
    assert [s["id"] for s in due] == [sid]          # vendor sources are never "due"
    assert repo.registered_hosts() == {"pricehistory.app", "vendor-a.example"}


def test_due_sources_respect_interval(repo):
    sid, _ = register_product(repo)
    repo.mark_source_attempt(sid, success=True)
    assert repo.due_price_sources(datetime.now(timezone.utc)) == []
    assert repo.due_price_sources(datetime.now(timezone.utc) + timedelta(hours=7))


def test_daily_view_flags_carried_forward_days(repo):
    sid, _ = register_product(repo)
    add_history(repo, "test-product", sid, [(datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc), 1000),
                                            (datetime(2026, 1, 4, 0, 0, tzinfo=timezone.utc), 900)])
    rows = repo.daily_prices("test-product")
    assert [(r["is_recorded"], float(r["price"])) for r in rows] == [(True, 1000), (False, 1000), (False, 1000), (True, 900)]


def test_vendor_snapshot_cache_retrieval_and_expiry(repo):
    from app.services import cache_service

    _, (vsid,) = register_product(repo, vendors=[("vendor-a", "https://vendor-a.example/p/1")])
    fetched = datetime.now(timezone.utc) - timedelta(hours=2)
    repo.insert_vendor_snapshot({"product_id": "test-product", "vendor_id": "vendor-a", "vendor_source_id": vsid,
                                 "fetched_at": fetched, "expires_at": fetched + timedelta(hours=1), "fetch_status": "ok",
                                 "price": Decimal("1099"), "extraction_report": {"expires": cache_service.expiry_map(fetched)}})
    snap = repo.latest_ok_vendor_snapshot(vsid)
    assert snap["price"] == Decimal("1099.00")
    assert cache_service.is_fresh(snap, ("emi",))               # 12 h EMI TTL
    assert not cache_service.is_fresh(snap, ("vendor_price",))  # 1 h price TTL expired
    assert cache_service.usable_when_stale(snap, ("vendor_price",))


def test_notifications_deduplicate(repo):
    register_product(repo)
    n = {"product_id": "test-product", "kind": "price_drop", "category": "observed", "message": "m",
         "observed_at": NOW, "dedupe_key": "k1"}
    assert repo.insert_notification(n) is True
    assert repo.insert_notification(n) is False


def test_sale_bands_without_retailer_are_not_stored_as_any(repo):
    from datetime import date
    from app.extraction.base import SaleBand

    assert repo.upsert_sale_bands([SaleBand("Big Billion Days", date(2025, 9, 22), date(2025, 10, 2), None)], "u") == 0
    assert repo.upsert_sale_bands([SaleBand("Big Billion Days", date(2025, 9, 22), date(2025, 10, 2), "flipkart.com")], "u") == 1
    assert [e["retailer"] for e in repo.list_sale_events()] == ["flipkart"]


def test_old_offer_price_is_not_reported_as_current(repo):
    from app.services.price_service import history_summary

    sid, _ = register_product(repo)
    add_history(repo, "test-product", sid, [(NOW - timedelta(days=90), 489)], "offer_price")
    add_history(repo, "test-product", sid, [(NOW - timedelta(days=90 - i), 500) for i in range(0, 90, 3)])
    s = history_summary(repo, "test-product", now=NOW)
    assert s["current_offer_price"] is None
    assert s["last_offer_price_seen"]["price"] == 489.0
