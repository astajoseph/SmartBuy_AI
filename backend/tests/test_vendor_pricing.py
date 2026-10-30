"""Delivery / membership pricing, per-vendor price history, adaptive check frequency, buy-timing score,
benchmark forecasters and user-submitted shop pages. All pages and prices are SYNTHETIC."""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.extraction.adapters.vendor_generic import VendorGenericAdapter
from app.extraction.base import ExtractionContext
from app.extraction.delivery import parse_delivery
from app.finance.effective_price import delivery_cost
from app.forecasting.baselines import BASELINES, holt_damped, seasonal_naive, theta
from app.recommendations.timing_score import TimingInputs, buy_timing_score
from app.services import discovery_service as ds
from app.services import emi_service, schedule_service, vendor_history_service, vendor_service
from conftest import FakeFetcher, FakePage, add_history, register_product

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
D = Decimal


# ------------------------------------------------------------------ delivery text (no database)
@pytest.mark.parametrize("text,expected", [
    ("FREE delivery Tomorrow. Delivery charge ₹40 on orders below ₹499",
     {"delivery_fee": D("40"), "free_delivery_min_order": D("499")}),
    ("Free delivery on orders over ₹499. Handling charge ₹7",
     {"free_delivery_min_order": D("499"), "handling_fee": D("7")}),
    ("FREE Delivery for Prime members. Prime price ₹899",
     {"membership_free_delivery": True, "member_price": D("899"), "membership_program": "Amazon Prime"}),
    ("Get FREE delivery with Myntra Insider. Insider price: ₹1,299",
     {"membership_free_delivery": True, "member_price": D("1299"), "membership_program": "Myntra Insider"}),
    ("Delivered by Fri, 3 Nov | Free delivery", {"free_delivery": True}),
    # wording from a real pharmacy page: free only for members, ₹99 for everyone else
    ("Best Price ₹767 with bank offers Get Free delivery ( ₹99 ) with Join Circle Extra 15% Off with Bank Offers",
     {"delivery_fee": D("99"), "membership_free_delivery": True, "membership_program": "Apollo Circle"}),
    ("₹49 delivery charges applicable", {"delivery_fee": D("49")}),
])
def test_delivery_text_is_structured(text, expected):
    got = parse_delivery(text)
    assert {k: got.get(k) for k in expected} == expected


def test_member_only_free_delivery_is_not_reported_as_free_for_everyone():
    got = parse_delivery("Get Free delivery ( ₹99 ) with Join Circle")
    assert "free_delivery" not in got and got["delivery_fee"] == D("99")


def test_delivery_text_never_guesses():
    assert parse_delivery("Great cream, 4.5 stars. Buy now.") == {}
    # a first-order promotion is not the delivery policy; the stated charge stays
    got = parse_delivery("Free shipping on your first order! Standard shipping charges ₹50")
    assert got["delivery_fee"] == D("50") and "free_delivery" not in got
    # free next to a fee with no stated condition is unclear, so it is not reported as free
    assert "free_delivery" not in parse_delivery("Free delivery. Delivery charge ₹40")


def test_vendor_adapter_reads_delivery_and_membership_from_the_page():
    html = """<html><head><script type="application/ld+json">{"@type":"Product","name":"Test","offers":
        {"@type":"Offer","price":"450","priceCurrency":"INR","availability":"https://schema.org/InStock"}}</script></head>
        <body><p>Delivery charge ₹40 on orders below ₹499. Free delivery for Prime members.</p></body></html>"""
    url = "https://shop.example/p"
    r = asyncio.run(VendorGenericAdapter().extract(url, ExtractionContext(fetcher=FakeFetcher({url: FakePage(200, html)}))))
    assert r.vendor["delivery_fee"] == D("40") and r.vendor["free_delivery_min_order"] == D("499")
    assert r.vendor["membership_free_delivery"] is True and r.vendor["membership_program"] == "Amazon Prime"


