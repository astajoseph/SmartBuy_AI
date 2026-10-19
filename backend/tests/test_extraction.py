import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.extraction.adapters.pricehistory_app import PriceHistoryAppAdapter
from app.extraction.adapters.vendor_generic import VendorGenericAdapter
from app.extraction.base import ExtractionContext
from app.extraction.registry import resolve_adapter
from app.extraction.validation import (InvalidValue, normalize_currency, parse_price, parse_source_timestamp,
                                       retailer_key, validate_timestamp)
from app.extraction.vendor_offers import parse_emi_text
from conftest import PH_CHARTS, PH_HTML, PH_MARKERS, PH_URL, FakeBrowser, FakeFetcher, FakePage


@pytest.mark.parametrize("raw,expected", [("₹1,050", Decimal("1050.00")), ("Rs. 999.5", Decimal("999.50")),
                                          (1050, Decimal("1050.00")), ("1,23,456", Decimal("123456.00"))])
def test_parse_price_valid(raw, expected):
    assert parse_price(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "-", "N/A"])
def test_missing_price_is_none_not_zero(raw):
    assert parse_price(raw) is None


@pytest.mark.parametrize("raw", ["0", 0, "-5", "abc", "₹", float("nan"), "999999999999"])
def test_invalid_price_raises(raw):
    with pytest.raises(InvalidValue):
        parse_price(raw)


def test_currency_parsing():
    assert normalize_currency("₹") == "INR"
    assert normalize_currency("INR") == "INR"
    assert normalize_currency("Rs.") == "INR"
    assert normalize_currency("??") is None


def test_date_only_values_are_ist_calendar_days():
    ts = parse_source_timestamp("2026-10-16")
    assert ts == datetime(2026, 10, 15, 18, 30, tzinfo=timezone.utc)   # 00:00 IST
    assert parse_source_timestamp(1758499201000).year == 2025
    assert parse_source_timestamp("garbage") is None


