"""Generic adapters for sites without a dedicated adapter.

* generic_jsonld  - static HTML, schema.org Product JSON-LD: current price, name, brand, codes.
* chart_page      - headless browser; reads chart objects (Highcharts / Chart.js / ...) with the
                    vendored detector. Which series are prices is configured via adapter_config.series_map.
Both only extract what the page publishes in a standard form; they contain no site-specific selectors.
"""
from __future__ import annotations

from app.extraction import jsonld
from app.extraction.adapters.common import history_from_charts, looks_blocked, sale_bands_from_markers
from app.extraction.base import Adapter, CurrentPrice, ExtractionContext, ExtractionResult
from app.extraction.validation import InvalidValue, normalize_currency, parse_price


def parse_product_jsonld(html: str, result: ExtractionResult) -> None:
    prods = jsonld.products(html)
    if not prods:
        return
    p = prods[0]
    offers = jsonld.offers_of(p)
    result.product.update({
        "name": p.get("name"),
        "brand": jsonld.name_of(p.get("brand")),
        "model": p.get("model") if isinstance(p.get("model"), str) else None,
        "product_code": p.get("sku") or p.get("mpn") or p.get("gtin13") or p.get("gtin"),
        "image_url": p.get("image") if isinstance(p.get("image"), str) else None,
    })
    for o in offers[:1]:
        currency = normalize_currency(o.get("priceCurrency"))
        try:
            price = parse_price(o.get("price") or o.get("lowPrice"))
        except InvalidValue:
            result.warnings.append(f"Invalid JSON-LD price {o.get('price')!r}")
            continue
        if price is None:
            continue
        if currency is None:
            result.warnings.append("JSON-LD offer has no currency; price ignored.")
            continue
        seller = jsonld.name_of(o.get("seller") or o.get("offeredBy"))
        result.product.setdefault("currency", currency)
        result.current_prices.append(CurrentPrice(price=price, price_type="selling_price", currency=currency,
                                                  availability=jsonld.availability_of(o), seller_name=seller))


class GenericJsonLdAdapter(Adapter):
    name = "generic_jsonld"
    version = "1.0.0"
    kind = "price_history"
    expected_fields = ("product.name", "current_price", "price_history")

    async def extract(self, url: str, ctx: ExtractionContext) -> ExtractionResult:
        result = self.new_result(url)
        page = await ctx.fetcher.fetch(url)
        result.http_status = page.status
        if looks_blocked(page.status, page.text):
            result.blocked = True
            result.warnings.append("Block / CAPTCHA page returned; not bypassed.")
            return result
        parse_product_jsonld(page.text, result)
        result.requires_js = False if result.current_prices else None
        if not result.current_prices:
            result.warnings.append("No structured price in static HTML; the page may need JavaScript (try chart_page).")
        result.warnings.append("This adapter reads the current price only; it cannot see historical prices.")
        return result


class ChartPageAdapter(Adapter):
    name = "chart_page"
    version = "1.0.0"
    kind = "price_history"
    expected_fields = ("product.name", "current_price", "price_history")

    async def extract(self, url: str, ctx: ExtractionContext) -> ExtractionResult:
        result = self.new_result(url)
        if ctx.browser is None:
            result.requires_js = True
            result.warnings.append("chart_page needs a headless browser, which is not available here.")
            return result
        # load robots.txt for the host and refuse if the page itself is disallowed
        page = await ctx.fetcher.fetch(url)
        result.http_status = page.status
        ctx.browser.robots = ctx.fetcher.robots_for(url)
        ctx.browser.respect_robots_for_subrequests = bool(ctx.config.get("respect_robots_for_subrequests", True))
        rendered = await ctx.browser.render(url, wait_seconds=float(ctx.config.get("wait_seconds", 6)),
                                            click_ranges=ctx.config.get("click_ranges"))
        result.requires_js = True
        if looks_blocked(None, rendered.html):
            result.blocked = True
            result.warnings.append("Block / CAPTCHA page returned; not bypassed.")
            return result
        parse_product_jsonld(rendered.html, result)
        currency = ctx.config.get("currency") or result.product.get("currency") or "INR"
        history_from_charts(result, rendered.charts, ctx.config.get("series_map") or {r"price": "selling_price"}, currency)
        sale_bands_from_markers(result, rendered.markers, ctx.config.get("retailer"))
        if not result.history:
            names = sorted({f"{c.get('chart')} / {c.get('name')}" for c in rendered.charts})
            result.warnings.append(f"No chart series matched series_map. Series seen: {names[:10]}")
        return result
