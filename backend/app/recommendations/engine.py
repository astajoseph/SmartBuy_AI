"""Recommendation engine: gathers verified data, runs the deterministic policy, stores the result.

Vendor pages are only touched when the request asks for vendor/EMI or review analysis, and even then
only through vendor_service (cache first).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.database.repositories import Repository
from app.forecasting.forecasting import build_forecast, store_forecast
from app.forecasting.seasonality import analyse_events, upcoming_events
from app.recommendations.policies import POLICY_VERSION, EventSignal, PolicyInputs, decide
from app.recommendations.schemas import Recommendation, RecommendationRequest
from app.services import emi_service, review_service, vendor_service
from app.services.alert_service import notify_recommendation_change
from app.services.event_service import events_for_product
from app.services.price_service import history_summary, points_frame, to_decimal

RECOMMENDATION_VERSION = f"engine-1.0/{POLICY_VERSION}"


class ProductNotFound(LookupError):
    pass


def _horizon(deadline: int | None) -> int:
    return 14 if deadline is None else max(7, min(30, deadline))


async def recommend(repo: Repository, req: RecommendationRequest, *, user_id=None,
                    vendor_deps: vendor_service.VendorDeps | None = None, now: datetime | None = None,
                    persist: bool = True) -> Recommendation:
    now = now or datetime.now(timezone.utc)
    product = repo.get_product(req.product_id)
    if not product:
        raise ProductNotFound(req.product_id)

    df = points_frame(repo.price_points(req.product_id, "selling_price"))
    summary = history_summary(repo, req.product_id, now)
    target = req.target_price if req.target_price is not None else None
    horizon = _horizon(req.deadline_days)
    fc = build_forecast(df, horizon, target if target is not None else req.max_budget, now)
    forecast_id = store_forecast(repo, req.product_id, fc) if persist and fc["status"] == "ok" else None

    latest_obs = repo.latest_observation(req.product_id)
    retailer = latest_obs["seller_name"] if latest_obs else None
    events = events_for_product(repo, product, retailer)
    today = now.date()
    seasonal = analyse_events(df, events, today=today)
    upcoming = upcoming_events(events, today)
    by_name = {s["event_name"].lower(): s for s in seasonal["by_event"]}
    signals = []
    for e in upcoming:
        if e["days_until"] is None:
            continue
        hist = by_name.get(e["event_name"].lower())
        drops = int(hist["drop_frequency"].split("/")[0]) if hist else 0
        signals.append(EventSignal(e["event_name"], e["days_until"], e["verification_status"],
                                   hist["comparable_events"] if hist else 0, drops,
                                   hist["median_pct_change"] if hist else None))

    vendor_block, review_block, vendor_accessed, emi_ids = None, None, False, []
    all_over, spread = False, None
    if req.include_vendor_analysis or req.include_reviews:
        needs = tuple(n for n, on in (("emi", req.include_vendor_analysis), ("reviews", req.include_reviews)) if on)
        vds = await vendor_service.get_vendor_data(repo, req.product_id, needs, force_refresh=req.refresh_vendor_data,
                                                   deps=vendor_deps, now=now)
        vendor_accessed = any(v.status == "live" for v in vds)
        if req.include_vendor_analysis:
            vendor_block = emi_service.build_comparison(
                vds, objective=req.objective,
                max_monthly=Decimal(str(req.max_monthly_emi)) if req.max_monthly_emi else None,
                preferred_tenure=req.preferred_tenure_months, user_banks=req.user_banks)
            emi_ids = [o["emi_offer_id"] for o in vendor_block["ranked_options"] if o.get("emi_offer_id")]
            emi_opts = [o for o in vendor_block["ranked_options"] + [e["option"] for e in vendor_block["excluded_options"]]
                        if o["kind"] == "emi"]
            all_over = bool(req.max_monthly_emi) and bool(emi_opts) and all(
                o in [e["option"] for e in vendor_block["excluded_options"]] for o in emi_opts)
            prices = [float(r["effective_upfront_price"]) for r in vendor_block["vendors"] if r.get("effective_upfront_price")]
            if len(prices) >= 2:
                spread = (max(prices) - min(prices)) / min(prices) * 100
        if req.include_reviews:
            review_block = [review_service.summarise(v.reviews, v.vendor_source.get("vendor_name") or v.vendor_source["vendor_id"])
                            | {"freshness": v.freshness, "status": v.status, "message": v.message} for v in vds]
            if not vds:
                review_block = []

    cur = summary.get("current") or {}
    q = summary.get("data_quality", {})
    bt = (fc.get("validation_metrics") or {}).get("backtest") or {}
    prob = fc.get("probability_detail") or {}
    inputs = PolicyInputs(
        current_price=cur.get("price"), price_age_days=q.get("days_since_last_record"),
        target_price=target, max_budget=req.max_budget, deadline_days=req.deadline_days,
        stats_sufficient=bool(q.get("sufficient_for_stats")), percentile_365=summary.get("current_percentile_last_365d"),
        historical_low=(summary.get("historical_low") or {}).get("price"),
        forecast_ok=fc["status"] == "ok", forecast_horizon=horizon, forecast_point=fc.get("predicted_price"),
        forecast_lower=fc.get("lower_price_bound"), forecast_upper=fc.get("upper_price_bound"),
        interval_coverage=bt.get("interval_holdout_coverage"),
        probability=fc.get("probability_below_target") if (target or req.max_budget) else None,
        probability_band=prob.get("band"), effective_samples=prob.get("effective_samples"),
        probability_skill=prob.get("skill_demonstrated"),
        upcoming_events=signals,
        available=None if not latest_obs or not latest_obs["availability"] else latest_obs["availability"] == "InStock",
        vendor_requested=req.include_vendor_analysis, all_emi_over_limit=all_over, vendor_price_spread_pct=spread)
    d = decide(inputs)

    limitations = list(summary.get("limitations", [])) + list(fc.get("limitations", [])) + seasonal["limitations"]
    if not req.include_vendor_analysis and not req.include_reviews:
        limitations.append("Vendor pages were not checked for this recommendation (no EMI/vendor/review analysis requested).")
    sources = [{"type": "price_history", "url": s["source_url"], "last_success_at": s["last_success_at"].isoformat()
                if s["last_success_at"] else None} for s in repo.list_price_sources(req.product_id)]
    freshness = {"price": {"as_of": cur.get("as_of"), "age_days": q.get("days_since_last_record")}}
    if vendor_block:
        for r in vendor_block["vendors"]:
            sources.append({"type": "vendor", "vendor": r["vendor"], "url": r.get("source_url"), "status": r["status"]})
            freshness[f"vendor:{r['vendor']}"] = r.get("freshness") or {"origin": r["status"], "label": r.get("message")}

    rec = Recommendation(
        product_id=req.product_id, product_name=product["name"], generated_at=now, action=d.action,
        current_price=cur.get("price"), current_price_as_of=cur.get("as_of"), target_price=target or req.max_budget,
        forecast_horizon_days=horizon, probability_below_target=inputs.probability, probability_band=inputs.probability_band,
        recommended_wait_days=d.wait_days, estimated_savings=d.expected_saving, estimated_savings_range=d.saving_range,
        savings_explanation=d.savings_explanation,
        confidence=d.confidence, reasoning=d.reasoning, risks=d.risks, suggested_action=d.suggested_action,
        evidence={"history": summary, "forecast": _public_forecast(fc), "seasonal": seasonal,
                  "upcoming_events": [_event_public(e) for e in upcoming],
                  "vendor_comparison": vendor_block, "reviews": review_block},
        data_sources=sources, data_freshness=freshness, limitations=limitations,
        vendor_data_accessed=vendor_accessed, recommendation_version=RECOMMENDATION_VERSION)

    if persist:
        prev = repo.latest_recommendation(req.product_id, user_id)
        rec.id = repo.insert_recommendation({
            "product_id": req.product_id, "user_id": user_id, "current_price": to_decimal(rec.current_price),
            "target_price": to_decimal(rec.target_price), "recommended_action": rec.action,
            "recommended_wait_days": rec.recommended_wait_days, "estimated_savings": to_decimal(rec.estimated_savings),
            "estimated_savings_range": rec.estimated_savings_range, "confidence_level": rec.confidence,
            "reasoning_summary": rec.reasoning, "risk_factors": rec.risks, "forecast_id": forecast_id,
            "emi_offer_ids": emi_ids, "recommendation_version": RECOMMENDATION_VERSION,
            "request": req.model_dump()})
        notify_recommendation_change(repo, req.product_id, user_id, prev["recommended_action"] if prev else None, rec.action)
    return rec


def _public_forecast(fc: dict) -> dict:
    keep = ("status", "message", "horizon_days", "forecast_method", "predicted_price", "lower_price_bound",
            "upper_price_bound", "probability_below_target", "probability_detail", "target_price", "data_points_used",
            "model_version", "limitations")
    out = {k: fc.get(k) for k in keep}
    bt = (fc.get("validation_metrics") or {}).get("backtest")
    if bt:
        out["validation"] = {"chosen": bt.get("chosen"), "reason": bt.get("reason"),
                             "metrics": bt.get("methods"), "interval_holdout_coverage": bt.get("interval_holdout_coverage")}
        out["probability_validation"] = (fc.get("validation_metrics") or {}).get("probability_brier_5pct_drop")
    return out


def _event_public(e: dict) -> dict:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in e.items()
            if k in ("event_name", "retailer", "start_date", "end_date", "verification_status", "status_label",
                     "days_until", "source_url", "notes")}
