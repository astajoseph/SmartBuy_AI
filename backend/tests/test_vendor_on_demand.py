"""Vendor pages must be fetched only on demand, cache first."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.security.url_validation import FetchError
from app.services import vendor_service
from app.workers.price_collector import CollectorDeps, collect_due
from conftest import PH_CHARTS, PH_HTML, PH_URL, FakeBrowser, FakeFetcher, FakePage, register_product
from test_extraction import VENDOR_HTML

pytestmark = pytest.mark.db
VENDOR_URL = "https://vendor-a.example/p/1"


def _deps(fetcher, browser=None):
    return vendor_service.VendorDeps(fetcher_factory=lambda hosts: fetcher, browser_factory=lambda: browser)


def test_scheduled_collection_never_touches_vendor_urls(repo):
    register_product(repo, vendors=[("vendor-a", VENDOR_URL)])
    fetcher = FakeFetcher({PH_URL: FakePage(200, PH_HTML), VENDOR_URL: FakePage(200, VENDOR_HTML)})
    browser = FakeBrowser(charts=PH_CHARTS)
    results = asyncio.run(collect_due(repo, deps=CollectorDeps(fetcher_factory=lambda h: fetcher,
                                                               browser_factory=lambda: browser)))
    assert results[0]["status"] in ("success", "partial")
    assert VENDOR_URL not in fetcher.requested and VENDOR_URL not in browser.rendered
    assert fetcher.requested == [PH_URL]


def test_price_history_endpoints_do_not_fetch_vendors(repo, monkeypatch):
    from app.main import app

    register_product(repo, vendors=[("vendor-a", VENDOR_URL)])

    async def boom(*a, **k):
        raise AssertionError("vendor data requested by a price-history endpoint")

    monkeypatch.setattr(vendor_service, "get_vendor_data", boom)
    c = TestClient(app)
    for path in ("/api/products", "/api/products/test-product", "/api/products/test-product/history",
                 "/api/products/test-product/prices", "/api/products/test-product/forecast",
                 "/api/products/test-product/seasonality", "/api/products/test-product/daily"):
        assert c.get(path).status_code == 200, path
    r = c.post("/api/recommendations", json={"product_id": "test-product"})
    assert r.status_code == 200 and r.json()["vendor_data_accessed"] is False


def test_fetch_when_missing_then_reuse_cache(repo):
    register_product(repo, vendors=[("vendor-a", VENDOR_URL)])
    fetcher = FakeFetcher({VENDOR_URL: FakePage(200, VENDOR_HTML)})
    first = asyncio.run(vendor_service.get_vendor_data(repo, "test-product", ("emi",), deps=_deps(fetcher)))
    assert first[0].status == "live" and len(fetcher.requested) == 1
    assert first[0].emi_offers and first[0].snapshot["price"] is not None
    stored = first[0].emi_offers[0]
    assert stored["fetched_at"] and stored["expires_at"] > stored["fetched_at"]
    second = asyncio.run(vendor_service.get_vendor_data(repo, "test-product", ("emi",), deps=_deps(fetcher)))
    assert second[0].status == "cache" and len(fetcher.requested) == 1      # no second visit


def test_expired_cache_refreshes_on_demand(repo):
    register_product(repo, vendors=[("vendor-a", VENDOR_URL)])
    fetcher = FakeFetcher({VENDOR_URL: FakePage(200, VENDOR_HTML)})
    asyncio.run(vendor_service.get_vendor_data(repo, "test-product", ("emi",), deps=_deps(fetcher)))
    later = datetime.now(timezone.utc) + timedelta(hours=13)               # beyond the 12 h EMI TTL
    res = asyncio.run(vendor_service.get_vendor_data(repo, "test-product", ("emi",), deps=_deps(fetcher), now=later))
    assert res[0].status == "live" and len(fetcher.requested) == 2


def test_explicit_refresh_bypasses_cache(repo):
    register_product(repo, vendors=[("vendor-a", VENDOR_URL)])
    fetcher = FakeFetcher({VENDOR_URL: FakePage(200, VENDOR_HTML)})
    asyncio.run(vendor_service.get_vendor_data(repo, "test-product", ("emi",), deps=_deps(fetcher)))
    asyncio.run(vendor_service.get_vendor_data(repo, "test-product", ("emi",), deps=_deps(fetcher), force_refresh=True))
    assert len(fetcher.requested) == 2


def test_vendor_failure_falls_back_to_labelled_stale_cache(repo):
    register_product(repo, vendors=[("vendor-a", VENDOR_URL)])
    ok = FakeFetcher({VENDOR_URL: FakePage(200, VENDOR_HTML)})
    asyncio.run(vendor_service.get_vendor_data(repo, "test-product", ("emi",), deps=_deps(ok)))
    broken = FakeFetcher(fail={VENDOR_URL: FetchError("timeout", transient=True)})
    later = datetime.now(timezone.utc) + timedelta(hours=13)
    res = asyncio.run(vendor_service.get_vendor_data(repo, "test-product", ("emi",), deps=_deps(broken), now=later))
    assert res[0].status == "stale_cache"
    assert "STALE" in res[0].freshness["label"] and "Live fetch failed" in res[0].message


def test_vendor_failure_without_cache_is_unavailable(repo):
    register_product(repo, vendors=[("vendor-a", VENDOR_URL)])
    broken = FakeFetcher(fail={VENDOR_URL: FetchError("timeout", transient=True)})
    res = asyncio.run(vendor_service.get_vendor_data(repo, "test-product", ("emi",), deps=_deps(broken)))
    assert res[0].status == "unavailable" and res[0].emi_offers == []


def test_expired_offers_are_never_presented(repo):
    register_product(repo, vendors=[("vendor-a", VENDOR_URL)])
    fetcher = FakeFetcher({VENDOR_URL: FakePage(200, VENDOR_HTML)})
    res = asyncio.run(vendor_service.get_vendor_data(repo, "test-product", ("emi",), deps=_deps(fetcher)))
    with repo.db.connection() as c:
        c.execute("update emi_offers set offer_valid_until = now() - interval '1 hour'")
    again = asyncio.run(vendor_service.get_vendor_data(repo, "test-product", ("emi",), deps=_deps(fetcher)))
    assert res[0].emi_offers and again[0].emi_offers == []
    assert "validity" in again[0].message


def test_blocked_vendor_is_recorded_and_not_retried_immediately(repo):
    register_product(repo, vendors=[("vendor-a", VENDOR_URL)])
    fetcher = FakeFetcher({VENDOR_URL: FakePage(403, "Access Denied")})
    asyncio.run(vendor_service.get_vendor_data(repo, "test-product", ("emi",), deps=_deps(fetcher)))
    res = asyncio.run(vendor_service.get_vendor_data(repo, "test-product", ("emi",), deps=_deps(fetcher)))
    assert res[0].status == "unavailable"
    assert len(fetcher.requested) == 1          # negative cache prevents hammering
