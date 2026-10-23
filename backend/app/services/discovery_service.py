"""Seller discovery: who sells each product right now, and who has stopped.

Run by app.workers.discovery (GitHub Actions, every 6 hours). Per active product:

  1. Marketplace sellers - for every registered vendor page whose adapter can list sellers (Amazon, Myntra),
     read all sellers and their offers; store them in vendor_sellers / vendor_seller_offers.
  2. Discovered websites - re-check every active vendor source that discovery added (origin web_search /
     admin_approved) through vendor_service, which also refreshes its cached snapshot.
  3. Web search - when BRAVE_SEARCH_API_KEY is set (and this month's BRAVE_MONTHLY_QUERY_LIMIT is not used up),
     search for the product, verify each new page with
     product_match and: strong match -> added as a vendor source; partial -> 'pending' for an admin;
     none -> 'rejected' (not fetched again for discovery_recheck_rejected_days).
  4. Lifecycle - anything discovered that has not been in stock for SELLER_INACTIVE_DAYS, and was checked again
     after it was last in stock (so a machine that was switched off removes nothing), is deactivated
     (kept with its history, re-activated when it is seen in stock again). Registry sources from
     config/products.yaml are never deactivated automatically.

Search results are untrusted: only http(s) shop pages pass the filters, and they are fetched through
SafeFetcher with an allowlist of just that host (robots.txt honoured, private addresses refused).
"""
from __future__ import annotations

import logging
import re
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from app.config import get_settings
from app.database.repositories import LOCK_NAMESPACE_DISCOVERY, Repository
from app.extraction import image_text
from app.extraction.base import ExtractionContext, ExtractionResult
from app.extraction.browser import BrowserRenderer
from app.extraction.registry import resolve_adapter
from app.security.url_validation import SafeFetcher, UnsafeURLError, host_matches_allowlist, validate_url_syntax
from app.services import vendor_service
from app.services.product_match import match_product, sizes
from app.services.web_search import BraveSearch, SearchError, queries_for, search_client

log = logging.getLogger(__name__)

# Never shops: search engines, social networks, video, forums, encyclopaedias and price-tracker sites
# (price trackers are price-history sources, registered separately).
NON_SHOP_HOSTS = {
    "google.com", "google.co.in", "bing.com", "duckduckgo.com", "youtube.com", "youtu.be", "facebook.com",
    "instagram.com", "x.com", "twitter.com", "threads.net", "linkedin.com", "pinterest.com", "pinterest.in",
    "reddit.com", "quora.com", "wikipedia.org", "medium.com", "blogspot.com", "wordpress.com", "tiktok.com",
    "pricehistory.app", "pricehistory.in", "pricebefore.com", "buyhatke.com", "smartprix.com", "mysmartprice.com",
    "pricee.com", "91mobiles.com", "keepa.com", "camelcamelcamel.com", "olay.com", "olay.co.in",
}
OUT_OF_STOCK = {"OutOfStock", "SoldOut", "Discontinued", "PreOrder", "BackOrder"}
PRICE_LOW_FACTOR = Decimal("0.6")      # below 60% of the lowest price ever seen is suspicious (fake / wrong item)
PRICE_HIGH_FACTOR = Decimal("1.5")     # above 150% of the MRP is a different pack or a reseller mark-up


@dataclass
class DiscoveryDeps:
    fetcher_factory: Callable[[set[str]], SafeFetcher] = lambda hosts: SafeFetcher(hosts)
    browser_factory: Callable[[], BrowserRenderer | None] = lambda: BrowserRenderer()
    search_factory: Callable[[], BraveSearch | None] = search_client   # returns None when web search is off
    marketplace: bool = True        # False = skip marketplace seller lists (cloud runs: Amazon/Myntra block data centres)
    image_reader: Callable[[bytes], str | None] = image_text.read_text   # OCR for product images


@dataclass
class ProductReport:
    product_id: str
    marketplace_sellers: list[dict] = field(default_factory=list)
    discovered_sources: list[dict] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    status: str = "success"

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("product_id", "status", "marketplace_sellers", "discovered_sources",
                                              "candidates", "errors")}


def in_stock(availability: str | None, price) -> bool:
    return price is not None and (availability or "InStock") not in OUT_OF_STOCK


def clean_url(url: str) -> str:
    """Drop fragments and tracking parameters so the same page is recognised again."""
    p = urlsplit(url.strip())
    q = "&".join(kv for kv in p.query.split("&")
                 if kv and not re.match(r"(utm_[a-z]+|gclid|fbclid|ref|ref_|srsltid|_pos|_sid|_ss|tag)=", kv, re.I))
    return urlunsplit((p.scheme.lower(), (p.hostname or "").lower(), p.path or "/", q, ""))


