"""Seller discovery: marketplace sub-sellers, web-search candidates, verification and the 30-day lifecycle.
All HTML is SYNTHETIC, shaped like the real pages inspected on 2026-10-24; no test touches a live site."""
import asyncio
import io
import os
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from app.extraction.adapters.marketplaces import AmazonInAdapter, myntra_sellers, parse_amazon_offers
from app.extraction.base import ExtractionContext, ExtractionResult
from app.services import discovery_service as ds
from app.services.product_match import match_product, sizes
from app.services.web_search import BraveSearch, SearchError, SearchHit, queries_for
from conftest import FakeBrowser, FakeFetcher, FakePage, register_product

ADMIN = {"Authorization": "Bearer test-admin-token"}
AMAZON_URL = "https://www.amazon.in/dp/B0TEST1234"


def _offer(tag_id, n, seller_id, name, price, *, cart=True, ships="Amazon", rating="4 out of 5 stars (120 ratings) 80% positive"):
    link = f'<a href="/gp/aag/main?ie=UTF8&seller={seller_id}&asin=B0TEST1234">{name}</a>' if seller_id else f"<span>{name}</span>"
    return f"""<div id="{tag_id}"><div id="aod-offer-heading"><span>New</span></div>
      <div id="aod-price-{n}"><span class="aok-offscreen">₹{price} with 40 percent savings</span>
        <span class="aok-offscreen">₹999.00 per 100 g</span><span>M.R.P.: ₹999.00</span></div>
      <div id="unified-delivery-message-{n}">FREE delivery Thursday</div>
      <div id="aod-offer-shipsFrom"><span>Ships from</span><span>{ships}</span></div>
      <div id="aod-offer-soldBy"><span>Sold by</span>{link}<div id="aod-offer-seller-rating">Seller rating is {rating} over last 12 months</div></div>
      {'<input name="submit.addToCart" aria-label="Add to Cart">' if cart else '<span>Currently unavailable</span>'}</div>"""


def amazon_panel(*offers, total=None):
    return (f'<html><body><div id="aod-container">{offers[0]}<div id="aod-offer-list">{"".join(offers[1:])}</div>'
            f'<input id="aod-total-offer-count" value="{total if total is not None else len(offers) - 1}"></div></body></html>')


def shop_page(name, price, *, brand="TestBrand", availability="InStock", code=None, images=()):
    sku = f',"sku":"{code}"' if code else ""
    if images:
        sku += ',"image":' + json.dumps(list(images))
    return f"""<html><head><script type="application/ld+json">
{{"@context":"https://schema.org","@type":"Product","name":"{name}","brand":{{"@type":"Brand","name":"{brand}"}}{sku},
 "offers":{{"@type":"Offer","price":"{price}","priceCurrency":"INR","availability":"https://schema.org/{availability}"}}}}
</script></head><body>{name}</body></html>"""


class FakeSearch:
    def __init__(self, hits):
        self.hits, self.queries = hits, []

    async def search(self, q, count=20):
        self.queries.append(q)
        return [SearchHit(url=u, title=t, description=None, query=q) for u, t in self.hits]


def _deps(fetcher, browser=None, search=None):
    return ds.DiscoveryDeps(fetcher_factory=lambda hosts: fetcher, browser_factory=lambda: browser,
                            search_factory=lambda: search)


PRODUCT = {"id": "p", "name": "TestBrand Glow Day Cream SPF 15 (Vitamin B5), 50 g", "brand": "TestBrand",
           "expected_attributes": {"retailer_product_code": "B0TEST1234", "size": "50 g"}}


def _result(name=None, brand=None, price=None, code=None, url="https://shop.example/p"):
    r = ExtractionResult(url=url, adapter="vendor_generic", adapter_version="1")
    r.product.update({"name": name, "brand": brand, "product_code": code})
    r.vendor["price"] = Decimal(str(price)) if price is not None else None
    return r


