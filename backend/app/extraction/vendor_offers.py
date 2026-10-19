"""Heuristic EMI-offer parsing from visible vendor-page text.

Vendor pages describe EMI in free text ("No Cost EMI", "EMI from ₹1,234/month",
"₹2,499 x 6 months", "12 months @ 15% p.a."). These patterns pull out only what is literally
stated. Every plan is tagged verification_status='parsed_heuristically' and keeps the raw
snippet so a person can check it. Unknown terms stay None (never 0).

Site-specific adapters should replace this once a real vendor page has been inspected.
"""
from __future__ import annotations

import re
from decimal import Decimal

from app.extraction.validation import InvalidValue, parse_price

_RUPEE = r"(?:₹|rs\.?|inr)\s*"
_NUM = r"([\d,]+(?:\.\d{1,2})?)"
_BANKS = ["HDFC", "ICICI", "SBI", "Axis", "Kotak", "Bajaj Finserv", "IDFC FIRST", "RBL", "AU Small Finance",
          "Yes Bank", "IndusInd", "OneCard", "Federal", "HSBC", "Standard Chartered", "Citi", "American Express",
          "Bank of Baroda", "Canara", "Flipkart Axis", "Amazon Pay ICICI", "BOBCARD", "DBS"]

PATTERNS = {
    "starting_from": re.compile(rf"emi\s+(?:starts?\s+(?:at|from)|starting\s+(?:at|from)|from|options?\s+from)\s*{_RUPEE}{_NUM}", re.I),
    "amount_x_tenure": re.compile(rf"{_RUPEE}{_NUM}\s*(?:/\s*(?:month|mo|m)\s*)?(?:x|×|for)\s*(\d{{1,2}})\s*(?:months?|m)\b", re.I),
    "tenure_at_rate": re.compile(r"(\d{1,2})\s*months?\s*(?:@|at)\s*(\d{1,2}(?:\.\d{1,2})?)\s*%\s*(p\.?\s*a\.?|per\s+annum|annual)?", re.I),
    "no_cost": re.compile(r"no[\s-]*cost\s+emi", re.I),
    "processing_fee": re.compile(rf"processing\s+fee[^₹\d]{{0,20}}{_RUPEE}{_NUM}", re.I),
}


def _snippet(text: str, start: int, end: int, pad: int = 80) -> str:
    return re.sub(r"\s+", " ", text[max(0, start - pad): end + pad]).strip()[:300]


def _bank_near(snippet: str) -> str | None:
    for b in _BANKS:
        if re.search(rf"\b{re.escape(b)}\b", snippet, re.I):
            return b
    return None


def _banks_in(text: str) -> list[str]:
    """All banks named in the text; a name contained in a longer match (Axis in Flipkart Axis) is dropped."""
    found = [b for b in _BANKS if re.search(rf"\b{re.escape(b)}\b", text, re.I)]
    return [b for b in found if not any(b != o and b.lower() in o.lower() for o in found)]


def _card_type(snippet: str) -> str | None:
    s = snippet.lower()
    if "debit card" in s:
        return "debit_card"
    if "credit card" in s:
        return "credit_card"
    if "cardless" in s or "pay later" in s:
        return "cardless"
    return None


def parse_emi_text(text: str) -> tuple[list[dict], dict]:
    """Returns (plans, page_level_facts)."""
    plans: list[dict] = []
    facts: dict = {"no_cost_emi_mentioned": bool(PATTERNS["no_cost"].search(text))}

    for m in PATTERNS["amount_x_tenure"].finditer(text):
        try:
            emi = parse_price(m.group(1))
        except InvalidValue:
            continue
        snip = _snippet(text, m.start(), m.end())
        plans.append({"monthly_emi": emi, "tenure_months": int(m.group(2)), "interest_rate": None,
                      "interest_rate_type": "unknown", "no_cost_emi_claim": bool(PATTERNS["no_cost"].search(snip)),
                      "bank_name": _bank_near(snip), "card_type": _card_type(snip), "raw_text": snip,
                      "pattern": "amount_x_tenure"})

    for m in PATTERNS["tenure_at_rate"].finditer(text):
        snip = _snippet(text, m.start(), m.end())
        rate = Decimal(m.group(2))
        if rate > 60:
            continue
        plans.append({"monthly_emi": None, "tenure_months": int(m.group(1)), "interest_rate": rate,
                      "interest_rate_type": "annual_reducing" if m.group(3) else "unknown",
                      "no_cost_emi_claim": bool(PATTERNS["no_cost"].search(snip)),
                      "bank_name": _bank_near(snip), "card_type": _card_type(snip), "raw_text": snip,
                      "pattern": "tenure_at_rate",
                      "assumption": None if m.group(3) else "Rate basis (annual/monthly, flat/reducing) not stated"})

    for m in PATTERNS["starting_from"].finditer(text):
        try:
            emi = parse_price(m.group(1))
        except InvalidValue:
            continue
        if any(p["monthly_emi"] == emi for p in plans):
            continue
        snip = _snippet(text, m.start(), m.end())
        plans.append({"monthly_emi": emi, "tenure_months": None, "interest_rate": None,
                      "interest_rate_type": "unknown", "no_cost_emi_claim": bool(PATTERNS["no_cost"].search(snip)),
                      "bank_name": _bank_near(snip), "card_type": _card_type(snip), "raw_text": snip,
                      "pattern": "starting_from",
                      "assumption": "Advertised minimum EMI; tenure and rate not stated"})

    fees = []
    for m in PATTERNS["processing_fee"].finditer(text):
        try:
            v = parse_price(m.group(1))
            if v:
                fees.append(v)
        except InvalidValue:
            pass
    if len(set(fees)) == 1:
        facts["processing_fee"] = fees[0]
    elif fees:
        facts["processing_fee_ambiguous"] = [str(f) for f in sorted(set(fees))]
    return plans[:30], facts


