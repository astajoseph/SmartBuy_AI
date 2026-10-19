"""Price series for charts. Recorded points only (each flagged by origin); a separate endpoint returns
the daily carry-forward series with is_recorded flags."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.database.repositories import Repository
from app.dependencies import get_repo
from app.services.price_service import chart_series

router = APIRouter(prefix="/api/products", tags=["prices"])


@router.get("/{product_id}/prices", summary="Recorded price points (selling and offer price)")
def prices(product_id: str, days: int | None = Query(None, ge=1, le=3650), repo: Repository = Depends(get_repo)) -> dict:
    if not repo.get_product(product_id):
        raise HTTPException(404, "Product not found")
    return {"product_id": product_id, "series": chart_series(repo, product_id, days),
            "legend": {"historical": "price recorded by the price-history source (its original date)",
                       "observation": "price seen by our collector at observed_at"}}


@router.get("/{product_id}/daily", summary="One row per IST day; carried-forward days flagged is_recorded=false")
def daily(product_id: str, price_type: str = Query("selling_price", pattern="^(selling_price|offer_price)$"),
          repo: Repository = Depends(get_repo)) -> dict:
    if not repo.get_product(product_id):
        raise HTTPException(404, "Product not found")
    rows = repo.daily_prices(product_id, price_type)
    return {"product_id": product_id, "price_type": price_type,
            "rows": [{"date": r["date_ist"].isoformat(), "price": float(r["price"]), "is_recorded": r["is_recorded"],
                      "days_since_recorded": r["days_since_recorded"]} for r in rows],
            "note": "Rows with is_recorded=false repeat the last recorded price; they are not observations."}
