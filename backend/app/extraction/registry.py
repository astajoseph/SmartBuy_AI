"""Adapter registry: maps adapter names (stored per source) to implementations.

To support a new site, add a module under extraction/adapters/, register it here and set
adapter_name for the source. 'auto' picks a host-specific adapter if one exists, otherwise
the generic JSON-LD adapter (price_history) or vendor_generic (vendor).
"""
from __future__ import annotations

from urllib.parse import urlsplit

from app.extraction.adapters.generic import ChartPageAdapter, GenericJsonLdAdapter
from app.extraction.adapters.marketplaces import AmazonInAdapter, MyntraAdapter, NykaaAdapter
from app.extraction.adapters.pricehistory_app import PriceHistoryAppAdapter
from app.extraction.adapters.vendor_generic import VendorGenericAdapter
from app.extraction.base import Adapter

ADAPTERS: dict[str, type[Adapter]] = {
    a.name: a for a in (PriceHistoryAppAdapter, GenericJsonLdAdapter, ChartPageAdapter, VendorGenericAdapter,
                        AmazonInAdapter, MyntraAdapter, NykaaAdapter)
}


class UnknownAdapter(KeyError):
    pass


def resolve_adapter(name: str, url: str, kind: str) -> Adapter:
    if name and name != "auto":
        cls = ADAPTERS.get(name)
        if cls is None:
            raise UnknownAdapter(f"Unknown adapter '{name}'. Available: {sorted(ADAPTERS)}")
        if cls.kind != kind:
            raise UnknownAdapter(f"Adapter '{name}' is a {cls.kind} adapter, not {kind}")
        return cls()
    host = (urlsplit(url).hostname or "").lower()
    for cls in ADAPTERS.values():
        if cls.kind == kind and any(host == h or host.endswith("." + h) for h in cls.hosts):
            return cls()
    return GenericJsonLdAdapter() if kind == "price_history" else VendorGenericAdapter()