def vendor_id_for(host: str) -> str:
    vid = re.sub(r"[^a-z0-9]+", "-", re.sub(r"^www\d?\.", "", host.lower())).strip("-")[:64]
    return vid if len(vid) >= 3 else f"site-{vid}"


def price_range(repo: Repository, product_id: str) -> tuple[Decimal, Decimal] | None:
    stats = repo.latest_source_stats(product_id)
    pts = [p["price"] for p in repo.price_points(product_id, "selling_price",
                                                 since=datetime.now(timezone.utc) - timedelta(days=365))]
    lows = [v for v in [stats.get("lowest", {}).get("value"), min(pts, default=None)] if v is not None]
    highs = [v for v in [stats.get("mrp", {}).get("value"), stats.get("highest", {}).get("value"), max(pts, default=None)]
             if v is not None]
    if not lows or not highs:
        return None
    return (min(lows) * PRICE_LOW_FACTOR).quantize(Decimal("1")), (max(highs) * PRICE_HIGH_FACTOR).quantize(Decimal("1"))


async def discover_product(repo: Repository, product: dict, *, deps: DiscoveryDeps | None = None) -> dict:
    deps = deps or DiscoveryDeps()
    with repo.db.connection() as lock_conn:
        key = zlib.crc32(product["id"].encode()) & 0x7FFFFFFF
        if not repo.try_lock(lock_conn, LOCK_NAMESPACE_DISCOVERY, key):
            return {"product_id": product["id"], "status": "skipped", "reason": "another discovery run is active"}
        job_id = repo.start_job("discovery")
        rep = ProductReport(product["id"])
        try:
            if deps.marketplace:
                await _marketplace_sellers(repo, product, deps, rep)
            await _recheck_discovered(repo, product, deps, rep)
            await _web_search(repo, product, deps, rep)
        except Exception as e:  # noqa: BLE001 - recorded; one product must not stop the others
            log.exception("discovery failed for %s", product["id"])
            rep.errors.append(f"{e.__class__.__name__}: {e}")
            rep.status = "failed"
        finally:
            repo.unlock(lock_conn, LOCK_NAMESPACE_DISCOVERY, key)
    if rep.status != "failed" and rep.errors:
        rep.status = "partial"
    found = sum(m.get("sellers", 0) for m in rep.marketplace_sellers) + len(rep.candidates)
    repo.finish_job(job_id, rep.status, extracted=found,
                    inserted=sum(m.get("offers_stored", 0) for m in rep.marketplace_sellers),
                    error="; ".join(rep.errors)[:2000] or None, details=rep.as_dict())
    return rep.as_dict()


async def _marketplace_sellers(repo: Repository, product: dict, deps: DiscoveryDeps, rep: ProductReport) -> None:
    for vs in repo.list_vendor_sources(product["id"]):
        adapter = resolve_adapter(vs["adapter_name"], vs["source_url"], "vendor")
        if not adapter.supports_sellers:
            continue
        entry = {"vendor_source_id": vs["id"], "vendor": vs["vendor_name"]}
        try:
            ctx = ExtractionContext(fetcher=deps.fetcher_factory(repo.registered_hosts()), browser=deps.browser_factory())
            result = await adapter.extract_sellers(vs["source_url"], ctx)
        except Exception as e:  # noqa: BLE001
            rep.errors.append(f"{vs['vendor_name']} sellers: {e.__class__.__name__}: {e}")
            rep.marketplace_sellers.append({**entry, "error": f"{e.__class__.__name__}: {e}"})
            continue
        if result.blocked or not result.sellers:
            msg = "; ".join(result.warnings) or "no sellers found"
            rep.errors.append(f"{vs['vendor_name']} sellers: {msg}")
            rep.marketplace_sellers.append({**entry, "error": msg})
            continue
        stored = 0
        for s in result.sellers:
            sid = repo.upsert_seller(vs["id"], product["id"], s, result.fetched_at)
            stored += repo.insert_seller_offer(sid, result.fetched_at, s)
        any_stock = any(s.get("in_stock") for s in result.sellers)
        repo.mark_vendor_source_checked(vs["id"], in_stock=any_stock, at=result.fetched_at)
        rep.marketplace_sellers.append({**entry, "sellers": len(result.sellers), "offers_stored": stored,
                                        "in_stock": sum(1 for s in result.sellers if s.get("in_stock")),
                                        "warnings": result.warnings})


