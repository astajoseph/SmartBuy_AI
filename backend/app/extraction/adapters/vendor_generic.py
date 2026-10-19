"""Generic on-demand vendor adapter (price, availability, seller, ratings, reviews, EMI text).

Used ONLY from services/vendor_service.py when a user's request needs vendor data and the cache
is missing or expired - never by the scheduled collector.

Reads schema.org JSON-LD for price / rating / reviews and the visible text for EMI wording.
Large marketplaces (Amazon, Flipkart) often serve bot checks or render offers with JavaScript;
a blocked page is reported as blocked and not bypassed. adapter_config.render_js: true makes the
adapter render the page in the headless browser instead of plain HTTP.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from app.extraction import jsonld
from app.extraction.adapters.common import looks_blocked
from app.extraction.base import Adapter, ExtractionContext, ExtractionResult
from app.extraction.validation import InvalidValue, normalize_currency, parse_price
from app.extraction.vendor_offers import parse_emi_text


class VendorGenericAdapter(Adapter):
    name = "vendor_generic"
    version = "1.0.0"
    kind = "vendor"
    expected_fields = ("vendor.price", "vendor.availability", "vendor.seller_name", "emi_plans",
                       "review_aggregate", "review_samples")

    async def extract(self, url: str, ctx: ExtractionContext) -> ExtractionResult:
        result = self.new_result(url)
        page = await ctx.fetcher.fetch(url)          # also enforces robots.txt for the page itself
        result.http_status = page.status
        html = page.text
        result.requires_js = False
        if ctx.config.get("render_js"):
            if ctx.browser is None:
                result.warnings.append("render_js requested but no browser available; using static HTML.")
            else:
                ctx.browser.robots = ctx.fetcher.robots_for(url)
                ctx.browser.respect_robots_for_subrequests = True
                rendered = await ctx.browser.render(url, wait_seconds=float(ctx.config.get("wait_seconds", 5)))
                html = rendered.html
                result.requires_js = True
        if looks_blocked(page.status, html):
            result.blocked = True
            result.warnings.append("Vendor returned a block / CAPTCHA page; not bypassed. No vendor data extracted.")
            return result

        prods = jsonld.products(html)
        if prods:
            p = prods[0]
            result.product.update({"name": p.get("name"), "brand": jsonld.name_of(p.get("brand")),
                                   "product_code": p.get("sku") or p.get("mpn")})
            offers = jsonld.offers_of(p)
            if offers:
                o = offers[0]
                try:
                    result.vendor["price"] = parse_price(o.get("price") or o.get("lowPrice"))
                except InvalidValue:
                    result.warnings.append(f"Invalid vendor price {o.get('price')!r}")
                result.vendor["currency"] = normalize_currency(o.get("priceCurrency"))
                result.vendor["availability"] = jsonld.availability_of(o)
                result.vendor["seller_name"] = jsonld.name_of(o.get("seller") or o.get("offeredBy"))
                if result.vendor.get("price") and not result.vendor.get("currency"):
                    result.warnings.append("Vendor price has no currency in structured data.")
            ar = jsonld.aggregate_rating(p)
            if ar and ar.get("rating") is not None:
                result.review_aggregates.append({"kind": "product_aggregate", **ar})
            result.reviews.extend({"kind": "product_review", **r} for r in jsonld.reviews_of(p))
        else:
            result.warnings.append("No schema.org Product JSON-LD; price/rating/reviews unavailable from structured data.")

        soup = BeautifulSoup(html, "lxml")
        for t in soup(["script", "style", "noscript"]):
            t.decompose()
        text = soup.get_text(" ", strip=True)
        plans, facts = parse_emi_text(text)
        result.emi_plans = plans
        result.vendor["emi_page_facts"] = {k: (str(v) if not isinstance(v, (bool, list)) else v) for k, v in facts.items()}
        if not plans:
            result.warnings.append("No EMI terms found in the page text (they may load via JavaScript or after login).")

        m = re.search(r"(?:delivery|delivered)\s+(?:by|in)\s+([^.|]{3,40})", text, re.I)
        if m:
            result.vendor["delivery_text"] = m.group(0)[:80]
        return result
