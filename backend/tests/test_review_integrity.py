"""Review sentiment + integrity (suspicious-review signals, vendor growth, sub-seller reputation).
All reviews are SYNTHETIC."""
import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.extraction.adapters.marketplaces import AmazonInAdapter
from app.extraction.base import ExtractionContext
from app.services import review_integrity as ri
from app.services.review_service import ASPECTS, score_review, summarise
from conftest import FakeFetcher, FakePage, register_product

NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)
PRODUCT = {"name": "TestBrand Glow Day Cream, 50 g", "expected_attributes": {"size": "50 g"}}
LONG_A = "The cream absorbs quickly, no white cast, my skin feels soft and hydrated all day without any greasy feeling"
LONG_B = "Used it for three weeks on dry skin, the texture is light and it did not cause any breakouts or irritation"


def rv(text, rating=5, *, rid=None, who=None, verified=True, d=date(2026, 9, 1), fetched=NOW, **kw):
    return {"review_kind": "product_review", "review_text": text, "rating": Decimal(str(rating)), "external_review_id": rid,
            "reviewer_name": who, "verified_purchase": verified, "review_date": d, "fetched_at": fetched,
            "review_source_url": "https://shop.example/p", **kw}


def agg(count, rating=4.3, fetched=NOW, dist=None):
    return {"review_kind": "product_aggregate", "rating": Decimal(str(rating)), "rating_scale": Decimal("5"),
            "review_count": count, "fetched_at": fetched, "review_source_url": "https://shop.example/p",
            "rating_distribution": dist}


def assess(reviews, **kw):
    return ri.assess_all(reviews, product=PRODUCT, sentiment_of=lambda t: score_review(t)[0], aspect_words=ASPECTS, **kw)


# ------------------------------------------------------------------ sentiment
@pytest.mark.parametrize("text, sign", [("Made my skin greasy and I got breakouts", -1), ("Absolutely love it, so lightweight 😍", 1),
                                        ("Not good at all", -1), ("fake product, smells different", -1), ("genuine product", 1)])
def test_vader_sentiment_with_product_words(text, sign):
    s, _ = score_review(text)
    assert s * sign > 0.1, s


# ------------------------------------------------------------------ per-review signals
def test_refetched_copies_are_one_review_not_a_duplicate():
    rows = [rv(LONG_A, rid="R1", who="Asha", fetched=NOW - timedelta(days=7)), rv(LONG_A, rid="R1", who="Asha")]
    items = ri.distinct_reviews(rows)
    assert len(items) == 1 and assess(items)[0].label == "likely_genuine"
    no_id = ri.distinct_reviews([rv(LONG_A, fetched=NOW - timedelta(days=7)), rv(LONG_A)])
    assert len(no_id) == 1
    mixed = ri.distinct_reviews([rv(LONG_A, fetched=NOW - timedelta(days=7)), rv(LONG_A, rid="R1", who="Asha")])  # id added later
    assert len(mixed) == 1 and mixed[0]["external_review_id"] == "R1"
    two_ids = ri.distinct_reviews([rv(LONG_A, rid="R1", who="Asha"), rv(LONG_A, rid="R2", who="Bina")])
    assert len(two_ids) == 2


def test_copied_text_by_different_reviewers():
    a = assess([rv(LONG_A, rid="R1", who="Asha"), rv(LONG_A + "!", rid="R2", who="Bina"), rv(LONG_B, rid="R3", who="Chitra")])
    assert ["copied" in x.flags for x in a] == [True, True, False]
    cross = assess([rv(LONG_B, rid="R3", who="Chitra")], other_vendor_texts=[{"review_text": LONG_B}])
    assert "copied" in cross[0].flags and "another site" in cross[0].reasons[0]


def test_incentivized_marked_or_stated():
    a = assess([rv(LONG_A, rid="R1", incentivized=True),
                rv("I received this product for free in exchange for my honest review, it is light", rid="R2"),
                rv(LONG_B, rid="R3")])
    assert [x.label for x in a] == ["uncertain", "uncertain", "likely_genuine"]
    assert all("incentivized" in x.flags for x in a[:2])


def test_unverified_counts_only_where_the_vendor_shows_verification():
    shown = assess([rv(LONG_A, rid="R1"), rv(LONG_B, rid="R2", verified=False)])
    assert "unverified" in shown[1].flags
    never = assess([rv(LONG_A, rid="R1", verified=None), rv(LONG_B, rid="R2", verified=None)])
    assert not any("unverified" in x.flags for x in never)


def test_generic_praise_mismatch_and_combined_suspicion():
    a = assess([rv("Nice product 👍", rid="R1"), rv("Worst cream ever, gave me a rash and burning", 5, rid="R2"),
                rv("Very good product", rid="R3", verified=False), rv(LONG_A, rid="R4")])
    assert "generic" in a[0].flags and a[0].label == "uncertain"
    assert "mismatch" in a[1].flags and "5-star rating but the text is negative" in a[1].reasons
    assert set(a[2].flags) == {"generic", "unverified"} and a[2].label == "suspicious"


