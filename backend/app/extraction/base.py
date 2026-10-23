"""Common types for extraction adapters.

An adapter turns one URL into an ExtractionResult. Anything the page does not expose stays
None / empty and is reported in `fields_missing` - it is never filled with 0 or a guess.
"""
from __future__ import annotations

import abc
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, ClassVar, Literal

PriceType = Literal["selling_price", "offer_price", "mrp"]


@dataclass
class PricePoint:
    timestamp: datetime              # historical_timestamp (timezone-aware)
    price: Decimal
    price_type: PriceType
    currency: str
    source_label: str | None = None  # the raw x value as the source showed it
    quality: str = "source_reported"


@dataclass
class CurrentPrice:
    price: Decimal
    price_type: PriceType
    currency: str
    availability: str | None = None
    seller_name: str | None = None
    discount_percent: Decimal | None = None


@dataclass
class SaleBand:
    label: str
    start: date
    end: date
    retailer: str | None = None


@dataclass
class ExtractionResult:
    url: str
    adapter: str
    adapter_version: str
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    requires_js: bool | None = None
    http_status: int | None = None
    blocked: bool = False
    product: dict[str, Any] = field(default_factory=dict)     # name, brand, model, product_code, image_url, retailer
    current_prices: list[CurrentPrice] = field(default_factory=list)
    history: list[PricePoint] = field(default_factory=list)
    source_stats: dict[str, Decimal] = field(default_factory=dict)  # lowest / average / highest / mrp / lowest_offer
    sale_bands: list[SaleBand] = field(default_factory=list)
    vendor: dict[str, Any] = field(default_factory=dict)      # price, mrp, availability, seller_name, delivery_text ...
    emi_plans: list[dict[str, Any]] = field(default_factory=list)
    review_aggregates: list[dict[str, Any]] = field(default_factory=list)
    reviews: list[dict[str, Any]] = field(default_factory=list)
    sellers: list[dict[str, Any]] = field(default_factory=list)  # marketplace offers: seller_key, seller_name, price, in_stock ...
    warnings: list[str] = field(default_factory=list)
    expected_fields: tuple[str, ...] = ()

    def present_fields(self) -> list[str]:
        found = [f"product.{k}" for k, v in self.product.items() if v not in (None, "")]
        if any(p.price_type == "selling_price" for p in self.current_prices):
            found.append("current_price")
        if any(p.price_type == "offer_price" for p in self.current_prices):
            found.append("offer_price")
        if self.history:
            found.append("price_history")
        found += [f"stat.{k}" for k in self.source_stats]
        if self.sale_bands:
            found.append("sale_bands")
        found += [f"vendor.{k}" for k, v in self.vendor.items() if v not in (None, "")]
        if self.emi_plans:
            found.append("emi_plans")
        if self.review_aggregates:
            found.append("review_aggregate")
        if self.reviews:
            found.append("review_samples")
        if self.sellers:
            found.append("sellers")
        return found

    def missing_fields(self) -> list[str]:
        present = set(self.present_fields())
        return [f for f in self.expected_fields if f not in present]

    def report(self, sample_points: int = 5) -> dict[str, Any]:
        hist = sorted(self.history, key=lambda p: (p.price_type, p.timestamp))
        by_type: dict[str, list[PricePoint]] = {}
        for p in hist:
            by_type.setdefault(p.price_type, []).append(p)
        return {
            "url": self.url,
            "adapter": self.adapter,
            "adapter_version": self.adapter_version,
            "fetched_at": self.fetched_at.isoformat(),
            "http_status": self.http_status,
            "blocked": self.blocked,
            "requires_js": self.requires_js,
            "fields_found": self.present_fields(),
            "fields_missing": self.missing_fields(),
            "warnings": self.warnings,
            "product": self.product,
            "current_prices": [_jsonable(asdict(c)) for c in self.current_prices],
            "source_stats": {k: str(v) for k, v in self.source_stats.items()},
            "history_summary": {
                t: {"points": len(ps), "first": ps[0].timestamp.isoformat(), "last": ps[-1].timestamp.isoformat(),
                    "first_points": [[p.timestamp.isoformat(), str(p.price)] for p in ps[:sample_points]],
                    "last_points": [[p.timestamp.isoformat(), str(p.price)] for p in ps[-sample_points:]]}
                for t, ps in by_type.items()
            },
            "sale_bands": [_jsonable(asdict(b)) for b in self.sale_bands],
            "vendor": _jsonable(self.vendor),
            "emi_plans": _jsonable(self.emi_plans),
            "review_aggregates": _jsonable(self.review_aggregates),
            "review_samples": len(self.reviews),
            "sellers": _jsonable(self.sellers),
        }


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj


@dataclass
class ExtractionContext:
    fetcher: Any                    # app.security.url_validation.SafeFetcher
    browser: Any | None = None      # app.extraction.browser.BrowserRenderer (None = JS rendering unavailable)
    config: dict[str, Any] = field(default_factory=dict)


class Adapter(abc.ABC):
    name: ClassVar[str]
    version: ClassVar[str]
    kind: ClassVar[Literal["price_history", "vendor"]]
    expected_fields: ClassVar[tuple[str, ...]] = ()
    hosts: ClassVar[tuple[str, ...]] = ()   # hostnames this adapter is specific to ("" = generic)

    def new_result(self, url: str) -> ExtractionResult:
        return ExtractionResult(url=url, adapter=self.name, adapter_version=self.version,
                                expected_fields=self.expected_fields)

    @abc.abstractmethod
    async def extract(self, url: str, ctx: ExtractionContext) -> ExtractionResult: ...

    supports_sellers: ClassVar[bool] = False   # marketplace page listing several sellers for the same product

    async def extract_sellers(self, url: str, ctx: ExtractionContext) -> ExtractionResult:
        """Every seller offering the product on this marketplace page (result.sellers). Discovery only."""
        raise NotImplementedError(f"{self.name} does not list sellers")