async def _recheck_discovered(repo: Repository, product: dict, deps: DiscoveryDeps, rep: ProductReport) -> None:
    vdeps = vendor_service.VendorDeps(fetcher_factory=deps.fetcher_factory, browser_factory=deps.browser_factory)
    for vs in repo.vendor_sources_for_discovery(product["id"], origins=("web_search", "admin_approved")):
        snap, error = await vendor_service.refresh_vendor_source(repo, vs, vdeps)
        stock = snap is not None and in_stock(snap.get("availability"), snap.get("price"))
        repo.mark_vendor_source_checked(vs["id"], in_stock=stock)
        rep.discovered_sources.append({"vendor_source_id": vs["id"], "url": vs["source_url"], "in_stock": stock,
                                       "price": str(snap["price"]) if snap and snap.get("price") else None,
                                       "error": error})


def _skip_host(host: str, product_hosts: set[str]) -> str | None:
    blocked = NON_SHOP_HOSTS | get_settings().discovery_blocked_host_set
    if host_matches_allowlist(host, blocked):
        return "not a shop"
    if host_matches_allowlist(host, product_hosts):
        return "already tracked for this product"
    return None


async def _web_search(repo: Repository, product: dict, deps: DiscoveryDeps, rep: ProductReport) -> None:
    client = deps.search_factory()
    if client is None:
        return
    s = get_settings()
    product_hosts = {(v["website_domain"] or "").lower() for v in repo.list_vendor_sources(product["id"])} - {""}
    product_hosts |= {re.sub(r"^www\.", "", h) for h in product_hosts}
    hits, seen_hosts = [], set()
    for q in queries_for(product):
        if not repo.reserve_search_queries("brave", 1, s.brave_monthly_query_limit):   # counted before the call
            rep.errors.append(f"Brave monthly query limit ({s.brave_monthly_query_limit}) reached; web search paused "
                              "until next month so no paid usage occurs")
            break
        try:
            hits += await client.search(q, s.discovery_search_count)
        except SearchError as e:
            rep.errors.append(str(e))
    rng = price_range(repo, product["id"])
    checks = 0
    recheck_before = datetime.now(timezone.utc) - timedelta(days=s.discovery_recheck_rejected_days)
    for h in hits:
        try:
            validate_url_syntax(h.url)
        except UnsafeURLError:
            continue
        url = clean_url(h.url)
        host = urlsplit(url).hostname or ""
        if host in seen_hosts or _skip_host(host, product_hosts):
            continue
        seen_hosts.add(host)
        cand = repo.note_candidate(product["id"], url, host, h.query, h.title)
        existing = repo.vendor_source_by_url(product["id"], url)
        if cand["status"] in ("auto_added", "approved") and existing and existing["is_active"]:
            continue                                              # tracked and checked in _recheck_discovered
        if cand["status"] == "pending":
            continue                                              # waiting for an admin
        if cand["status"] in ("rejected", "error") and cand["last_checked_at"] and cand["last_checked_at"] > recheck_before:
            continue
        if checks >= s.discovery_max_checks_per_product:
            break
        checks += 1
        rep.candidates.append(await _verify(repo, product, cand, deps, rng, h.title, existing))


async def _verify(repo: Repository, product: dict, cand: dict, deps: DiscoveryDeps, rng, title: str | None,
                  existing: dict | None) -> dict:
    url, host = cand["url"], cand["host"]
    now = datetime.now(timezone.utc)
    adapter = resolve_adapter("auto", url, "vendor")
    try:
        ctx = ExtractionContext(fetcher=deps.fetcher_factory({host}), browser=None)
        result = await adapter.extract(url, ctx)
    except Exception as e:  # noqa: BLE001 - robots refusal, 404, timeout ...
        msg = f"{e.__class__.__name__}: {e}"[:500]
        repo.update_candidate(cand["id"], status="error", error_message=msg, last_checked_at=now)
        return {"url": url, "status": "error", "error": msg}

    m = match_product(product, result, price_range=rng, extra_text=title or "")
    img = None
    if m.level == "partial" and m.evidence.get("sizes_expected") and not m.evidence.get("sizes_on_page"):
        img = await _read_images(result, host, deps)        # the title has no pack size: look at the product photos
        if img and img["text"]:
            m = match_product(product, result, price_range=rng, extra_text=title or "", image_text=img["text"])
    price = result.vendor.get("price")
    stock = in_stock(result.vendor.get("availability"), price)
    evidence = {**m.evidence, "reasons": m.reasons, "availability": result.vendor.get("availability"),
                "adapter": adapter.name, "warnings": result.warnings[:5]}
    if img:
        evidence["images"] = {"read": img["read"], "text_excerpt": (img["text"] or "")[:300] or None}
    fields = {"match_level": m.level, "match_evidence": evidence, "observed_price": price, "last_checked_at": now,
              "error_message": None}
    if m.level == "strong" and stock:
        vid = vendor_id_for(host)
        repo.upsert_vendor(vid, re.sub(r"^www\.", "", host), host)
        origin = existing["origin"] if existing and existing["origin"] != "registry" else "web_search"
        vsid = repo.add_discovered_vendor_source(product["id"], vid, url, adapter_name=adapter.name, origin=origin,
                                                 in_stock_at=now if stock else None)
        status = "approved" if cand["status"] == "approved" else "auto_added"
        repo.update_candidate(cand["id"], status=status, vendor_source_id=vsid, decided_at=now, decided_by="discovery",
                              **fields)
        log.info("discovery added %s for %s (%s)", url, product["id"], "; ".join(m.reasons))
        return {"url": url, "status": status, "vendor_source_id": vsid, "reasons": m.reasons, "price": str(price)}
    if m.level == "strong" and existing:
        status = cand["status"]          # an earlier added page, still out of stock: stays deactivated, evidence updated
    else:
        status = "pending" if m.level in ("strong", "partial") else "rejected"   # new page, unclear or out of stock
    repo.update_candidate(cand["id"], status=status, **fields)
    return {"url": url, "status": status, "reasons": m.reasons, "price": str(price) if price else None}


