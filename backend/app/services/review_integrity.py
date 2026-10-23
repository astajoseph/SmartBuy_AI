"""Review integrity: which reviews look organic, which look manipulated, and how each vendor's ratings grow.

Deterministic signals, each with a plain-language reason (nothing here is a verdict - a flagged review is
SUSPICIOUS, not proven fake):

Per review                                                     weight
  incentivized   vendor marks it as for a free product, or the text says so      -0.50
  copied         near-identical text to another review by someone else             -0.45
  unverified     the vendor shows "Verified Purchase" but not on this review       -0.30
  generic        a few words of praise with nothing specific                       -0.35
  mismatch       star rating contradicts the text (5 stars, negative words)        -0.30
  burst          one of many high ratings posted within the same couple of days    -0.20
  repeat author  the same named reviewer posted several reviews of this product    -0.30
  + helpful votes from other shoppers, photos/video, long specific text            +0.05 .. +0.10
  Reviews of another variant (e.g. a 20 g pack) are not counted for this product at all.

Per vendor: share of likely-genuine reviews, trust-weighted rating and sentiment, star-distribution shape
(polarized 5-star/1-star patterns), page rating vs. genuine-sample rating, and review GROWTH from the
rating-count history (steady organic growth vs. sudden spikes). Marketplace sub-sellers: rating-count growth
and % positive trend from every discovery check.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from statistics import mean, median

from app.services.product_match import sizes

GENERIC_NAMES = {"amazon customer", "myntra customer", "flipkart customer", "nykaa customer", "anonymous", "customer",
                 "user", "guest", "verified buyer", "verified customer"}
INCENTIVE = re.compile(r"\b(received (?:this|the|it)?\s*(?:product )?(?:for )?free|free (?:sample|product) (?:in exchange|for (?:an? )?(?:honest )?review)"
                       r"|in exchange for (?:an? |my )?(?:honest |unbiased )?review|gifted|sponsored|#ad\b|cashback for (?:the )?review"
                       r"|discount(?:ed)? (?:in exchange )?for (?:a |my )?review|review (?:for|against) (?:a )?(?:refund|cashback))", re.I)
GENERIC_WORDS = {"good", "nice", "great", "best", "awesome", "excellent", "super", "superb", "amazing", "wow", "perfect",
                 "product", "very", "so", "item", "quality", "value", "money", "for", "worth", "it", "is", "was", "the",
                 "a", "love", "loved", "this", "thanks", "ok", "okay", "fine", "satisfied", "happy", "must", "buy", "go",
                 "cream", "osm", "gud", "nyc", "v", "really", "too", "much", "i", "my", "nice.", "recommended", "recommend"}
WEIGHTS = {"incentivized": 0.50, "copied": 0.45, "mismatch": 0.30, "repeat_author": 0.30, "unverified": 0.30,
           "generic": 0.35, "burst": 0.20}
GENUINE_AT, SUSPICIOUS_BELOW = 0.70, 0.40
MIN_FOR_CONCLUSION = 8


@dataclass
class Assessment:
    score: float
    label: str                         # likely_genuine | uncertain | suspicious | other_variant
    flags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    boosts: list[str] = field(default_factory=list)


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()


def _shingles(text: str, k: int = 3) -> set[tuple[str, ...]]:
    w = text.split()
    return {tuple(w[i:i + k]) for i in range(max(1, len(w) - k + 1))}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _content_key(r: dict) -> str:
    return f"{_norm(r.get('review_text'))[:300]}|{r.get('rating')}|{r.get('review_date')}"


def distinct_reviews(rows: list[dict]) -> list[dict]:
    """Pages are re-fetched; each fetch stores the reviews again. Keep one (the latest) row per review: rows are the
    same review when the vendor's review id matches, or - for rows stored without an id - when text, rating and date
    match a review (with or without id). Two DIFFERENT ids with the same text stay separate (that is copying)."""
    rows = [r for r in rows if r["review_kind"].endswith("_review") and (r.get("review_text") or r.get("rating") is not None)]
    by_id: dict[str, dict] = {}
    for r in sorted((r for r in rows if r.get("external_review_id")), key=lambda x: x["fetched_at"]):
        by_id[r["external_review_id"]] = r
    content_with_id = {_content_key(r) for r in by_id.values()}
    no_id: dict[str, dict] = {}
    for r in sorted((r for r in rows if not r.get("external_review_id")), key=lambda x: x["fetched_at"]):
        if _content_key(r) not in content_with_id:
            no_id[_content_key(r)] = r
    return list(by_id.values()) + list(no_id.values())


def _named(r: dict) -> str | None:
    n = (r.get("reviewer_name") or "").strip().lower()
    return n if n and n not in GENERIC_NAMES else None


def assess_all(reviews: list[dict], *, product: dict | None = None, sentiment_of=None,
               other_vendor_texts: list[dict] | None = None, aspect_words: dict[str, list[str]] | None = None) -> list[Assessment]:
    """reviews: distinct reviews of ONE vendor page. sentiment_of: text -> [-1, 1].
    other_vendor_texts: rows (vendor_source_id, external_review_id, reviewer_name, review_text) of the other vendors."""
    want_sizes = sizes((product or {}).get("name")) | sizes(str(((product or {}).get("expected_attributes") or {}).get("size") or ""))
    shows_verification = any(r.get("verified_purchase") for r in reviews)
    texts = [_norm(r.get("review_text")) for r in reviews]
    sh = [_shingles(t) for t in texts]
    other_sh = [_shingles(t) for t in (_norm(o.get("review_text")) for o in other_vendor_texts or []) if len(t.split()) >= 6]

    authors = defaultdict(int)
    for r in reviews:
        if _named(r):
            authors[_named(r)] += 1
    high_dates = [r["review_date"] for r in reviews if r.get("review_date") and (r.get("rating") or 0) >= 4]
    burst_days = set()
    for d in set(high_dates) if len(reviews) >= MIN_FOR_CONCLUSION else ():   # tiny samples: same-day reviews are normal
        near = sum(1 for x in high_dates if abs((x - d).days) <= 1)
        if near >= 3 and near >= 0.25 * len(reviews):
            burst_days.add(d)

    out = []
    for i, r in enumerate(reviews):
        a = Assessment(1.0, "likely_genuine")
        text, words = texts[i], texts[i].split()
        variant_sizes = sizes(r.get("variant_text"))
        if want_sizes and variant_sizes and not (want_sizes & variant_sizes):
            shown = ", ".join(sorted(f"{v:f} {u}" for u, v in variant_sizes))
            out.append(Assessment(0.0, "other_variant", ["other_variant"], [f"review of another variant ({shown}); not counted"]))
            continue
        if r.get("incentivized") or INCENTIVE.search(r.get("review_text") or "") or INCENTIVE.search(r.get("review_title") or ""):
            a.flags.append("incentivized")
            a.reasons.append("written for a free / discounted product (marked by the vendor or stated in the text)")
        if len(words) >= 6:
            same_page = any(j != i and len(texts[j].split()) >= 6 and _jaccard(sh[i], sh[j]) >= 0.7
                            and (_named(r) is None or _named(r) != _named(reviews[j])) for j in range(len(reviews)))
            if same_page:
                a.flags.append("copied")
                a.reasons.append("near-identical text to another review on this page by a different reviewer")
            elif any(_jaccard(sh[i], o) >= 0.7 for o in other_sh):
                a.flags.append("copied")
                a.reasons.append("same text appears on another site (copied, or syndicated by the brand)")
        if shows_verification and r.get("verified_purchase") is False:
            a.flags.append("unverified")
            a.reasons.append("not marked as a verified purchase, while other reviews on this page are")
        if 0 < len(words) <= 6 and set(words) <= GENERIC_WORDS:
            a.flags.append("generic")
            a.reasons.append("short generic praise with nothing specific about the product")
        sent = sentiment_of(r["review_text"]) if sentiment_of and r.get("review_text") else None
        rating = float(r["rating"]) if r.get("rating") is not None else None
        if rating is not None and sent is not None and len(words) >= 4:
            if rating >= 4 and sent <= -0.35:
                a.flags.append("mismatch")
                a.reasons.append(f"{rating:g}-star rating but the text is negative")
            elif rating <= 2 and sent >= 0.35:
                a.flags.append("mismatch")
                a.reasons.append(f"{rating:g}-star rating but the text is positive")
        if (rating or 0) >= 4 and r.get("review_date") and any(abs((r["review_date"] - d).days) <= 1 for d in burst_days):
            a.flags.append("burst")
            a.reasons.append("one of several high ratings posted within the same couple of days")
        if _named(r) and authors[_named(r)] >= 2:
            a.flags.append("repeat_author")
            a.reasons.append("the same reviewer posted several reviews of this product here")

        a.score -= sum(WEIGHTS[f] for f in a.flags)
        if (r.get("helpful_votes") or 0) >= 2:
            a.score += 0.10
            a.boosts.append(f"{r['helpful_votes']} shoppers found it helpful")
        if r.get("has_media"):
            a.score += 0.10
            a.boosts.append("has photos / video")
        if len(words) >= 25 and aspect_words and any(k in text for kws in aspect_words.values() for k in kws):
            a.score += 0.05
            a.boosts.append("detailed and specific")
        a.score = round(max(0.0, min(1.0, a.score)), 2)
        a.label = "likely_genuine" if a.score >= GENUINE_AT else "suspicious" if a.score < SUSPICIOUS_BELOW else "uncertain"
        out.append(a)
    return out


def distribution_check(dist: dict | None) -> dict | None:
    """dist: {"unit": "count"|"percent", "stars": {"5": n, ...}} -> shares and shape flags."""
    if not dist or not dist.get("stars"):
        return None
    stars = {int(k): float(v) for k, v in dist["stars"].items() if str(k).isdigit()}
    total = sum(stars.values())
    if total <= 0:
        return None
    pct = {k: round(100 * stars.get(k, 0) / total, 1) for k in range(5, 0, -1)}
    flags = []
    middle = pct[2] + pct[3] + pct[4]
    if pct[5] >= 70 and pct[1] >= 8 and middle <= 20:
        flags.append("polarized: mostly 5-star and 1-star with few in between - a pattern common where ratings are bought")
    elif pct[5] >= 88:
        flags.append("unusually uniform: almost all ratings are 5-star")
    return {"percent": {str(k): v for k, v in pct.items()}, "flags": flags}


def growth(aggregates: list[dict]) -> dict:
    """Rating-count history of one vendor page -> ratings per day, spikes, rating jumps."""
    pts: dict[date, dict] = {}
    for a in sorted(aggregates, key=lambda x: x["fetched_at"]):
        if a.get("review_count") is not None:
            pts[a["fetched_at"].date()] = a                       # last fetch of each day
    series = sorted(pts.values(), key=lambda x: x["fetched_at"])
    out: dict = {"snapshots": len(series), "series": [{"date": s["fetched_at"].date().isoformat(), "ratings": s["review_count"],
                                                       "rating": float(s["rating"]) if s.get("rating") is not None else None}
                                                      for s in series], "flags": []}
    if len(series) < 2:
        out["status"] = "not enough history yet"
        return out
    rates, steps = [], []
    for a, b in zip(series, series[1:]):
        days = max((b["fetched_at"] - a["fetched_at"]).total_seconds() / 86400, 0.5)
        d_count = b["review_count"] - a["review_count"]
        rates.append(d_count / days)
        steps.append((a, b, d_count, days))
    span = (series[-1]["fetched_at"] - series[0]["fetched_at"]).total_seconds() / 86400
    out["ratings_per_day"] = round((series[-1]["review_count"] - series[0]["review_count"]) / max(span, 0.5), 1)
    out["growth_pct_per_month"] = round(100 * out["ratings_per_day"] * 30 / max(series[0]["review_count"], 1), 2)
    if len(rates) >= 2:
        base = median(rates[:-1])
        if rates[-1] >= max(3 * base, base + 20) and steps[-1][2] >= 20:
            out["flags"].append(f"spike: {steps[-1][2]} new ratings in {steps[-1][3]:.1f} days vs about {base:.1f} per day before")
    for a, b, d_count, _ in steps:
        if a.get("rating") is not None and b.get("rating") is not None and a["review_count"]:
            if abs(float(b["rating"]) - float(a["rating"])) >= 0.2 and d_count < 0.02 * a["review_count"]:
                out["flags"].append(f"rating moved {float(a['rating']):g} → {float(b['rating']):g} with only {d_count} new ratings")
    if any(r < 0 for r in rates):
        out["flags"].append("rating count went down (ratings removed by the vendor)")
    out["status"] = "irregular" if out["flags"] else "steady" if len(series) >= 3 else "steady so far"
    return out


def vendor_integrity(reviews: list[dict], assessments: list[Assessment], *, aggregate: dict | None,
                     aggregate_history: list[dict], sentiment_of, hand_picked: bool = False) -> dict:
    counted = [(r, a) for r, a in zip(reviews, assessments) if a.label != "other_variant"]
    n = len(counted)
    by = defaultdict(int)
    for _, a in counted:
        by[a.label] += 1
    flag_counts = defaultdict(int)
    for _, a in counted:
        for f in a.flags:
            flag_counts[f] += 1
    rated = [(float(r["rating"]), a.score) for r, a in counted if r.get("rating") is not None]
    sents = [(sentiment_of(r["review_text"]), a.score) for r, a in counted if r.get("review_text")]
    wavg = lambda pairs: round(sum(v * w for v, w in pairs) / sum(w for _, w in pairs), 2) if pairs and sum(w for _, w in pairs) else None
    genuine_rated = [float(r["rating"]) for r, a in counted if a.label == "likely_genuine" and r.get("rating") is not None]

    flags: list[str] = []
    dist = distribution_check((aggregate or {}).get("rating_distribution"))
    if dist:
        flags += dist["flags"]
    page_rating = float(aggregate["rating"]) if aggregate and aggregate.get("rating") is not None else None
    if page_rating is not None and len(genuine_rated) >= 5 and abs(page_rating - mean(genuine_rated)) >= 0.8:
        flags.append(f"the page shows {page_rating:g}★ but the genuine-looking reviews we read average {mean(genuine_rated):.1f}★")
    g = growth(aggregate_history)
    flags += g["flags"]
    if n and flag_counts.get("incentivized", 0) / n >= 0.2:
        flags.append(f"{flag_counts['incentivized']} of {n} reviews were written for free or discounted products")

    score = None
    if n:
        score = 100 * mean(a.score for _, a in counted) - 10 * len(flags)
        score = max(0, min(100, round(score)))
    label = None if score is None else "organic" if score >= 75 else "mixed" if score >= 50 else "doubtful"
    confidence = "none" if not n else "low" if n < MIN_FOR_CONCLUSION or hand_picked else "medium" if n < 30 else "high"
    limitations = []
    if n < MIN_FOR_CONCLUSION:
        limitations.append(f"Only {n} distinct reviews could be read; the integrity score is indicative.")
    if hand_picked:
        limitations.append("This vendor shows hand-picked 'top' reviews, not a random sample.")
    if not any(r.get("verified_purchase") is not None for r, _ in counted):
        limitations.append("This vendor does not show whether a reviewer bought the product.")
    limitations.append("Reviewer histories are not available; flags mark suspicious reviews, they do not prove a review fake.")
    return {
        "integrity_score": score, "integrity_label": label, "confidence": confidence,
        "distinct_reviews": len(reviews), "counted": n,
        "other_variant_reviews": len(reviews) - n,
        "likely_genuine": by["likely_genuine"], "uncertain": by["uncertain"], "suspicious": by["suspicious"],
        "verified_share": round(sum(1 for r, _ in counted if r.get("verified_purchase")) / n, 2) if n and any(
            r.get("verified_purchase") is not None for r, _ in counted) else None,
        "flag_counts": dict(flag_counts),
        "sample_avg_rating": round(mean(v for v, _ in rated), 2) if rated else None,
        "trust_weighted_rating": wavg(rated),
        "sample_sentiment": round(mean(v for v, _ in sents), 2) if sents else None,
        "trust_weighted_sentiment": wavg(sents),
        "page_rating": page_rating,
        "rating_distribution": dist["percent"] if dist else None,
        "growth": g,
        "vendor_flags": flags,
        "limitations": limitations,
        "method": "review integrity v1 (deterministic signals)",
    }


def seller_reputation(series: list[dict]) -> list[dict]:
    """Marketplace sub-sellers: growth of their seller-rating count and % positive across discovery checks."""
    by: dict[int, list[dict]] = defaultdict(list)
    for row in series:
        by[row["seller_id"]].append(row)
    out = []
    for sid, rows in by.items():
        rows = [r for r in rows if r.get("seller_rating_count") is not None]
        if not rows:
            continue
        first, last = rows[0], rows[-1]
        days = (last["observed_at"] - first["observed_at"]).total_seconds() / 86400
        entry = {"seller_id": sid, "seller_name": first["seller_name"], "marketplace": first["marketplace"],
                 "is_active": first["is_active"], "observations": len(rows),
                 "rating": float(last["seller_rating"]) if last.get("seller_rating") is not None else None,
                 "ratings": last["seller_rating_count"],
                 "positive_pct": float(last["seller_positive_pct"]) if last.get("seller_positive_pct") is not None else None,
                 "flags": []}
        if days < 1:
            entry.update(status="not enough history yet", ratings_per_day=None)
            out.append(entry)
            continue
        gained = last["seller_rating_count"] - first["seller_rating_count"]
        entry["ratings_per_day"] = round(gained / days, 2)
        entry["positive_pct_change"] = (round(float(last["seller_positive_pct"]) - float(first["seller_positive_pct"]), 1)
                                        if first.get("seller_positive_pct") is not None and last.get("seller_positive_pct") is not None else None)
        steps = [(b["seller_rating_count"] - a["seller_rating_count"],
                  max((b["observed_at"] - a["observed_at"]).total_seconds() / 86400, 0.05)) for a, b in zip(rows, rows[1:])]
        rates = [d / t for d, t in steps]
        if len(rates) >= 3:
            base = median(rates[:-1])
            if rates[-1] >= max(5 * base, base + 10) and steps[-1][0] >= 10:
                entry["flags"].append(f"spike: {steps[-1][0]} new seller ratings in {steps[-1][1]:.1f} days")
        if entry.get("positive_pct_change") is not None and entry["positive_pct_change"] <= -5:
            entry["flags"].append(f"% positive fell by {-entry['positive_pct_change']:g} points")
        if any(d < 0 for d, _ in steps):
            entry["flags"].append("rating count went down")
        entry["status"] = "irregular" if entry["flags"] else "growing steadily" if gained > 0 else "flat"
        out.append(entry)
    return sorted(out, key=lambda e: (e["status"] != "growing steadily", -(e.get("ratings_per_day") or 0)))
