from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.database.repositories import Repository
from app.dependencies import get_repo
from app.forecasting.seasonality import status_label, upcoming_events
from app.security.permissions import CurrentUser, optional_user

router = APIRouter(tags=["events"])


@router.get("/api/events", summary="Indian sale-event registry with verification status")
def events(repo: Repository = Depends(get_repo)) -> dict:
    rows = repo.list_sale_events()
    today = datetime.now(timezone.utc).date()
    return {"events": [{**r, "status_label": status_label(r["verification_status"])} for r in rows],
            "upcoming": upcoming_events(rows, today, 180)}


@router.get("/api/notifications", summary="Notifications (broadcast + the signed-in user's own)")
def notifications(repo: Repository = Depends(get_repo), user: CurrentUser | None = Depends(optional_user)) -> list[dict]:
    rows = repo.list_notifications(user.id if user else None)
    if user:
        repo.log_activity(user.id, "notifications_viewed", f"Viewed notifications ({len(rows)})", throttle_minutes=30)
    return rows