async def _read_images(result: ExtractionResult, page_host: str, deps: DiscoveryDeps) -> dict | None:
    """OCR the first product images the page itself lists (structured data). Each image is fetched through
    SafeFetcher (public addresses only, size limit, robots.txt); the image host is allowed for that fetch only."""
    s = get_settings()
    urls = (result.product.get("images") or [])[: s.discovery_max_images]
    if not s.discovery_read_images or not urls:
        return None
    texts, read = [], 0
    for url in urls:
        try:
            img_host = urlsplit(url).hostname or ""
            res = await deps.fetcher_factory({page_host, img_host}).fetch(url)
        except Exception as e:  # noqa: BLE001
            log.info("product image %s not fetched: %s", url[:120], e.__class__.__name__)
            continue
        ctype = res.headers.get("content-type", "")
        if ctype and not ctype.startswith("image/"):
            continue
        text = deps.image_reader(res.content)
        read += 1
        if text:
            texts.append(text)
            if sizes(text):
                break                                     # a pack size was found; no need to read more
    return {"read": read, "text": " | ".join(texts) or None}


def approve_candidate(repo: Repository, candidate_id: int, decided_by: str) -> dict:
    """Admin approval of a pending (or rejected) candidate: becomes an active vendor source."""
    c = repo.get_candidate(candidate_id)
    if not c:
        raise LookupError("candidate not found")
    adapter = resolve_adapter("auto", c["url"], "vendor")
    vid = vendor_id_for(c["host"])
    repo.upsert_vendor(vid, re.sub(r"^www\.", "", c["host"]), c["host"])
    in_stock_at = c["last_checked_at"] if c["observed_price"] is not None else None
    vsid = repo.add_discovered_vendor_source(c["product_id"], vid, c["url"], adapter_name=adapter.name,
                                             origin="admin_approved", in_stock_at=in_stock_at)
    return repo.update_candidate(candidate_id, status="approved", vendor_source_id=vsid,
                                 decided_at=datetime.now(timezone.utc), decided_by=decided_by)


def reject_candidate(repo: Repository, candidate_id: int, decided_by: str) -> dict:
    c = repo.get_candidate(candidate_id)
    if not c:
        raise LookupError("candidate not found")
    if c["vendor_source_id"]:
        with repo.db.connection() as conn:
            conn.execute("""update product_vendor_sources set is_active = false, deactivated_at = now(),
                            deactivation_reason = 'rejected by admin' where id = %s and origin <> 'registry'""",
                         (c["vendor_source_id"],))
    return repo.update_candidate(candidate_id, status="rejected", decided_at=datetime.now(timezone.utc),
                                 decided_by=decided_by)


async def run_discovery(repo: Repository, *, product_id: str | None = None, deps: DiscoveryDeps | None = None) -> dict:
    products = [p for p in repo.list_products() if product_id is None or p["id"] == product_id]
    results = [await discover_product(repo, p, deps=deps) for p in products]
    gone = repo.deactivate_not_selling(get_settings().seller_inactive_days)
    return {"products": results,
            "deactivated": {"sellers": len(gone["sellers"]), "sources": [r["source_url"] for r in gone["sources"]]}}
