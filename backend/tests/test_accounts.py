"""Sign-in (Supabase tokens), admin access, activity history and resumable assistant conversations.

Tokens are minted locally with a test HS256 secret; no Supabase project is contacted."""
import asyncio
import time
import uuid
from datetime import datetime

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from conftest import UTC, add_history, daily_prices, register_product

pytestmark = pytest.mark.db
SECRET = "test-jwt-secret-for-unit-tests-only-0123456789"


@pytest.fixture
def auth(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "supabase_jwt_secret", type(s.admin_api_token)(SECRET))
    monkeypatch.setattr(s, "supabase_url", None)
    monkeypatch.setattr(s, "admin_emails", "boss@example.com")


def token(email: str, uid: uuid.UUID | None = None, *, exp_in: int = 3600, secret: str = SECRET) -> dict:
    uid = uid or uuid.uuid5(uuid.NAMESPACE_DNS, email)
    t = jwt.encode({"sub": str(uid), "email": email, "aud": "authenticated", "exp": int(time.time()) + exp_in},
                   secret, algorithm="HS256")
    return {"Authorization": f"Bearer {t}"}


def product(repo):
    sid, _ = register_product(repo)
    add_history(repo, "test-product", sid, daily_prices(datetime(2026, 9, 1, tzinfo=UTC), [1100] * 10 + [1050] * 5))


def test_sign_in_creates_profile_and_rejects_bad_tokens(repo, auth):
    c = TestClient(app)
    assert c.get("/api/me").status_code == 401
    me = c.get("/api/me", headers=token("ana@example.com")).json()
    assert me["email"] == "ana@example.com" and me["role"] == "user" and not me["is_disabled"]
    assert c.get("/api/me", headers=token("ana@example.com", exp_in=-10)).status_code == 401
    assert c.get("/api/me", headers=token("ana@example.com", secret="x" * 40)).status_code == 401
    c.post("/api/me/sign-in", headers=token("ana@example.com"))
    assert [a["kind"] for a in c.get("/api/me/activity", headers=token("ana@example.com")).json()] == ["sign_in"]
    assert c.post("/api/me", json={"display_name": " Ana "}, headers=token("ana@example.com")).json()["display_name"] == "Ana"


def test_admin_access_by_role_and_bootstrap_email(repo, auth):
    c = TestClient(app)
    assert c.get("/api/admin/users", headers=token("ana@example.com")).status_code == 403
    boss = token("boss@example.com")
    assert c.get("/api/me", headers=boss).json()["role"] == "admin"
    assert c.get("/api/admin/whoami", headers=boss).json() == {"via": "account", "email": "boss@example.com"}
    emails = {u["email"] for u in c.get("/api/admin/users", headers=boss).json()}
    assert emails == {"ana@example.com", "boss@example.com"}
    assert c.get("/api/admin/users", headers={"Authorization": "Bearer test-admin-token"}).status_code == 200
    assert c.get("/api/admin/stats", headers=boss).json()["users"] == 2


def test_admin_can_disable_and_promote_but_not_lock_everyone_out(repo, auth):
    c = TestClient(app)
    boss, ana = token("boss@example.com"), token("ana@example.com")
    boss_id = c.get("/api/me", headers=boss).json()["user_id"]
    ana_id = c.get("/api/me", headers=ana).json()["user_id"]
    assert c.post(f"/api/admin/users/{boss_id}/disabled", json={"disabled": True}, headers=boss).status_code == 409
    assert c.post(f"/api/admin/users/{boss_id}/role", json={"role": "user"}, headers=boss).status_code == 409
    assert c.post(f"/api/admin/users/{ana_id}/disabled", json={"disabled": True}, headers=boss).json()["is_disabled"]
    assert c.get("/api/me", headers=ana).status_code == 403
    c.post(f"/api/admin/users/{ana_id}/disabled", json={"disabled": False}, headers=boss)
    assert c.post(f"/api/admin/users/{ana_id}/role", json={"role": "admin"}, headers=boss).json()["role"] == "admin"
    assert c.get("/api/admin/users", headers=ana).status_code == 200
    audit = c.get("/api/admin/activity?kind=admin_action", headers=boss).json()
    assert len(audit) == 3 and all(a["payload"]["target_email"] == "ana@example.com" for a in audit)


def test_conversation_is_saved_with_context_and_retry_is_idempotent(repo, auth, monkeypatch):
    from app.services import chat_service
    product(repo)
    seen = []
    real_chat = chat_service.chat

    async def spy(*a, **kw):
        seen.append(kw["history"])
        return await real_chat(*a, **kw)
    monkeypatch.setattr(chat_service, "chat", spy)

    c, ana = TestClient(app), token("ana@example.com")
    sid = str(uuid.uuid4())
    assert c.post("/api/assistant/sessions", json={"id": sid, "product_id": "test-product"}, headers=ana).status_code == 200
    assert c.post("/api/assistant/sessions", json={"id": sid}, headers=ana).json()["id"] == sid      # retried create
    r1 = str(uuid.uuid4())
    first = c.post(f"/api/assistant/sessions/{sid}/messages", json={"request_id": r1, "message": "Should I buy now?"},
                   headers=ana).json()
    assert first["message"]["status"] == "complete" and first["message"]["content"]
    again = c.post(f"/api/assistant/sessions/{sid}/messages", json={"request_id": r1, "message": "Should I buy now?"},
                   headers=ana).json()
    assert again["message"]["id"] == first["message"]["id"] and len(seen) == 1     # answered once, not twice
    c.post(f"/api/assistant/sessions/{sid}/messages", json={"request_id": str(uuid.uuid4()), "message": "And under 1000?"},
           headers=ana)
    assert [m["content"] for m in seen[1]][0] == "Should I buy now?" and len(seen[1]) == 2   # previous turn as context

    convo = c.get(f"/api/assistant/sessions/{sid}", headers=ana).json()
    assert convo["title"] == "Should I buy now?" and [m["role"] for m in convo["messages"]] == ["user", "assistant"] * 2
    assert c.get("/api/assistant/sessions", headers=ana).json()[0]["questions"] == 2
    assert c.get(f"/api/assistant/sessions/{sid}", headers=token("eve@example.com")).status_code == 404
    kinds = [a["kind"] for a in c.get("/api/me/activity", headers=ana).json()]
    assert kinds.count("assistant_chat") == 2
    boss = token("boss@example.com")
    assert len(c.get(f"/api/admin/chats/{sid}", headers=boss).json()["messages"]) == 4
    assert c.delete(f"/api/assistant/sessions/{sid}", headers=ana).json() == {"deleted": True}
    assert c.get("/api/assistant/sessions", headers=ana).json() == []


