"""Marketplace vendor adapters. Fixtures are SYNTHETIC, built in the shape of the real pages inspected 2026-10-16
(element ids / embedded JSON keys only; prices, names and texts are made up)."""
import asyncio
import json
from decimal import Decimal

import pytest

from app.extraction.adapters.marketplaces import AmazonInAdapter, MyntraAdapter, NykaaAdapter
from app.extraction.base import ExtractionContext
from app.extraction.registry import resolve_adapter
from app.extraction.vendor_offers import offer_value, parse_bank_offer
from conftest import FakeFetcher, FakePage

AMZ = "https://www.amazon.in/dp/B000TEST01"
AMZ_HTML = """<html><body><span id="productTitle"> Test Day Cream 50 g </span>
<div id="corePriceDisplay_desktop_feature_div"><span class="priceToPay"><span class="a-price-whole">449.</span><span class="a-price-fraction">00</span></span>
 <span class="basisPrice"><span class="a-offscreen">₹999</span></span></div>
<div id="availability"> In stock </div><a id="sellerProfileTriggerId">Test Seller Pvt Ltd</a>
<div id="deliveryBlockMessage">FREE delivery Sunday</div><div id="inemi_feature_div"></div>
<div id="vsxoffers_feature_div">
 <div class="a-carousel-card"><h6 class="offers-items-title">Bank Offer</h6><div class="offers-items-content">Upto ₹3,000.00 discount on SBI Credit Cards</div></div>
 <div class="a-carousel-card"><h6 class="offers-items-title">Bank Offer</h6><div class="offers-items-content">Upto ₹3,000.00 discount on SBI Credit Cards</div></div>
 <div class="a-carousel-card"><h6 class="offers-items-title">Partner Offers</h6><div class="offers-items-content">Buy 3 Get 5% Off</div></div>
</div>
<span id="acrPopover" title="4.3 out of 5 stars"></span><span id="acrCustomerReviewText" aria-label="12,614 Reviews">(12,614)</span>
<div data-hook="review"><i data-hook="review-star-rating">5 out of 5 stars</i><span data-hook="reviewTitle">Nice</span>
 <span data-hook="review-date">Reviewed in India on 17 August 2026</span><span data-hook="avp-badge">Verified Purchase</span>
 <span data-hook="reviewText">Brief content visible, double tap to read full content. Light texture, no white cast. Read more</span></div>
</body></html>"""

MYN = "https://www.myntra.com/day-cream/test/test-cream/123/buy"
MYN_DATA = {"pdpData": {"id": 123, "name": "Test Cream", "brand": {"name": "TestBrand"}, "price": {"mrp": 999, "discounted": 669},
    "flags": {"emiEnabled": False, "outOfStock": False, "codEnabled": True},
    "selectedSeller": {"sellerPartnerId": 1, "discountedPrice": 669, "discount": {"label": "(33% OFF)", "timerEnd": "1792142124"}},
    "sellers": [{"sellerPartnerId": 1, "displayName": "Test Retail Pvt Ltd"}],
    "sizes": [{"sizeSellerData": [{"availableCount": 918}]}],
    "serviceability": {"descriptors": ["Easy 14 days exchanges"]},
    "offers": [{"type": "BANK", "title": "10% Instant Discount on HDFC Bank Credit Card", "description": "Min Spend ₹3,500 Max Discount ₹800"}],
    "ratings": {"averageRating": 4.58, "totalCount": 11415, "ratingInfo": [{"rating": 5, "count": 8385}],
                "reviewInfo": {"topReviews": [{"reviewId": "a", "reviewText": "Absorbs quickly", "userRating": 5, "timestamp": "1770017313000"}]}}}}
MYN_HTML = f"<html><script>window.__myx = {json.dumps(MYN_DATA)}</script></html>"

NYK = "https://www.nykaaman.com/test-cream/p/261613?skuId=261613"
NYK_DATA = {"productPage": {"product": {"name": "Test Cream", "brandName": "TestBrand", "sku": "490", "offerPrice": 799, "mrp": 999,
    "inStock": True, "quantity": 6516, "sellerName": "Test E retail", "returnMessage": "15 days, defects only", "expiry": "15 January 2029",
    "offers": [], "rating": 4.4, "ratingCount": 11694, "reviewSplitUp": [{"id": 5, "count": "7912"}],
    "latestReviews": [{"title": None, "description": None, "createdOn": "2026-04-06 13:05:23", "isBuyer": True, "rating": 5}]}}}
