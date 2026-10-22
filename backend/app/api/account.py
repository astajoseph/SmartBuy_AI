"""The signed-in user's own account: profile and activity history."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.database.repositories import Repository
from app.dependencies import get_repo
from app.security.permissions import CurrentUser, require_user

router = APIRouter(prefix="/api/me", tags=["account"])


class ProfileIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)


@router.get("", summary="The signed-in user's profile (created on first sign-in)")
def me(repo: Repository = Depends(get_repo), user: CurrentUser = Depends(require_user)) -> dict:
    return repo.get_profile(user.id)


@router.post("", summary="Update the signed-in user's display name")
def update_me(body: ProfileIn, repo: Repository = Depends(get_repo), user: CurrentUser = Depends(require_user)) -> dict:
    return repo.update_profile(user.id, display_name=(body.display_name or "").strip() or None)


@router.post("/sign-in", summary="Record a sign-in in the user's history (called by the browser after login)")
def signed_in(repo: Repository = Depends(get_repo), user: CurrentUser = Depends(require_user)) -> dict:
    repo.log_activity(user.id, "sign_in", "Signed in", throttle_minutes=5)
    return repo.get_profile(user.id)


@router.get("/activity", summary="The signed-in user's history: assistant, advisor, notifications, targets, sign-ins")
def my_activity(kind: str | None = None, before: int | None = None, limit: int = Query(50, ge=1, le=200),
                repo: Repository = Depends(get_repo), user: CurrentUser = Depends(require_user)) -> list[dict]:
    return repo.list_activity(user.id, kind=kind, before_id=before, limit=limit)


@router.get("/targets", summary="The signed-in user's saved target prices / price alerts")
def my_targets(repo: Repository = Depends(get_repo), user: CurrentUser = Depends(require_user)) -> list[dict]:
    return repo.targets_for_user(user.id)