def test_burst_of_high_ratings_and_repeat_author():
    burst = [rv(f"{LONG_A} {i}", rid=f"B{i}", who=f"P{i}", d=date(2026, 10, 1) + timedelta(days=i % 2)) for i in range(4)]
    spread = [rv(LONG_B + f" week {i}", rid=f"S{i}", who=f"Q{i}", d=date(2026, 6, 1) + timedelta(days=20 * i)) for i in range(4)]
    a = assess(burst + spread)
    assert all("burst" in x.flags for x in a[:4]) and not any("burst" in x.flags for x in a[4:])
    rep = assess([rv(LONG_A, rid="R1", who="Ravi K"), rv(LONG_B, rid="R2", who="ravi k"),
                  rv("ok", rid="R3", who="Amazon Customer"), rv("fine", rid="R4", who="Amazon Customer")])
    assert "repeat_author" in rep[0].flags and "repeat_author" not in rep[2].flags


def test_other_variant_is_excluded_and_boosts_apply():
    a = assess([rv(LONG_A, rid="R1", variant_text="Size: 20 g (Pack of 1)"),
                rv(LONG_B + " and I would buy it again for the price", rid="R2", helpful_votes=4, has_media=True)])
    assert a[0].label == "other_variant" and "20 g" in a[0].reasons[0]
    assert a[1].score == 1.0 and len(a[1].boosts) == 3


# ------------------------------------------------------------------ vendor-level
def test_distribution_shapes():
    assert ri.distribution_check({"unit": "count", "stars": {"5": 8437, "4": 2056, "3": 525, "2": 156, "1": 307}})["flags"] == []
    pol = ri.distribution_check({"unit": "percent", "stars": {"5": 78, "4": 4, "3": 3, "2": 3, "1": 12}})
    assert pol["flags"][0].startswith("polarized")
    assert ri.distribution_check({"unit": "percent", "stars": {"5": 92, "4": 5, "3": 2, "2": 0, "1": 1}})["flags"][0].startswith("unusually")


def test_growth_steady_vs_spike_vs_rating_jump():
    steady = ri.growth([agg(12000 + 4 * i, fetched=NOW - timedelta(days=30 - 7 * i)) for i in range(5)])
    assert steady["status"] == "steady" and steady["flags"] == [] and steady["ratings_per_day"] == 0.6
    spike = ri.growth([agg(12000, fetched=NOW - timedelta(days=21)), agg(12030, fetched=NOW - timedelta(days=14)),
                       agg(12060, fetched=NOW - timedelta(days=7)), agg(12600, fetched=NOW)])
    assert spike["status"] == "irregular" and spike["flags"][0].startswith("spike: 540 new ratings")
    jump = ri.growth([agg(12000, 4.0, NOW - timedelta(days=7)), agg(12010, 4.4, NOW)])
    assert "rating moved 4 → 4.4 with only 10 new ratings" in jump["flags"]
    assert ri.growth([agg(100)])["status"] == "not enough history yet"


def test_summary_down_weights_suspicious_reviews():
    texts = ["Absorbs fast and keeps my dry skin soft until evening", "Smell is mild, texture is light, a bit pricey for the size",
             "Took two weeks to see brighter skin, no breakouts so far", "Good under makeup and not sticky in humid weather",
             "The SPF is low for summer so I add sunscreen on top", "Pump stopped working after a month but the cream is fine",
             "My mother uses it daily, she likes how gentle it feels", "Delivery was quick and the seal was intact on arrival"]
    genuine = [rv(t, 3, rid=f"G{i}", who=f"G{i}", d=date(2026, 5, 1) + timedelta(days=11 * i), helpful_votes=2)
               for i, t in enumerate(texts)]
    fakes = [rv("Nice product 👍", 5, rid=f"F{i}", who=f"F{i}", verified=False, d=date(2026, 5, 3) + timedelta(days=13 * i))
             for i in range(6)]
    out = summarise(genuine + fakes + [agg(500, 4.6, dist={"unit": "percent", "stars": {"5": 80, "4": 3, "3": 4, "2": 3, "1": 10}})],
                    "Shop", product=PRODUCT)["product"]
    i = out["integrity"]
    assert (i["likely_genuine"], i["suspicious"]) == (8, 6) and i["flag_counts"] == {"generic": 6, "unverified": 6}
    assert i["sample_avg_rating"] == 3.86 and i["trust_weighted_rating"] < 3.5
    assert any(f.startswith("polarized") for f in i["vendor_flags"])
    assert any("page shows 4.6★" in f for f in i["vendor_flags"])
    assert i["integrity_label"] in ("mixed", "doubtful") and i["confidence"] == "medium"
    assert all(e["trust"] == "likely_genuine" for e in out["excerpts"]) and out["flagged_examples"][0]["trust"] == "suspicious"
    assert "6 suspicious review(s) were left out of the themes." in out["limitations"]


