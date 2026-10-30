"""Site-specific vendor adapters, written from the real pages inspected on 2026-10-16:

* amazon_in  - https://www.amazon.in/dp/<ASIN>        (static HTML; stable element ids / data-hook attributes)
* myntra     - https://www.myntra.com/.../<id>/buy     (embedded JSON: window.__myx = {...pdpData...})
* nykaa      - https://www.nykaa.com|nykaaman.com/.../p/<id>  (embedded JSON: window.__PRELOADED_STATE__)

Each is used on demand (vendor_service). Plain HTTP is enough for all three - no JavaScript rendering.
A bot-check page is reported as blocked and never bypassed. Values the page does not show stay None.

extract_sellers() lists every seller of the product on the marketplace, for seller discovery
(services/discovery_service.py):
* amazon_in - the "Other sellers on Amazon" panel (/dp/<ASIN>?aod=1), filled by JavaScript, so it is rendered
              in the headless browser and scrolled to load more offers (inspected 2026-10-24: 1 featured + 24 offers)
* myntra    - pdpData.sellers + sizes[].sizeSellerData (price and stock per seller) from the same embedded JSON
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlsplit
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from bs4 import BeautifulSoup

from app.extraction.adapters.common import looks_blocked
from app.extraction.delivery import parse_delivery
from app.extraction.base import Adapter, ExtractionContext, ExtractionResult
from app.extraction.validation import InvalidValue, parse_price
from app.security.url_validation import RobotsDisallowed
from app.extraction.vendor_offers import parse_bank_offer, parse_emi_text

VENDOR_FIELDS = ("vendor.price", "vendor.mrp", "vendor.availability", "vendor.seller_name", "vendor.emi_status",
                 "review_aggregate")


def _price(v) -> Decimal | None:
    try:
        return parse_price(v)
    except InvalidValue:
        return None


def _delivery_strings(obj, depth: int = 2) -> list[str]:
    """String values in embedded product JSON that talk about delivery / shipping (keys differ per site)."""
    out: list[str] = []
    if isinstance(obj, str):
        if re.search(r"deliver|shipping", obj, re.I) and len(obj) < 300:
            out.append(obj)
    elif depth > 0 and isinstance(obj, dict):
        for v in obj.values():
            out += _delivery_strings(v, depth - 1)
    elif depth > 0 and isinstance(obj, list):
        for v in obj[:20]:
            out += _delivery_strings(v, depth - 1)
    return out


def _txt(el) -> str | None:
    if el is None:
        return None
    t = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
    return t or None


def _int_or_none(v) -> int | None:
    return int(v) if str(v or "").isdigit() else None


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
        buybox = " ".join(filter(None, (_txt(s.select_one(sel)) for sel in
                                        ("#deliveryBlockMessage", "#mir-layout-DELIVERY_BLOCK", "#price-shipping-message",
                                         "#amazonGlobal_feature_div", "#buybox-see-all-buying-choices"))))
        r.vendor.update(parse_delivery(buybox))
        if emi_status == "not_shown":
            r.warnings.append("No EMI option is shown on this Amazon product page.")
        if any(o["min_spend"] is None for o in offers):
            r.warnings.append("Some bank-offer conditions (minimum spend etc.) are only in a pop-up and were not read.")

        rating = s.select_one("#acrPopover")
        m = re.search(r"([\d.]+) out of 5", (rating.get("title") if rating else "") or "")
        cnt = s.select_one("#acrCustomerReviewText")
        cm = re.search(r"([\d,]+)", (cnt.get("aria-label") or cnt.get_text()) if cnt else "")
        dist = {}
        for a in s.select("#histogramTable a[aria-label], #histogramTable li a[aria-label]"):
            hm = re.match(r"(\d+) percent of reviews have (\d) stars?", a.get("aria-label") or "")
            if hm:
                dist[hm.group(2)] = int(hm.group(1))
        if m:
            r.review_aggregates.append({"kind": "product_aggregate", "rating": m.group(1), "best": 5,
                                        "count": cm.group(1).replace(",", "") if cm else None,
                                        "distribution": dist or None, "distribution_unit": "percent"})
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
                helpful = re.search(r"([\d,]+|One) (?:person|people) found this helpful",
                                    _txt(rv.select_one('[data-hook="helpful-vote-statement"]')) or "")
                badges = (_txt(rv.select_one('[data-hook="review-badges"]')) or "") + " " + (_txt(rv) or "")[:400]
                r.reviews.append({"kind": "product_review", "text": text or None, "rating": star.group(1) if star else None,
                                  "best": 5, "date": date, "verified": rv.select_one('[data-hook="avp-badge"]') is not None,
                                  "title": _txt(rv.select_one('[data-hook="reviewTitle"]')),
                                  "id": rv.get("id"), "reviewer": _txt(rv.select_one(".a-profile-name")),
                                  "helpful": (1 if helpful.group(1) == "One" else int(helpful.group(1).replace(",", ""))) if helpful else 0,
                                  "media": rv.select_one('[data-hook="review-image-tile"], [data-hook="review-video"]') is not None,
                                  "variant": _txt(rv.select_one('[data-hook="format-strip"]')),
                                  "incentivized": "vine customer review" in badges.lower()})
        if price is None:
            r.warnings.append("Price element not found; Amazon may have changed the page layout.")
        return r


    supports_sellers = True

    async def extract_sellers(self, url: str, ctx: ExtractionContext) -> ExtractionResult:
        r = self.new_result(url)
        asin = re.search(r"/dp/([A-Z0-9]{10})", url)
        if not asin:
            r.warnings.append("No ASIN in the URL; cannot open the seller list.")
            return r
        aod_url = f"https://www.amazon.in/dp/{asin.group(1)}?aod=1"
        if ctx.browser is None:
            r.warnings.append("Amazon's seller list is loaded by JavaScript and no browser is available.")
            return r
        if not await ctx.fetcher.robots_allowed(httpx.URL(aod_url)):
            raise RobotsDisallowed(f"robots.txt disallows {aod_url}")
        ctx.browser.robots = ctx.fetcher.robots_for(aod_url)
        ctx.browser.respect_robots_for_subrequests = True
        rendered = await ctx.browser.render(aod_url, wait_seconds=float(ctx.config.get("wait_seconds", 3)),
                                            wait_for_selector="#aod-offer-list", scroll_selector="#all-offers-display-scroller",
                                            scroll_rounds=int(ctx.config.get("scroll_rounds", 8)),
                                            load_images=True)     # the panel opens only after the page's assets load
        r.requires_js = True
        if looks_blocked(None, rendered.html):
            r.blocked = True
            r.warnings.append("Amazon returned a bot-check page; not bypassed.")
            return r
        r.product.update({"product_code": asin.group(1)})
        r.sellers, total = parse_amazon_offers(rendered.html)
        if not r.sellers:
            r.warnings.append("The 'Other sellers' panel did not load or Amazon changed its layout.")
        elif total is not None and len(r.sellers) < total + 1:
            r.warnings.append(f"Read {len(r.sellers)} of {total + 1} offers; the rest did not load in the panel.")
        return r


_RATING = re.compile(r"([\d.]+) out of 5 stars\s*\(([\d,]+) ratings?\)(?:\s*(\d+)% positive)?")


def parse_amazon_offers(html: str) -> tuple[list[dict], int | None]:
    """Offers in Amazon's All Offers Display: the featured (#aod-pinned-offer) and the others (#aod-offer).
    Returns (sellers, number of non-featured offers Amazon says exist)."""
    s = BeautifulSoup(html, "lxml")
    out: dict[str, dict] = {}
    for o in s.select("#aod-pinned-offer, #aod-offer"):
        sold = o.select_one("#aod-offer-soldBy")
        link = sold.select_one("a[href]") if sold else None
        name = _txt(link) if link else re.sub(r"^Sold by\s*", "", (_txt(sold) or "")).split(" Seller rating")[0].strip() or None
        key = None
        if link:
            key = (parse_qs(urlsplit(link["href"]).query).get("seller") or [None])[0]
        key = key or (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") if name else None)
        if not key or key in out:
            continue
        price_box = o.select_one("[id^=aod-price-]")
        price = None
        for el in price_box.select(".aok-offscreen, .a-offscreen") if price_box else []:
            m = re.search(r"₹\s*([\d,]+(?:\.\d+)?)", el.get_text())
            if m and "per " not in el.get_text():
                price = _price(m.group(1))
                break
        text = _txt(o) or ""
        mrp = re.search(r"M\.R\.P\.:\s*₹\s*([\d,]+(?:\.\d+)?)", text)
        rating = _RATING.search(_txt(o.select_one("#aod-offer-seller-rating")) or "")
        qty = re.search(r"Minimum Order Quantity:\s*(\d+)", text)
        ships = re.sub(r"^Ships from\s*", "", _txt(o.select_one("#aod-offer-shipsFrom")) or "") or None
        delivery = _txt(o.select_one("[id^=unified-delivery-message]"))
        out[key] = {
            "seller_key": key, "seller_name": name,
            "seller_url": f"https://www.amazon.in/sp?seller={key}" if link else None,
            "price": price, "mrp": _price(mrp.group(1)) if mrp else None, "currency": "INR" if price else None,
            "in_stock": price is not None and o.select_one('input[name="submit.addToCart"]') is not None,
            "condition": _txt(o.select_one("#aod-offer-heading")), "fulfilled_by": ships,
            "min_quantity": int(qty.group(1)) if qty else None,
            "seller_rating": rating.group(1) if rating else None,
            "seller_rating_count": int(rating.group(2).replace(",", "")) if rating else None,
            "seller_positive_pct": rating.group(3) if rating and rating.group(3) else None,
            "delivery_text": delivery[:200] if delivery else None,
            "featured": o.get("id") == "aod-pinned-offer",
        }
    total = s.select_one("#aod-total-offer-count")
    n = int(total["value"]) if total and str(total.get("value") or "").isdigit() else None
    return list(out.values()), n


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
                              "best": 5, "date": date, "verified": None, "id": rv.get("reviewId"),
                              "reviewer": rv.get("userName"), "helpful": _int_or_none(rv.get("upvotes")),
                              "unhelpful": _int_or_none(rv.get("downvotes")), "media": bool(rv.get("reviewImages"))})
        if r.reviews:
            r.warnings.append("Myntra's 'top reviews' are hand-picked by Myntra and may cover related variants; not a random sample.")
        r.vendor.update(parse_delivery(" ".join([returns or ""] + [str(v) for v in svc.values() if isinstance(v, str)])))
        if r.vendor["emi_status"] == "not_offered":
            r.warnings.append("Myntra marks EMI as not available for this item (emiEnabled = false).")
        r.sellers = myntra_sellers(p)
        return r

    supports_sellers = True

    async def extract_sellers(self, url: str, ctx: ExtractionContext) -> ExtractionResult:
        return await self.extract(url, ctx)          # the seller list is in the same embedded JSON


def myntra_sellers(p: dict) -> list[dict]:
    """One entry per seller in pdpData.sellers; price and stock from that seller's size entries."""
    mrp = _price((p.get("price") or {}).get("mrp") or p.get("mrp"))
    offers: dict[str, dict] = {}
    for sz in p.get("sizes") or []:
        for sd in sz.get("sizeSellerData") or []:
            key = str(sd.get("sellerPartnerId") or "")
            if not key:
                continue
            o = offers.setdefault(key, {"price": None, "units": 0})
            price = _price(sd.get("discountedPrice"))
            if price and (o["price"] is None or price < o["price"]):
                o["price"] = price
            o["units"] += sd.get("availableCount") or 0
    out = []
    for sl in p.get("sellers") or []:
        key = str(sl.get("sellerPartnerId") or "")
        if not key:
            continue
        o = offers.get(key, {"price": None, "units": 0})
        out.append({"seller_key": key, "seller_name": sl.get("displayName") or sl.get("sellerName"), "seller_url": None,
                    "price": o["price"], "mrp": mrp, "currency": "INR" if o["price"] else None,
                    "in_stock": bool(o["units"]) and not (p.get("flags") or {}).get("outOfStock"),
                    "condition": "New", "fulfilled_by": None, "min_quantity": None, "seller_rating": None,
                    "seller_rating_count": None, "seller_positive_pct": None, "delivery_text": None,
                    "featured": key == str((p.get("selectedSeller") or {}).get("sellerPartnerId"))})
    return out


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
        r.vendor.update(parse_delivery(" ".join(_delivery_strings(p))))
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
                                  "date": date, "verified": rv.get("isBuyer"), "id": str(rv["id"]) if rv.get("id") else None,
                                  "reviewer": rv.get("name") or rv.get("userName"),
                                  "helpful": _int_or_none(rv.get("likeCount") or rv.get("helpfulCount")),
                                  "media": bool(rv.get("images") or rv.get("reviewImages")) if ("images" in rv or "reviewImages" in rv) else None})
        if p.get("latestReviews") and not r.reviews:
            r.warnings.append("Nykaa's latest reviews on the page contain ratings but no text.")
        return r