# ------------------------------------------------------------------ effective price
def test_delivery_cost_cases():
    assert delivery_cost(D("450"), fee=D("40"), free_min_order=D("499"))[0] == D("40")
    assert delivery_cost(D("520"), fee=D("40"), free_min_order=D("499"))[0] == D("0")
    assert delivery_cost(D("450"), fee=D("40"), free_min_order=D("499"), handling_fee=D("7"))[0] == D("47")
    assert delivery_cost(D("450"), fee=D("40"), member_free=True, as_member=True)[0] == D("0")
    unknown, notes = delivery_cost(D("450"))
    assert unknown is None and "not stated" in notes[0]                    # never assumed to be free
    below, notes = delivery_cost(D("450"), free_min_order=D("499"))
    assert below is None and "499" in notes[0]


def _vd(snap, status="live"):
    vs = {"vendor_id": "shop", "vendor_name": "Shop", "source_url": "https://shop.example/p"}
    return vendor_service.VendorData(vendor_source=vs, snapshot={"price": D("450"), "mrp": None, "currency": "INR",
                                                                 "availability": "InStock", **snap}, status=status)


def test_comparison_adds_delivery_and_offers_a_membership_option():
    cmp = emi_service.build_comparison([_vd({"delivery_fee": D("40"), "free_delivery_min_order": D("499"),
                                             "membership_program": "Amazon Prime", "membership_free_delivery": True})])
    row = cmp["vendors"][0]
    assert row["displayed_price"] == "450" and row["effective_upfront_price"] == "490"
    assert row["delivery_cost"] == "40" and row["membership"]["total_with_membership"] == "450"
    assert [o["total_cost"] for o in cmp["ranked_options"]] == ["490"]
    assert [o["total_cost"] for o in cmp["conditional_options"]] == ["450"]
    assert "Amazon Prime" in cmp["conditional_options"][0]["notes"][0]


def test_comparison_without_delivery_info_keeps_the_price_and_says_so():
    row = emi_service.build_comparison([_vd({})])["vendors"][0]
    assert row["effective_upfront_price"] == "450" and row["delivery_cost"] is None
    assert any("not stated" in n for n in row["notes"])


# ------------------------------------------------------------------ per-vendor history (database)
def _point(repo, vsid, vendor_id, at, price, *, in_stock=True, fee=None, free_min=None):
    eff = None if price is None else price + (fee or 0)
    repo.insert_vendor_price_point({
        "product_id": "test-product", "vendor_id": vendor_id, "vendor_source_id": vsid, "observed_at": at,
        "price": price, "mrp": 1000, "in_stock": in_stock, "availability": "InStock" if in_stock else "OutOfStock",
        "delivery_fee": fee, "free_delivery_min_order": free_min, "effective_price": eff, "delivery_known": fee is not None})


@pytest.mark.db
def test_each_vendor_check_is_stored_as_a_price_point_with_delivery(repo):
    _, (vsid,) = register_product(repo, vendors=[("shop-a", "https://shop-a.example/p/1")])
    html = """<html><head><script type="application/ld+json">{"@type":"Product","name":"Test","offers":{"@type":"Offer",
      "price":"450","priceCurrency":"INR","availability":"https://schema.org/InStock"}}</script></head>
      <body>Delivery charge ₹40 on orders below ₹499. Free delivery for Prime members.</body></html>"""
    fetcher = FakeFetcher({"https://shop-a.example/p/1": FakePage(200, html)})
    deps = vendor_service.VendorDeps(fetcher_factory=lambda h: fetcher, browser_factory=lambda: None)
    vd = asyncio.run(vendor_service.get_vendor_data(repo, "test-product", ("price",), deps=deps))[0]
    assert vd.snapshot["delivery_fee"] == D("40.00") and vd.snapshot["membership_program"] == "Amazon Prime"
    pts = repo.vendor_price_points("test-product")
    assert len(pts) == 1 and pts[0]["price"] == D("450.00") and pts[0]["in_stock"] is True
    assert pts[0]["effective_price"] == D("490.00") and pts[0]["delivery_known"] is True
    asyncio.run(vendor_service.get_vendor_data(repo, "test-product", ("price",), deps=deps, force_refresh=True))
    assert len(repo.vendor_price_points("test-product")) == 2