# ----------------------------------------------------------------------------- bank / cashback offers
_PCT = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s*%")
_MIN_SPEND = re.compile(rf"(?:min(?:imum)?\.?\s*(?:spend|purchase|order|transaction)(?:\s*value)?(?:\s*of)?)\s*{_RUPEE}{_NUM}", re.I)
_MAX = re.compile(rf"(?:max(?:imum)?\.?\s*(?:discount|cashback)?(?:\s*of)?|up\s*to|upto)\s*{_RUPEE}{_NUM}", re.I)
_FLAT = re.compile(rf"(?:flat|get)\s*{_RUPEE}{_NUM}\s*(?:off|discount|cashback|instant)", re.I)


def parse_bank_offer(title: str, description: str | None = None, kind_hint: str | None = None) -> dict:
    """Structure one bank / cashback / partner offer from its visible wording. Unknown terms stay None."""
    text = " ".join(t for t in (title, description) if t)
    low = text.lower()
    def money(rx):
        m = rx.search(text)
        if not m:
            return None
        try:
            return parse_price(m.group(1))
        except InvalidValue:
            return None
    pct = _PCT.search(text)
    kind = ("cashback" if "cashback" in low else "instant_discount" if ("discount" in low or "off" in low) else "other")
    if kind_hint and "partner" in kind_hint.lower():
        kind = "partner_offer"
    return {
        "title": title.strip()[:300], "description": (description or "").strip()[:300] or None,
        "kind": kind, "bank": ", ".join(_banks_in(text)) or None,
        "card_type": "credit_card" if "credit card" in low else "debit_card" if "debit card" in low else None,
        "percent": Decimal(pct.group(1)) if pct else None,
        "flat_amount": money(_FLAT),
        "max_discount": money(_MAX),
        "min_spend": money(_MIN_SPEND),
        "applies_to_emi": bool(re.search(r"\bemi\b", low)),
        "terms_complete": bool(_MIN_SPEND.search(text)) and bool(pct or _FLAT.search(text)),
    }


def offer_value(offer: dict, price: Decimal) -> tuple[Decimal | None, str]:
    """Estimated saving on `price` and an explanation. None when it cannot be computed from the stated terms."""
    if offer.get("kind") == "partner_offer":
        return None, "Multi-buy / partner offer; not applied to a single-unit price."
    if offer.get("min_spend") is not None and price < offer["min_spend"]:
        return Decimal("0"), f"Not applicable: minimum spend ₹{offer['min_spend']} is above the price."
    if offer.get("percent") is not None:
        v = price * offer["percent"] / 100
        if offer.get("max_discount") is not None:
            v = min(v, offer["max_discount"])
        note = "" if offer.get("min_spend") is not None else " Minimum spend not stated on the page."
        return v.quantize(Decimal("0.01")), f"{offer['percent']}% of price{' capped at ₹' + str(offer['max_discount']) if offer.get('max_discount') else ''}.{note}"
    if offer.get("flat_amount") is not None:
        return min(offer["flat_amount"], price), "Flat amount as stated."
    if offer.get("max_discount") is not None:
        return None, f"Only an 'up to ₹{offer['max_discount']}' amount is shown; the actual saving depends on terms not on the page."
    return None, "Terms not stated on the product page."
