"""Vendor/EMI comparison and review analysis. These endpoints are the ONLY public routes that can cause
vendor pages to be fetched, and only when the cache is missing/expired or refresh=true. Rate limited."""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.database.repositories import Repository
from app.dependencies import get_repo, vendor_rate_limit
from app.services import emi_service, review_service, vendor_service

router = APIRouter(tags=["vendors"])


@router.get("/api/products/{product_id}/vendor-comparison", dependencies=[Depends(vendor_rate_limit)],
            summary="Compare vendors and EMI plans (fetches vendor pages on demand, cache first)")
async def vendor_comparison(product_id: str,
                            objective: str = Query("lowest_total_cost", pattern="^(lowest_total_cost|lowest_monthly_payment|shortest_tenure|lowest_interest|balanced)$"),
                            max_monthly_emi: Decimal | None = Query(None, gt=0),
                            preferred_tenure: int | None = Query(None, ge=1, le=60),
                            banks: str | None = Query(None, max_length=300, description="comma-separated bank names you hold cards with"),
                            refresh: bool = False, repo: Repository = Depends(get_repo)) -> dict:
    if not repo.get_product(product_id):
        raise HTTPException(404, "Product not found")
    vds = await vendor_service.get_vendor_data(repo, product_id, ("emi",), force_refresh=refresh)
    if not vds:
        return {"product_id": product_id, "vendors": [], "message": "No vendor URLs are registered for this product yet."}
    out = emi_service.build_comparison(vds, objective=objective, max_monthly=max_monthly_emi,  # type: ignore[arg-type]
                                       preferred_tenure=preferred_tenure,
                                       user_banks=[b.strip() for b in (banks or "").split(",") if b.strip()])
    return {"product_id": product_id, **out}


@router.get("/api/products/{product_id}/reviews", dependencies=[Depends(vendor_rate_limit)],
            summary="Product and seller review analysis per vendor (on demand, cache first)")
async def reviews(product_id: str, refresh: bool = False, repo: Repository = Depends(get_repo)) -> dict:
    if not repo.get_product(product_id):
        raise HTTPException(404, "Product not found")
    vds = await vendor_service.get_vendor_data(repo, product_id, ("reviews",), force_refresh=refresh)
    return {"product_id": product_id,
            "vendors": [review_service.summarise(v.reviews, v.vendor_source.get("vendor_name") or v.vendor_source["vendor_id"])
                        | {"status": v.status, "freshness": v.freshness, "message": v.message} for v in vds],
            "message": None if vds else "No vendor URLs are registered for this product yet."}


@router.get("/api/products/{product_id}/sellers", summary="Marketplace sellers found by discovery (never fetches)")
def sellers(product_id: str, include_inactive: bool = False, repo: Repository = Depends(get_repo)) -> dict:
    if not repo.get_product(product_id):
        raise HTTPException(404, "Product not found")
    rows = repo.list_sellers(product_id, include_inactive=include_inactive)
    discovered = [v for v in repo.list_vendor_sources(product_id) if v["origin"] != "registry"]
    return {"product_id": product_id, "sellers": rows,
            "discovered_sites": [{k: v[k] for k in ("id", "vendor_name", "source_url", "origin", "first_seen_at",
                                                     "last_checked_at", "last_in_stock_at")} for v in discovered],
            "last_checked_at": max((r["last_seen_at"] for r in rows), default=None)}


@router.get("/api/vendors/{vendor_id}/offers", summary="Cached EMI offers for a vendor (never fetches)")
def vendor_offers(vendor_id: str, repo: Repository = Depends(get_repo)) -> list[dict]:
    return repo._all(  # noqa: SLF001
        """select o.*, p.name as product_name from emi_offers o join products p on p.id = o.product_id
           where o.vendor_id = %s and o.expires_at > now()
             and (o.offer_valid_until is null or o.offer_valid_until > now())
           order by o.fetched_at desc limit 200""", (vendor_id,))
