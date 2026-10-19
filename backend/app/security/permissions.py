"""Authentication / authorization helpers for FastAPI routes.

* Admin endpoints require `Authorization: Bearer <ADMIN_API_TOKEN>` (constant-time compare).
  If ADMIN_API_TOKEN is not configured, admin endpoints are disabled (403), never open.
* User endpoints accept a Supabase access token (HS256, verified with SUPABASE_JWT_SECRET).
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    email: str | None = None


def require_admin(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> None:
    configured = settings.admin_api_token.get_secret_value() if settings.admin_api_token else ""
    if not configured:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrative API is disabled (ADMIN_API_TOKEN not set)")
    supplied = creds.credentials if creds else ""
    if not hmac.compare_digest(supplied.encode(), configured.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid administrator token",
                            headers={"WWW-Authenticate": "Bearer"})


def _decode_user(token: str, settings: Settings) -> CurrentUser:
    if not settings.supabase_jwt_secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "User authentication is not configured")
    try:
        claims = jwt.decode(token, settings.supabase_jwt_secret.get_secret_value(),
                            algorithms=["HS256"], audience="authenticated")
        return CurrentUser(id=UUID(claims["sub"]), email=claims.get("email"))
    except (jwt.PyJWTError, KeyError, ValueError) as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session") from e


def require_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign-in required", headers={"WWW-Authenticate": "Bearer"})
    return _decode_user(creds.credentials, settings)


def optional_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> CurrentUser | None:
    if not creds or not settings.supabase_jwt_secret:
        return None
    return _decode_user(creds.credentials, settings)