# ------------------------------------------------------------------ parsing (no database)
def test_amazon_offer_panel_lists_every_seller():
    html = amazon_panel(_offer("aod-pinned-offer", 0, "AFEATURED1", "Featured Seller", "499.00"),
                        _offer("aod-offer", 1, "ACHEAP0001", "Cheap Shop", "479.00", ships="Cheap Shop"),
                        _offer("aod-offer", 2, "AGONE00001", "Gone Shop", "650.00", cart=False),
                        _offer("aod-offer", 3, "ACHEAP0001", "Cheap Shop", "479.00"),            # duplicate render
                        total=5)
    sellers, total = parse_amazon_offers(html)
    assert total == 5 and [s["seller_key"] for s in sellers] == ["AFEATURED1", "ACHEAP0001", "AGONE00001"]
    f, c, g = sellers
    assert f["featured"] and f["price"] == Decimal("499.00") and f["mrp"] == Decimal("999.00")
    assert f["seller_rating"] == "4" and f["seller_rating_count"] == 120 and f["seller_positive_pct"] == "80"
    assert c["fulfilled_by"] == "Cheap Shop" and c["seller_url"].endswith("seller=ACHEAP0001")
    assert c["in_stock"] and not g["in_stock"]


def test_amazon_extract_sellers_renders_the_offer_panel():
    html = amazon_panel(_offer("aod-pinned-offer", 0, "AFEATURED1", "Featured Seller", "499.00"), total=3)
    browser = FakeBrowser(html=html)
    r = asyncio.run(AmazonInAdapter().extract_sellers(AMAZON_URL, ExtractionContext(fetcher=FakeFetcher(), browser=browser)))
    assert browser.rendered == ["https://www.amazon.in/dp/B0TEST1234?aod=1"]
    assert len(r.sellers) == 1 and "Read 1 of 4 offers" in r.warnings[0]


def test_myntra_sellers_from_embedded_json():
    p = {"price": {"mrp": 999}, "flags": {"outOfStock": False}, "selectedSeller": {"sellerPartnerId": 1},
         "sellers": [{"sellerPartnerId": 1, "displayName": "Retail One"}, {"sellerPartnerId": 2, "sellerName": "Two"}],
         "sizes": [{"sizeSellerData": [{"sellerPartnerId": 1, "availableCount": 5, "discountedPrice": 519},
                                       {"sellerPartnerId": 2, "availableCount": 0, "discountedPrice": 499}]}]}
    one, two = myntra_sellers(p)
    assert one["seller_name"] == "Retail One" and one["in_stock"] and one["featured"] and one["price"] == Decimal("519")
    assert two["seller_key"] == "2" and not two["in_stock"]


def test_sizes_normalise_units():
    assert sizes("50 g") == sizes("50gm") == sizes("0.05 kg") == {("g", Decimal("50"))}
    assert sizes("Pack of 1, 100 ml") == {("ml", Decimal("100"))}


@pytest.mark.parametrize("kw, level", [
    (dict(name="Glow cream", price=500, code="B0TEST1234"), "strong"),                       # product code
    (dict(name="TestBrand Glow Day Cream SPF15, 50gm", brand="TestBrand", price=520), "strong"),
    (dict(name="TestBrand Glow Day Cream SPF15, 100 g", brand="TestBrand", price=520), "none"),   # other size
    (dict(name="TestBrand Glow Day Cream 50 g", brand="TestBrand", price=90), "none"),         # implausible price
    (dict(name="Glow Day Cream 50 g", brand="OtherCo", price=520), "none"),                    # other brand
    (dict(name="TestBrand Glow Day Cream", brand="TestBrand", price=520), "partial"),           # size not shown
    (dict(name="TestBrand Glow Day Cream 50 g", brand="TestBrand"), "partial"),                # no price
    (dict(name="TestBrand shampoo 50 g", brand="TestBrand", price=520), "none"),               # different product
    (dict(), "none"),                                                                           # no product data
])
def test_match_levels(kw, level):
    m = match_product(PRODUCT, _result(**kw), price_range=(Decimal("300"), Decimal("1500")))
    assert m.level == level, (m.reasons, m.evidence)


def test_queries_and_url_cleaning():
    assert queries_for(PRODUCT) == ["TestBrand Glow Day Cream SPF 15 50 g buy online", "TestBrand Glow Day Cream SPF 15 50 g price"]
    assert ds.clean_url("https://Shop.Example/p/1?utm_source=x&id=3&gclid=y#reviews") == "https://shop.example/p/1?id=3"
    assert ds.vendor_id_for("www.shop.example") == "shop-example" and ds.vendor_id_for("ab.in") == "ab-in"