NYK_HTML = f"<html><script>window.__PRELOADED_STATE__ = {json.dumps(NYK_DATA)}</script></html>"


def run(adapter, url, html, status=200):
    return asyncio.run(adapter.extract(url, ExtractionContext(fetcher=FakeFetcher({url: FakePage(status, html)}))))


def test_amazon_adapter():
    r = run(AmazonInAdapter(), AMZ, AMZ_HTML)
    v = r.vendor
    assert (v["price"], v["mrp"], v["availability"], v["seller_name"]) == (Decimal("449.00"), Decimal("999.00"), "InStock", "Test Seller Pvt Ltd")
    assert v["emi_status"] == "not_shown" and r.emi_plans == []
    assert len(v["bank_offers"]) == 2                    # duplicated carousel card removed
    assert r.review_aggregates[0]["rating"] == "4.3" and r.review_aggregates[0]["count"] == "12614"
    rv = r.reviews[0]
    assert rv["text"] == "Light texture, no white cast." and rv["date"] == "2026-08-17" and rv["verified"] is True
    assert r.product["product_code"] == "B000TEST01" and r.missing_fields() == []


def test_myntra_adapter():
    r = run(MyntraAdapter(), MYN, MYN_HTML)
    v = r.vendor
    assert v["price"] == Decimal("669.00") and v["mrp"] == Decimal("999.00") and v["emi_status"] == "not_offered"
    assert v["seller_name"] == "Test Retail Pvt Ltd" and v["stock_units"] == 918 and v["discount_ends_at"].startswith("2026-10")
    assert v["bank_offers"][0]["min_spend"] == Decimal("3500.00")
    assert r.review_aggregates[0]["distribution"] == {"5": 8385}
    assert r.reviews[0]["date"] == "2026-02-02"


def test_nykaa_adapter_ratings_without_text():
    r = run(NykaaAdapter(), NYK, NYK_HTML)
    assert r.vendor["price"] == Decimal("799.00") and r.vendor["return_policy"].startswith("15 days")
    assert r.reviews == [] and any("no text" in w for w in r.warnings)


def test_layout_change_reports_missing():
    r = run(MyntraAdapter(), MYN, "<html>new layout</html>")
    assert "vendor.price" in r.missing_fields() and any("not found" in w for w in r.warnings)


def test_bot_check_not_bypassed():
    r = run(AmazonInAdapter(), AMZ, "<html>Enter the characters you see below. captcha</html>", 503)
    assert r.blocked and r.vendor == {}


def test_hosts_resolve_to_marketplace_adapters():
    assert resolve_adapter("auto", AMZ, "vendor").name == "amazon_in"
    assert resolve_adapter("auto", MYN, "vendor").name == "myntra"
    assert resolve_adapter("auto", NYK, "vendor").name == "nykaa"


@pytest.mark.parametrize("title,desc,price,expected", [
    ("10% Instant Discount on HDFC Bank Credit Card", "Min Spend ₹3,500 Max Discount ₹800", "669", "0"),       # below min spend
    ("10% Instant Discount on HDFC Bank Credit Card", "Min Spend ₹3,500 Max Discount ₹800", "5000", "500.00"),
    ("10% Instant Discount on HDFC Bank Credit Card", "Min Spend ₹3,500 Max Discount ₹800", "20000", "800.00"),  # capped
    ("Upto ₹3,000.00 discount on SBI Credit Cards", None, "449", None),                                          # terms unknown
])
def test_offer_value(title, desc, price, expected):
    v, why = offer_value(parse_bank_offer(title, desc), Decimal(price))
    assert (None if v is None else str(v)) == expected and why


def test_multiple_banks_detected():
    assert parse_bank_offer("7.5% Cashback on Flipkart Axis Bank & SBI Credit Cards")["bank"] == "SBI, Flipkart Axis"
