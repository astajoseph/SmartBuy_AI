"""Collection worker entry point.

    python -m app.workers.scheduler --once [--grace-minutes 30]   # collect due sources, exit (GitHub Actions / cron)
    python -m app.workers.scheduler --loop 300    # persistent worker: check for due sources every 300 s

Both modes also evaluate forecasts whose horizon has passed (prediction_outcomes) and refresh
forecasts for products that received new data. Running several workers at once is safe:
advisory locks make each source collected by one worker at a time.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal

from app.database.client import get_database
from app.database.repositories import Repository
from app.logging_config import configure_logging
from app.services.alert_service import evaluate_product_alerts
from app.workers.price_collector import CollectorDeps, collect_due

log = logging.getLogger("scheduler")


async def run_once(repo: Repository, grace_minutes: float = 0) -> list[dict]:
    from app.forecasting.forecasting import evaluate_due_forecasts, refresh_forecasts

    results = await collect_due(repo, deps=CollectorDeps(after_collect=evaluate_product_alerts), grace_minutes=grace_minutes)
    for pid in {r.get("product_id") for r in results if r.get("status") in ("success", "partial")}:
        try:
            refresh_forecasts(repo, pid)
        except Exception:  # noqa: BLE001
            log.exception("forecast refresh failed for %s", pid)
    try:
        evaluated = evaluate_due_forecasts(repo)
        log.info("evaluated %d matured forecasts", evaluated)
    except Exception:  # noqa: BLE001
        log.exception("forecast evaluation failed")
    log.info("collection run finished: %s", json.dumps(
        [{k: r.get(k) for k in ("source_id", "product_id", "status", "records_inserted", "error")} for r in results]))
    return results


async def run_loop(repo: Repository, interval: float) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - Windows
            pass
    while not stop.is_set():
        try:
            await run_once(repo)
        except Exception:  # noqa: BLE001 - keep the worker alive
            log.exception("collection run crashed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true")
    g.add_argument("--loop", type=float, metavar="SECONDS")
    ap.add_argument("--grace-minutes", type=float, default=0,
                    help="with --once: also collect sources due within this many minutes (for cron schedules)")
    a = ap.parse_args()
    configure_logging()
    repo = Repository(get_database())
    results = asyncio.run(run_once(repo, a.grace_minutes)) if a.once else asyncio.run(run_loop(repo, a.loop))
    if a.once and results and all(r.get("status") == "failed" for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
