"""Pydantic schemas for recommendation requests and the structured recommendation object."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Action = Literal["BUY_NOW", "WAIT", "MONITOR", "BUY_IF_TARGET_REACHED", "COMPARE_VENDORS", "INSUFFICIENT_DATA"]
Confidence = Literal["LOW", "MEDIUM", "HIGH"]
ObjectiveT = Literal["lowest_total_cost", "lowest_monthly_payment", "shortest_tenure", "lowest_interest", "balanced"]


class RecommendationRequest(BaseModel):
    product_id: str
    target_price: float | None = Field(default=None, gt=0)
    max_budget: float | None = Field(default=None, gt=0)
    deadline_days: int | None = Field(default=None, ge=0, le=365)
    preferred_vendor: str | None = None
    max_monthly_emi: float | None = Field(default=None, gt=0)
    preferred_tenure_months: int | None = Field(default=None, ge=1, le=60)
    objective: ObjectiveT = "lowest_total_cost"
    include_vendor_analysis: bool = False      # EMI / vendor comparison -> may fetch vendor pages (cached)
    include_reviews: bool = False              # review analysis -> may fetch vendor pages (cached)
    refresh_vendor_data: bool = False          # explicit user request to bypass the cache
    user_banks: list[str] = Field(default_factory=list, max_length=20)  # bank names only, never card numbers


class Recommendation(BaseModel):
    id: int | None = None
    product_id: str
    product_name: str | None = None
    generated_at: datetime
    action: Action
    current_price: float | None
    current_price_as_of: datetime | None = None
    target_price: float | None = None
    forecast_horizon_days: int | None = None
    probability_below_target: float | None = None
    probability_band: str | None = None
    recommended_wait_days: int | None = None
    estimated_savings: float | None = None
    estimated_savings_range: list[float] | None = None
    savings_explanation: str | None = None
    confidence: Confidence
    reasoning: list[str]
    risks: list[str]
    suggested_action: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    data_sources: list[dict[str, Any]] = Field(default_factory=list)
    data_freshness: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    vendor_data_accessed: bool = False
    recommendation_version: str
