"""Site-specific vendor adapters, written from the real pages inspected on 2026-10-16:

* amazon_in  - https://www.amazon.in/dp/<ASIN>        (static HTML; stable element ids / data-hook attributes)
* myntra     - https://www.myntra.com/.../<id>/buy     (embedded JSON: window.__myx = {...pdpData...})
* nykaa      - https://www.nykaa.com|nykaaman.com/.../p/<id>  (embedded JSON: window.__PRELOADED_STATE__)

Each is used only on demand (vendor_service). Plain HTTP is enough for all three - no JavaScript rendering.
A bot-check page is reported as blocked and never bypassed. Values the page does not show stay None.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal

from bs4 import BeautifulSoup

from app.extraction.adapters.common import looks_blocked
from app.extraction.base import Adapter, ExtractionContext, ExtractionResult
from app.extraction.validation import InvalidValue, parse_price
from app.extraction.vendor_offers import parse_bank_offer, parse_emi_text

VENDOR_FIELDS = ("vendor.price", "vendor.mrp", "vendor.availability", "vendor.seller_name", "vendor.emi_status",
                 "review_aggregate")


def _price(v) -> Decimal | None:
    try:
        return parse_price(v)
    except InvalidValue:
        return None


def _txt(el) -> str | None:
    if el is None:
        return None
    t = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
    return t or None


def _embedded_json(html: str, marker: str) -> dict | None:
    i = html.find(marker)
    if i < 0:
        return None
    start = html.find("{", i)
    end = html.find("</script>", start)
    try:
        return json.loads(html[start:end].strip().rstrip(";"))
    except (json.JSONDecodeError, ValueError):
        return None


class _MarketplaceAdapter(Adapter):
    kind = "vendor"
    expected_fields = VENDOR_FIELDS

    async def _page(self, url: str, ctx: ExtractionContext, result: ExtractionResult) -> str | None:
        page = await ctx.fetcher.fetch(url)
        result.http_status = page.status
        result.requires_js = False
        if looks_blocked(page.status, page.text):
            result.blocked = True
            result.warnings.append("Vendor returned a bot-check / access-denied page; not bypassed.")
            return None
        return page.text


# ----------------------------------------------------------------------------- Amazon.in
_A11Y_NOISE = re.compile(r"Brief content visible, double tap to read full content\.|Full content visible, double tap to read "
                         r"brief content\.|Read more|Read less|Helpful|Report|Sending feedback\.\.\.|Thank you for your feedback\.")


class AmazonInAdapter(_MarketplaceAdapter):
    name = "amazon_in"
    version = "1.0.0"
    hosts = ("amazon.in",)

    async def extract(self, url: str, ctx: ExtractionContext) -> ExtractionResult:
        r = self.new_result(url)
        html = await self._page(url, ctx, r)
        if html is None:
            return r
        s = BeautifulSoup(html, "lxml")
        for t in s(["script", "style", "noscript"]):
            t.decompose()
        core = s.select_one("#corePriceDisplay_desktop_feature_div") or s.select_one("#apex_offerDisplay_desktop")
        price = None
        if core:
            whole = core.select_one(".priceToPay .a-price-whole")
            if whole:
                frac = core.select_one(".priceToPay .a-price-fraction")
                price = _price(whole.get_text().strip().rstrip(".") + ("." + frac.get_text().strip() if frac else ""))
            if price is None:
                first = core.select_one(".a-price .a-offscreen")
                price = _price(first.get_text()) if first else None
        mrp_el = s.select_one("#corePriceDisplay_desktop_feature_div .basisPrice .a-offscreen")
        avail = _txt(s.select_one("#availability"))
        seller = _txt(s.select_one("#sellerProfileTriggerId"))
        if not seller:
            m = re.search(r"Sold by\s+(.{2,80}?)(?:\s+Sold by|$)", _txt(s.select_one("#merchantInfoFeature_feature_div")) or "")
            seller = m.group(1).strip() if m else None
        asin = re.search(r"/dp/([A-Z0-9]{10})", url)
        title = _txt(s.select_one("#productTitle"))
        r.product.update({"name": title, "product_code": asin.group(1) if asin else None})

        # EMI: Amazon renders EMI in #inemi_feature_div / #installmentPaymentPrice_feature_div when it offers it
        emi_text = " ".join(filter(None, (_txt(s.select_one(sel)) for sel in
                                          ("#inemi_feature_div", "#installmentPaymentPrice_feature_div", "#emi_feature_div"))))
        plans, facts = parse_emi_text(emi_text) if emi_text else ([], {})
        r.emi_plans = plans
        emi_status = "available" if plans else "not_shown"

        offers = []
        box = s.select_one("#vsxoffers_feature_div")
        if box:
            for card in box.select(".a-carousel-card"):
                head = _txt(card.select_one(".offers-items-title"))
                body = _txt(card.select_one(".offers-items-content"))
                if body and body not in {o["title"] for o in offers}:      # the carousel is rendered twice
                    offers.append({**parse_bank_offer(body, None, head), "category": head})
        r.vendor.update({
            "price": price, "mrp": _price(mrp_el.get_text()) if mrp_el else None, "currency": "INR" if price else None,
            "availability": "InStock" if avail and "in stock" in avail.lower() else ("OutOfStock" if avail and "unavailable" in avail.lower() else avail),
            "seller_name": seller, "delivery_text": (_txt(s.select_one("#deliveryBlockMessage")) or "")[:160] or None,
            "emi_status": emi_status, "bank_offers": offers,
            "emi_page_facts": {k: (str(v) if not isinstance(v, (bool, list)) else v) for k, v in facts.items()},
        })
        if emi_status == "not_shown":
            r.warnings.append("No EMI option is shown on this Amazon product page.")
        if any(o["min_spend"] is None for o in offers):
            r.warnings.append("Some bank-offer conditions (minimum spend etc.) are only in a pop-up and were not read.")

        rating = s.select_one("#acrPopover")
        m = re.search(r"([\d.]+) out of 5", (rating.get("title") if rating else "") or "")
        cnt = s.select_one("#acrCustomerReviewText")
        cm = re.search(r"([\d,]+)", (cnt.get("aria-label") or cnt.get_text()) if cnt else "")
        if m:
            r.review_aggregates.append({"kind": "product_aggregate", "rating": m.group(1), "best": 5,
                                        "count": cm.group(1).replace(",", "") if cm else None})
        for rv in s.select('[data-hook="review"]')[:20]:
            text = _txt(rv.select_one('[data-hook="reviewText"]') or rv.select_one('[data-hook="review-body"]')) or ""
            text = re.sub(r"\s+", " ", _A11Y_NOISE.sub(" ", text)).strip()
            star = re.search(r"([\d.]+) out of 5", _txt(rv.select_one('[data-hook="review-star-rating"]')) or "")
            dm = re.search(r"on (\d{1,2} \w+ \d{4})", _txt(rv.select_one('[data-hook="review-date"]')) or "")
            date = None
            if dm:
                try:
                    date = datetime.strptime(dm.group(1), "%d %B %Y").date().isoformat()
                except ValueError:
                    date = None
            if text or star:
                r.reviews.append({"kind": "product_review", "text": text or None, "rating": star.group(1) if star else None,
                                  "best": 5, "date": date, "verified": rv.select_one('[data-hook="avp-badge"]') is not None,
                                  "title": _txt(rv.select_one('[data-hook="reviewTitle"]'))})
        if price is None:
            r.warnings.append("Price element not found; Amazon may have changed the page layout.")
        return r


# ----------------------------------------------------------------------------- Myntra
class MyntraAdapter(_MarketplaceAdapter):
    name = "myntra"
    version = "1.0.0"
    hosts = ("myntra.com",)

    async def extract(self, url: str, ctx: ExtractionContext) -> ExtractionResult:
        r = self.new_result(url)
        html = await self._page(url, ctx, r)
        if html is None:
            return r
        data = _embedded_json(html, "window.__myx")
        p = (data or {}).get("pdpData")
        if not p:
            r.warnings.append("Embedded product data (window.__myx.pdpData) not found; page layout may have changed.")
            return r
        price_info = p.get("price") or {}
        sel = p.get("selectedSeller") or {}
        sellers = {s.get("sellerPartnerId"): s for s in p.get("sellers") or []}
        seller = sellers.get(sel.get("sellerPartnerId"), {})
        flags = p.get("flags") or {}
        svc = p.get("serviceability") or {}
        disc = sel.get("discount") or ((p.get("discounts") or [None])[0]) or {}
        offer_end = None
        if str(disc.get("timerEnd") or "").isdigit() and int(disc["timerEnd"]) > 0:
            offer_end = datetime.fromtimestamp(int(disc["timerEnd"]), tz=timezone.utc).isoformat()
        stock = sum((sd.get("availableCount") or 0) for sz in p.get("sizes") or [] for sd in sz.get("sizeSellerData") or [])
        r.product.update({"name": p.get("name"), "brand": (p.get("brand") or {}).get("name"), "product_code": str(p.get("id"))})
        offers = [{**parse_bank_offer(o.get("title") or "", o.get("description")), "category": o.get("type")}
                  for o in p.get("offers") or [] if o.get("title")]
        returns = " ".join(svc.get("descriptors") or []) or None
        r.vendor.update({
            "price": _price(sel.get("discountedPrice") or price_info.get("discounted")), "mrp": _price(price_info.get("mrp") or p.get("mrp")),
            "currency": "INR", "availability": "OutOfStock" if flags.get("outOfStock") else "InStock",
            "seller_name": seller.get("displayName") or sel.get("sellerName"),
            "return_policy": returns, "stock_units": stock or None,
            "discount_label": disc.get("label"), "discount_ends_at": offer_end,
            "emi_status": "not_offered" if flags.get("emiEnabled") is False else ("available" if flags.get("emiEnabled") else "unknown"),
            "bank_offers": offers, "cod_available": flags.get("codEnabled"),
        })
        ratings = p.get("ratings") or {}
        if ratings.get("averageRating") is not None:
            r.review_aggregates.append({"kind": "product_aggregate", "rating": round(float(ratings["averageRating"]), 2), "best": 5,
                                        "count": ratings.get("totalCount"),
                                        "distribution": {str(x["rating"]): x["count"] for x in ratings.get("ratingInfo") or []}})
        info = ratings.get("reviewInfo") or {}
        seen = set()
        for rv in (info.get("topReviews") or []) + (info.get("topImageReviews") or []):
            if rv.get("reviewId") in seen or not rv.get("reviewText"):
                continue
            seen.add(rv.get("reviewId"))
            ts = rv.get("timestamp")
            date = None
            if str(ts or "").isdigit():
                v = int(ts)
                date = datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=timezone.utc).date().isoformat()
            r.reviews.append({"kind": "product_review", "text": rv["reviewText"], "rating": rv.get("userRating"),
                              "best": 5, "date": date, "verified": None})
        if r.reviews:
            r.warnings.append("Myntra's 'top reviews' are hand-picked by Myntra and may cover related variants; not a random sample.")
        if r.vendor["emi_status"] == "not_offered":
            r.warnings.append("Myntra marks EMI as not available for this item (emiEnabled = false).")
        return r


# ----------------------------------------------------------------------------- Nykaa / Nykaa Man
class NykaaAdapter(_MarketplaceAdapter):
    name = "nykaa"
    version = "1.0.0"
    hosts = ("nykaa.com", "nykaaman.com", "nykaafashion.com")

    async def extract(self, url: str, ctx: ExtractionContext) -> ExtractionResult:
        r = self.new_result(url)
        html = await self._page(url, ctx, r)
        if html is None:
            return r
        data = _embedded_json(html, "window.__PRELOADED_STATE__")
        p = ((data or {}).get("productPage") or {}).get("product")
        if not p:
            r.warnings.append("Embedded product data (__PRELOADED_STATE__.productPage.product) not found; layout may have changed.")
            return r
        r.product.update({"name": p.get("name"), "brand": p.get("brandName"), "product_code": str(p.get("sku") or p.get("id"))})
        offers = [{**parse_bank_offer(o.get("title") or o.get("offerText") or "", o.get("description")), "category": "offer"}
                  for o in p.get("offers") or [] if isinstance(o, dict) and (o.get("title") or o.get("offerText"))]
        r.vendor.update({
            "price": _price(p.get("offerPrice")), "mrp": _price(p.get("mrp")), "currency": "INR",
            "availability": "InStock" if p.get("inStock") else "OutOfStock", "seller_name": p.get("sellerName"),
            "return_policy": p.get("returnMessage") or p.get("messageOnReturn"), "stock_units": p.get("quantity"),
            "product_expiry": p.get("expiry"), "emi_status": "not_shown", "bank_offers": offers,
        })
        r.warnings.append("Nykaa does not show EMI terms on the product page.")
        if p.get("rating") is not None:
            r.review_aggregates.append({
                "kind": "product_aggregate", "rating": p["rating"], "best": 5, "count": p.get("ratingCount"),
                "distribution": {str(x.get("id")): int(x.get("count")) for x in p.get("reviewSplitUp") or [] if x.get("count")}})
        for rv in p.get("latestReviews") or []:
            text = " ".join(filter(None, [rv.get("title"), rv.get("description")])) or None
            date = (rv.get("createdOn") or "")[:10] or None
            if text:
                r.reviews.append({"kind": "product_review", "text": text, "rating": rv.get("rating"), "best": 5,
                                  "date": date, "verified": rv.get("isBuyer")})
        if p.get("latestReviews") and not r.reviews:
            r.warnings.append("Nykaa's latest reviews on the page contain ratings but no text.")
        return r
