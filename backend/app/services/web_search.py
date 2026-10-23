"""Web search for seller discovery (Brave Search API, https://api-dashboard.search.brave.com).

Only the fixed Brave endpoint is called here, with the key from BRAVE_SEARCH_API_KEY. The result URLs
are untrusted: discovery_service filters them and fetches them only through SafeFetcher.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from app.config import get_settings

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class SearchError(RuntimeError):
    pass


@dataclass
class SearchHit:
    url: str
    title: str | None
    description: str | None
    query: str


class BraveSearch:
    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = api_key
        self._transport = transport

    async def search(self, query: str, count: int = 20) -> list[SearchHit]:
        params = {"q": query, "country": "IN", "search_lang": "en", "count": min(count, 20), "safesearch": "moderate"}
        headers = {"Accept": "application/json", "X-Subscription-Token": self.api_key}
        try:
            async with httpx.AsyncClient(timeout=20, transport=self._transport) as client:
                resp = await client.get(BRAVE_ENDPOINT, params=params, headers=headers)
        except httpx.HTTPError as e:
            raise SearchError(f"Brave search request failed: {e.__class__.__name__}") from e
        if resp.status_code != 200:
            raise SearchError(f"Brave search returned HTTP {resp.status_code}")
        results = ((resp.json().get("web") or {}).get("results")) or []
        return [SearchHit(url=r["url"], title=_plain(r.get("title")), description=_plain(r.get("description")), query=query)
                for r in results if isinstance(r, dict) and r.get("url")]


def _plain(text: str | None) -> str | None:
    return re.sub(r"<[^>]+>", "", text).strip() if text else None       # Brave marks matches with <strong>


def search_client() -> BraveSearch | None:
    key = get_settings().brave_search_api_key
    return BraveSearch(key.get_secret_value()) if key and key.get_secret_value() else None


def queries_for(product: dict) -> list[str]:
    """Two queries per product: the name as a shopper would type it (detail in brackets dropped) with
    'buy online', and with 'price'. Brand is included because the name usually starts with it."""
    core = re.sub(r"\([^)]*\)", " ", product["name"])
    core = re.sub(r"\s+", " ", core.replace(",", " ")).strip()
    if product.get("brand") and product["brand"].lower() not in core.lower():
        core = f"{product['brand']} {core}"
    return [f"{core} buy online", f"{core} price"]
