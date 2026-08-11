"""YouTube OAuth 2.0 routes.

Route map:
  POST   /.../{workspace_id}/channels/{channel_id}/accounts/{account_id}/oauth/youtube/start
  GET    /api/v1/oauth/youtube/callback
  DELETE /api/v1/workspaces/{workspace_id}/channels/{channel_id}/accounts/{account_id}/oauth/youtube
  GET    /api/v1/workspaces/{workspace_id}/channels/{channel_id}/accounts/{account_id}/connection

Authentication:
  - Start / Disconnect / Connection status: JWT Bearer (owner or admin role required)
  - Callback: unauthenticated at HTTP layer; authenticated via validated OAuth state
    (the state embeds the user_id, workspace_id, channel_id, account_id — all binding
    comes from server-side state, never from callback query parameters)

RBAC:
  - Only workspace owners or admins may connect/disconnect YouTube accounts.
  - Any authenticated user may read connection status (future: scope by role).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.api.deps import get_db
from app.api.jwt_auth import CurrentUser, get_current_user
from app.auth.rbac import has_permission
from app.core.config import get_config
from app.core.logging import get_logger
from app.oauth.errors import (
    OAuthAccountNotFoundError,
    OAuthChannelMismatchError,
    OAuthChannelVerificationError,
    OAuthCodeExchangeError,
    OAuthInsufficientRoleError,
    OAuthNotConfiguredError,
    OAuthStateExpiredError,
    OAuthStateMismatchError,
    OAuthStateNotFoundError,
    OAuthStateReplayError,
)
from app.oauth.flow import (
    complete_youtube_oauth,
    disconnect_youtube_account,
    get_connection_status,
    start_youtube_oauth,
    verify_youtube_connection,
)

logger = get_logger(__name__)

router = APIRouter(tags=["oauth"])

# Reusable prefix for account-scoped OAuth routes
_ACCT_PREFIX = "/workspaces/{workspace_id}/channels/{channel_id}/accounts/{account_id}"


def _redirect(path: str) -> RedirectResponse:
    """Build a 302 redirect prefixed with ACE_FRONTEND_URL when set.

    In local dev the OAuth callback lands on :8000 (FastAPI) while the SPA lives
    on :5173 (Vite).  Without an absolute prefix the browser stays on :8000 where
    no SPA routes exist.  Set ACE_FRONTEND_URL=http://localhost:5173 in dev.
    In production frontend and backend share an origin, so the prefix is empty and
    the path is used as-is (a relative redirect).
    """
    base = get_config().frontend_url  # "" in production, "http://localhost:5173" in dev
    return RedirectResponse(url=f"{base}{path}", status_code=302)


def _require_connect_permission(current_user: CurrentUser, workspace_id: str) -> None:
    """Raise OAuthInsufficientRoleError unless user has platform_account:connect permission."""
    if not has_permission("platform_account:connect", workspace_id, current_user.workspace_roles):
        raise OAuthInsufficientRoleError(
            f"Actor '{current_user.actor}' does not have permission to connect/disconnect "
            f"platform accounts in workspace {workspace_id}. "
            "Required role: admin or owner."
        )


def _get_oauth_client() -> Any:
    """Return the appropriate GoogleOAuthClient for the current environment.

    In tests, inject FakeGoogleOAuthClient via dependency override.
    In live use, return RealGoogleOAuthClient (requires google-auth-oauthlib installed).
    """
    cfg = get_config()
    if not cfg.youtube_client_secrets_path:
        raise OAuthNotConfiguredError(
            "YOUTUBE_CLIENT_SECRETS_PATH is not set. "
            "Set this to the path of the Google Cloud OAuth client secrets JSON file."
        )
    from app.oauth.client_google import RealGoogleOAuthClient

    return RealGoogleOAuthClient(
        client_secrets_path=cfg.youtube_client_secrets_path,
        redirect_uri=cfg.youtube_redirect_uri,
    )


# ---------------------------------------------------------------------------
# Dependency: injectable OAuth client (overridden in tests)
# ---------------------------------------------------------------------------


def get_oauth_client() -> Any:
    """FastAPI dependency — override in tests to inject FakeGoogleOAuthClient."""
    return _get_oauth_client()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(f"{_ACCT_PREFIX}/oauth/youtube/start")
def start_youtube_oauth_route(
    workspace_id: str,
    channel_id: str,
    account_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Any = Depends(get_db),
    oauth_client: Any = Depends(get_oauth_client),
) -> dict[str, str]:
    """Begin the YouTube OAuth 2.0 authorization flow.

    Returns the Google authorization URL. The frontend should redirect the
    user's browser to this URL. After the user authorizes (or denies), Google
    redirects to the callback endpoint.

    Required role: workspace owner or admin.
    """
    try:
        _require_connect_permission(current_user, workspace_id)
    except OAuthInsufficientRoleError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    try:
        result = start_youtube_oauth(
            db,
            account_id=account_id,
            user_id=current_user.actor,
            workspace_id=workspace_id,
            channel_id=channel_id,
            oauth_client=oauth_client,
        )
    except OAuthAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OAuthNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PermissionError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OAuth start failed: {exc}") from exc

    return {"authorization_url": result.authorization_url}


@router.get("/oauth/youtube/callback")
def youtube_oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    db: Any = Depends(get_db),
    oauth_client: Any = Depends(get_oauth_client),
) -> RedirectResponse:
    """Google OAuth 2.0 callback endpoint.

    This endpoint is unauthenticated at the HTTP layer — Google redirects
    here after the user grants or denies consent. All workspace/account
    binding comes from the server-side state store (never from query params).

    On success: redirects to {frontend_url}/workspaces/{id}/channels?oauth_success=true&...
    On error:   redirects to {frontend_url}/?oauth_error={code}
    """
    # ── User denied consent or Google error ──────────────────────────────────
    if error:
        error_code = _sanitize_error_code(error)
        logger.warning(
            "OAuth callback: Google returned error=%s description=%s", error_code, error_description
        )
        return _redirect(f"/?oauth_error={error_code}")

    # ── Missing required params ───────────────────────────────────────────────
    if not code or not state:
        logger.warning("OAuth callback: missing code or state params")
        return _redirect("/?oauth_error=missing_params")

    # ── Validate state and complete flow ─────────────────────────────────────
    try:
        result = complete_youtube_oauth(
            db,
            code=code,
            state_nonce=state,
            oauth_client=oauth_client,
        )
    except (OAuthStateNotFoundError, OAuthStateExpiredError) as exc:
        logger.warning("OAuth callback: state invalid or expired — %s", exc)
        return _redirect("/?oauth_error=state_expired")
    except OAuthStateReplayError as exc:
        logger.warning("OAuth callback: state replay detected — %s", exc)
        return _redirect("/?oauth_error=state_replayed")
    except OAuthStateMismatchError as exc:
        logger.warning("OAuth callback: state mismatch — %s", exc)
        return _redirect("/?oauth_error=state_mismatch")
    except OAuthCodeExchangeError as exc:
        # Log the full error so operators can diagnose (e.g. insecure_transport,
        # redirect_uri_mismatch) without exposing the authorization code or tokens.
        logger.error("OAuth callback: authorization code exchange failed — %s", exc)
        return _redirect("/?oauth_error=exchange_failed")
    except OAuthChannelVerificationError as exc:
        logger.warning("OAuth callback: channel identity verification failed — %s", exc)
        return _redirect("/?oauth_error=no_youtube_channel")
    except OAuthChannelMismatchError as exc:
        logger.warning("OAuth callback: channel mismatch — %s", exc)
        return _redirect("/?oauth_error=channel_mismatch")
    except OAuthNotConfiguredError as exc:
        logger.error("OAuth callback: not configured — %s", exc)
        return _redirect("/?oauth_error=not_configured")
    except Exception as exc:
        logger.exception("OAuth callback: unexpected error — %s", exc)
        return _redirect("/?oauth_error=internal_error")

    logger.info(
        "OAuth callback: account %s connected to YouTube channel %s",
        result.account_id,
        result.provider_channel_id,
    )
    return RedirectResponse(
        url=(
            f"{get_config().frontend_url}/workspaces/{result.workspace_id}/channels"
            f"?oauth_success=true&account_id={result.account_id}&channel_id={result.channel_id}"
        ),
        status_code=302,
    )


@router.delete(f"{_ACCT_PREFIX}/oauth/youtube")
def disconnect_youtube_oauth_route(
    workspace_id: str,
    channel_id: str,
    account_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Any = Depends(get_db),
    oauth_client: Any = Depends(get_oauth_client),
) -> dict[str, str]:
    """Disconnect a YouTube account: revoke token and remove credential.

    The platform account record is preserved for audit history.
    Only the credential and token are removed.

    Required role: workspace owner or admin.
    """
    try:
        _require_connect_permission(current_user, workspace_id)
    except OAuthInsufficientRoleError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    try:
        disconnect_youtube_account(
            db,
            account_id=account_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            actor=current_user.actor,
            oauth_client=oauth_client,
        )
    except OAuthAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Disconnect failed: {exc}") from exc

    return {"status": "disconnected", "account_id": account_id}


@router.post(f"{_ACCT_PREFIX}/oauth/youtube/verify")
def verify_youtube_connection_route(
    workspace_id: str,
    channel_id: str,
    account_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Any = Depends(get_db),
    oauth_client: Any = Depends(get_oauth_client),
) -> dict[str, Any]:
    """Live YouTube connection verification.

    Calls the YouTube Data API with the account's persisted credential,
    refreshing the access token if expired. Compares the returned YouTube
    Channel ID against the registered external_account_id.

    Returns a sanitized result — no tokens are exposed.
    Fails closed on Channel ID mismatch; never rewrites external_account_id.

    Required role: workspace owner or admin.
    """
    try:
        _require_connect_permission(current_user, workspace_id)
    except OAuthInsufficientRoleError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    try:
        result = verify_youtube_connection(
            db,
            account_id=account_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            oauth_client=oauth_client,
        )
    except OAuthAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Verification failed: {exc}") from exc

    return {
        "account_id": result.account_id,
        "verified": result.verified,
        "registered_channel_id": result.registered_channel_id,
        "live_channel_id": result.live_channel_id,
        "channel_title": result.channel_title,
        "verified_at": result.verified_at_utc.isoformat() if result.verified_at_utc else None,
        "failure_reason": result.failure_reason,
    }


@router.get(f"{_ACCT_PREFIX}/connection")
def get_account_connection_status(
    workspace_id: str,
    channel_id: str,
    account_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    """Return the current OAuth connection status for a platform account.

    Safe to call frequently — does not make live API calls.
    Returns: connected, provider_channel_id, channel_title, verified_at,
    granted_scopes, credential_status, health_status.
    """
    try:
        status = get_connection_status(
            db,
            account_id=account_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
        )
    except OAuthAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "account_id": status.account_id,
        "connected": status.connected,
        "provider_channel_id": status.provider_channel_id,
        "channel_title": status.channel_title,
        "verified_at": status.verified_at_utc.isoformat() if status.verified_at_utc else None,
        "granted_scopes": status.granted_scopes,
        "credential_status": status.credential_status,
        "health_status": status.health_status,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_error_code(error: str) -> str:
    """Allow only alphanumeric and underscore characters in error codes for redirect safety."""
    return "".join(c for c in error if c.isalnum() or c == "_")[:64]
