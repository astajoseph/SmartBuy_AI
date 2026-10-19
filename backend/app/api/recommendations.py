from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.database.repositories import Repository
from app.dependencies import get_repo, vendor_rate_limit
from app.recommendations.engine import ProductNotFound, recommend
from app.recommendations.schemas import Recommendation, RecommendationRequest
from app.security.permissions import CurrentUser, optional_user

router = APIRouter(tags=["recommendations"])


@router.post("/api/recommendations", response_model=Recommendation,
             summary="Buy / wait / monitor recommendation with evidence (deterministic policy)")
async def create_recommendation(req: RecommendationRequest, request: Request, repo: Repository = Depends(get_repo),
                                user: CurrentUser | None = Depends(optional_user)) -> Recommendation:
    if req.include_vendor_analysis or req.include_reviews:
        vendor_rate_limit(request)       # only requests that may reach vendor sites count against the limit
    try:
        return await recommend(repo, req, user_id=user.id if user else None)
    except ProductNotFound:
        raise HTTPException(404, "Product not found") from None
