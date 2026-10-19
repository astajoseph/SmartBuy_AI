import pytest
from fastapi.testclient import TestClient

from app.main import app
from conftest import PH_URL, add_history, daily_prices, register_product, UTC

pytestmark = pytest.mark.db
ADMIN = {"Authorization": "Bearer test-admin-token"}


def test_health():
    assert TestClient(app).get("/api/health").json()["status"] == "ok"


def test_products_flow(repo):
    from datetime import datetime
    sid, _ = register_product(repo)
    add_history(repo, "test-product", sid, daily_prices(datetime(2026, 9, 1, tzinfo=UTC), [1100] * 10 + [1050] * 5))
    c = TestClient(app)
    lst = c.get("/api/products").json()
    assert lst[0]["id"] == "test-product" and lst[0]["current"]["price"] == 1050.0
    assert c.get("/api/products/nope").status_code == 404
    series = c.get("/api/products/test-product/prices").json()["series"]["selling_price"]
    assert len(series) == 15
    rec = c.post("/api/recommendations", json={"product_id": "test-product", "target_price": 1000}).json()
    assert rec["action"] in {"MONITOR", "BUY_IF_TARGET_REACHED", "INSUFFICIENT_DATA"}
    assert c.get("/api/products/test-product/recommendations").json()[0]["recommended_action"] == rec["action"]


def test_target_price_requires_sign_in(repo):
    register_product(repo)
    assert TestClient(app).post("/api/products/test-product/target-price", json={"target_price": 900}).status_code in (401, 503)


def test_admin_register_and_jobs(repo):
    c = TestClient(app)
    body = {"product_id": "api-product", "name": "API Product", "price_source": {"url": PH_URL, "adapter": "pricehistory_app"},
            "vendors": [{"vendor_id": "vendor-x", "name": "Vendor X", "url": "https://vendor-x.example/p"}]}
    r = c.post("/api/products/register", json=body, headers=ADMIN)
    assert r.status_code == 200 and r.json()["vendor_source_ids"]
    bad = {**body, "price_source": {"url": "http://127.0.0.1/x"}}
    assert c.post("/api/products/register", json=bad, headers=ADMIN).status_code == 422
    assert c.get("/api/admin/jobs", headers=ADMIN).status_code == 200
    assert c.get("/api/admin/sources/health", headers=ADMIN).json()[0]["health"] == "never_run"


def test_rate_limited_routes_work_over_http(repo, monkeypatch):
    """Regression: vendor-comparison, reviews and chat returned 422 because the limiter's Request was not resolved."""
    from app.dependencies import chat_rate_limit, vendor_rate_limit
    from app.services import vendor_service

    vendor_rate_limit.calls.clear()
    chat_rate_limit.calls.clear()
    register_product(repo, vendors=[("vendor-a", "https://vendor-a.example/p/1")])

    async def no_vendor_data(*a, **k):
        return []

    monkeypatch.setattr(vendor_service, "get_vendor_data", no_vendor_data)
    c = TestClient(app)
    assert c.get("/api/products/test-product/vendor-comparison").status_code == 200
    assert c.get("/api/products/test-product/reviews").status_code == 200
    r = c.post("/api/assistant/chat", json={"message": "Should I buy?", "product_id": "test-product"})
    assert r.status_code == 200 and "reply" in r.json()


def test_vendor_rate_limit_returns_429(repo, monkeypatch):
    from app.dependencies import vendor_rate_limit
    from app.services import vendor_service

    vendor_rate_limit.calls.clear()
    register_product(repo)

    async def no_vendor_data(*a, **k):
        return []

    monkeypatch.setattr(vendor_service, "get_vendor_data", no_vendor_data)
    c = TestClient(app)
    codes = [c.get("/api/products/test-product/reviews").status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
    vendor_rate_limit.calls.clear()
