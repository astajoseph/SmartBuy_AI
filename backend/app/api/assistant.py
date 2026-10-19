from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.database.repositories import Repository
from app.dependencies import chat_rate_limit, get_repo
from app.llm.assistant import chat
from app.recommendations.engine import ProductNotFound
from app.security.permissions import CurrentUser, optional_user

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=4000)


class ChatPreferences(BaseModel):
    target_price: float | None = Field(default=None, gt=0)
    max_budget: float | None = Field(default=None, gt=0)
    deadline_days: int | None = Field(default=None, ge=0, le=365)
    max_monthly_emi: float | None = Field(default=None, gt=0)
    preferred_tenure_months: int | None = Field(default=None, ge=1, le=60)
    objective: str | None = Field(default=None, pattern="^(lowest_total_cost|lowest_monthly_payment|shortest_tenure|lowest_interest|balanced)$")
    user_banks: list[str] = Field(default_factory=list, max_length=20)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    product_id: str | None = None
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)
    preferences: ChatPreferences = Field(default_factory=ChatPreferences)


@router.post("/chat", dependencies=[Depends(chat_rate_limit)],
             summary="Ask the purchase advisor (read-only; vendor pages only fetched for EMI/vendor/review questions)")
async def chat_endpoint(req: ChatRequest, repo: Repository = Depends(get_repo),
                        user: CurrentUser | None = Depends(optional_user)) -> dict:
    try:
        return await chat(repo, req.message, product_id=req.product_id,
                          history=[m.model_dump() for m in req.history],
                          preferences=req.preferences.model_dump(exclude_none=True), user_id=user.id if user else None)
    except ProductNotFound:
        raise HTTPException(404, "Product not found") from None
