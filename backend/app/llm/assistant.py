"""Conversational purchase advisor.

USER QUESTION -> identify product & intent (deterministic) -> recommendation engine (Supabase data;
vendor pages only if the intent needs EMI/vendor/review data, cache first) -> evidence -> LLM explanation
-> numeric guard (every ₹ amount in the answer must appear in the evidence) -> response.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.database.repositories import Repository
from app.llm.prompts import SYSTEM_PROMPT, build_user_prompt
from app.llm.provider import LLMError, LLMProvider, get_provider
from app.recommendations.engine import recommend
from app.recommendations.schemas import Recommendation, RecommendationRequest

VENDOR_WORDS = r"\b(emi|instal?ments?|monthly|no[\s-]?cost|bank|card|vendor|seller|store|where (?:to|should i) buy|which (?:site|shop|website)|cheapest (?:place|site|vendor)|financ\w*|interest|tenure)\b"
REVIEW_WORDS = r"\b(reviews?|complain\w*|ratings?|feedback|quality|genuine|fake|issues?|problems?)\b"
REFRESH_WORDS = r"\b(refresh|latest offers?|check again|re-?check|live)\b"


@dataclass
class ParsedQuestion:
    product_id: str | None
    needs_vendor: bool
    needs_reviews: bool
    refresh: bool
    target_price: float | None = None
    max_monthly: float | None = None
    deadline_days: int | None = None
    candidates: list[str] = field(default_factory=list)


def _amount(s: str) -> float:
    return float(s.replace(",", ""))


def parse_question(text: str, products: list[dict], selected: str | None) -> ParsedQuestion:
    low = text.lower()
    pid = selected
    if not pid:
        scores = []
        for p in products:
            words = {w for w in re.findall(r"[a-z0-9]+", (p["name"] + " " + (p.get("brand") or "")).lower()) if len(w) > 3}
            hit = sum(1 for w in words if w in low)
            if hit or p["id"] in low:
                scores.append((hit + (5 if p["id"] in low else 0), p["id"]))
        scores.sort(reverse=True)
        if scores and (len(scores) == 1 or scores[0][0] > scores[1][0]):
            pid = scores[0][1]
        elif len(products) == 1:
            pid = products[0]["id"]
    pq = ParsedQuestion(product_id=pid, needs_vendor=bool(re.search(VENDOR_WORDS, low)),
                        needs_reviews=bool(re.search(REVIEW_WORDS, low)), refresh=bool(re.search(REFRESH_WORDS, low)),
                        candidates=[p["id"] for p in products])
    m = re.search(r"(?:₹|rs\.?|inr)\s*([\d,]+)\s*(?:/|per|a)\s*(?:month|mo)\b", low) or \
        re.search(r"([\d,]+)\s*(?:₹|rs\.?|rupees)?\s*(?:/|per|a)\s*month", low)
    if m:
        pq.max_monthly = _amount(m.group(1))
        pq.needs_vendor = True
    m = re.search(r"(?:target|below|under|less than|drop to|reach(?:es)?|at)\s*(?:of\s*)?(?:₹|rs\.?|inr)?\s*([\d,]{3,})", low)
    if m and (pq.max_monthly is None or _amount(m.group(1)) != pq.max_monthly):
        pq.target_price = _amount(m.group(1))
    m = re.search(r"(?:within|in|next)\s+(\d{1,3})\s*(day|week|month)s?", low)
    if m:
        pq.deadline_days = int(m.group(1)) * {"day": 1, "week": 7, "month": 30}[m.group(2)]
    elif "diwali" in low or "festive" in low or "big billion" in low or "great indian" in low:
        pq.deadline_days = 60
    return pq


_MONEY = re.compile(r"₹\s*([\d,]+(?:\.\d+)?)")


def _numbers_in(obj) -> set[float]:
    found: set[float] = set()

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, (int, float)) and not isinstance(o, bool):
            found.add(round(float(o), 2))
        elif isinstance(o, str):
            for m in re.findall(r"\d[\d,]*(?:\.\d+)?", o):
                try:
                    found.add(round(float(m.replace(",", "")), 2))
                except ValueError:
                    pass
    walk(obj)
    return found


def check_numbers(answer: str, evidence: dict) -> list[str]:
    """Return ₹ amounts in the answer that do not appear (±₹1) in the evidence or as simple differences."""
    known = _numbers_in(evidence)
    bad = []
    for m in _MONEY.finditer(answer):
        v = float(m.group(1).replace(",", ""))
        if any(abs(v - k) <= 1 for k in known):
            continue
        if any(abs(v - abs(a - b)) <= 1 for a in known for b in known if a < 1e7 and b < 1e7):
            continue
        bad.append(m.group(0))
    return bad


def template_explanation(rec: Recommendation) -> str:
    lines = [f"**Recommendation: {rec.action.replace('_', ' ')}** (confidence: {rec.confidence.lower()})", ""]
    if rec.current_price is not None:
        lines.append(f"Current recorded price: ₹{rec.current_price:,.0f} (as of {rec.current_price_as_of:%d %b %Y}).")
    lines += [f"- {r}" for r in rec.reasoning]
    if rec.risks:
        lines += ["", "Risks:"] + [f"- {r}" for r in rec.risks]
    lines += ["", f"Suggested action: {rec.suggested_action}"]
    if rec.limitations:
        lines += ["", "Limitations: " + " ".join(rec.limitations[:3])]
    return "\n".join(lines)


async def chat(repo: Repository, message: str, *, product_id: str | None = None, history: list[dict] | None = None,
               preferences: dict | None = None, provider: LLMProvider | None = None, vendor_deps=None,
               user_id=None) -> dict:
    products = repo.list_products()
    pq = parse_question(message, products, product_id)
    if not pq.product_id:
        return {"reply": "Which product do you mean? Tracked products: " + ", ".join(p["name"] for p in products),
                "needs_product": True, "products": [{"id": p["id"], "name": p["name"]} for p in products]}
    prefs = preferences or {}
    req = RecommendationRequest(
        product_id=pq.product_id,
        target_price=pq.target_price or prefs.get("target_price"),
        max_budget=prefs.get("max_budget"),
        deadline_days=pq.deadline_days if pq.deadline_days is not None else prefs.get("deadline_days"),
        max_monthly_emi=pq.max_monthly or prefs.get("max_monthly_emi"),
        preferred_tenure_months=prefs.get("preferred_tenure_months"),
        objective=prefs.get("objective") or ("lowest_monthly_payment" if pq.max_monthly else "lowest_total_cost"),
        include_vendor_analysis=pq.needs_vendor, include_reviews=pq.needs_reviews,
        refresh_vendor_data=pq.refresh and (pq.needs_vendor or pq.needs_reviews),
        user_banks=prefs.get("user_banks") or [])
    rec = await recommend(repo, req, user_id=user_id, vendor_deps=vendor_deps)
    evidence = rec.model_dump(mode="json")

    provider = provider or get_provider()
    guard: list[str] = []
    llm_used = False
    try:
        reply = await provider.generate(SYSTEM_PROMPT, build_user_prompt(message, evidence, history))
        llm_used = True
        guard = check_numbers(reply, evidence)
        if guard:
            reply = template_explanation(rec) + (
                "\n\n_(The AI explanation was replaced because it mentioned amounts not found in the verified data.)_")
            llm_used = False
    except LLMError as e:
        reply = template_explanation(rec) + f"\n\n_(AI explanation unavailable: {e}.)_"
    return {"reply": reply, "recommendation": evidence, "intent": {
                "product_id": pq.product_id, "vendor_analysis": pq.needs_vendor, "reviews": pq.needs_reviews,
                "refresh_requested": req.refresh_vendor_data, "target_price": req.target_price,
                "max_monthly_emi": req.max_monthly_emi, "deadline_days": req.deadline_days},
            "vendor_data_accessed": rec.vendor_data_accessed, "llm_used": llm_used,
            "llm_provider": getattr(provider, "name", "unknown"), "guard_rejected_amounts": guard}
