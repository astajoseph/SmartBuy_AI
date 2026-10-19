"""schema.org JSON-LD extraction (Product / Offer / AggregateRating / Review).

Structured data is preferred over CSS selectors because sites publish it deliberately and it
changes far less often than page layout.
"""
from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup


def _types(node: dict) -> set[str]:
    t = node.get("@type")
    if isinstance(t, list):
        return {str(x) for x in t}
    return {str(t)} if t else set()


def iter_nodes(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for tag in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
        text = tag.string or tag.get_text() or ""
        try:
            data = json.loads(text.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        stack = [data]
        while stack:
            cur = stack.pop()
            if isinstance(cur, list):
                stack.extend(cur)
            elif isinstance(cur, dict):
                out.append(cur)
                if "@graph" in cur:
                    stack.append(cur["@graph"])
    return out


def products(html: str) -> list[dict]:
    return [n for n in iter_nodes(html) if "Product" in _types(n)]


def node_by_id(html: str, node_id: str) -> dict | None:
    return next((n for n in iter_nodes(html) if n.get("@id") == node_id), None)


def offers_of(product: dict) -> list[dict]:
    offers = product.get("offers")
    if offers is None:
        return []
    if isinstance(offers, dict):
        if "AggregateOffer" in _types(offers) and isinstance(offers.get("offers"), list):
            return [o for o in offers["offers"] if isinstance(o, dict)] or [offers]
        return [offers]
    return [o for o in offers if isinstance(o, dict)]


def name_of(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("name")
    if isinstance(value, str):
        return value
    return None


def availability_of(offer: dict) -> str | None:
    a = offer.get("availability")
    if not a:
        return None
    return str(a).rsplit("/", 1)[-1]  # https://schema.org/InStock -> InStock


def additional_property(product: dict, name: str) -> str | None:
    props = product.get("additionalProperty") or []
    if isinstance(props, dict):
        props = [props]
    for p in props:
        if isinstance(p, dict) and str(p.get("name", "")).lower() == name.lower():
            return str(p.get("value"))
    return None


def aggregate_rating(product: dict) -> dict | None:
    ar = product.get("aggregateRating")
    if not isinstance(ar, dict):
        return None
    return {
        "rating": ar.get("ratingValue"),
        "best": ar.get("bestRating") or 5,
        "count": ar.get("ratingCount") or ar.get("reviewCount"),
    }


def reviews_of(product: dict) -> list[dict]:
    raw = product.get("review") or []
    if isinstance(raw, dict):
        raw = [raw]
    out = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        rating = r.get("reviewRating") if isinstance(r.get("reviewRating"), dict) else {}
        body = r.get("reviewBody") or r.get("description")
        if not body and not rating:
            continue
        out.append({
            "text": str(body).strip() if body else None,
            "rating": rating.get("ratingValue"),
            "best": rating.get("bestRating") or 5,
            "date": r.get("datePublished"),
        })
    return out