def test_failed_or_abandoned_answers_are_reclaimed(repo, auth):
    uid = uuid.uuid4()
    repo.ensure_profile(uid, "x@example.com")
    s = repo.create_chat_session(uuid.uuid4(), uid)
    rid = uuid.uuid4()
    row, claimed = repo.begin_chat_turn(s["id"], rid, "q")
    assert claimed and row["status"] == "pending"
    assert repo.begin_chat_turn(s["id"], rid, "q")[1] is False             # still being generated elsewhere
    assert repo.begin_chat_turn(s["id"], rid, "q", stale_after_seconds=0)[1] is True   # server died mid-answer
    repo.finish_chat_turn(row["id"], "oops", "failed", {})
    assert repo.begin_chat_turn(s["id"], rid, "q")[1] is True               # Retry after a failure
    assert len(repo.chat_messages(s["id"])) == 2                            # still one question + one answer


def test_answer_is_saved_even_if_the_connection_drops(repo, auth, monkeypatch):
    from app.security.permissions import CurrentUser
    from app.services import chat_service
    product(repo)
    uid = uuid.uuid4()
    repo.ensure_profile(uid, "x@example.com")
    s = repo.create_chat_session(uuid.uuid4(), uid)

    async def slow_chat(repo, message, **kw):
        await asyncio.sleep(0.2)
        return {"reply": "saved anyway", "intent": {"product_id": "test-product"}}
    monkeypatch.setattr(chat_service, "chat", slow_chat)

    async def scenario():
        req = asyncio.create_task(chat_service.answer(repo, s, uuid.uuid4(), "hi", user=CurrentUser(id=uid)))
        await asyncio.sleep(0.05)
        req.cancel()                                                        # browser went offline
        await asyncio.sleep(0.4)
    asyncio.run(scenario())
    answer = [m for m in repo.chat_messages(s["id"]) if m["role"] == "assistant"][0]
    assert answer["status"] == "complete" and answer["content"] == "saved anyway"


def test_recommendation_notifications_and_targets_go_to_history(repo, auth):
    product(repo)
    c, ana = TestClient(app), token("ana@example.com")
    c.post("/api/recommendations", json={"product_id": "test-product", "target_price": 1000}, headers=ana)
    c.get("/api/notifications", headers=ana)
    c.get("/api/notifications", headers=ana)                                # throttled: logged once
    c.post("/api/products/test-product/target-price", json={"target_price": 999}, headers=ana)
    kinds = [a["kind"] for a in c.get("/api/me/activity", headers=ana).json()]
    assert kinds == ["target_saved", "notifications_viewed", "recommendation"]
    assert float(c.get("/api/me/targets", headers=ana).json()[0]["target_price"]) == 999
    assert c.post("/api/recommendations", json={"product_id": "test-product"}).status_code == 200   # anonymous still works


def test_es256_tokens_are_verified_with_the_project_signing_keys(repo, monkeypatch):
    """Current Supabase projects sign with ES256 keys published at <SUPABASE_URL>/auth/v1/.well-known/jwks.json."""
    from cryptography.hazmat.primitives.asymmetric import ec

    from app.security import permissions
    key = ec.generate_private_key(ec.SECP256R1())
    url = "https://demo-project.supabase.co"
    s = get_settings()
    monkeypatch.setattr(s, "supabase_url", url)
    monkeypatch.setattr(s, "supabase_jwt_secret", None)

    class FakeJWKS:
        def get_signing_key_from_jwt(self, _):
            return type("K", (), {"key": key.public_key()})()
    monkeypatch.setattr(permissions, "_jwks_client", lambda _: FakeJWKS())

    def es(iss: str) -> dict:
        t = jwt.encode({"sub": str(uuid.uuid4()), "email": "es@example.com", "aud": "authenticated", "iss": iss,
                        "exp": int(time.time()) + 600}, key, algorithm="ES256", headers={"kid": "k1"})
        return {"Authorization": f"Bearer {t}"}
    c = TestClient(app)
    assert c.get("/api/me", headers=es(f"{url}/auth/v1")).json()["email"] == "es@example.com"
    assert c.get("/api/me", headers=es("https://other-project.supabase.co/auth/v1")).status_code == 401
    assert c.get("/api/me", headers=token("hs@example.com")).status_code == 401      # HS256 refused without a secret
