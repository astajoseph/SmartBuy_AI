"""Shared test fixtures.

Database tests need a disposable PostgreSQL (never the production Supabase project):
    docker run -d --name smartbuy-test-pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=smartbuy_test \
        -p 127.0.0.1:54329:5432 postgres:17-alpine
and SMARTBUY_TEST_DATABASE_URL (default below). Tests marked `db` are skipped if it is unreachable.
All HTML / chart fixtures are SYNTHETIC and labelled as such; no test depends on a live website.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND.parent / "scripts"))

TEST_DSN = os.environ.get("SMARTBUY_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:54329/smartbuy_test")
os.environ.setdefault("LLM_PROVIDER", "none")
os.environ["DATABASE_URL"] = TEST_DSN          # never let tests touch the real database
os.environ.setdefault("ADMIN_API_TOKEN", "test-admin-token")
os.environ.setdefault("PER_DOMAIN_MIN_INTERVAL_SECONDS", "0")
os.environ.setdefault("COLLECTION_MAX_RETRIES", "1")

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()


def _db_available() -> bool:
    try:
        with psycopg.connect(TEST_DSN, connect_timeout=3):
            return True
    except Exception:  # noqa: BLE001
        return False


DB_AVAILABLE = _db_available()


def pytest_collection_modifyitems(config, items):
    if DB_AVAILABLE:
        return
    skip = pytest.mark.skip(reason=f"test database not reachable at {TEST_DSN}")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def database():
    from app.database.client import Database, set_database
    from migrate import migrate

    with psycopg.connect(TEST_DSN, autocommit=True) as c:
        c.execute("drop schema if exists smartbuy cascade")
    migrate(TEST_DSN)
    db = Database(TEST_DSN, min_size=1, max_size=4)
    set_database(db)
    yield db
    db.close()
    set_database(None)


@pytest.fixture
def repo(database):
    from app.database.repositories import Repository

    with database.connection() as conn:
        tables = [r["tablename"] for r in conn.execute(
            "select tablename from pg_tables where schemaname = 'smartbuy' and tablename <> 'schema_migrations'")]
        conn.execute("truncate " + ", ".join(f"smartbuy.{t}" for t in tables) + " restart identity cascade")
    return Repository(database)


# --------------------------------------------------------------------------- fakes
@dataclass
class FakePage:
    status: int
    text: str


class FakeFetcher:
    """Stands in for SafeFetcher. Records every URL requested."""

    def __init__(self, pages: dict[str, FakePage] | None = None, fail: dict[str, Exception] | None = None):
        self.pages = pages or {}
        self.fail = fail or {}
        self.requested: list[str] = []

    async def fetch(self, url: str):
        from app.security.url_validation import FetchResult

        self.requested.append(url)
        if url in self.fail:
            raise self.fail[url]
        p = self.pages.get(url)
        if p is None:
            from app.security.url_validation import FetchError
            raise FetchError("HTTP 404", status=404)
        return FetchResult(url=url, final_url=url, status=p.status, headers={}, text=p.text)

    def robots_for(self, url):
        return None


@dataclass
class FakeBrowser:
    """Stands in for BrowserRenderer: returns pre-baked chart objects (synthetic)."""
    charts: list[dict] = field(default_factory=list)
    markers: list[dict] = field(default_factory=list)
    html: str = "<html></html>"
    rendered: list[str] = field(default_factory=list)
    robots: object = None
    respect_robots_for_subrequests: bool = True

    async def render(self, url, **kw):
        from app.extraction.browser import RenderResult

        self.rendered.append(url)
        return RenderResult(url=url, final_url=url, title="t", html=self.html, charts=self.charts, markers=self.markers)


# SYNTHETIC fixture modelled on the structure of a pricehistory.app product page (inspected 2026-10-16).
PH_URL = "https://pricehistory.app/p/test-product-abc123"
PH_HTML = """<html><head><title>Test Sunscreen 50 ml - Price History</title>
<script type="application/ld+json">{"@context":"http://schema.org","@graph":[
 {"@type":"ImageObject","@id":"%(u)s#primaryimage","url":"https://images.example/test.jpg"},
 {"@type":"Product","name":"Test Sunscreen 50 ml","image":{"@id":"%(u)s#primaryimage"},
  "description":"Get Price History of Test Sunscreen. Lowest Price 1035 Average Price 1234 Highest Price 1500 MRP 1500 Flipkart Price in India on 16102026 1050.",
  "brand":{"@type":"Brand","name":"TestBrand"},
  "offers":[{"@type":"Offer","name":"Price","priceCurrency":"INR","price":"1050","offeredBy":{"@type":"Organization","name":"flipkart.com"},"availability":"https://schema.org/InStock"},
            {"@type":"Offer","name":"Offer Price","priceCurrency":"INR","price":"945","offeredBy":{"@type":"Organization","name":"flipkart.com"},"availability":"https://schema.org/InStock"}],
  "additionalProperty":[{"@type":"PropertyValue","name":"Product Code","value":"TESTFSN123"}]}]}</script>
</head><body><div>Lowest Offer: ₹945</div></body></html>""" % {"u": PH_URL}

PH_CHARTS = [
    {"lib": "chartjs", "chart": "Price History Chart", "name": "Prices",
     "x": ["2026-01-01", "2026-01-05", "2026-01-05", "2026-02-01", "not-a-date", "2026-03-01"],
     "y": [1350, 1300, 1300, 1250, 1200, 1050]},
    {"lib": "chartjs", "chart": "Price History Chart", "name": "Offer Price",
     "x": ["2026-01-01", "2026-02-01", "2026-03-01"], "y": [None, 1100, 945]},
]
PH_MARKERS = [{"lib": "chartjs", "chart": "Price History Chart", "axis": "x", "kind": "band",
               "from": 1758499201000, "to": 1759449599000, "label": "Big Billion Days", "datetime": True}]


def register_product(repo, product_id="test-product", url=PH_URL, vendors=()):
    repo.upsert_product({"id": product_id, "name": "Test Sunscreen 50 ml", "brand": "TestBrand", "category": "beauty"})
    sid = repo.upsert_price_source(product_id, url, adapter_name="pricehistory_app", interval_minutes=360)
    vids = []
    for vid, vurl in vendors:
        repo.upsert_vendor(vid, vid.title(), vurl.split("/")[2])
        vids.append(repo.upsert_vendor_source(product_id, vid, vurl))
    return sid, vids


def add_history(repo, product_id, source_id, prices: list[tuple[datetime, float]], price_type="selling_price"):
    from app.extraction.base import PricePoint

    pts = [PricePoint(timestamp=t, price=Decimal(str(p)), price_type=price_type, currency="INR") for t, p in prices]
    return repo.insert_history_points(product_id, source_id, pts, "test_synthetic")


def daily_prices(start: datetime, values: list[float]) -> list[tuple[datetime, float]]:
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


UTC = timezone.utc