def test_brave_client_sends_key_and_strips_markup():
    seen = {}

    def handler(req):
        seen.update(key=req.headers["X-Subscription-Token"], q=req.url.params["q"], country=req.url.params["country"])
        return httpx.Response(200, json={"web": {"results": [{"url": "https://s.example/p", "title": "<strong>Glow</strong> cream"}]}})

    hits = asyncio.run(BraveSearch("k123", transport=httpx.MockTransport(handler)).search("glow cream"))
    assert seen == {"key": "k123", "q": "glow cream", "country": "IN"} and hits[0].title == "Glow cream"
    with pytest.raises(SearchError):
        asyncio.run(BraveSearch("k", transport=httpx.MockTransport(lambda r: httpx.Response(429))).search("x"))


def test_jsonld_images_in_every_shape():
    from app.extraction.jsonld import images_of
    assert images_of({"image": "https://i.example/a.jpg"}) == ["https://i.example/a.jpg"]
    assert images_of({"image": [{"@type": "ImageObject", "url": "https://i.example/a.jpg"}, "https://i.example/a.jpg",
                                {"contentUrl": "https://i.example/b.jpg"}, "data:x", None]}) == ["https://i.example/a.jpg", "https://i.example/b.jpg"]
    assert images_of({}) == []


@pytest.mark.parametrize("image_text, level, reason", [
    ("OLAY TOTAL EFFECTS DAY CREAM SPF15 20g", "none", "different pack size (20 g on the product image)"),
    ("Glow Day Cream NET WT. 50 g", "strong", "brand, pack size and product name match, price plausible (pack size read from the product image)"),
    ("Hydrates skin, fights 7 signs", "partial", "unconfirmed: pack size"),
])
def test_pack_size_from_image_text(image_text, level, reason):
    r = _result(name="TestBrand Glow Day Cream SPF 15", brand="TestBrand", price=520)
    m = match_product(PRODUCT, r, price_range=(Decimal("300"), Decimal("1500")), image_text=image_text)
    assert (m.level, m.reasons) == (level, [reason])


def test_page_size_wins_over_image_text():
    r = _result(name="TestBrand Glow Day Cream SPF 15, 50 g", brand="TestBrand", price=520)
    assert match_product(PRODUCT, r, price_range=(Decimal("300"), Decimal("1500")), image_text="20 g").level == "strong"


def test_real_ocr_reads_pack_size_from_an_image():
    """Runs the bundled open-source model on a SYNTHETIC label (white card, black text)."""
    from app.extraction import image_text
    if not image_text.available():
        assert not os.environ.get("CI"), "OCR must work in CI (rapidocr_onnxruntime / OpenCV failed to load)"
        pytest.skip("rapidocr_onnxruntime not installed")
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (900, 360), "white")
    d = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=64)
    d.text((40, 60), "DAY CREAM SPF15", fill="black", font=font)
    d.text((40, 200), "NET WT. 20g", fill="black", font=font)
    buf = io.BytesIO(); im.save(buf, "JPEG")
    text = image_text.read_text(buf.getvalue())
    assert text and sizes(text) == {("g", Decimal("20"))}, text
    assert image_text.read_text(b"<html>not an image</html>") is None


# ------------------------------------------------------------------ database


def _setup(repo):
    register_product(repo, vendors=[("amazon-in", AMAZON_URL)])
    with repo.db.connection() as c:
        c.execute("""update products set name = %s, brand = 'TestBrand', expected_attributes = %s::jsonb where id = 'test-product'""",
                  (PRODUCT["name"], '{"retailer_product_code": "B0TEST1234", "size": "50 g"}'))
        c.execute("update product_vendor_sources set adapter_name = 'amazon_in'")
    return repo.get_product("test-product")