def test_future_and_ancient_timestamps_rejected():
    with pytest.raises(InvalidValue):
        validate_timestamp(datetime(2099, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(InvalidValue):
        validate_timestamp(datetime(1990, 1, 1, tzinfo=timezone.utc))


def test_retailer_key():
    assert retailer_key("www.Flipkart.com") == retailer_key("flipkart") == "flipkart"


def _run(adapter, fetcher, browser=None, config=None):
    return asyncio.run(adapter.extract(PH_URL, ExtractionContext(fetcher=fetcher, browser=browser, config=config or {})))


def test_pricehistory_adapter_static_and_chart():
    fetcher = FakeFetcher({PH_URL: FakePage(200, PH_HTML)})
    browser = FakeBrowser(charts=PH_CHARTS, markers=PH_MARKERS)
    r = _run(PriceHistoryAppAdapter(), fetcher, browser, {"respect_robots_for_subrequests": False})
    assert r.product["name"] == "Test Sunscreen 50 ml"
    assert r.product["product_code"] == "TESTFSN123"
    assert r.product["image_url"] == "https://images.example/test.jpg"
    assert {(c.price_type, c.price) for c in r.current_prices} == {("selling_price", Decimal("1050.00")),
                                                                    ("offer_price", Decimal("945.00"))}
    assert r.source_stats == {"lowest": Decimal("1035.00"), "average": Decimal("1234.00"), "highest": Decimal("1500.00"),
                              "mrp": Decimal("1500.00"), "lowest_offer": Decimal("945.00")}
    selling = [p for p in r.history if p.price_type == "selling_price"]
    offer = [p for p in r.history if p.price_type == "offer_price"]
    assert len(selling) == 4            # duplicate 2026-01-05 and unparseable date removed
    assert len(offer) == 2              # None (chart gap) skipped, never stored as 0
    assert any("rejected" in w for w in r.warnings)
    assert r.sale_bands[0].label == "Big Billion Days" and r.sale_bands[0].retailer == "flipkart.com"
    assert r.missing_fields() == []
    assert browser.respect_robots_for_subrequests is False


def test_pricehistory_adapter_without_browser_reports_missing_history():
    r = _run(PriceHistoryAppAdapter(), FakeFetcher({PH_URL: FakePage(200, PH_HTML)}), None)
    assert r.requires_js is True
    assert "price_history" in r.missing_fields()
    assert r.current_prices  # static data still extracted


def test_pricehistory_adapter_layout_change_reports_missing_fields():
    r = _run(PriceHistoryAppAdapter(), FakeFetcher({PH_URL: FakePage(200, "<html><body>redesigned</body></html>")}), None)
    assert not r.current_prices
    assert "current_price" in r.missing_fields()
    assert any("JSON-LD" in w for w in r.warnings)


def test_blocked_page_is_not_bypassed():
    r = _run(PriceHistoryAppAdapter(), FakeFetcher({PH_URL: FakePage(200, "<title>Robot Check</title> captcha")}),
             FakeBrowser(charts=PH_CHARTS))
    assert r.blocked and not r.history and not r.current_prices


def test_robots_respected_by_default_yields_no_history_warning():
    browser = FakeBrowser(charts=[])
    r = _run(PriceHistoryAppAdapter(), FakeFetcher({PH_URL: FakePage(200, PH_HTML)}), browser)
    assert browser.respect_robots_for_subrequests is True
    assert "price_history" in r.missing_fields()


def test_auto_resolution():
    assert resolve_adapter("auto", PH_URL, "price_history").name == "pricehistory_app"
    assert resolve_adapter("auto", "https://shop.example/x", "price_history").name == "generic_jsonld"
    assert resolve_adapter("auto", "https://shop.example/x", "vendor").name == "vendor_generic"


VENDOR_URL = "https://vendor.example/item/1"
# SYNTHETIC vendor page
VENDOR_HTML = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Test Sunscreen","sku":"V1",
 "offers":{"@type":"Offer","price":"1099","priceCurrency":"INR","availability":"https://schema.org/InStock",
           "seller":{"@type":"Organization","name":"Good Seller"}},
 "aggregateRating":{"@type":"AggregateRating","ratingValue":"4.3","ratingCount":"1,250","bestRating":"5"},
 "review":[{"@type":"Review","reviewBody":"Great texture, no white cast. Delivery was late though.",
            "reviewRating":{"ratingValue":"4"},"datePublished":"2026-09-01"}]}
</script></head><body>
<p>No Cost EMI available on HDFC Bank Credit Card. ₹367 x 3 months.</p>
<p>EMI starts at ₹183/month. 6 months @ 15% p.a. on ICICI credit card.</p>
<p>Processing fee of ₹199 applies.</p><p>Delivery by Tomorrow</p></body></html>"""


def test_vendor_generic_extracts_price_rating_reviews_and_emi_text():
    r = asyncio.run(VendorGenericAdapter().extract(
        VENDOR_URL, ExtractionContext(fetcher=FakeFetcher({VENDOR_URL: FakePage(200, VENDOR_HTML)}))))
    assert r.vendor["price"] == Decimal("1099.00") and r.vendor["seller_name"] == "Good Seller"
    assert r.review_aggregates[0]["rating"] == "4.3"
    assert r.reviews[0]["text"].startswith("Great texture")
    plans = {p["pattern"]: p for p in r.emi_plans}
    assert plans["amount_x_tenure"]["monthly_emi"] == Decimal("367.00") and plans["amount_x_tenure"]["tenure_months"] == 3
    assert plans["amount_x_tenure"]["no_cost_emi_claim"] and plans["amount_x_tenure"]["bank_name"] == "HDFC"
    assert plans["tenure_at_rate"]["interest_rate"] == Decimal("15") and plans["tenure_at_rate"]["interest_rate_type"] == "annual_reducing"
    assert plans["starting_from"]["tenure_months"] is None
    assert r.vendor["emi_page_facts"]["processing_fee"] == "199.00"


def test_emi_text_without_terms_gives_nothing():
    plans, facts = parse_emi_text("Free delivery. 4.5 stars. Buy now.")
    assert plans == [] and facts["no_cost_emi_mentioned"] is False


def test_pricehistory_single_unnamed_offer_is_selling_price():
    # SYNTHETIC page shaped like an Amazon product on pricehistory.app (one offer without a name)
    html = PH_HTML.replace('"offers":[{"@type":"Offer","name":"Price",', '"offers":[{"@type":"Offer",').replace(
        ',\n            {"@type":"Offer","name":"Offer Price","priceCurrency":"INR","price":"945","offeredBy":{"@type":"Organization","name":"flipkart.com"},"availability":"https://schema.org/InStock"}', "")
    r = _run(PriceHistoryAppAdapter(), FakeFetcher({PH_URL: FakePage(200, html)}), FakeBrowser(charts=PH_CHARTS))
    assert [(c.price_type, c.price) for c in r.current_prices] == [("selling_price", Decimal("1050.00"))]
    assert "offer_price" not in r.missing_fields() and "current_price" not in r.missing_fields()
