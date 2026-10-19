"""Headless-browser rendering (Playwright), used only when a page needs JavaScript.

Every sub-request the page makes passes through a route guard: non-HTTP(S) schemes and any
host resolving to a non-public address are aborted. Images, media and fonts are not loaded.
Captured JSON responses and chart objects are returned for the adapters to interpret with the
vendored detector (timeseries_detect.CHART_JS).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from app.config import get_settings
from app.extraction.timeseries_detect import CHART_JS, parse_json_text
from app.security.url_validation import FetchError, UnsafeURLError, resolve_public_ips, validate_url_syntax

log = logging.getLogger(__name__)
_BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}


@dataclass
class RenderResult:
    url: str
    final_url: str
    title: str | None
    html: str
    charts: list[dict] = field(default_factory=list)     # CHART_JS "series" entries
    markers: list[dict] = field(default_factory=list)    # CHART_JS "markers" entries
    captured_json: list[tuple[str, Any]] = field(default_factory=list)
    blocked_requests: list[str] = field(default_factory=list)


class BrowserRenderer:
    def __init__(self, *, robots: RobotFileParser | None = None, respect_robots_for_subrequests: bool = True):
        s = get_settings()
        self.timeout_ms = int(s.browser_timeout_seconds * 1000)
        self.user_agent = s.user_agent
        self.robots = robots
        self.respect_robots_for_subrequests = respect_robots_for_subrequests
        self._host_ok: dict[str, bool] = {}

    def _allowed(self, url: str, main_host: str) -> bool:
        if url.startswith(("data:", "blob:")):
            return True
        try:
            parsed = validate_url_syntax(url)
        except UnsafeURLError:
            return False
        host = parsed.host
        if host not in self._host_ok:
            try:
                resolve_public_ips(host)
                self._host_ok[host] = True
            except (UnsafeURLError, FetchError):
                self._host_ok[host] = False
        if not self._host_ok[host]:
            return False
        if self.respect_robots_for_subrequests and self.robots is not None and host == main_host:
            return self.robots.can_fetch(self.user_agent, url)
        return True

    async def render(self, url: str, *, wait_seconds: float = 6.0, click_ranges: list[str] | None = None,
                     capture_json_url: str | None = None) -> RenderResult:
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:  # pragma: no cover - dependency is in requirements.txt
            raise FetchError("Playwright is not installed (pip install playwright && playwright install chromium)") from e

        main_host = urlsplit(url).hostname or ""
        url_filter = re.compile(capture_json_url) if capture_json_url else None
        captured: list[tuple[str, Any]] = []
        blocked: list[str] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                ctx = await browser.new_context(user_agent=self.user_agent, viewport={"width": 1400, "height": 900},
                                                accept_downloads=False, service_workers="block")
                page = await ctx.new_page()

                async def guard(route):
                    req = route.request
                    if req.resource_type in _BLOCKED_RESOURCE_TYPES:
                        return await route.abort()
                    if not self._allowed(req.url, main_host):
                        blocked.append(req.url[:200])
                        return await route.abort()
                    return await route.continue_()

                await page.route("**/*", guard)

                async def on_response(resp):
                    try:
                        if resp.status >= 400 or (url_filter and not url_filter.search(resp.url)):
                            return
                        ct = (resp.headers.get("content-type") or "").lower()
                        if "json" not in ct:
                            return
                        body = await resp.body()
                        if body and len(body) < 5_000_000:
                            captured.append((resp.url, parse_json_text(body.decode("utf-8", "replace"))))
                    except Exception:  # noqa: BLE001 - non-JSON or unavailable bodies are expected
                        pass

                page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                except Exception as e:  # noqa: BLE001
                    log.warning("navigation did not finish cleanly: %s", e.__class__.__name__)
                try:
                    await page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
                except Exception:  # noqa: BLE001
                    pass
                await page.wait_for_timeout(int(wait_seconds * 1000))

                charts, markers = await self._read_charts(page)
                for label in click_ranges or []:
                    c, m = await self._click_and_read(page, label)
                    charts += c
                    markers += m
                html = await page.content()
                title = await page.title()
                final_url = page.url
            finally:
                await browser.close()
        return RenderResult(url=url, final_url=final_url, title=title, html=html, charts=charts,
                            markers=markers, captured_json=captured, blocked_requests=blocked)

    @staticmethod
    async def _read_charts(page) -> tuple[list[dict], list[dict]]:
        charts, markers = [], []
        for frame in page.frames:
            try:
                res = await frame.evaluate(CHART_JS) or {}
            except Exception:  # noqa: BLE001
                continue
            charts += res.get("series") or []
            markers += res.get("markers") or []
        return charts, markers

    async def _click_and_read(self, page, label: str) -> tuple[list[dict], list[dict]]:
        rx = re.compile(rf"^\s*{re.escape(label)}\s*$", re.I)
        for loc in (page.get_by_role("button", name=rx), page.get_by_role("tab", name=rx), page.get_by_text(rx)):
            try:
                if await loc.count():
                    await loc.first.click(timeout=5000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:  # noqa: BLE001
                        pass
                    await page.wait_for_timeout(2500)
                    return await self._read_charts(page)
            except Exception:  # noqa: BLE001
                continue
        return [], []


def dump_render(result: RenderResult) -> str:
    """Debug helper: compact JSON of what the browser saw (no HTML)."""
    return json.dumps({"charts": [{k: v for k, v in c.items() if k in ("lib", "chart", "name")} | {"n": len(c.get("x") or [])}
                                  for c in result.charts],
                       "markers": len(result.markers), "json_responses": len(result.captured_json),
                       "blocked": len(result.blocked_requests)})
