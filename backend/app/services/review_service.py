"""Aspect-based review analysis (lexicon method, deterministic and inspectable).

Product reviews and seller reviews are summarised separately. Only reviews actually retrieved are
analysed; quotations are never invented - the UI shows short excerpts of real stored review text.
Review text is untrusted data: it is only ever passed to the LLM inside a clearly delimited data
block (see llm/prompts.py).
"""
from __future__ import annotations

import re
from collections import defaultdict
from statistics import mean

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
POSITIVE = {"good", "great", "excellent", "amazing", "love", "loved", "best", "nice", "perfect", "awesome", "happy",
            "recommend", "fast", "smooth", "sturdy", "durable", "reliable", "worth", "genuine", "original", "authentic",
            "lightweight", "gentle", "affordable", "premium", "satisfied", "fine", "effective", "quick", "absorbs"}
NEGATIVE = {"bad", "poor", "worst", "terrible", "awful", "hate", "slow", "lag", "broke", "broken", "defective", "faulty",
            "fake", "duplicate", "counterfeit", "late", "delayed", "damaged", "expensive", "overpriced", "sticky", "greasy",
            "irritation", "rash", "burning", "breakout", "disappointed", "waste", "useless", "issue", "problem", "oily",
            "itch", "pimples", "flimsy", "refund"}
NEGATORS = {"not", "no", "never", "n't", "dont", "don't", "didnt", "didn't", "isnt", "isn't", "wasnt", "wasn't"}
SMALL_SAMPLE = 20


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower())


def _polarity(sentence: str) -> float:
    toks = _tokens(sentence)
    score = 0
    for i, t in enumerate(toks):
        s = 1 if t in POSITIVE else -1 if t in NEGATIVE else 0
        if s and any(n in NEGATORS for n in toks[max(0, i - 3):i]):
            s = -s
        score += s
    return max(-1.0, min(1.0, score / 3))


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


def summarise(reviews: list[dict], vendor_name: str) -> dict:
    """reviews: vendor_reviews rows for one vendor (aggregates + individual reviews)."""
    out = {"vendor": vendor_name, "product": None, "seller": None}
    for scope in ("product", "seller"):
        aggs = [r for r in reviews if r["review_kind"] == f"{scope}_aggregate"]
        items = [r for r in reviews if r["review_kind"] == f"{scope}_review" and r.get("review_text")]
        if not aggs and not items:
            continue
        agg = aggs[0] if aggs else None
        block: dict = {
            "aggregate_rating": float(agg["rating"]) if agg and agg["rating"] is not None else None,
            "rating_scale": float(agg["rating_scale"]) if agg and agg["rating_scale"] is not None else None,
            "rating_count": agg["review_count"] if agg else None,
            "reviews_analysed": len(items),
            "source_url": (agg or items[0])["review_source_url"],
            "fetched_at": (agg or items[0])["fetched_at"].isoformat(),
        }
        if items:
            dates = sorted(r["review_date"] for r in items if r.get("review_date"))
            block["review_date_range"] = [dates[0].isoformat(), dates[-1].isoformat()] if dates else None
            mentions: dict[str, list[float]] = defaultdict(list)
            for r in items:
                for a, s in (r.get("aspect_labels") or {}).items():
                    mentions[a].append(float(s))
            min_mentions = max(2, int(0.1 * len(items)))
            themes = {a: (len(v), mean(v)) for a, v in mentions.items() if len(v) >= min_mentions}
            block["positive_themes"] = [{"aspect": a, "mentions": n, "avg_sentiment": round(s, 2)}
                                        for a, (n, s) in sorted(themes.items(), key=lambda x: -x[1][0]) if s > 0.15]
            block["negative_themes"] = [{"aspect": a, "mentions": n, "avg_sentiment": round(s, 2)}
                                        for a, (n, s) in sorted(themes.items(), key=lambda x: -x[1][0]) if s < -0.15]
            block["aspect_sentiment"] = {a: {"mentions": len(v), "avg": round(mean(v), 2)} for a, v in mentions.items()}
            block["excerpts"] = [{"text": r["review_text"][:240], "rating": float(r["rating"]) if r.get("rating") is not None else None,
                                  "date": r["review_date"].isoformat() if r.get("review_date") else None}
                                 for r in items[:5]]
        limitations = []
        if not items:
            limitations.append("Only an aggregate rating is available; individual reviews were not analysed.")
        elif len(items) < SMALL_SAMPLE:
            limitations.append(f"Only {len(items)} reviews were available; themes are indicative, not conclusive.")
        limitations.append("Sentiment is a keyword-based estimate and is not proof of product quality.")
        block["limitations"] = limitations
        block["method"] = "lexicon aspect sentiment v1"
        out[scope] = block
    return out