@pytest.mark.db
def test_vendor_history_compares_vendors_with_stock_delivery_and_relation(repo):
    sid, (a, b, c) = register_product(repo, vendors=[("shop-a", "https://a.example/p"), ("shop-b", "https://b.example/p"),
                                                     ("shop-c", "https://c.example/p")])
    start = NOW - timedelta(days=20)
    add_history(repo, "test-product", sid, [(start + timedelta(days=i), 1000 + (i % 5) * 10) for i in range(21)])
    for i in range(21):
        t = start + timedelta(days=i, hours=1)
        _point(repo, a, "shop-a", t, 950 + (i % 5) * 10)                         # tracks the main series, 50 lower
        _point(repo, b, "shop-b", t, 900, fee=60, free_min=2000)                 # cheaper sticker, costs 60 to deliver
        _point(repo, c, "shop-c", t, None if i >= 15 else 940, in_stock=i < 15)  # went out of stock

    h = vendor_history_service.vendor_history(repo, "test-product", now=NOW)
    by = {v["vendor_id"]: v for v in h["vendors"]}
    assert set(by) == {"shop-a", "shop-b", "shop-c"}
    assert by["shop-c"]["stats"]["in_stock"] is False and by["shop-c"]["stats"]["in_stock_share"] < 1
    assert by["shop-b"]["stats"]["price"] == 900 and by["shop-b"]["stats"]["effective_price"] == 960
    assert by["shop-a"]["relation_to_main_price"]["median_gap"] == -50.0
    assert by["shop-a"]["relation_to_main_price"]["correlation"] == pytest.approx(1.0)   # moves with the main price
    assert by["shop-b"]["relation_to_main_price"]["correlation"] is None                  # flat: movements not comparable
    # shop-b has the lowest sticker price but shop-a is cheaper once delivery is included
    cmp = h["comparison"]
    assert cmp["cheapest_now"]["vendor"] == "Shop-A" and cmp["compared"] == 2
    assert {"vendor": "Shop-C", "reason": "out of stock"} in cmp["excluded"]
    assert by["shop-a"]["forecast"]["status"] == "insufficient_data"                     # 21 days is too short; stated, not guessed


@pytest.mark.db
def test_vendor_history_endpoint_reads_the_database_only(repo, monkeypatch):
    from app.main import app

    _, (a,) = register_product(repo, vendors=[("shop-a", "https://a.example/p")])
    _point(repo, a, "shop-a", datetime.now(timezone.utc) - timedelta(hours=2), 500)

    async def boom(*args, **kw):
        raise AssertionError("a vendor page was requested")

    monkeypatch.setattr(vendor_service, "get_vendor_data", boom)
    r = TestClient(app).get("/api/products/test-product/vendor-price-history")
    assert r.status_code == 200 and r.json()["vendors"][0]["stats"]["price"] == 500
    assert TestClient(app).get("/api/products/nope/vendor-price-history").status_code == 404


# ------------------------------------------------------------------ adaptive frequency
def test_adaptive_interval_rules():
    base = 360
    assert schedule_service.adaptive_interval(base, {}) == (360, [])
    assert schedule_service.adaptive_interval(base, {"has_alert": True})[0] == 180
    assert schedule_service.adaptive_interval(base, {"has_alert": True, "sale_on": True})[0] == 90
    assert schedule_service.adaptive_interval(base, {"changes_14d": 4})[0] == 180
    assert schedule_service.adaptive_interval(base, {"points_30d": 10, "changes_30d": 0})[0] == 720
    assert schedule_service.adaptive_interval(base, {"points_30d": 40, "changes_30d": 0, "points_90d": 90, "changes_90d": 0})[0] == 1080
    assert schedule_service.adaptive_interval(base, {"out_of_stock": True})[0] == 1080
    assert schedule_service.adaptive_interval(base, {}, consecutive_failures=3)[0] == 720
    assert schedule_service.adaptive_interval(base, {}, consecutive_failures=7)[0] == 1440
    # limits: never below 30 minutes; never above a day unless the configured interval is already longer
    assert schedule_service.adaptive_interval(60, {"has_alert": True, "sale_on": True, "changes_14d": 5})[0] == 30
    assert schedule_service.adaptive_interval(360, {"out_of_stock": True}, consecutive_failures=7)[0] == 1440
    assert schedule_service.adaptive_interval(2000, {})[0] == 2000
    # too little data is not "flat"
    assert schedule_service.adaptive_interval(base, {"points_30d": 3, "changes_30d": 0})[0] == 360


