#!/usr/bin/env python3
"""Inspect a URL with an extraction adapter and print the fields found / missing. Nothing is stored.

    python scripts/test_source.py https://pricehistory.app/p/<slug> [--kind vendor] [--adapter auto]
           [--config '{"respect_robots_for_subrequests": false}'] [--no-browser]
"""
import argparse
import asyncio
import json
from urllib.parse import urlsplit

import _bootstrap  # noqa: F401

from app.extraction.base import ExtractionContext
from app.extraction.browser import BrowserRenderer
from app.extraction.registry import resolve_adapter
from app.security.url_validation import SafeFetcher

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--kind", default="price_history", choices=["price_history", "vendor"])
    ap.add_argument("--adapter", default="auto")
    ap.add_argument("--config", default="{}")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    adapter = resolve_adapter(a.adapter, a.url, a.kind)
    ctx = ExtractionContext(fetcher=SafeFetcher({urlsplit(a.url).hostname or ""}),
                            browser=None if a.no_browser else BrowserRenderer(), config=json.loads(a.config))
    result = asyncio.run(adapter.extract(a.url, ctx))
    print(json.dumps(result.report(), indent=2, ensure_ascii=False))
