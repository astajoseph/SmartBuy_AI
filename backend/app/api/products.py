"""Product catalogue, history statistics, forecasts, seasonality and user targets.
None of these endpoints access vendor websites."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.database.repositories import Repository
from app.dependencies import get_repo
from app.forecasting.forecasting import HORIZONS, build_forecast
from app.forecasting.seasonality import analyse_events, calendar_patterns, upcoming_events
from app.security.permissions import CurrentUser, require_user
from app.services.event_service import events_for_product
from app.services.price_service import history_summary, points_frame

router = APIRouter(prefix="/api/products", tags=["products"])


def _product_or_404(repo: Repository, product_id: str) -> dict:
    p = repo.get_product(product_id)
    if not p:
        raise HTTPException(404, "Product not found")
    return p


@router.get("", summary="Tracked products with current price and tracking status")
def list_products(repo: Repository = Depends(get_repo)) -> list[dict]:
    out = []
    health = {h["product_id"]: h for h in repo.source_health()}
    for p in repo.list_products():
        s = history_summary(repo, p["id"])
        h = health.get(p["id"], {})
        out.append({
            "id": p["id"], "name": p["name"], "brand": p["brand"], "category": p["category"], "currency": p["currency"],
            "image_url": p["image_url"], "current": s.get("current"), "current_offer_price": s.get("current_offer_price"),
            "changes": s.get("changes"), "historical_low": s.get("historical_low"),
            "source_stated_stats": s.get("source_stated_stats"),
            "data_quality": s.get("data_quality"), "last_collection_at": p["last_collection_at"],
            "tracking_status": h.get("health", "no_source"), "last_error": h.get("last_error"),
        })
    return out


@router.get("/{product_id}", summary="Product details, registered sources and vendors")
def get_product(product_id: str, repo: Repository = Depends(get_repo)) -> dict:
    p = _product_or_404(repo, product_id)
    return {**p, "price_sources": [{k: s[k] for k in ("id", "source_url", "adapter_name", "collection_interval_minutes",
                                                     "is_active", "last_success_at", "last_attempt_at", "last_error")}
                                   for s in repo.list_price_sources(product_id)],
            "vendors": [{k: v[k] for k in ("id", "vendor_id", "vendor_name", "source_url", "adapter_name")}
                        for v in repo.list_vendor_sources(product_id)]}


@router.get("/{product_id}/history", summary="Historical statistics, period changes and data quality")
def history(product_id: str, repo: Repository = Depends(get_repo)) -> dict:
    _product_or_404(repo, product_id)
    return history_summary(repo, product_id)


@router.get("/{product_id}/forecast", summary="Baseline forecasts for 7/14/30 days (computed now, not stored)")
def forecast(product_id: str, target_price: float | None = Query(None, gt=0), repo: Repository = Depends(get_repo)) -> dict:
    _product_or_404(repo, product_id)
    df = points_frame(repo.price_points(product_id, "selling_price"))
    out = []
    for h in HORIZONS:
        f = build_forecast(df, h, target_price)
        f.pop("data_quality", None)
        bt = (f.get("validation_metrics") or {}).get("backtest") or {}
        f["validation_summary"] = {"chosen": bt.get("chosen"), "reason": bt.get("reason"), "methods": bt.get("methods"),
                                   "interval_holdout_coverage": bt.get("interval_holdout_coverage"),
                                   "probability_validation": (f.get("validation_metrics") or {}).get("probability_brier_5pct_drop")}
        f.pop("validation_metrics", None)
        out.append(f)
    return {"product_id": product_id, "forecasts": out,
            "evaluated_outcomes": repo.outcome_summary(product_id),
            "note": "Forecast horizons are not promises that the price will change within those periods."}


@router.get("/{product_id}/seasonality", summary="Indian sale-event windows and calendar patterns")
def seasonality(product_id: str, repo: Repository = Depends(get_repo)) -> dict:
    p = _product_or_404(repo, product_id)
    df = points_frame(repo.price_points(product_id, "selling_price"))
    obs = repo.latest_observation(product_id)
    events = events_for_product(repo, p, obs["seller_name"] if obs else None)
    today = datetime.now(timezone.utc).date()
    return {"product_id": product_id, "relevant_events": events,
            "event_analysis": analyse_events(df, events, today=today),
            "calendar_patterns": calendar_patterns(df), "upcoming_events": upcoming_events(events, today, 180)}


@router.get("/{product_id}/recommendations", summary="Recommendation history")
def recommendations(product_id: str, repo: Repository = Depends(get_repo)) -> list[dict]:
    _product_or_404(repo, product_id)
    return repo.list_recommendations(product_id)


class TargetIn(BaseModel):
    target_price: Decimal | None = Field(default=None, gt=0)
    max_budget: Decimal | None = Field(default=None, gt=0)
    alerts_enabled: bool = True


@router.post("/{product_id}/target-price", summary="Save the signed-in user's target price (requires Supabase auth)")
def set_target(product_id: str, body: TargetIn, user: CurrentUser = Depends(require_user),
               repo: Repository = Depends(get_repo)) -> dict:
    _product_or_404(repo, product_id)
    row = repo.upsert_target(user.id, product_id, body.target_price, body.max_budget, body.alerts_enabled)
    summary = f"Saved price alert for {product_id}" + (f" at ₹{body.target_price}" if body.target_price else "")
    repo.log_activity(user.id, "target_saved", summary + ("" if body.alerts_enabled else " (alerts off)"),
                      product_id=product_id, payload=body.model_dump(mode="json"))
    return row
