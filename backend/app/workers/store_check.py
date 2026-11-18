"""Daily store check: fetch every active store page of every active product and record its price.

    python -m app.workers.store_check                     # all products; skips pages checked in the last 20 h
    python -m app.workers.store_check --product <id>      # one product
    python -m app.workers.store_check --min-age-hours 0   # check everything now

Each check is stored as a vendor snapshot and a point in vendor_price_points, which is what the product's current
price (price_service.best_store_now), the per-store chart lines and the store comparison read. Since price-history
sites are read only once per product (settings.price_history_mode = "once"), this job is what keeps prices current.

Amazon and Myntra block data-centre addresses (GitHub Actions), so this runs from the owner's PC via Windows Task
Scheduler: scripts/run_daily_local.ps1. Blocked pages (CAPTCHA / Access Denied) are recorded as failed, never bypassed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from app.database.client import get_database
from app.database.repositories import Repository
from app.logging_config import configure_logging
from app.services import vendor_service

log = logging.getLogger("store_check")


async def check_all(repo: Repository, *, product_id: str | None = None, min_age_hours: float = 20,
                    now: datetime | None = None, deps: vendor_service.VendorDeps | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    products = [p for p in repo.list_products() if p.get("is_active", True) and (product_id is None or p["id"] == product_id)]
    out = {"products": len(products), "checked": 0, "ok": 0, "failed": 0, "skipped_recent": 0, "results": []}
    for p in products:
        out.setdefault("continued", 0)
        out["continued"] += vendor_service.backfill_continuation(repo, p["id"])
        for vs in repo.list_vendor_sources(p["id"]):
            snap = repo.latest_vendor_snapshot(vs["id"])
            last = snap.get("fetched_at") if snap else None
            if last and min_age_hours > 0 and now - last < timedelta(hours=min_age_hours):
                out["skipped_recent"] += 1
                continue
            out["checked"] += 1
            try:
                ok_snap, error = await vendor_service.refresh_vendor_source(repo, vs, deps)
            except Exception as e:                     # one bad page must not stop the run
                ok_snap, error = None, f"{type(e).__name__}: {e}"
            if ok_snap:
                out["ok"] += 1
            else:
                out["failed"] += 1
            out["results"].append({"product": p["id"], "store": vs.get("vendor_name"), "ok": bool(ok_snap),
                                   "error": (error or "")[:200] or None})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--product", help="only this product_id")
    ap.add_argument("--min-age-hours", type=float, default=20, help="skip pages checked more recently (default 20)")
    a = ap.parse_args()
    configure_logging()
    res = asyncio.run(check_all(Repository(get_database()), product_id=a.product, min_age_hours=a.min_age_hours))
    log.info("store check finished: %s", json.dumps({k: v for k, v in res.items() if k != "results"}))
    for r in res["results"]:
        log.info("%s | %s | %s", r["product"], r["store"], "ok" if r["ok"] else f"failed: {r['error']}")
    if res["checked"] and res["ok"] == 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