@pytest.mark.db
def test_marketplace_sellers_stored_and_expire_after_30_days(repo):
    product = _setup(repo)
    panel = amazon_panel(_offer("aod-pinned-offer", 0, "AFEATURED1", "Featured Seller", "499.00"),
                         _offer("aod-offer", 1, "ATEMP00001", "Temp Seller", "479.00"))
    rep = asyncio.run(ds.discover_product(repo, product, deps=_deps(FakeFetcher(), FakeBrowser(html=panel))))
    assert rep["status"] == "success" and rep["marketplace_sellers"][0]["sellers"] == 2
    assert {s["seller_name"] for s in repo.list_sellers("test-product")} == {"Featured Seller", "Temp Seller"}

    # next check: the temporary seller has gone
    panel2 = amazon_panel(_offer("aod-pinned-offer", 0, "AFEATURED1", "Featured Seller", "489.00"))
    asyncio.run(ds.discover_product(repo, product, deps=_deps(FakeFetcher(), FakeBrowser(html=panel2))))
    assert len(repo.list_sellers("test-product")) == 2                      # gone for a few hours is not gone
    gone = repo.deactivate_not_selling(30, now=datetime.now(timezone.utc) + timedelta(days=29))
    assert gone["sellers"] == []

    with repo.db.connection() as c:                                       # temp seller last in stock 31 days ago
        c.execute("update vendor_sellers set last_in_stock_at = now() - interval '31 days' where seller_key = 'ATEMP00001'")
    gone = repo.deactivate_not_selling(30)
    assert len(gone["sellers"]) == 1 and gone["sources"] == []           # the registered Amazon page stays
    assert [s["seller_name"] for s in repo.list_sellers("test-product")] == ["Featured Seller"]
    inactive = [s for s in repo.list_sellers("test-product", include_inactive=True) if not s["is_active"]]
    assert inactive[0]["deactivation_reason"] == "not in stock for 30 days" and inactive[0]["price"] == Decimal("479.00")

    # it comes back -> re-activated
    asyncio.run(ds.discover_product(repo, product, deps=_deps(FakeFetcher(), FakeBrowser(html=panel))))
    assert {s["seller_name"] for s in repo.list_sellers("test-product")} == {"Featured Seller", "Temp Seller"}
    assert len(repo.seller_offer_history(inactive[0]["id"])) == 2


@pytest.mark.db
def test_missed_checks_never_remove_sellers(repo):
    """Worker switched off / marketplace blocking us: no new reading of the page -> nothing is deactivated."""
    product = _setup(repo)
    panel = amazon_panel(_offer("aod-pinned-offer", 0, "AFEATURED1", "Featured Seller", "499.00"))
    asyncio.run(ds.discover_product(repo, product, deps=_deps(FakeFetcher(), FakeBrowser(html=panel))))
    with repo.db.connection() as c:       # last successful read of the page was when the seller was last in stock
        c.execute("update vendor_sellers set last_in_stock_at = now() - interval '40 days', first_seen_at = now() - interval '40 days'")
        c.execute("""update product_vendor_sources vs set last_checked_at = s.last_in_stock_at
                     from vendor_sellers s where s.vendor_source_id = vs.id""")
    blocked = ds.DiscoveryDeps(fetcher_factory=lambda h: FakeFetcher(), browser_factory=lambda: FakeBrowser(html="<html>captcha</html>"),
                               search_factory=lambda: None)
    asyncio.run(ds.discover_product(repo, product, deps=blocked))                    # blocked: page not read
    asyncio.run(ds.discover_product(repo, product, deps=ds.DiscoveryDeps(marketplace=False, search_factory=lambda: None)))
    assert repo.deactivate_not_selling(30)["sellers"] == []
    assert [s["seller_name"] for s in repo.list_sellers("test-product")] == ["Featured Seller"]


@pytest.mark.db
def test_web_search_adds_verified_shops_queues_unclear_and_rejects_mismatches(repo):
    product = _setup(repo)
    good, other_size, unclear = "https://good.example/glow-50", "https://other.example/glow-100", "https://unclear.example/glow"
    hits = [(good + "?utm_source=brave", "Glow"), ("https://www.youtube.com/watch?v=1", "review"),
            ("https://www.amazon.in/Glow/dp/B0TEST1234", "Amazon"), (other_size, "Glow 100 g"), (unclear, "Glow")]
    fetcher = FakeFetcher({
        good: FakePage(200, shop_page("TestBrand Glow Day Cream SPF 15, 50 g", 549)),
        other_size: FakePage(200, shop_page("TestBrand Glow Day Cream SPF 15, 100 g", 899)),
        unclear: FakePage(200, shop_page("TestBrand Glow Day Cream", 549)),
    })
    search = FakeSearch(hits)
    rep = asyncio.run(ds.discover_product(repo, product, deps=_deps(fetcher, FakeBrowser(html="<html></html>"), search)))
    by_url = {c["url"]: c["status"] for c in rep["candidates"]}
    assert by_url == {good: "auto_added", other_size: "rejected", unclear: "pending"}
    assert not any("youtube" in u or "amazon" in u for u in fetcher.requested)  # not shops / already tracked

    added = [v for v in repo.list_vendor_sources("test-product") if v["origin"] == "web_search"]
    assert [v["source_url"] for v in added] == [good] and added[0]["last_in_stock_at"] is not None
    assert added[0]["adapter_name"] == "vendor_generic" and "good.example" in repo.registered_hosts()
    cand = repo.list_candidates(status="rejected")[0]
    assert cand["match_evidence"]["reasons"] == ["different pack size (100 g on the page)"]

    # next run: the added shop is re-checked through vendor_service; rejected / pending pages are not re-fetched
    fetcher.requested.clear()
    rep = asyncio.run(ds.discover_product(repo, product, deps=_deps(fetcher, FakeBrowser(html="<html></html>"), search)))
    assert fetcher.requested == [good] and rep["discovered_sources"][0]["in_stock"] is True
    assert rep["candidates"] == []


