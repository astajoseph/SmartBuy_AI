"""Delivery charges, free-delivery thresholds and membership benefits read from visible page text.

Only what the text literally states is returned; anything else is left out (never 0 or a guess):

  delivery_fee              charge for one order below any free-delivery threshold
  handling_fee              separate handling / platform charge (quick-commerce style)
  free_delivery             True if delivery is stated to be free for everyone
  free_delivery_min_order   order value from which delivery is free
  membership_program        e.g. "Amazon Prime", "Flipkart Plus", "Myntra Insider"
  membership_free_delivery  True if free delivery is stated for members only
  member_price              price shown for members
  delivery_text             the first delivery sentence, for display
"""
from __future__ import annotations

import re
from decimal import Decimal

from app.extraction.validation import InvalidValue, parse_price

_RUPEE = r"(?:₹|rs\.?|inr)\s*"
_NUM = r"([\d,]+(?:\.\d{1,2})?)"
_CAP = Decimal("100000")

PROGRAMS = [
    ("Amazon Prime", r"amazon\s+prime|\bprime\b"),
    ("Flipkart Plus", r"flipkart\s+(?:plus|vip)"),
    ("Myntra Insider", r"myntra\s+insider|\binsider\b"),
    ("Nykaa Prive", r"nykaa\s+priv[eé]|\bpriv[eé]\b"),
    ("Tata Neu", r"neupass|tata\s+neu"),
    ("Swiggy One", r"swiggy\s+one"),
    ("Zepto Pass", r"zepto\s+pass"),
    ("Apollo Circle", r"apollo\s+circle|\bcircle\b"),
]
_PROGRAM_ANY = "|".join(rx for _, rx in PROGRAMS) + r"|members?(?:ship)?|\bjoin\s+\w+"

_FREE = re.compile(r"\bfree\s+(?:home\s+|standard\s+|express\s+)?(?:delivery|shipping)\b", re.I)
_FIRST_ORDER = re.compile(r"first\s+(?:order|purchase)", re.I)
_THRESHOLD_AFTER = re.compile(
    rf"(?:on|for|with)?\s*(?:orders?|purchases?|carts?|spends?)?\s*(?:of\s*)?(?:over|above|worth|from|more\s+than|at\s+least|min(?:imum)?\.?)\s*{_RUPEE}{_NUM}",
    re.I)
_THRESHOLD_BEFORE = re.compile(
    rf"(?:orders?|purchases?)\s+(?:over|above|of|worth|from)\s*{_RUPEE}{_NUM}\s*(?:or\s+more\s+)?(?:get|qualify|ship|are|is|have|receive)?", re.I)
_FEE_AFTER = re.compile(
    rf"(?:delivery|shipping)\s+(?:charges?|fees?|costs?)\s*(?:of|is|are|:|-|–|=)?\s*(?:will\s+be\s+)?{_RUPEE}{_NUM}", re.I)
_FEE_BEFORE = re.compile(rf"{_RUPEE}{_NUM}\s*(?:as\s+)?(?:delivery|shipping)\s+(?:charges?|fees?)", re.I)
_FEE_PLUS = re.compile(rf"\+\s*{_RUPEE}{_NUM}\s*(?:for\s+)?(?:delivery|shipping)", re.I)
_BELOW = re.compile(rf"(?:orders?|purchases?)\s+(?:below|under|less\s+than)\s*{_RUPEE}{_NUM}", re.I)
_FEE_SAVED = re.compile(rf"\bfree\s+(?:delivery|shipping)\s*\(\s*{_RUPEE}{_NUM}\s*\)", re.I)   # "Free delivery (₹99) with ..."
_HANDLING = re.compile(rf"(?:handling|platform|packaging)\s+(?:charges?|fees?)\s*(?:of|is|:|-|–)?\s*{_RUPEE}{_NUM}", re.I)
_DELIVERY_SENTENCE = re.compile(r"(?:(?:free|fast|standard|express)\s+)?(?:delivery|shipping|delivered)\b[^.|\n]{0,100}", re.I)


def _money(raw: str) -> Decimal | None:
    try:
        v = parse_price(raw)
    except InvalidValue:
        return None
    return v if v is not None and Decimal("0") <= v <= _CAP else None


def _program_near(snippet: str) -> str | None:
    for name, rx in PROGRAMS:
        if re.search(rx, snippet, re.I):
            return name
    return None


def parse_delivery(text: str) -> dict:
    """Structured delivery / membership facts from page text. Keys are present only when the text states them."""
    text = re.sub(r"\s+", " ", text or "")
    out: dict = {}

    m = next(iter(_DELIVERY_SENTENCE.finditer(text)), None)
    if m:
        out["delivery_text"] = m.group(0).strip()[:160]

    for rx in (_FEE_AFTER, _FEE_BEFORE, _FEE_PLUS, _FEE_SAVED):
        fees = [v for v in (_money(m.group(1)) for m in rx.finditer(text)) if v is not None]
        if fees:
            out["delivery_fee"] = max(fees) if len(set(fees)) > 1 else fees[0]    # several different fees: the highest tier
            break
    handling = [v for v in (_money(m.group(1)) for m in _HANDLING.finditer(text)) if v is not None]
    if handling:
        out["handling_fee"] = max(handling)

    general_free, member_free, threshold, program = False, False, None, None
    for m in _FREE.finditer(text):
        before, after = text[max(0, m.start() - 70): m.start()], text[m.end(): m.end() + 90]
        around = before + " " + m.group(0) + " " + after
        if _FIRST_ORDER.search(around):
            continue                                              # a one-off promotion, not the delivery policy
        member_ctx = re.search(_PROGRAM_ANY, around, re.I)
        thr = _THRESHOLD_AFTER.search(after) or _THRESHOLD_BEFORE.search(before + " " + m.group(0))
        value = _money(thr.group(1)) if thr else None
        if member_ctx:
            member_free = True
            program = program or _program_near(around)
        elif value:
            threshold = value if threshold is None else min(threshold, value)
        else:
            general_free = True
    if threshold is None and "delivery_fee" in out:
        below = _BELOW.search(text)                               # "charge Rs 40 on orders below Rs 499" = free from 499
        threshold = _money(below.group(1)) if below else None
    if threshold is not None:
        out["free_delivery_min_order"] = threshold
    elif general_free and "delivery_fee" not in out:              # "free" next to a fee with no stated condition: unclear
        out["free_delivery"] = True
    if member_free:
        out["membership_free_delivery"] = True

    pm = re.search(rf"(?:{_PROGRAM_ANY})\s+(?:price|deal|offer)\s*[:\-–]?\s*{_RUPEE}{_NUM}", text, re.I) \
        or re.search(rf"{_RUPEE}{_NUM}\s*(?:for|with|to)\s+(?:{_PROGRAM_ANY})", text, re.I)
    if pm:
        v = _money(pm.group(1))
        if v:
            out["member_price"] = v
            program = program or _program_near(pm.group(0))
            out["membership_text"] = pm.group(0)[:200]
    if program:
        out["membership_program"] = program
    elif member_free or "member_price" in out:
        out["membership_program"] = "membership"
    if member_free and "membership_text" not in out:
        mt = re.search(rf"[^.|]{{0,60}}(?:{_PROGRAM_ANY})[^.|]{{0,60}}", text, re.I)
        if mt:
            out["membership_text"] = mt.group(0).strip()[:200]
    return out
