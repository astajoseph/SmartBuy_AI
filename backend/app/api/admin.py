"""Administrative API. Every route requires a signed-in admin account or `Authorization: Bearer <ADMIN_API_TOKEN>`."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import get_settings
from app.database.repositories import Repository
from app.dependencies import get_repo
from app.extraction.base import ExtractionContext
from app.extraction.browser import BrowserRenderer
from app.extraction.registry import UnknownAdapter, resolve_adapter
from app.api.assistant import message_out
from app.security.permissions import CurrentUser, require_admin
from app.security.url_validation import FetchError, RobotsDisallowed, SafeFetcher, UnsafeURLError, validate_url_syntax
from app.services import vendor_service
from app.services.alert_service import evaluate_product_alerts
from app.services.event_service import sync_events
from app.services.import_service import import_dashboard_ts
from app.services.registry_service import ProductConfig, sync_product, sync_registry
from app.workers.price_collector import CollectorDeps, collect_source

router = APIRouter(tags=["admin"], dependencies=[Depends(require_admin)])


class SourceTestIn(BaseModel):
    url: str = Field(max_length=2048)
    kind: str = Field("price_history", pattern="^(price_history|vendor)$")
    adapter: str = "auto"
    adapter_config: dict[str, Any] = Field(default_factory=dict)
    use_browser: bool = True


@router.post("/api/sources/test", summary="Inspect a URL with an adapter and report extracted / missing fields")
async def test_source(body: SourceTestIn, repo: Repository = Depends(get_repo)) -> dict:
    try:
        parsed = validate_url_syntax(body.url)
        adapter = resolve_adapter(body.adapter, body.url, body.kind)
    except (UnsafeURLError, UnknownAdapter) as e:
        raise HTTPException(422, str(e)) from None
    job_id = repo.start_job("source_test")
    hosts = repo.registered_hosts() | {parsed.host}
    ctx = ExtractionContext(fetcher=SafeFetcher(hosts), browser=BrowserRenderer() if body.use_browser else None,
                            config=body.adapter_config)
    try:
        result = await adapter.extract(body.url, ctx)
    except (UnsafeURLError, RobotsDisallowed, FetchError) as e:
        repo.finish_job(job_id, "failed", error=f"{e.__class__.__name__}: {e}")
        return {"success": False, "url": body.url, "adapter": adapter.name, "error": f"{e.__class__.__name__}: {e}"}
    report = result.report()
    success = not result.blocked and bool(result.current_prices or result.history or result.vendor.get("price"))
    repo.record_source_test(body.kind, None, report, success)
    repo.finish_job(job_id, "success" if success else "failed", extracted=len(report["fields_found"]), details=report)
    return {"success": success, **report}


@router.post("/api/products/register", summary="Register or update a product with its price source and vendors")
def register_product(body: ProductConfig, repo: Repository = Depends(get_repo)) -> dict:
    return sync_product(repo, body)


@router.post("/api/admin/sync-registry", summary="Load config/products.yaml and config/sale_events.yaml into the database")
def sync_all(repo: Repository = Depends(get_repo)) -> dict:
    s = get_settings()
    return {"products": sync_registry(repo, s.products_config), "new_events": sync_events(repo, s.sale_events_config)}


@router.post("/api/admin/collect/{product_id}", summary="Collect the product's price-history source now")
async def collect_now(product_id: str, repo: Repository = Depends(get_repo)) -> list[dict]:
    sources = [s for s in repo.list_price_sources(product_id) if s["is_active"]]
    if not sources:
        raise HTTPException(404, "No active price-history source for this product")
    return [await collect_source(repo, s, job_type="manual_price", deps=CollectorDeps(after_collect=evaluate_product_alerts))
            for s in sources]


@router.post("/api/admin/refresh-vendor-data/{product_id}", summary="Force a fresh fetch of all vendor pages")
async def refresh_vendor(product_id: str, repo: Repository = Depends(get_repo)) -> list[dict]:
    vds = await vendor_service.get_vendor_data(repo, product_id, ("emi", "reviews"), force_refresh=True)
    return [{"vendor": v.vendor_source.get("vendor_name"), "status": v.status, "message": v.message,
             "emi_offers": len(v.emi_offers), "reviews": len(v.reviews), "freshness": v.freshness} for v in vds]


@router.post("/api/admin/import-dashboard-ts/{product_id}", summary="Import history saved by extract_dashboard_timeseries.py")
def import_legacy(product_id: str, repo: Repository = Depends(get_repo)) -> list[dict]:
    return [import_dashboard_ts(repo, product_id, s["source_url"]) for s in repo.list_price_sources(product_id)]


@router.get("/api/admin/jobs", summary="Collection / vendor / test job log")
def jobs(status: str | None = None, limit: int = 100, repo: Repository = Depends(get_repo)) -> list[dict]:
    rows = repo.list_jobs(min(limit, 500), status)
    for r in rows:
        r.pop("details", None)
    return rows


@router.get("/api/admin/jobs/{job_id}", summary="One job including its extraction report")
def job(job_id: int, repo: Repository = Depends(get_repo)) -> dict:
    row = repo._one("select * from collection_jobs where id = %s", (job_id,))  # noqa: SLF001
    if not row:
        raise HTTPException(404, "Job not found")
    return row


@router.get("/api/admin/sources/health", summary="Health of every price-history source")
def sources_health(repo: Repository = Depends(get_repo)) -> list[dict]:
    return repo.source_health()


@router.get("/api/admin/cache-status", summary="Latest vendor snapshot per vendor source and its expiry")
def cache_status(repo: Repository = Depends(get_repo)) -> list[dict]:
    return repo._all(  # noqa: SLF001
        """select distinct on (vs.id) vs.id as vendor_source_id, vs.product_id, v.name as vendor, vs.source_url,
                  s.fetched_at, s.expires_at, s.fetch_status, s.error_message,
                  s.extraction_report -> 'expires' as expires_by_kind
           from product_vendor_sources vs join vendors v on v.id = vs.vendor_id
           left join vendor_snapshots s on s.vendor_source_id = vs.id
           order by vs.id, s.fetched_at desc nulls last""")


# ---------------------------------------------------------------- users, activity and conversations
class RoleIn(BaseModel):
    role: str = Field(pattern="^(user|admin)$")


class DisabledIn(BaseModel):
    disabled: bool


def _profile_or_404(repo: Repository, user_id: UUID) -> dict:
    p = repo.get_profile(user_id)
    if not p:
        raise HTTPException(404, "User not found")
    return p


def _audit(repo: Repository, admin: CurrentUser | None, summary: str, target: dict, **payload) -> None:
    if admin:                                       # token-based calls have no account to record against
        repo.log_activity(admin.id, "admin_action", summary,
                          payload={"target_user_id": str(target["user_id"]), "target_email": target["email"], **payload})


@router.get("/api/admin/whoami", summary="Who is calling: an admin account, or the static admin token")
def whoami(admin: CurrentUser | None = Depends(require_admin)) -> dict:
    return {"via": "account" if admin else "token", "email": admin.email if admin else None}


@router.get("/api/admin/stats", summary="Users, activity and assistant usage at a glance")
def stats(repo: Repository = Depends(get_repo)) -> dict:
    return repo.activity_stats()


@router.get("/api/admin/users", summary="All accounts with chat / activity counts")
def users(q: str | None = Query(None, max_length=100), limit: int = Query(200, ge=1, le=1000),
          repo: Repository = Depends(get_repo)) -> list[dict]:
    return repo.list_profiles(q or None, limit)


@router.get("/api/admin/users/{user_id}", summary="One account with its targets and conversations")
def user_detail(user_id: UUID, repo: Repository = Depends(get_repo)) -> dict:
    return {**_profile_or_404(repo, user_id), "targets": repo.targets_for_user(user_id),
            "sessions": repo.list_chat_sessions(user_id, 200)}


@router.get("/api/admin/activity", summary="Activity of all users (or one), newest first")
def activity(user_id: UUID | None = None, kind: str | None = None, before: int | None = None,
             limit: int = Query(100, ge=1, le=500), repo: Repository = Depends(get_repo)) -> list[dict]:
    return repo.list_activity(user_id, kind=kind, before_id=before, limit=limit)


@router.get("/api/admin/chats/{session_id}", summary="Read any user's conversation")
def chat_detail(session_id: UUID, repo: Repository = Depends(get_repo)) -> dict:
    s = repo.get_chat_session(session_id)
    if not s:
        raise HTTPException(404, "Conversation not found")
    return {**s, "owner": repo.get_profile(s["user_id"]), "messages": [message_out(m) for m in repo.chat_messages(session_id)]}


@router.post("/api/admin/users/{user_id}/role", summary="Make an account an admin or a normal user")
def set_role(user_id: UUID, body: RoleIn, repo: Repository = Depends(get_repo),
             admin: CurrentUser | None = Depends(require_admin)) -> dict:
    target = _profile_or_404(repo, user_id)
    if body.role != "admin" and target["role"] == "admin":
        if admin and admin.id == user_id:
            raise HTTPException(409, "You cannot remove your own admin role")
        if repo.count_admins() <= 1:
            raise HTTPException(409, "At least one active admin must remain")
    row = repo.set_profile_role(user_id, body.role)
    _audit(repo, admin, f"Set role of {target['email']} to {body.role}", target, role=body.role)
    return row


@router.post("/api/admin/users/{user_id}/disabled", summary="Disable (block) or re-enable an account")
def set_disabled(user_id: UUID, body: DisabledIn, repo: Repository = Depends(get_repo),
                 admin: CurrentUser | None = Depends(require_admin)) -> dict:
    target = _profile_or_404(repo, user_id)
    if body.disabled and admin and admin.id == user_id:
        raise HTTPException(409, "You cannot disable your own account")
    if body.disabled and target["role"] == "admin" and not target["is_disabled"] and repo.count_admins() <= 1:
        raise HTTPException(409, "At least one active admin must remain")
    row = repo.set_profile_disabled(user_id, body.disabled)
    _audit(repo, admin, f"{'Disabled' if body.disabled else 'Re-enabled'} {target['email']}", target, disabled=body.disabled)
    return row
