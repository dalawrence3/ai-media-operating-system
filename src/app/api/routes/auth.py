"""Auth routes — login, token refresh, logout, and /me.

Thin HTTP transport over AuthService.  No business logic here.
Rate limiting: login is 10/minute, refresh is 30/minute per IP.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.api.deps import get_actor, get_db
from app.api.jwt_auth import CurrentUser, get_current_user
from app.api.limiter import limiter
from app.auth.service import (
    AccountDisabledError,
    AuthError,
    AuthService,
    InvalidCredentialsError,
    RefreshTokenError,
    UserNotFoundError,
)
from app.core.config import get_config

router = APIRouter(prefix="/auth", tags=["auth"])


def _auth_service() -> AuthService:
    cfg = get_config()
    if not cfg.secret_key:
        raise HTTPException(status_code=503, detail="Authentication not configured")
    return AuthService(
        secret_key=cfg.secret_key,
        access_expire=cfg.access_token_expire_seconds,
        refresh_expire=cfg.refresh_token_expire_seconds,
    )


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------


@router.post("/login")
@limiter.limit("10/minute")
def login(
    request: Request,
    body: dict[str, Any] = Body(...),
    conn: Any = Depends(get_db),
) -> dict[str, Any]:
    """Authenticate with email + password; returns access and refresh tokens."""
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    if not email or not password:
        raise HTTPException(status_code=422, detail="email and password are required")

    svc = _auth_service()
    try:
        result = svc.login(conn, email, password)
    except (UserNotFoundError, InvalidCredentialsError) as exc:
        # Unified message avoids user enumeration.
        raise HTTPException(status_code=401, detail="Invalid email or password") from exc
    except AccountDisabledError as exc:
        raise HTTPException(status_code=403, detail="Account is disabled") from exc
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    return result


# ---------------------------------------------------------------------------
# POST /auth/refresh
# ---------------------------------------------------------------------------


@router.post("/refresh")
@limiter.limit("30/minute")
def refresh(
    request: Request,
    body: dict[str, Any] = Body(...),
    conn: Any = Depends(get_db),
) -> dict[str, Any]:
    """Issue a new access token using a valid refresh token."""
    raw_token = body.get("refresh_token") or ""
    if not raw_token:
        raise HTTPException(status_code=422, detail="refresh_token is required")

    svc = _auth_service()
    try:
        return svc.refresh(conn, raw_token)
    except RefreshTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------


@router.post("/logout")
def logout(
    body: dict[str, Any] = Body(default={}),
    conn: Any = Depends(get_db),
    actor: str = Depends(get_actor),
) -> dict[str, Any]:
    """Revoke a refresh token.  Requires authentication."""
    raw_token = body.get("refresh_token") or ""
    if not raw_token:
        raise HTTPException(status_code=422, detail="refresh_token is required")

    svc = _auth_service()
    revoked = svc.revoke_refresh_token(conn, raw_token)
    return {"revoked": revoked}


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------


@router.get("/me")
def me(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Return identity information for the authenticated user."""
    return {
        "user_id": current_user.user_id,
        "email": current_user.email,
        "actor": current_user.actor,
        "workspace_roles": current_user.workspace_roles,
    }
