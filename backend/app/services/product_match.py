"""Does a page found by web search sell THIS product? Deterministic checks, no LLM.

Levels (used by services/discovery_service.py):
  strong  - a registered product code (ASIN, GTIN, ...) is on the page, or brand + pack size + most name words
            all match and the page's price is plausible -> added automatically
  partial - nothing contradicts it but the proof is incomplete (no size shown, fewer name words, no price)
            -> waits for an admin
  none    - a different size, another brand, an implausible price, or no product on the page -> rejected
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.extraction.base import ExtractionResult

_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|gms?|grams?|g|ml|ltrs?|litres?|liters?|l)\b", re.I)
_UNIT = {"kg": ("g", 1000), "g": ("g", 1), "gm": ("g", 1), "gms": ("g", 1), "gram": ("g", 1), "grams": ("g", 1),
         "ml": ("ml", 1), "l": ("ml", 1000), "ltr": ("ml", 1000), "ltrs": ("ml", 1000), "litre": ("ml", 1000),
         "litres": ("ml", 1000), "liter": ("ml", 1000), "liters": ("ml", 1000)}
_STOP = {"with", "and", "for", "the", "of", "in", "an", "to", "by", "spf", "pack"}
_CODE_KEYS = re.compile(r"code|gtin|ean|upc|asin|sku|mpn|fsn", re.I)
NAME_WORDS_STRONG = 0.6
NAME_WORDS_PARTIAL = 0.4


@dataclass
class Match:
    level: str                                   # strong | partial | none
    evidence: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


def sizes(text: str | None) -> set[tuple[str, Decimal]]:
    """'50 g', '50gm', '0.05 kg' -> {('g', Decimal('50'))}"""
    out = set()
    for num, unit in _SIZE.findall(text or ""):
        base, mult = _UNIT[unit.lower()]
        out.add((base, (Decimal(num) * mult).normalize()))
    return out


def _words(text: str | None) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) > 1 and w not in _STOP and not w.isdigit() and not _SIZE.fullmatch(w)}


def product_codes(product: dict) -> set[str]:
    attrs = product.get("expected_attributes") or {}
    return {str(v).strip().upper() for k, v in attrs.items() if _CODE_KEYS.search(k) and v and len(str(v).strip()) >= 6}


def match_product(product: dict, result: ExtractionResult, *, price_range: tuple[Decimal, Decimal] | None = None,
                  extra_text: str = "", image_text: str | None = None) -> Match:
    """product: products row. result: a vendor adapter's extraction of the candidate page.
    price_range: (lowest plausible, highest plausible) from this product's own price history.
    extra_text: the search result title, used only as a fallback when the page shows no product name.
    image_text: text read from the product images (image_text.read_text); used for the pack size when the
    page's text does not state one."""
    page_name = result.product.get("name") or ""
    page_brand = result.product.get("brand") or ""
    page_code = str(result.product.get("product_code") or "").upper()
    price = result.vendor.get("price")
    ev: dict = {"page_name": page_name or None, "page_brand": page_brand or None, "page_code": page_code or None,
                "price": str(price) if price is not None else None}
    m = Match("partial", ev)

    if result.blocked:
        return Match("none", ev, ["page blocked automated access"])
    if not page_name and not page_code:
        return Match("none", ev, ["no product found on the page (no structured product data)"])

    codes = product_codes(product)
    haystack = f"{page_code} {page_name} {result.url}".upper()
    hit = sorted(c for c in codes if c in haystack)
    ev["code_match"] = hit or None

    name_for_words = page_name or extra_text
    want_sizes = sizes(product.get("name")) | sizes(str((product.get("expected_attributes") or {}).get("size") or ""))
    got_sizes = sizes(name_for_words)
    ev["sizes_expected"] = sorted(f"{v:f} {u}" for u, v in want_sizes) or None
    ev["sizes_on_page"] = sorted(f"{v:f} {u}" for u, v in got_sizes) or None
    where = "on the page"
    if want_sizes and not got_sizes and image_text:
        got_sizes = sizes(image_text)
        ev["sizes_on_image"] = sorted(f"{v:f} {u}" for u, v in got_sizes) or None
        where = "on the product image"
    if want_sizes and got_sizes and not (want_sizes & got_sizes):
        shown = ", ".join(sorted(f"{v:f} {u}" for u, v in got_sizes))
        m.level, m.reasons = "none", [f"different pack size ({shown} {where})"]
        return m

    brand = (product.get("brand") or "").lower()
    brand_ok = bool(brand) and (brand in page_brand.lower() or brand in name_for_words.lower())
    ev["brand_match"] = brand_ok
    if brand and page_brand and not brand_ok:
        m.level, m.reasons = "none", [f"different brand ({page_brand})"]
        return m

    core_name = re.sub(r"\([^)]*\)", " ", product.get("name") or "")     # "(Vitamin B5, Niacinamide)" is detail
    want_words = _words(core_name) - _words(product.get("brand"))
    got_words = _words(name_for_words)
    share = len(want_words & got_words) / len(want_words) if want_words else 0.0
    ev["name_word_share"] = round(share, 2)

    price_ok = None
    if price is not None and price_range:
        lo, hi = price_range
        price_ok = lo <= price <= hi
        ev["price_range"] = [str(lo), str(hi)]
        if not price_ok:
            m.level, m.reasons = "none", [f"price ₹{price} outside the plausible range ₹{lo}-₹{hi}"]
            return m
    ev["price_plausible"] = price_ok

    if hit and price_ok is not False:
        m.level, m.reasons = "strong", [f"product code {hit[0]} on the page"]
    elif brand_ok and want_sizes & got_sizes and share >= NAME_WORDS_STRONG and price is not None and price_ok is not False:
        m.level, m.reasons = "strong", ["brand, pack size and product name match, price plausible"
                                        + (" (pack size read from the product image)" if where != "on the page" else "")]
    elif share < NAME_WORDS_PARTIAL and not hit:
        m.level, m.reasons = "none", [f"product name does not match (only {share:.0%} of its words)"]
    else:
        missing = [label for label, ok in (("brand", brand_ok), ("pack size", bool(want_sizes & got_sizes)),
                                           ("enough name words", share >= NAME_WORDS_STRONG), ("price", price is not None))
                   if not ok]
        m.reasons = [f"unconfirmed: {', '.join(missing)}"]
    return m