@pytest.mark.db
def test_collector_checks_alert_products_sooner_and_out_of_stock_products_later(repo):
    sid, _ = register_product(repo)
    repo.mark_source_attempt(sid, success=True)
    with repo.db.connection() as c:
        c.execute("update product_sources set last_success_at = %s, last_attempt_at = %s",
                  (datetime.now(timezone.utc) - timedelta(minutes=200),) * 2)
    now = datetime.now(timezone.utc)
    assert schedule_service.due_sources(repo, now) == []                       # 200 min < 360 min baseline
    with repo.db.connection() as c:
        c.execute("insert into user_targets (user_id, product_id, target_price) values (gen_random_uuid(), 'test-product', 500)")
    due = schedule_service.due_sources(repo, now)
    assert [d["id"] for d in due] == [sid] and due[0]["effective_interval_minutes"] == 180
    assert "price alert set (x0.5)" in due[0]["schedule_reasons"]
    with repo.db.connection() as c:
        c.execute("delete from user_targets")
        c.execute("""insert into price_observations (product_id, source_id, observed_at, price, currency, availability)
                     values ('test-product', %s, now(), 500, 'INR', 'OutOfStock')""", (sid,))
        c.execute("update product_sources set last_success_at = %s", (now - timedelta(minutes=400),))
    assert schedule_service.due_sources(repo, now) == []                       # out of stock: 1080 min
    assert schedule_service.schedule_overview(repo, now)[0]["effective_interval_minutes"] == 1080


@pytest.mark.db
def test_out_of_stock_shops_are_rechecked_daily_not_every_run(repo):
    from test_discovery import FakeSearch, _deps, _setup, shop_page

    product = _setup(repo)
    good = "https://good.example/glow-50"
    page = FakePage(200, shop_page("TestBrand Glow Day Cream SPF 15, 50 g", 549, availability="OutOfStock"))
    fetcher = FakeFetcher({good: page})
    vid = ds.vendor_id_for("good.example")
    repo.upsert_vendor(vid, "good.example", "good.example")
    vsid = repo.add_discovered_vendor_source("test-product", vid, good, adapter_name="vendor_generic",
                                             origin="web_search", in_stock_at=None)
    with repo.db.connection() as c:
        c.execute("update product_vendor_sources set last_checked_at = null where id = %s", (vsid,))
    deps = _deps(fetcher, None, FakeSearch([]))
    asyncio.run(ds.discover_product(repo, product, deps=deps, search=False))
    assert fetcher.requested == [good]                                         # first look
    asyncio.run(ds.discover_product(repo, product, deps=deps, search=False))
    assert fetcher.requested == [good]                                         # not selling: not again within 24 h
    with repo.db.connection() as c:
        c.execute("update product_vendor_sources set last_checked_at = now() - interval '25 hours' where id = %s", (vsid,))
    asyncio.run(ds.discover_product(repo, product, deps=deps, search=False))
    assert fetcher.requested == [good, good]


# ------------------------------------------------------------------ buy-timing score
def test_buy_timing_score_is_high_at_a_low_and_low_at_a_high():
    good = buy_timing_score(TimingInputs(current=900, percentile_365=5, median_30d=1000, historical_low=890,
                                         forecast_point=905))
    bad = buy_timing_score(TimingInputs(current=1200, percentile_365=95, median_30d=1000, historical_low=890,
                                        forecast_point=1100, supported_sale_days=5))
    assert good["score"] >= 75 and good["label"] == "Good time to buy"
    assert bad["score"] <= 35 and bad["label"] == "Better to wait"
    assert good["coverage"] == 0.85 and bad["coverage"] == 1.0
    assert {c["name"] for c in bad["components"]} == {"percentile_365d", "vs_30d_median", "vs_historical_low",
                                                       "forecast", "upcoming_sale"}