def test_seller_reputation_growth():
    def obs(sid, name, counts, pos):
        return [{"seller_id": sid, "seller_name": name, "seller_key": name, "is_active": True, "marketplace": "Amazon.in",
                 "observed_at": NOW - timedelta(hours=6 * (len(counts) - 1 - k)), "seller_rating": Decimal("4"),
                 "seller_rating_count": c, "seller_positive_pct": Decimal(str(p)), "price": None, "in_stock": True}
                for k, (c, p) in enumerate(zip(counts, pos))]
    rows = obs(1, "Steady", [300, 302, 304, 306, 308], [80] * 5) + obs(2, "Spiky", [50, 51, 52, 53, 120], [70, 70, 70, 70, 62]) \
        + obs(3, "New", [10], [90])
    res = {r["seller_name"]: r for r in ri.seller_reputation(rows)}
    assert res["Steady"]["status"] == "growing steadily" and res["Steady"]["ratings_per_day"] == 8.0
    assert res["Spiky"]["status"] == "irregular" and len(res["Spiky"]["flags"]) == 2
    assert res["New"]["status"] == "not enough history yet"


# ------------------------------------------------------------------ extraction + database
AMAZON_HTML = """<html><body><span id="productTitle">Glow</span>
<div id="corePriceDisplay_desktop_feature_div"><span class="priceToPay"><span class="a-price-whole">499</span></span></div>
<div id="histogramTable"><a aria-label="61 percent of reviews have 5 stars"></a><a aria-label="5 percent of reviews have 1 stars"></a></div>
<span id="acrPopover" title="4.3 out of 5 stars"></span><span id="acrCustomerReviewText">12,641 ratings</span>
<div data-hook="review" id="RTEST1"><span class="a-profile-name">Anita</span>
 <i data-hook="review-star-rating"><span>5.0 out of 5 stars</span></i><span data-hook="review-date">Reviewed in India on 17 August 2026</span>
 <a data-hook="format-strip">Size: 50 g (Pack of 1)</a><span data-hook="review-badges">Vine Customer Review of Free Product</span>
 <span data-hook="reviewText">Lovely texture, absorbs fast.</span><img data-hook="review-image-tile" src="x">
 <span data-hook="helpful-vote-statement">4 people found this helpful</span></div></body></html>"""


def test_amazon_review_details_are_extracted():
    url = "https://www.amazon.in/dp/B0TEST1234"
    r = asyncio.run(AmazonInAdapter().extract(url, ExtractionContext(fetcher=FakeFetcher({url: FakePage(200, AMAZON_HTML)}))))
    x = r.reviews[0]
    assert (x["id"], x["reviewer"], x["helpful"], x["media"], x["incentivized"]) == ("RTEST1", "Anita", 4, True, True)
    assert x["variant"] == "Size: 50 g (Pack of 1)"
    assert r.review_aggregates[0]["distribution"] == {"5": 61, "1": 5}


@pytest.mark.db
def test_reviews_endpoint_pools_fetches_and_reports_integrity(repo):
    from app.main import app

    _, (vsid,) = register_product(repo, vendors=[("shop-a", "https://shop-a.example/p")])
    base = {"product_id": "test-product", "vendor_id": "shop-a", "vendor_source_id": vsid,
            "review_source_url": "https://shop-a.example/p", "rating_scale": Decimal("5")}
    for k, when in enumerate((NOW - timedelta(days=7), NOW)):           # two fetches, the same 3 reviews each time
        exp = when + timedelta(days=1)
        repo.insert_reviews([{**base, "review_kind": "product_aggregate", "rating": Decimal("4.4"), "review_count": 1000 + 20 * k,
                              "fetched_at": when, "expires_at": exp,
                              "rating_distribution": {"unit": "count", "stars": {"5": 600, "4": 250, "3": 80, "2": 30, "1": 40}}}]
                            + [{**base, "review_kind": "product_review", "rating": Decimal(r), "review_text": t, "verified_purchase": True,
                                "external_review_id": rid, "review_date": date(2026, 9, 1), "fetched_at": when, "expires_at": exp}
                               for rid, r, t in (("R1", "5", LONG_A), ("R2", "4", LONG_B), ("R3", "5", "Nice product"))])
    with repo.db.connection() as c:
        c.execute("insert into vendor_snapshots (product_id, vendor_id, vendor_source_id, fetched_at, expires_at, fetch_status,"
                  " extraction_report) values ('test-product', 'shop-a', %s, now(), now() + interval '1 day', 'ok', %s::jsonb)",
                  (vsid, '{"expires": {"review_aggregate": "2099-01-01T00:00:00+00:00", "review_samples": "2099-01-01T00:00:00+00:00"}}'))
    body = TestClient(app).get("/api/products/test-product/reviews").json()
    p = body["vendors"][0]["product"]
    assert p["reviews_analysed"] == 3 and p["integrity"]["flag_counts"] == {"generic": 1}
    assert p["integrity"]["growth"]["ratings_per_day"] == 2.9 and p["integrity"]["rating_distribution"]["5"] == 60.0
    rep = TestClient(app).get("/api/products/test-product/seller-reputation").json()
    assert rep["sellers"] == []
