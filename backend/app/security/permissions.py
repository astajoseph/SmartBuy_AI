"""Authentication / authorization helpers for FastAPI routes.

* Users sign in with e-mail + password through Supabase Auth in the browser; every API call carries the
  Supabase access token. It is verified here with the project's signing keys (JWKS, ES256/RS256, fetched
  from SUPABASE_URL) or, for projects still on the legacy shared secret, with SUPABASE_JWT_SECRET (HS256).
* The first time a user is seen a row in smartbuy.profiles is created. Disabled profiles are refused (403).
* Admin endpoints accept either a signed-in user whose profile role is 'admin', or
  `Authorization: Bearer <ADMIN_API_TOKEN>` (constant-time compare) for scripts and automation.
  E-mails listed in ADMIN_EMAILS are made admins on sign-in, which bootstraps the first administrator.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings
from app.database.repositories import Repository
from app.dependencies import get_repo

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    email: str | None = None
    role: str = "user"
    display_name: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def auth_configured(settings: Settings) -> bool:
    return bool(settings.supabase_url or settings.supabase_jwt_secret)


@lru_cache(maxsize=4)
def _jwks_client(supabase_url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json", cache_keys=True, lifespan=3600)


def decode_token(token: str, settings: Settings) -> dict:
    """Verify a Supabase access token and return its claims (raises HTTPException 401/503)."""
    if not auth_configured(settings):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "User authentication is not configured")
    try:
        alg = jwt.get_unverified_header(token).get("alg")
        options = {"require": ["exp", "sub"]}
        issuer = f"{settings.supabase_url.rstrip('/')}/auth/v1" if settings.supabase_url else None
        if alg == "HS256":
            if not settings.supabase_jwt_secret:
                raise jwt.InvalidAlgorithmError("HS256 tokens need SUPABASE_JWT_SECRET")
            key = settings.supabase_jwt_secret.get_secret_value()
        else:
            if not settings.supabase_url:
                raise jwt.InvalidAlgorithmError("asymmetric tokens need SUPABASE_URL")
            key = _jwks_client(settings.supabase_url).get_signing_key_from_jwt(token).key
        return jwt.decode(token, key, algorithms=[alg], audience="authenticated", issuer=issuer, options=options)
    except (jwt.PyJWTError, KeyError, ValueError) as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session",
                            headers={"WWW-Authenticate": "Bearer"}) from e


def _user_from_token(token: str, settings: Settings, repo: Repository) -> CurrentUser:
    claims = decode_token(token, settings)
    try:
        uid = UUID(claims["sub"])
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session") from e
    email = claims.get("email")
    profile = repo.ensure_profile(uid, email, make_admin=bool(email) and email.lower() in settings.admin_email_set)
    if profile["is_disabled"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been disabled. Contact the administrator.")
    return CurrentUser(id=uid, email=email, role=profile["role"], display_name=profile["display_name"])


def require_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
    repo: Repository = Depends(get_repo),
) -> CurrentUser:
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign-in required", headers={"WWW-Authenticate": "Bearer"})
    return _user_from_token(creds.credentials, settings, repo)


def optional_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
    repo: Repository = Depends(get_repo),
) -> CurrentUser | None:
    if not creds or not auth_configured(settings):
        return None
    return _user_from_token(creds.credentials, settings, repo)


def require_admin(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
    repo: Repository = Depends(get_repo),
) -> CurrentUser | None:
    """Returns the admin user, or None when the static ADMIN_API_TOKEN was used."""
    configured = settings.admin_api_token.get_secret_value() if settings.admin_api_token else ""
    supplied = creds.credentials if creds else ""
    if configured and hmac.compare_digest(supplied.encode(), configured.encode()):
        return None
    if supplied.count(".") == 2 and auth_configured(settings):          # looks like a Supabase access token
        user = _user_from_token(supplied, settings, repo)
        if not user.is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access required")
        return user
    if not configured and not auth_configured(settings):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrative API is disabled (ADMIN_API_TOKEN not set)")
    if not configured and supplied:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access required")
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in as an administrator",
                        headers={"WWW-Authenticate": "Bearer"})
