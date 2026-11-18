"""History-once collection, current price from store checks, and the daily store-check worker (no database needed)."""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services import price_service, vendor_service
from app.workers import store_check

NOW = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)


def _pt(src, store, hours_ago, price, in_stock=True, active=True):
    return {"vendor_source_id": src, "vendor_name": store, "source_url": f"https://{store.lower()}/p",
            "observed_at": NOW - timedelta(hours=hours_ago), "price": None if price is None else Decimal(str(price)),
            "in_stock": in_stock, "is_active": active}


class PointsRepo:
    def __init__(self, rows): self.rows = rows
    def vendor_price_points(self, product_id, since=None):
        return sorted([r for r in self.rows if since is None or r["observed_at"] >= since],
                      key=lambda r: (r["vendor_source_id"], r["observed_at"]))


def test_best_store_now_uses_latest_check_per_store_and_ignores_stale_or_out_of_stock():
    repo = PointsRepo([
        _pt(1, "Amazon.in", 30, 499), _pt(1, "Amazon.in", 2, 579),     # latest Amazon check wins (579, not 499)
        _pt(2, "Myntra", 5, 560),
        _pt(3, "Nykaa", 1, 450, in_stock=False),                         # out of stock: ignored
        _pt(4, "OldShop", 100, 300),                                     # older than 48 h: ignored
        _pt(5, "Gone", 1, 200, active=False),                            # deactivated source: ignored
    ])
    best = price_service.best_store_now(repo, "p", NOW)
    assert best["store"] == "Myntra" and best["price"] == 560 and best["stores_compared"] == 2


def test_best_store_now_none_without_recent_in_stock_prices():
    assert price_service.best_store_now(PointsRepo([_pt(1, "Amazon.in", 80, 499)]), "p", NOW) is None


class StoreRepo:
    def __init__(self, snaps): self.snaps = snaps
    def list_products(self): return [{"id": "p1", "is_active": True}]
    def list_price_sources(self, pid): return []        # no continuation store configured
    def list_vendor_sources(self, pid): return [{"id": i, "vendor_id": f"s{i}", "vendor_name": f"S{i}", "product_id": pid} for i in (1, 2, 3)]
    def latest_vendor_snapshot(self, sid):
        h = self.snaps.get(sid)
        return None if h is None else {"fetched_at": NOW - timedelta(hours=h)}


def test_store_check_skips_recent_pages_and_survives_errors(monkeypatch):
    calls = []
    async def fake_refresh(repo, vs, deps=None):
        calls.append(vs["id"])
        if vs["id"] == 3:
            raise RuntimeError("boom")
        return {"id": 99}, None
    monkeypatch.setattr(vendor_service, "refresh_vendor_source", fake_refresh)
    res = asyncio.run(store_check.check_all(StoreRepo({1: 2, 2: 30}), now=NOW))   # 1 checked 2 h ago, 2 30 h ago, 3 never
    assert calls == [2, 3]
    assert (res["skipped_recent"], res["checked"], res["ok"], res["failed"]) == (1, 2, 1, 1)
    assert "boom" in res["results"][1]["error"]


def test_in_stock_handles_any_capitalisation_and_shop_wording():
    from app.services.stock import in_stock
    for avail in ("OutOfStock", "OutofStock", "out_of_stock", "Sold out", "Currently unavailable.", "PreOrder"):
        assert not in_stock(avail, Decimal("100")), avail
    for avail in ("InStock", "In stock", "Only 2 left in stock", None):
        assert in_stock(avail, Decimal("100")), avail
    assert not in_stock("InStock", None)
