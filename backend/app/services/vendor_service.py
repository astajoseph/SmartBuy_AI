"""On-demand vendor data (price, EMI, reviews) with caching.

Vendor URLs are fetched ONLY here, ONLY when a caller explicitly needs vendor data (EMI / vendor
comparison / review analysis), and ONLY when the cached snapshot for the needed kinds is missing
or expired (or the user explicitly asks for a refresh). Dashboard and price-history endpoints
never call this module. The one scheduled caller is seller discovery (services/discovery_service.py),
which re-checks the vendor pages IT added (refresh_vendor_source) to see whether they still sell.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable

from app.database.repositories import LOCK_NAMESPACE_VENDOR, Repository
from app.extraction.base import ExtractionContext, ExtractionResult
from app.extraction.browser import BrowserRenderer
from app.extraction.registry import resolve_adapter
from app.finance.emi_calculator import EmiTerms, calculate
from app.security.url_validation import SafeFetcher
from app.services import cache_service
from app.services.review_service import score_review

log = logging.getLogger(__name__)
NEEDS = {"emi": ("vendor_price", "emi"), "reviews": ("review_aggregate", "review_samples"),
         "price": ("vendor_price",)}


@dataclass
class VendorDeps:
    fetcher_factory: Callable[[set[str]], SafeFetcher] = lambda hosts: SafeFetcher(hosts)
    browser_factory: Callable[[], BrowserRenderer | None] = lambda: BrowserRenderer()


@dataclass
class VendorData:
    vendor_source: dict
    snapshot: dict | None
    emi_offers: list[dict] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)
    bank_offers: list[dict] = field(default_factory=list)
    freshness: dict | None = None
    status: str = "unavailable"          # live | cache | stale_cache | unavailable
    message: str | None = None


def kinds_for(needs: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    for n in needs:
        out += [k for k in NEEDS[n] if k not in out]
    return tuple(out)


async def get_vendor_data(repo: Repository, product_id: str, needs: tuple[str, ...], *, force_refresh: bool = False,
                          deps: VendorDeps | None = None, now: datetime | None = None) -> list[VendorData]:
    deps = deps or VendorDeps()
    kinds = kinds_for(needs)
    out = []
    for vs in repo.list_vendor_sources(product_id):
        out.append(await _one_vendor(repo, vs, kinds, force_refresh, deps, now or datetime.now(timezone.utc)))
    return out


async def refresh_vendor_source(repo: Repository, vs: dict, deps: VendorDeps | None = None) -> tuple[dict | None, str | None]:
    """Fetch one vendor page now and cache its snapshot. Returns (snapshot, None) or (None, error)."""
    snap_id, error = await _fetch_and_store(repo, vs, deps or VendorDeps())
    return (repo.latest_ok_vendor_snapshot(vs["id"]) if snap_id else None), error


async def _one_vendor(repo: Repository, vs: dict, kinds: tuple[str, ...], force: bool, deps: VendorDeps,
                      now: datetime) -> VendorData:
    latest = repo.latest_vendor_snapshot(vs["id"])
    cached_ok = repo.latest_ok_vendor_snapshot(vs["id"])
    if not force and cache_service.is_fresh(cached_ok, kinds, now):
        return _from_snapshot(repo, vs, cached_ok, "cache", now)
    recently_failed = (latest is not None and latest["fetch_status"] in ("failed", "blocked")
                       and latest["expires_at"] > now)
    if recently_failed and not force:
        return _fallback(repo, vs, cached_ok, kinds, now, latest.get("error_message") or "recent fetch failed")

    snap_id, error = await _fetch_and_store(repo, vs, deps)
    if snap_id is None:
        return _fallback(repo, vs, cached_ok, kinds, now, error)
    fresh = repo.latest_ok_vendor_snapshot(vs["id"])
    return _from_snapshot(repo, vs, fresh, "live", now)


def _from_snapshot(repo: Repository, vs: dict, snap: dict, origin: str, now: datetime) -> VendorData:
    offers = [o for o in repo.emi_offers_at(vs["id"], snap["fetched_at"])]
    valid, expired = [], 0
    for o in offers:
        if o["offer_valid_until"] is not None and o["offer_valid_until"] <= now:
            expired += 1
            continue
        valid.append(o)
    vd = VendorData(vendor_source=vs, snapshot=snap, emi_offers=valid,
                    reviews=repo.reviews_at(vs["id"], snap["fetched_at"]),
                    bank_offers=repo.bank_offers_at(vs["id"], snap["fetched_at"]),
                    freshness=cache_service.freshness_label(snap["fetched_at"], origin, now), status=origin)
    if expired:
        vd.message = f"{expired} EMI offer(s) past their stated validity date were excluded."
    return vd


def _fallback(repo: Repository, vs: dict, cached_ok: dict | None, kinds, now: datetime, error: str | None) -> VendorData:
    if cache_service.usable_when_stale(cached_ok, kinds, now):
        vd = _from_snapshot(repo, vs, cached_ok, "stale_cache", now)
        vd.message = f"Live fetch failed ({error}). " + (vd.message or "")
        return vd
    return VendorData(vendor_source=vs, snapshot=None, status="unavailable",
                      message=f"Vendor data unavailable: {error or 'not fetched'}. No usable cached data.")


async def _fetch_and_store(repo: Repository, vs: dict, deps: VendorDeps) -> tuple[int | None, str | None]:
    with repo.db.connection() as lock_conn:
        if not repo.try_lock(lock_conn, LOCK_NAMESPACE_VENDOR, vs["id"]):
            return None, "another request is already fetching this vendor"
        job_id = repo.start_job("vendor_on_demand", vendor_source_id=vs["id"])
        try:
            adapter = resolve_adapter(vs["adapter_name"], vs["source_url"], "vendor")
            ctx = ExtractionContext(fetcher=deps.fetcher_factory(repo.registered_hosts()),
                                    browser=deps.browser_factory(), config={})
            result = await adapter.extract(vs["source_url"], ctx)
        except Exception as e:  # noqa: BLE001
            msg = f"{e.__class__.__name__}: {e}"
            now = datetime.now(timezone.utc)
            repo.insert_vendor_snapshot({"product_id": vs["product_id"], "vendor_id": vs["vendor_id"],
                                         "vendor_source_id": vs["id"], "fetched_at": now,
                                         "expires_at": now + timedelta(minutes=cache_service.NEGATIVE_CACHE_MINUTES),
                                         "fetch_status": "failed", "error_message": msg[:1000]})
            repo.finish_job(job_id, "failed", error=msg)
            return None, msg
        finally:
            repo.unlock(lock_conn, LOCK_NAMESPACE_VENDOR, vs["id"])

    return _store_result(repo, vs, result, job_id)


def _store_result(repo: Repository, vs: dict, result: ExtractionResult, job_id: int) -> tuple[int | None, str | None]:
    fetched = result.fetched_at
    report = result.report()
    if result.blocked:
        repo.insert_vendor_snapshot({"product_id": vs["product_id"], "vendor_id": vs["vendor_id"],
                                     "vendor_source_id": vs["id"], "fetched_at": fetched,
                                     "expires_at": fetched + timedelta(minutes=cache_service.NEGATIVE_CACHE_MINUTES),
                                     "fetch_status": "blocked", "error_message": "; ".join(result.warnings)[:1000],
                                     "extraction_report": report})
        repo.finish_job(job_id, "failed", error="blocked by vendor", details=report)
        return None, "vendor blocked automated access"

    v = result.vendor
    report["expires"] = cache_service.expiry_map(fetched)
    status = "ok" if not result.missing_fields() else "partial"
    snap_id = repo.insert_vendor_snapshot({
        "product_id": vs["product_id"], "vendor_id": vs["vendor_id"], "vendor_source_id": vs["id"],
        "fetched_at": fetched, "expires_at": fetched + cache_service.ttl("vendor_price"), "fetch_status": status,
        "price": v.get("price"), "mrp": v.get("mrp"), "currency": v.get("currency"), "availability": v.get("availability"),
        "seller_name": v.get("seller_name"), "delivery_text": (v.get("delivery_text") or "")[:300] or None,
        "return_policy": v.get("return_policy"), "emi_status": v.get("emi_status") or ("available" if result.emi_plans else "unknown"),
        "details": {k: v.get(k) for k in ("stock_units", "discount_label", "discount_ends_at", "cod_available", "product_expiry")
                    if v.get(k) is not None},
        "extraction_report": report})
    repo.insert_bank_offers([{**o, "product_id": vs["product_id"], "vendor_id": vs["vendor_id"], "vendor_source_id": vs["id"],
                              "fetched_at": fetched, "expires_at": fetched + cache_service.ttl("emi")}
                             for o in v.get("bank_offers") or []])

    facts = v.get("emi_page_facts") or {}
    page_fee = Decimal(facts["processing_fee"]) if facts.get("processing_fee") else None
    offers = []
    for p in result.emi_plans:
        assumptions = [a for a in [p.get("assumption")] if a]
        fee = page_fee
        if page_fee is not None:
            assumptions.append("Processing fee taken from a single fee mentioned elsewhere on the page.")
        calc = calculate(EmiTerms(emi_price=v["price"], tenure_months=p.get("tenure_months"), rate_pct=p.get("interest_rate"),
                                  rate_type=p.get("interest_rate_type") or "unknown", advertised_emi=p.get("monthly_emi"),
                                  processing_fee=fee, no_cost_claim=p.get("no_cost_emi_claim", False),
                                  upfront_price=v["price"])) if v.get("price") else None
        offers.append({
            "product_id": vs["product_id"], "vendor_id": vs["vendor_id"], "vendor_source_id": vs["id"],
            "product_price": v.get("price"), "principal_amount": v.get("price"),
            "down_payment": None, "financed_amount": calc.financed_amount if calc else None,
            "interest_rate": p.get("interest_rate"), "interest_rate_type": p.get("interest_rate_type") or "unknown",
            "tenure_months": p.get("tenure_months"), "monthly_emi": p.get("monthly_emi"), "processing_fee": fee,
            "total_interest": calc.total_interest if calc else None,
            "total_repayment": calc.total_instalments if calc else None,
            "total_cost": calc.total_cost if calc and calc.status == "complete" else None,
            "no_cost_emi_claim": p.get("no_cost_emi_claim", False), "bank_name": p.get("bank_name"),
            "card_type": p.get("card_type"), "raw_text": p.get("raw_text"), "assumptions": assumptions,
            "source_url": vs["source_url"], "fetched_at": fetched, "expires_at": fetched + cache_service.ttl("emi"),
            "verification_status": "parsed_heuristically",
        })
    repo.insert_emi_offers(offers)

    reviews = []
    for agg in result.review_aggregates:
        reviews.append({"product_id": vs["product_id"], "vendor_id": vs["vendor_id"], "vendor_source_id": vs["id"],
                        "review_kind": agg["kind"], "review_source_url": vs["source_url"],
                        "rating": _dec(agg.get("rating")), "rating_scale": _dec(agg.get("best")),
                        "review_count": _int(agg.get("count")), "fetched_at": fetched,
                        "rating_distribution": {"unit": agg.get("distribution_unit") or "count",
                                                "stars": {str(k): v for k, v in agg["distribution"].items()}}
                                               if agg.get("distribution") else None,
                        "expires_at": fetched + cache_service.ttl("review_aggregate")})
    for r in result.reviews:
        sentiment, aspects = score_review(r.get("text") or "")
        reviews.append({"product_id": vs["product_id"], "vendor_id": vs["vendor_id"], "vendor_source_id": vs["id"],
                        "review_kind": r.get("kind", "product_review"), "review_source_url": vs["source_url"],
                        "rating": _dec(r.get("rating")), "rating_scale": _dec(r.get("best")),
                        "review_text": (r.get("text") or "")[:4000] or None, "review_date": _date(r.get("date")),
                        "verified_purchase": r.get("verified"), "external_review_id": r.get("id"),
                        "reviewer_name": (r.get("reviewer") or "")[:120] or None, "review_title": (r.get("title") or "")[:300] or None,
                        "helpful_votes": _int(r.get("helpful")), "unhelpful_votes": _int(r.get("unhelpful")),
                        "has_media": r.get("media"), "variant_text": (r.get("variant") or "")[:200] or None,
                        "incentivized": r.get("incentivized"),
                        "sentiment": sentiment, "aspect_labels": aspects, "fetched_at": fetched,
                        "expires_at": fetched + cache_service.ttl("review_samples")})
    repo.insert_reviews(reviews)
    repo.finish_job(job_id, "success" if status == "ok" else "partial",
                    extracted=len(offers) + len(reviews) + (1 if v.get("price") else 0),
                    inserted=len(offers) + len(reviews) + 1, details=report)
    return snap_id, None


def _dec(v) -> Decimal | None:
    try:
        return None if v in (None, "") else Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None


def _int(v) -> int | None:
    try:
        return None if v in (None, "") else int(str(v).replace(",", ""))
    except ValueError:
        return None


def _date(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)[:10]).date()
    except ValueError:
        return None
