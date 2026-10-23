"""Review analysis: sentiment, aspect themes and review integrity (deterministic and inspectable).

Sentiment: VADER (vaderSentiment, MIT - rule-based model that handles negation, intensifiers, CAPS, "!!!", "but"
and emojis) with its lexicon extended by product-domain words ("greasy", "breakout", "counterfeit" ...), scored
per sentence. Integrity: services/review_integrity.py flags suspicious reviews and down-weights them.

Product reviews and seller reviews are summarised separately. Only reviews actually retrieved are
analysed; quotations are never invented - the UI shows short excerpts of real stored review text.
Review text is untrusted data: it is only ever passed to the LLM inside a clearly delimited data
block (see llm/prompts.py).
"""
from __future__ import annotations

import re
from collections import defaultdict
from functools import lru_cache
from statistics import mean

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

ASPECTS: dict[str, list[str]] = {
    "build_quality": ["build", "quality", "material", "sturdy", "flimsy", "finish", "premium feel"],
    "performance": ["performance", "fast", "slow", "lag", "smooth", "speed", "works well"],
    "battery_life": ["battery", "charge", "charging", "backup"],
    "reliability": ["stopped working", "defect", "defective", "broke", "durable", "reliable", "faulty"],
    "value_for_money": ["value", "worth", "price", "expensive", "cheap", "overpriced", "affordable"],
    "delivery": ["delivery", "delivered", "late", "on time", "courier", "shipping"],
    "packaging": ["packaging", "packed", "package", "seal", "sealed", "damaged box"],
    "customer_service": ["customer service", "customer care", "support", "service"],
    "returns_refunds": ["return", "refund", "replacement", "replace"],
    "warranty": ["warranty", "guarantee"],
    "authenticity": ["fake", "original", "genuine", "authentic", "duplicate", "counterfeit"],
    "texture_feel": ["texture", "sticky", "greasy", "lightweight", "white cast", "absorbs", "oily", "smell", "fragrance"],
    "skin_reaction": ["breakout", "break out", "irritation", "rash", "burning", "itch", "allergy", "pimples", "gentle"],
}
SMALL_SAMPLE = 20


# VADER valences (-4 .. +4) for product words its general-purpose lexicon does not know or rates neutrally.
DOMAIN_LEXICON = {
    "greasy": -1.6, "sticky": -1.3, "oily": -1.2, "breakout": -2.0, "breakouts": -2.0, "pimples": -1.8, "acne": -1.2,
    "rash": -2.0, "rashes": -2.0, "irritation": -2.0, "itchy": -1.6, "itching": -1.6, "burning": -1.8, "allergic": -1.8,
    "fake": -2.2, "duplicate": -1.8, "counterfeit": -2.5, "expired": -2.0, "leaked": -1.6, "leaking": -1.6,
    "damaged": -1.6, "overpriced": -1.8, "refund": -0.8, "flimsy": -1.6, "defective": -2.2, "faulty": -2.0,
    "genuine": 1.6, "original": 1.0, "authentic": 1.6, "lightweight": 1.2, "absorbs": 1.0, "non-greasy": 1.6,
    "hydrated": 1.3, "hydrating": 1.3, "glowing": 1.6, "glow": 1.2, "soft": 1.0, "smooth": 1.2, "affordable": 1.3,
    "durable": 1.6, "sturdy": 1.4,
}


@lru_cache(maxsize=1)
def _analyzer() -> SentimentIntensityAnalyzer:
    a = SentimentIntensityAnalyzer()
    a.lexicon.update(DOMAIN_LEXICON)
    return a


def _polarity(sentence: str) -> float:
    """VADER compound score in [-1, 1]."""
    return _analyzer().polarity_scores(sentence)["compound"]


def score_review(text: str) -> tuple[float | None, dict[str, float]]:
    """Returns (overall sentiment in [-1, 1], {aspect: sentiment}) for one review."""
    if not text or not text.strip():
        return None, {}
    sentences = [s for s in re.split(r"[.!?\n]+", text) if s.strip()]
    overall = round(mean(_polarity(s) for s in sentences), 3) if sentences else None
    aspects: dict[str, list[float]] = defaultdict(list)
    for s in sentences:
        low = s.lower()
        for aspect, kws in ASPECTS.items():
            if any(re.search(rf"\b{re.escape(k)}\b", low) for k in kws):
                aspects[aspect].append(_polarity(s))
    return overall, {a: round(mean(v), 3) for a, v in aspects.items()}


HAND_PICKED_ADAPTERS = {"myntra"}          # pages that show curated "top reviews" instead of a random sample


def analyse_vendor(repo, vendor_data, product: dict | None) -> dict:
    """Review analysis for one vendor from EVERY fetch stored for its page (a larger sample than the latest fetch),
    with the other vendors' review texts for the cross-site copy check."""
    vs = vendor_data.vendor_source
    rows = repo.review_history(vs["id"]) or vendor_data.reviews
    others = [o for o in repo.product_review_texts(vs["product_id"]) if o["vendor_source_id"] != vs["id"]]
    return summarise(rows, vs.get("vendor_name") or vs["vendor_id"], product=product, other_vendor_texts=others,
                     hand_picked=vs.get("adapter_name") in HAND_PICKED_ADAPTERS)