def test_buy_timing_score_says_when_it_cannot_judge():
    assert buy_timing_score(TimingInputs(current=None))["score"] is None
    assert buy_timing_score(TimingInputs(current=500))["score"] is None
    thin = buy_timing_score(TimingInputs(current=500, median_30d=500))
    assert thin["coverage"] == 0.2 and any("rough" in c for c in thin["caveats"])


# ------------------------------------------------------------------ benchmark forecasters
def _daily(values):
    return pd.Series(values, index=pd.date_range("2026-01-01", periods=len(values)), dtype=float)


def test_benchmark_forecasters_on_known_shapes():
    flat = _daily([100.0] * 120)
    for name in ("seasonal_naive_7d", "ses_auto", "theta", "holt_damped"):
        assert BASELINES[name](flat, 14) == pytest.approx(100.0), name
    ramp = _daily([100 + i for i in range(120)])                               # rising 1/day
    assert BASELINES["theta"](ramp, 14) > 219 + 3 and BASELINES["holt_damped"](ramp, 14) > 219 + 3
    assert BASELINES["holt_damped"](ramp, 14) < 219 + 14                       # damped: less than the straight line
    weekly = _daily([100, 100, 100, 100, 100, 80, 80] * 20)                    # cheaper at weekends
    last = len(weekly) - 1
    assert seasonal_naive(7)(weekly, 7) == weekly.iloc[last]                   # one week ahead = same weekday
    assert seasonal_naive(7)(weekly, 1) == weekly.iloc[last - 6]


def test_benchmark_forecasters_ignore_unknown_days():
    s = _daily([100.0] * 60 + [np.nan] * 5 + [120.0] * 60)
    for name, f in BASELINES.items():
        assert np.isfinite(f(s, 7)), name


# ------------------------------------------------------------------ user-submitted shop pages (database)
@pytest.mark.db
def test_submitted_page_is_checked_like_a_search_result(repo):
    from test_discovery import _deps, _setup, shop_page

    _setup(repo)
    good, other = "https://good.example/glow-50", "https://other.example/glow-100"
    fetcher = FakeFetcher({good: FakePage(200, shop_page("TestBrand Glow Day Cream SPF 15, 50 g", 549)),
                           other: FakePage(200, shop_page("TestBrand Glow Day Cream SPF 15, 100 g", 899))})
    deps = _deps(fetcher)

    ok = asyncio.run(ds.submit_url(repo, "test-product", good + "?utm_source=mail", "someone@example.com", deps=deps))
    assert ok["status"] == "auto_added" and ok["vendor_source_id"]
    assert repo.get_candidate(ok["candidate_id"])["submitted_by"] == "someone@example.com"

    again = asyncio.run(ds.submit_url(repo, "test-product", good, "someone@example.com", deps=deps))
    assert again["status"] == "already_tracked"

    wrong = asyncio.run(ds.submit_url(repo, "test-product", other, "someone@example.com", deps=deps))
    assert wrong["status"] == "rejected" and "different pack size" in wrong["reasons"][0]

    with pytest.raises(ds.SubmissionError):
        asyncio.run(ds.submit_url(repo, "test-product", "https://www.youtube.com/watch?v=1", "x", deps=deps))
    with pytest.raises(ds.SubmissionError):
        asyncio.run(ds.submit_url(repo, "test-product", "http://127.0.0.1/admin", "x", deps=deps))
    with pytest.raises(LookupError):
        asyncio.run(ds.submit_url(repo, "nope", good, "x", deps=deps))


@pytest.mark.db
def test_submitting_a_url_requires_sign_in(repo):
    from app.main import app

    register_product(repo)
    r = TestClient(app).post("/api/products/test-product/vendor-urls", json={"url": "https://good.example/glow-50"})
    assert r.status_code == 401
