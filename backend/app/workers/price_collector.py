"""Scheduled collection from PRIMARY PRICE-HISTORY sources only.

This module never reads product_vendor_sources and never calls vendor adapters - vendor pages
are fetched on demand by services/vendor_service.py. tests/test_vendor_on_demand.py checks this.

Per source:
  1. take a Postgres advisory lock (a second worker skips the same source instead of duplicating it)
  2. open a collection_jobs row
  3. run the adapter (HTTP retries with backoff happen inside SafeFetcher; whole-adapter retries here)
  4. validate -> insert history points / observation / stated stats / sale bands (all idempotent)
  5. on failure keep every previously stored price, record the error, and move on to the next source
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Callable

from app.config import get_settings
from app.database.repositories import LOCK_NAMESPACE_PRICE, Repository
from app.extraction.base import ExtractionContext, ExtractionResult
from app.extraction.browser import BrowserRenderer
from app.extraction.registry import resolve_adapter
from app.security.url_validation import FetchError, RobotsDisallowed, SafeFetcher, UnsafeURLError

log = logging.getLogger(__name__)


@dataclass
class CollectorDeps:
    fetcher_factory: Callable[[set[str]], SafeFetcher] = lambda hosts: SafeFetcher(hosts)
    browser_factory: Callable[[], BrowserRenderer | None] = lambda: BrowserRenderer()
    after_collect: Callable[[Repository, str], None] | None = None   # e.g. alert evaluation


def _is_transient(exc: Exception) -> bool:
    return isinstance(exc, FetchError) and exc.transient


async def collect_source(repo: Repository, source: dict, *, job_type: str = "scheduled_price",
                         deps: CollectorDeps | None = None) -> dict:
    deps = deps or CollectorDeps()
    settings = get_settings()
    if source["source_type"] != "price_history":
        raise ValueError("collect_source only handles price_history sources")

    with repo.db.connection() as lock_conn:
        if not repo.try_lock(lock_conn, LOCK_NAMESPACE_PRICE, source["id"]):
            log.info("source %s locked by another worker; skipped", source["id"])
            return {"source_id": source["id"], "status": "skipped", "reason": "locked"}
        try:
            return await _collect_locked(repo, source, job_type, deps, settings.collection_max_retries)
        finally:
            repo.unlock(lock_conn, LOCK_NAMESPACE_PRICE, source["id"])


async def _collect_locked(repo: Repository, source: dict, job_type: str, deps: CollectorDeps, max_retries: int) -> dict:
    job_id = repo.start_job(job_type, source_id=source["id"])
    product_id = source["product_id"]
    attempt = 0
    while True:
        try:
            adapter = resolve_adapter(source["adapter_name"], source["source_url"], "price_history")
            ctx = ExtractionContext(fetcher=deps.fetcher_factory(repo.registered_hosts()),
                                    browser=deps.browser_factory(), config=source.get("adapter_config") or {})
            result = await adapter.extract(source["source_url"], ctx)
            break
        except Exception as e:  # noqa: BLE001 - every failure is recorded, none may stop other sources
            if _is_transient(e) and attempt < max_retries:
                attempt += 1
                await asyncio.sleep(min(120, 5 * 2 ** attempt) * (0.5 + random.random() / 2))
                continue
            msg = f"{e.__class__.__name__}: {e}"
            if isinstance(e, (RobotsDisallowed, UnsafeURLError)):
                msg = f"refused: {msg}"
            repo.mark_source_attempt(source["id"], success=False, error=msg)
            repo.finish_job(job_id, "failed", error=msg, retries=attempt)
            log.warning("collection failed for source %s: %s", source["id"], msg)
            return {"source_id": source["id"], "product_id": product_id, "status": "failed", "error": msg, "job_id": job_id}

    return _store(repo, source, result, job_id, attempt, deps)


def _store(repo: Repository, source: dict, result: ExtractionResult, job_id: int, retries: int,
           deps: CollectorDeps) -> dict:
    product_id = source["product_id"]
    report = result.report()
    if result.blocked or (not result.current_prices and not result.history):
        msg = "; ".join(result.warnings) or "No price data extracted"
        repo.mark_source_attempt(source["id"], success=False, error=msg)
        repo.finish_job(job_id, "failed", error=msg, retries=retries, details=report)
        return {"source_id": source["id"], "product_id": product_id, "status": "failed", "error": msg, "job_id": job_id}

    currency = result.product.get("currency") or "INR"
    extracted = len(result.history) + len(result.current_prices) + len(result.source_stats)
    inserted = repo.insert_history_points(product_id, source["id"], result.history, "source_chart_data")
    inserted += repo.insert_observations(product_id, source["id"], result.fetched_at, result.current_prices,
                                         f"{result.adapter}@{result.adapter_version}")
    inserted += repo.insert_source_stats(product_id, source["id"], result.fetched_at, result.source_stats, currency)
    bands = repo.upsert_sale_bands(result.sale_bands, source["source_url"])
    repo.set_product_image_if_missing(product_id, result.product.get("image_url"))

    status = "partial" if result.missing_fields() else "success"
    repo.mark_source_attempt(source["id"], success=True)
    repo.finish_job(job_id, status, extracted=extracted, inserted=inserted, retries=retries,
                    details={**report, "new_sale_events": bands})
    if deps.after_collect:
        try:
            deps.after_collect(repo, product_id)
        except Exception:  # noqa: BLE001 - alerts must not fail a successful collection
            log.exception("post-collection hook failed for %s", product_id)
    return {"source_id": source["id"], "product_id": product_id, "status": status, "job_id": job_id,
            "records_extracted": extracted, "records_inserted": inserted,
            "fields_missing": result.missing_fields(), "warnings": result.warnings}


async def collect_due(repo: Repository, *, deps: CollectorDeps | None = None, concurrency: int | None = None,
                      grace_minutes: float = 0) -> list[dict]:
    """grace_minutes: also collect sources that become due within this many minutes, so a cron job that fires a
    little early (or a source whose last run finished a little late) does not skip a whole cycle."""
    sources = repo.due_price_sources(datetime.now(timezone.utc) + timedelta(minutes=grace_minutes))
    sem = asyncio.Semaphore(concurrency or get_settings().collection_concurrency)

    async def run(s: dict) -> dict:
        async with sem:
            try:
                return await collect_source(repo, s, deps=deps)
            except Exception as e:  # noqa: BLE001
                log.exception("unexpected collector error for source %s", s["id"])
                return {"source_id": s["id"], "status": "failed", "error": f"{e.__class__.__name__}: {e}"}

    return list(await asyncio.gather(*(run(s) for s in sources)))
