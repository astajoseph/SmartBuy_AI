"""Adapter for pricehistory.app product pages (https://pricehistory.app/p/<slug>-<id>).

Inspected 2026-10-16 against /p/beauty-joseon-sunscreen-spf-50-pa-sun-qXOOmq2h:

* Static HTML (no JS needed) carries a schema.org Product in JSON-LD (Flipkart products list offers
  "Price" and "Offer Price"; Amazon products, e.g. /p/olay-total-effects-day-cream-vitamin-b5-u4L6fhce,
  list one unnamed offer which is the selling price):
    name, brand.name, additionalProperty "Product Code" (Flipkart FSN / Amazon ASIN),
    offers[] {name: "Price" | "Offer Price", price, priceCurrency, availability, offeredBy.name},
    description "... Lowest Price 1035 Average Price 1234 Highest Price 1500 MRP 1500 ..."
  plus visible "Lowest Offer: ₹945".
* The price-history chart is Chart.js, filled by page JavaScript from POST /api/price/<id>.
  robots.txt disallows /api/ - this adapter never calls that endpoint itself. History is read
  from the rendered chart only when a browser is available and the product's adapter_config
  sets respect_robots_for_subrequests: false (an explicit operator decision; see docs/extraction.md).
  Datasets: "Prices" (selling price, date-only x values) and "Offer Price" (sparse, nulls = no offer).
  Shaded x-axis bands are labelled retailer sale windows (e.g. "Big Billion Days").
"""
from __future__ import annotations

import re
from decimal import Decimal

from bs4 import BeautifulSoup

from app.extraction import jsonld
from app.extraction.adapters.common import history_from_charts, looks_blocked, sale_bands_from_markers
from app.extraction.base import Adapter, CurrentPrice, ExtractionContext, ExtractionResult
from app.extraction.validation import InvalidValue, normalize_currency, parse_price

_STAT_PATTERNS = {
    "lowest": r"Lowest Price\s+([\d,.]+)",
    "average": r"Average Price\s+([\d,.]+)",
    "highest": r"Highest Price\s+([\d,.]+)",
    "mrp": r"MRP\s+([\d,.]+)",
}
_OFFER_NAME_TO_TYPE = {"price": "selling_price", "offer price": "offer_price"}


class PriceHistoryAppAdapter(Adapter):
    name = "pricehistory_app"
    version = "1.0.0"
    kind = "price_history"
    hosts = ("pricehistory.app",)
    # offer_price is optional: the page only lists it while a bank/coupon offer exists
    expected_fields = ("product.name", "product.brand", "product.product_code", "current_price",
                       "stat.lowest", "stat.average", "stat.highest", "stat.mrp", "price_history", "sale_bands")

    async def extract(self, url: str, ctx: ExtractionContext) -> ExtractionResult:
        result = self.new_result(url)
        page = await ctx.fetcher.fetch(url)
        result.http_status = page.status
        if looks_blocked(page.status, page.text):
            result.blocked = True
            result.warnings.append("Source returned a block / CAPTCHA page; nothing extracted (not bypassed).")
            return result

        self._parse_static(page.text, result)

        if not ctx.config.get("history", True):
            result.warnings.append("History collection disabled in adapter_config.")
            return result
        if ctx.browser is None:
            result.requires_js = True
            result.warnings.append("Price history is drawn by JavaScript; no browser available, history skipped.")
            return result

        ctx.browser.robots = ctx.fetcher.robots_for(url)
        ctx.browser.respect_robots_for_subrequests = bool(ctx.config.get("respect_robots_for_subrequests", True))
        rendered = await ctx.browser.render(url, wait_seconds=float(ctx.config.get("wait_seconds", 6)),
                                            click_ranges=ctx.config.get("click_ranges"))
        result.requires_js = True
        currency = result.product.get("currency") or "INR"
        history_from_charts(result, rendered.charts, ctx.config.get("series_map") or {
            r"/ Prices$": "selling_price", r"/ Offer Price$": "offer_price"}, currency)
        sale_bands_from_markers(result, rendered.markers, result.product.get("retailer"))
        if not result.history:
            if rendered.blocked_requests:
                result.warnings.append(
                    f"No chart data. {len(rendered.blocked_requests)} sub-requests were blocked "
                    "(robots.txt or non-public address); the chart's data endpoint is under /api/, which robots.txt disallows.")
            else:
                result.warnings.append("Chart rendered but no price series matched series_map.")
        return result

    def _parse_static(self, html: str, result: ExtractionResult) -> None:
        prods = jsonld.products(html)
        if not prods:
            result.warnings.append("No schema.org Product JSON-LD found; page layout may have changed.")
            return
        p = prods[0]
        offers = jsonld.offers_of(p)
        currency = next((normalize_currency(o.get("priceCurrency")) for o in offers if o.get("priceCurrency")), None)
        retailer = next((jsonld.name_of(o.get("offeredBy") or o.get("seller")) for o in offers), None)
        image = p.get("image")
        if isinstance(image, dict) and "@id" in image:
            node = jsonld.node_by_id(html, image["@id"])
            image = node.get("url") if node else None
        result.product.update({
            "name": self._visible_title(html) or p.get("name"),
            "brand": jsonld.name_of(p.get("brand")),
            "product_code": jsonld.additional_property(p, "Product Code") or p.get("sku"),
            "category": p.get("category"),
            "image_url": image if isinstance(image, str) else None,
            "retailer": retailer,
            "currency": currency,
        })
        if currency is None:
            result.warnings.append("Offer currency missing; prices not stored.")
            return

        desc = str(p.get("description") or "")
        for stat, rx in _STAT_PATTERNS.items():
            m = re.search(rx, desc)
            if m:
                try:
                    v = parse_price(m.group(1))
                    if v:
                        result.source_stats[stat] = v
                except InvalidValue:
                    result.warnings.append(f"Unparseable {stat} statistic: {m.group(1)!r}")
        text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
        m = re.search(r"Lowest Offer:\s*₹\s*([\d,]+)", text)
        if m:
            try:
                v = parse_price(m.group(1))
                if v:
                    result.source_stats["lowest_offer"] = v
            except InvalidValue:
                pass

        mrp = result.source_stats.get("mrp")
        for o in offers:
            name = str(o.get("name") or "").strip().lower()
            # Amazon pages (inspected 2026-10-16) publish a single unnamed offer = the selling price
            ptype = _OFFER_NAME_TO_TYPE.get(name) if name else ("selling_price" if len(offers) == 1 else None)
            if ptype is None:
                continue
            try:
                price = parse_price(o.get("price"))
            except InvalidValue:
                result.warnings.append(f"Invalid {ptype} value {o.get('price')!r}; ignored.")
                continue
            if price is None:
                continue
            discount = None
            if mrp and mrp >= price:
                discount = ((mrp - price) / mrp * 100).quantize(Decimal("0.01"))
            result.current_prices.append(CurrentPrice(
                price=price, price_type=ptype, currency=currency,  # type: ignore[arg-type]
                availability=jsonld.availability_of(o), seller_name=jsonld.name_of(o.get("offeredBy")),
                discount_percent=discount))

    @staticmethod
    def _visible_title(html: str) -> str | None:
        soup = BeautifulSoup(html, "lxml")
        t = soup.title.string if soup.title and soup.title.string else None
        if t and " - Price History" in t:
            return t.split(" - Price History")[0].strip()
        return None