@pytest.mark.db
def test_discovered_shop_out_of_stock_for_30_days_is_removed_and_returns(repo):
    product = _setup(repo)
    url = "https://good.example/glow-50"
    fetcher = FakeFetcher({url: FakePage(200, shop_page("TestBrand Glow Day Cream SPF 15, 50 g", 549))})
    search = FakeSearch([(url, "Glow")])
    asyncio.run(ds.discover_product(repo, product, deps=_deps(fetcher, None, search)))
    fetcher.pages[url] = FakePage(200, shop_page("TestBrand Glow Day Cream SPF 15, 50 g", 549, availability="OutOfStock"))
    with repo.db.connection() as c:
        c.execute("update product_vendor_sources set last_in_stock_at = now() - interval '31 days' where origin = 'web_search'")
        c.execute("delete from vendor_snapshots")
    asyncio.run(ds.discover_product(repo, product, deps=_deps(fetcher, None, None)))   # re-check: still out of stock
    gone = repo.deactivate_not_selling(30)
    assert [r["source_url"] for r in gone["sources"]] == [url]
    assert all(v["origin"] == "registry" for v in repo.list_vendor_sources("test-product"))

    # back in stock and found by search again -> re-activated
    fetcher.pages[url] = FakePage(200, shop_page("TestBrand Glow Day Cream SPF 15, 50 g", 529))
    rep = asyncio.run(ds.discover_product(repo, product, deps=_deps(fetcher, None, search)))
    assert rep["candidates"][0]["status"] == "auto_added"
    assert [v["source_url"] for v in repo.list_vendor_sources("test-product") if v["origin"] == "web_search"] == [url]


@pytest.mark.db
def test_brave_monthly_limit_is_never_exceeded(repo, monkeypatch):
    product = _setup(repo)
    monkeypatch.setenv("BRAVE_MONTHLY_QUERY_LIMIT", "3")
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        search = FakeSearch([])
        for _ in range(3):
            rep = asyncio.run(ds.discover_product(repo, product, deps=_deps(FakeFetcher(), None, search)))
        assert len(search.queries) == 3 and repo.search_usage("brave") == 3     # 2 + 1, then stopped
        assert any("monthly query limit (3) reached" in e for e in rep["errors"])
        assert not repo.reserve_search_queries("brave", 1, 3)
        assert repo.reserve_search_queries("brave", 1, 3, now=datetime.now(timezone.utc) + timedelta(days=32))
    finally:
        get_settings.cache_clear()


@pytest.mark.db
def test_product_images_decide_when_the_page_has_no_pack_size(repo):
    product = _setup(repo)
    small, right = "https://small.example/glow", "https://right.example/glow"
    pages = {small: FakePage(200, shop_page("TestBrand Glow Day Cream SPF 15", 422, images=["https://cdn.small.example/1.jpg"])),
             right: FakePage(200, shop_page("TestBrand Glow Day Cream SPF 15", 549,
                                            images=["https://img.right.example/back.jpg", "https://img.right.example/front.jpg"])),
             "https://cdn.small.example/1.jpg": FakePage(200, "IMG OLAY 20g"),
             "https://img.right.example/back.jpg": FakePage(200, "IMG ingredients only"),
             "https://img.right.example/front.jpg": FakePage(200, "IMG NET WT 50 g")}
    fetcher = FakeFetcher(pages)
    allowed = []

    def factory(hosts):
        allowed.append(set(hosts))
        return fetcher

    deps = ds.DiscoveryDeps(fetcher_factory=factory, browser_factory=lambda: None, marketplace=False,
                            search_factory=lambda: FakeSearch([(small, "Glow"), (right, "Glow")]),
                            image_reader=lambda b: b.decode().removeprefix("IMG "))
    rep = asyncio.run(ds.discover_product(repo, product, deps=deps))
    assert {c["url"]: c["status"] for c in rep["candidates"]} == {small: "rejected", right: "auto_added"}
    assert {"small.example", "cdn.small.example"} in allowed               # image host allowed for that fetch only
    c = {x["url"]: x for x in repo.list_candidates()}
    assert c[small]["match_evidence"]["reasons"] == ["different pack size (20 g on the product image)"]
    assert c[right]["match_evidence"]["images"] == {"read": 2, "text_excerpt": "ingredients only | NET WT 50 g"}