def summarise(reviews: list[dict], vendor_name: str, *, product: dict | None = None,
              other_vendor_texts: list[dict] | None = None, hand_picked: bool = False) -> dict:
    """reviews: vendor_reviews rows for one vendor (aggregates + individual reviews, any number of fetches)."""
    from app.services import review_integrity as ri

    out = {"vendor": vendor_name, "product": None, "seller": None}
    sentiment = lambda t: score_review(t)[0]
    for scope in ("product", "seller"):
        aggs = sorted((r for r in reviews if r["review_kind"] == f"{scope}_aggregate"), key=lambda r: r["fetched_at"])
        items = ri.distinct_reviews([r for r in reviews if r["review_kind"] == f"{scope}_review"])
        if not aggs and not items:
            continue
        agg = aggs[-1] if aggs else None
        assessed = ri.assess_all(items, product=product, sentiment_of=sentiment, other_vendor_texts=other_vendor_texts,
                                 aspect_words=ASPECTS)
        integrity = ri.vendor_integrity(items, assessed, aggregate=agg, aggregate_history=aggs, sentiment_of=sentiment,
                                        hand_picked=hand_picked)
        counted = [(r, a) for r, a in zip(items, assessed) if a.label != "other_variant"]
        trusted = [(r, a) for r, a in counted if a.label != "suspicious" and r.get("review_text")]
        block: dict = {
            "aggregate_rating": float(agg["rating"]) if agg and agg["rating"] is not None else None,
            "rating_scale": float(agg["rating_scale"]) if agg and agg["rating_scale"] is not None else None,
            "rating_count": agg["review_count"] if agg else None,
            "reviews_analysed": len(counted),
            "source_url": (agg or items[0])["review_source_url"],
            "fetched_at": max(r["fetched_at"] for r in (aggs + items)).isoformat(),
            "integrity": integrity,
        }
        if counted:
            dates = sorted(r["review_date"] for r, _ in counted if r.get("review_date"))
            block["review_date_range"] = [dates[0].isoformat(), dates[-1].isoformat()] if dates else None
            mentions: dict[str, list[tuple[float, float]]] = defaultdict(list)
            for r, a in trusted:                     # themes from reviews that do not look manipulated, trust-weighted
                for asp, sc in score_review(r["review_text"])[1].items():
                    mentions[asp].append((sc, a.score))
            min_mentions = max(2, int(0.1 * len(trusted)))
            wavg = lambda v: sum(x * w for x, w in v) / sum(w for _, w in v) if sum(w for _, w in v) else mean(x for x, _ in v)
            themes = {asp: (len(v), wavg(v)) for asp, v in mentions.items() if len(v) >= min_mentions}
            block["positive_themes"] = [{"aspect": asp, "mentions": n, "avg_sentiment": round(sv, 2)}
                                        for asp, (n, sv) in sorted(themes.items(), key=lambda x: -x[1][0]) if sv > 0.15]
            block["negative_themes"] = [{"aspect": asp, "mentions": n, "avg_sentiment": round(sv, 2)}
                                        for asp, (n, sv) in sorted(themes.items(), key=lambda x: -x[1][0]) if sv < -0.15]
            block["aspect_sentiment"] = {asp: {"mentions": len(v), "avg": round(wavg(v), 2)} for asp, v in mentions.items()}
            ordered = sorted(counted, key=lambda ra: (-ra[1].score, -(ra[0].get("helpful_votes") or 0)))
            block["excerpts"] = [_excerpt(r, a) for r, a in ordered if r.get("review_text") and a.label == "likely_genuine"][:5]
            block["flagged_examples"] = [_excerpt(r, a) for r, a in sorted(counted, key=lambda ra: ra[1].score)
                                         if r.get("review_text") and a.label in ("suspicious", "uncertain")][:3]
        limitations = []
        if not items:
            limitations.append("Only an aggregate rating is available; individual reviews were not analysed.")
        elif len(counted) < SMALL_SAMPLE:
            limitations.append(f"Only {len(counted)} reviews were available; themes are indicative, not conclusive.")
        if trusted and len(trusted) < len(counted):
            limitations.append(f"{len(counted) - len(trusted)} suspicious review(s) were left out of the themes.")
        limitations.append("Sentiment is a model estimate (VADER + product words) and is not proof of product quality.")
        block["limitations"] = limitations
        block["method"] = "VADER sentiment + domain lexicon v2; review integrity v1"
        out[scope] = block
    return out


def _excerpt(r: dict, a) -> dict:
    return {"text": r["review_text"][:240], "rating": float(r["rating"]) if r.get("rating") is not None else None,
            "date": r["review_date"].isoformat() if r.get("review_date") else None,
            "verified": r.get("verified_purchase"), "helpful_votes": r.get("helpful_votes"),
            "trust": a.label, "trust_score": a.score, "reasons": a.reasons, "boosts": a.boosts}
