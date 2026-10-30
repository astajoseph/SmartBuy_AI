"""Stock status of a vendor listing, from the availability text / schema.org value and the price."""
from __future__ import annotations

OUT_OF_STOCK = {"OutOfStock", "SoldOut", "Discontinued", "PreOrder", "BackOrder"}


def in_stock(availability: str | None, price) -> bool:
    return price is not None and (availability or "InStock") not in OUT_OF_STOCK