@pytest.mark.db
def test_admin_reviews_pending_candidates(repo):
    from app.main import app

    product = _setup(repo)
    unclear = "https://unclear.example/glow"
    fetcher = FakeFetcher({unclear: FakePage(200, shop_page("TestBrand Glow Day Cream", 549))})
    asyncio.run(ds.discover_product(repo, product, deps=_deps(fetcher, None, FakeSearch([(unclear, "Glow")]))))
    c = TestClient(app)
    assert c.get("/api/admin/discovery/candidates").status_code in (401, 403)
    pending = c.get("/api/admin/discovery/candidates?status=pending", headers=ADMIN).json()
    assert [p["url"] for p in pending] == [unclear] and pending[0]["match_level"] == "partial"

    r = c.post(f"/api/admin/discovery/candidates/{pending[0]['id']}/approve", headers=ADMIN).json()
    assert r["status"] == "approved" and r["decided_by"] == "admin token"
    src = [v for v in repo.list_vendor_sources("test-product") if v["source_url"] == unclear]
    assert src and src[0]["origin"] == "admin_approved"

    c.post(f"/api/admin/discovery/candidates/{pending[0]['id']}/reject", headers=ADMIN)
    assert not [v for v in repo.list_vendor_sources("test-product") if v["source_url"] == unclear]
    assert c.post("/api/admin/discovery/candidates/999999/approve", headers=ADMIN).status_code == 404


@pytest.mark.db
def test_sellers_endpoint_reads_the_database_only(repo, monkeypatch):
    from app.main import app

    product = _setup(repo)
    panel = amazon_panel(_offer("aod-pinned-offer", 0, "AFEATURED1", "Featured Seller", "499.00"))
    asyncio.run(ds.discover_product(repo, product, deps=_deps(FakeFetcher(), FakeBrowser(html=panel))))

    async def boom(*a, **k):
        raise AssertionError("sellers endpoint must not fetch")

    monkeypatch.setattr(ds, "discover_product", boom)
    body = TestClient(app).get("/api/products/test-product/sellers").json()
    assert body["sellers"][0]["seller_name"] == "Featured Seller" and body["sellers"][0]["price"] == "499.00"
    assert body["discovered_sites"] == []


def test_search_interval_keeps_brave_within_budget(monkeypatch):
    from app.config import get_settings
    get_settings.cache_clear()
    assert ds.search_interval_hours(16) == 48                     # 16 x 2 x 15 searches/month = 480 <= 720
    assert ds.search_interval_hours(40) == 80                     # stretched: 40 x 2 x 9 = 720
    for n in (1, 16, 25, 40, 100):
        monthly = n * ds.QUERIES_PER_SEARCH * 720 / ds.search_interval_hours(n)
        assert monthly <= 0.9 * 800 + 1e-9, n


def test_products_take_turns_for_web_search():
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    products = [{"id": f"p{i}", "last_web_search_at": now - timedelta(hours=60 - i)} for i in range(16)] + [{"id": "new"}]
    turn = ds.products_to_search(products, now)
    # 17 products, interval 48 h, runs every 6 h -> share ceil(17*6/48) = 3; never-searched first, then the oldest
    assert turn == {"new", "p0", "p1"}
    assert ds.products_to_search([{"id": "x", "last_web_search_at": now - timedelta(hours=10)}], now) == set()


@pytest.mark.db
def test_run_discovery_searches_only_this_runs_turn(repo):
    product = _setup(repo)
    search = FakeSearch([])
    deps = ds.DiscoveryDeps(fetcher_factory=lambda h: FakeFetcher(), browser_factory=lambda: None, marketplace=False,
                            search_factory=lambda: search)
    asyncio.run(ds.run_discovery(repo, deps=deps))
    assert len(search.queries) == 2 and repo.get_product("test-product")["last_web_search_at"] is not None
    asyncio.run(ds.run_discovery(repo, deps=deps))                 # searched minutes ago: not its turn
    assert len(search.queries) == 2
    asyncio.run(ds.run_discovery(repo, product_id="test-product", deps=deps))   # explicit product run: searched
    assert len(search.queries) == 4
