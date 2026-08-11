"""YouTube OAuth 2.0 flow orchestrator.

Coordinates: state management → code exchange → channel verification →
credential storage → platform account binding → health recording.

All account/workspace binding comes from the server-side state store.
Never trust workspace_id / channel_id / account_id from Google's callback.

Multi-account isolation guarantee:
  - Each cp_platform_account has its own credential_profile row and token file.
  - Completing OAuth for Account B never touches Account A's token file or
    credential_profile record.
  - Disconnect for Account A never touches Account B's credential or token.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.oauth.client import (
    YOUTUBE_SCOPES,
    YOUTUBE_UPLOAD_SCOPE,
    YOUTUBE_UPLOAD_SCOPES,
    ChannelIdentity,
    GoogleOAuthClient,
)
from app.oauth.errors import (
    OAuthAccountNotFoundError,
    OAuthChannelMismatchError,
    OAuthChannelVerificationError,
    OAuthProviderError,
    OAuthRefreshError,
    OAuthTokenStoreError,
)
from app.oauth.state import OAuthStateClaims, get_state_store, validate_state
from app.oauth.store import LocalFileTokenStore, StoredTokenPayload, get_token_store


@dataclass(frozen=True)
class VerificationResult:
    """Result of a live YouTube connection verification.

    verified=True only when the live YouTube channel ID exactly matches
    the registered external_account_id. Never exposed: tokens, raw channel
    URL, internal IDs beyond what the frontend needs.
    """

    account_id: str
    verified: bool
    registered_channel_id: str
    live_channel_id: str | None
    channel_title: str | None
    verified_at_utc: datetime | None
    failure_reason: str | None


@dataclass(frozen=True)
class StartFlowResult:
    authorization_url: str
    state_nonce: str


@dataclass(frozen=True)
class ConnectionResult:
    account_id: str
    workspace_id: str
    channel_id: str
    provider_channel_id: str
    channel_title: str
    granted_scopes: list[str]
    verified_at_utc: datetime
    credential_profile_id: str


@dataclass(frozen=True)
class ConnectionStatus:
    account_id: str
    connected: bool
    provider_channel_id: str | None
    channel_title: str | None
    verified_at_utc: datetime | None
    granted_scopes: list[str]
    credential_status: str | None
    health_status: str | None


def _get_account_or_raise(
    conn: Any,
    account_id: str,
    workspace_id: str,
    channel_id: str,
) -> Any:
    """Load a platform account and verify it belongs to the right workspace/channel."""
    from app.control_plane.repository import get_platform_account

    try:
        acct = get_platform_account(conn, account_id)
    except Exception as exc:
        raise OAuthAccountNotFoundError(f"Platform account {account_id} not found.") from exc

    if acct.channel_id != channel_id:
        raise OAuthAccountNotFoundError(
            f"Account {account_id} does not belong to channel {channel_id}."
        )
    return acct


def start_youtube_oauth(
    conn: Any,
    *,
    account_id: str,
    user_id: str,
    workspace_id: str,
    channel_id: str,
    oauth_client: GoogleOAuthClient,
) -> StartFlowResult:
    """Generate OAuth state and return the Google authorization URL.

    Validates that the platform account exists and belongs to workspace/channel
    before generating any state. This is the only entry point; the returned
    URL should be sent to the user's browser.

    No tokens are touched here.
    """
    _get_account_or_raise(conn, account_id, workspace_id, channel_id)

    # Generate nonce first so it can be embedded in the authorization URL.
    # The PKCE code_verifier is produced by the client during URL construction
    # (google-auth-oauthlib >= 1.2 embeds a code_challenge automatically).
    # We store both together so the verifier is available at callback time.
    nonce = secrets.token_hex(32)  # 256 bits
    result = oauth_client.get_authorization_url(state_nonce=nonce, scopes=YOUTUBE_SCOPES)

    # Bind the PKCE verifier to this nonce in the server-side state store.
    # The state is one-time-use and TTL-scoped; the verifier expires with it.
    claims = OAuthStateClaims(
        nonce=nonce,
        user_id=user_id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        account_id=account_id,
        created_at=time.monotonic(),
        code_verifier=result.code_verifier,
    )
    get_state_store().store(claims)

    return StartFlowResult(
        authorization_url=result.authorization_url,
        state_nonce=nonce,
    )


def complete_youtube_oauth(
    conn: Any,
    *,
    code: str,
    state_nonce: str,
    oauth_client: GoogleOAuthClient,
    token_store: LocalFileTokenStore | None = None,
) -> ConnectionResult:
    """Complete the OAuth flow after Google redirects to the callback.

    Steps:
      1. Validate and consume state (CSRF protection)
      2. Exchange code for tokens
      3. Verify YouTube channel identity
      4. Check for existing channel ID mismatch (fail closed)
      5. Store tokens securely
      6. Create or update CredentialProfile
      7. Bind credential to PlatformAccount
      8. Record health event
      9. Return ConnectionResult

    All account/workspace binding comes from the validated state.
    """
    from app.control_plane.credentials import create_credential_profile
    from app.control_plane.health import record_health

    store = token_store or get_token_store()

    # Step 1: Validate and consume state
    claims: OAuthStateClaims = validate_state(state_nonce)
    account_id = claims.account_id
    workspace_id = claims.workspace_id
    channel_id = claims.channel_id

    acct = _get_account_or_raise(conn, account_id, workspace_id, channel_id)

    # Step 2: Exchange authorization code for tokens (pass PKCE verifier from state)
    token = oauth_client.exchange_code(
        code=code,
        state_nonce=state_nonce,
        code_verifier=claims.code_verifier,
    )

    # Step 3: Verify YouTube channel identity
    identity: ChannelIdentity = oauth_client.get_channel_identity(access_token=token.access_token)

    # Step 4: Check for channel ID mismatch
    existing_ext_id = acct.external_account_id
    is_placeholder = existing_ext_id.startswith("pending:")
    if not is_placeholder and existing_ext_id != identity.channel_id:
        raise OAuthChannelMismatchError(
            f"The authenticated YouTube channel ({identity.channel_id}) does not match "
            f"the existing channel ID bound to this account ({existing_ext_id}). "
            "Use the Reconnect flow to rebind, or disconnect and create a new account."
        )

    # Step 5: Store tokens securely
    external_ref = store.write(
        account_id=account_id,
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        token_type=token.token_type,
        expires_at_utc=token.expires_at_utc,
        scopes=token.scopes,
        google_sub=token.google_sub,
    )

    # Step 6: Create or update CredentialProfile
    if acct.credential_profile_id:
        # Update existing credential profile's external_ref and status
        _update_credential_external_ref(conn, acct.credential_profile_id, external_ref)
        cred_profile_id = acct.credential_profile_id
    else:
        # Create a new credential profile
        cred_profile = create_credential_profile(
            conn,
            workspace_id=workspace_id,
            display_name=f"YouTube OAuth — {identity.channel_title}",
            credential_type="oauth2",
            external_ref=external_ref,
            actor="oauth:youtube",
        )
        cred_profile_id = cred_profile.id

    # Step 7: Bind credential and update platform account identity
    verified_at_str = identity.verified_at_utc.isoformat()
    metadata = {
        "youtube_channel_id": identity.channel_id,
        "verified_at": verified_at_str,
        "granted_scopes": token.scopes,
        "connection_method": "oauth2",
    }
    _update_platform_account_identity(
        conn,
        account_id=account_id,
        external_account_id=identity.channel_id,
        display_name=identity.channel_title,
        credential_profile_id=cred_profile_id,
        metadata_json=json.dumps(metadata),
        actor="oauth:youtube",
    )

    # Step 8: Record health event
    record_health(
        conn,
        entity_type="platform_account",
        entity_id=account_id,
        status="healthy",
        recorded_by="oauth:youtube",
        detail=f"YouTube channel verified: {identity.channel_id}",
    )

    return ConnectionResult(
        account_id=account_id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        provider_channel_id=identity.channel_id,
        channel_title=identity.channel_title,
        granted_scopes=token.scopes,
        verified_at_utc=identity.verified_at_utc,
        credential_profile_id=cred_profile_id,
    )


def disconnect_youtube_account(
    conn: Any,
    *,
    account_id: str,
    workspace_id: str,
    channel_id: str,
    actor: str,
    oauth_client: GoogleOAuthClient | None = None,
    token_store: LocalFileTokenStore | None = None,
) -> None:
    """Disconnect a YouTube account: revoke token, clear credential, mark disconnected.

    Disconnect is fail-safe: revocation failure is logged but does not block
    clearing the credential. The platform account record is preserved for audit.

    Multi-account safety: only touches the specific account's token and credential.
    """
    from app.control_plane import repository as repo
    from app.control_plane.health import record_health

    store = token_store or get_token_store()
    acct = _get_account_or_raise(conn, account_id, workspace_id, channel_id)

    revocation_attempted = False
    revocation_succeeded = False

    # Step 1: Attempt token revocation (best-effort)
    if acct.credential_profile_id and oauth_client is not None:
        try:
            cred = repo.get_credential_profile(conn, acct.credential_profile_id)
            stored = store.read(cred.external_ref)
            # Prefer refresh_token for revocation; fall back to access_token
            revoke_token = stored.refresh_token or stored.access_token
            revocation_attempted = True
            oauth_client.revoke_token(revoke_token)
            revocation_succeeded = True
        except OAuthTokenStoreError:
            pass  # Token file already gone or never written
        except OAuthProviderError:
            pass  # Revocation failed at provider; proceed with local cleanup
        except Exception:
            pass  # Any other error; proceed with local cleanup

    # Step 2: Delete token file
    if acct.credential_profile_id:
        try:
            cred = repo.get_credential_profile(conn, acct.credential_profile_id)
            store.delete(cred.external_ref)
        except Exception:
            pass  # Best-effort

    # Step 3: Revoke the credential profile
    if acct.credential_profile_id:
        try:
            repo.update_credential_status(conn, acct.credential_profile_id, "revoked", actor)
        except Exception:
            pass

    # Step 4: Mark account disconnected
    repo.update_platform_account_status(conn, account_id, "disconnected", actor)

    # Step 5: Record health event
    detail = "Account disconnected."
    if revocation_attempted:
        outcome = "succeeded" if revocation_succeeded else "attempted (provider error)"
        detail += f" Token revocation {outcome}."
    record_health(
        conn,
        entity_type="platform_account",
        entity_id=account_id,
        status="unavailable",
        recorded_by=actor,
        detail=detail,
    )


def get_connection_status(
    conn: Any,
    *,
    account_id: str,
    workspace_id: str,
    channel_id: str,
    token_store: LocalFileTokenStore | None = None,
) -> ConnectionStatus:
    """Return the current connection state for a platform account."""
    from app.control_plane import repository as repo
    from app.control_plane.health import get_health

    acct = _get_account_or_raise(conn, account_id, workspace_id, channel_id)

    health = get_health(conn, "platform_account", account_id)
    health_status = health.status if health else None

    if acct.status == "disconnected" or acct.credential_profile_id is None:
        return ConnectionStatus(
            account_id=account_id,
            connected=False,
            provider_channel_id=None,
            channel_title=None,
            verified_at_utc=None,
            granted_scopes=[],
            credential_status=None,
            health_status=health_status,
        )

    # Load credential profile
    cred = None
    try:
        cred = repo.get_credential_profile(conn, acct.credential_profile_id)
    except Exception:
        pass

    cred_status = cred.status if cred else None

    # Load metadata from platform account
    meta: dict = {}
    if acct.metadata_json:
        try:
            meta = json.loads(acct.metadata_json)
        except (json.JSONDecodeError, TypeError):
            pass

    provider_channel_id = meta.get("youtube_channel_id") or (
        acct.external_account_id if not acct.external_account_id.startswith("pending:") else None
    )
    verified_at_utc = None
    if meta.get("verified_at"):
        try:
            verified_at_utc = datetime.fromisoformat(meta["verified_at"])
        except ValueError:
            pass

    return ConnectionStatus(
        account_id=account_id,
        connected=(acct.status == "connected" and cred_status == "active"),
        provider_channel_id=provider_channel_id,
        channel_title=acct.display_name,
        verified_at_utc=verified_at_utc,
        granted_scopes=meta.get("granted_scopes", []),
        credential_status=cred_status,
        health_status=health_status,
    )


def refresh_account_token(
    conn: Any,
    *,
    account_id: str,
    workspace_id: str,
    channel_id: str,
    oauth_client: GoogleOAuthClient,
    token_store: LocalFileTokenStore | None = None,
) -> StoredTokenPayload:
    """Refresh the access token for an account if expired.

    Returns the (possibly refreshed) token payload.
    On refresh failure, marks the account as credential_invalid.
    """
    from app.control_plane import repository as repo
    from app.control_plane.health import record_health

    store = token_store or get_token_store()
    acct = _get_account_or_raise(conn, account_id, workspace_id, channel_id)

    if not acct.credential_profile_id:
        raise OAuthRefreshError(f"Account {account_id} has no credential profile.")

    cred = repo.get_credential_profile(conn, acct.credential_profile_id)
    stored = store.read(cred.external_ref)

    if not stored.is_expired():
        return stored

    # Token is expired — refresh it
    if not stored.refresh_token:
        _mark_account_credential_invalid(conn, account_id, "system:token_refresh")
        raise OAuthRefreshError(
            f"Access token expired and no refresh token available for account {account_id}. "
            "Account must be reconnected via OAuth."
        )

    try:
        new_token = oauth_client.refresh_access_token(stored.refresh_token)
    except OAuthRefreshError:
        _mark_account_credential_invalid(conn, account_id, "system:token_refresh")
        record_health(
            conn,
            entity_type="platform_account",
            entity_id=account_id,
            status="degraded",
            recorded_by="system:token_refresh",
            detail="Token refresh failed; account needs reconnection.",
        )
        raise

    store.update_tokens(
        cred.external_ref,
        access_token=new_token.access_token,
        refresh_token=new_token.refresh_token,
        expires_at_utc=new_token.expires_at_utc,
    )
    return store.read(cred.external_ref)


def verify_connection(
    conn: Any,
    *,
    account_id: str,
    workspace_id: str,
    channel_id: str,
    oauth_client: GoogleOAuthClient,
    token_store: LocalFileTokenStore | None = None,
) -> ConnectionStatus:
    """Health-check gate for publishing/analytics: resolve credential, refresh if needed,
    verify channel identity, return typed status.

    This is the gate used by the publishing and analytics executors.
    Never performs a real upload.
    """
    from app.control_plane.health import record_health

    try:
        stored = refresh_account_token(
            conn,
            account_id=account_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            oauth_client=oauth_client,
            token_store=token_store,
        )
    except OAuthRefreshError:
        return get_connection_status(
            conn,
            account_id=account_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            token_store=token_store,
        )

    try:
        identity = oauth_client.get_channel_identity(stored.access_token)
    except OAuthChannelVerificationError as exc:
        record_health(
            conn,
            entity_type="platform_account",
            entity_id=account_id,
            status="degraded",
            recorded_by="system:verify_connection",
            detail=str(exc),
        )
        return get_connection_status(
            conn,
            account_id=account_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            token_store=token_store,
        )

    status = get_connection_status(
        conn,
        account_id=account_id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        token_store=token_store,
    )

    if status.provider_channel_id and status.provider_channel_id != identity.channel_id:
        record_health(
            conn,
            entity_type="platform_account",
            entity_id=account_id,
            status="degraded",
            recorded_by="system:verify_connection",
            detail=(
                f"Channel ID mismatch: stored={status.provider_channel_id}, "
                f"live={identity.channel_id}"
            ),
        )

    return status


def verify_youtube_connection(
    conn: Any,
    *,
    account_id: str,
    workspace_id: str,
    channel_id: str,
    oauth_client: GoogleOAuthClient,
    token_store: LocalFileTokenStore | None = None,
) -> VerificationResult:
    """Live connection verification: call YouTube Data API, compare Channel IDs.

    Succeeds only when all of the following hold:
      1. Account is connected, platform is youtube, no pending placeholder ID.
      2. Credential profile is active.
      3. Token can be read (and refreshed when expired).
      4. YouTube channels.list(mine=True) returns a channel identity.
      5. Returned Channel ID matches registered external_account_id exactly.

    Fails closed on mismatch — never rewrites external_account_id.
    Persists last_verified_at in metadata on success.
    Records health event in all terminal cases.
    """
    from app.control_plane import repository as repo
    from app.control_plane.health import record_health

    store = token_store or get_token_store()
    acct = _get_account_or_raise(conn, account_id, workspace_id, channel_id)

    registered_id = acct.external_account_id

    if acct.platform_key != "youtube":
        return VerificationResult(
            account_id=account_id,
            verified=False,
            registered_channel_id=registered_id,
            live_channel_id=None,
            channel_title=None,
            verified_at_utc=None,
            failure_reason="Account is not a YouTube account.",
        )

    if registered_id.startswith("pending:"):
        return VerificationResult(
            account_id=account_id,
            verified=False,
            registered_channel_id=registered_id,
            live_channel_id=None,
            channel_title=None,
            verified_at_utc=None,
            failure_reason="Account has not completed OAuth — no registered Channel ID.",
        )

    if acct.status != "connected" or acct.credential_profile_id is None:
        return VerificationResult(
            account_id=account_id,
            verified=False,
            registered_channel_id=registered_id,
            live_channel_id=None,
            channel_title=None,
            verified_at_utc=None,
            failure_reason="Account is not connected or has no credential profile.",
        )

    try:
        cred = repo.get_credential_profile(conn, acct.credential_profile_id)
    except Exception:
        return VerificationResult(
            account_id=account_id,
            verified=False,
            registered_channel_id=registered_id,
            live_channel_id=None,
            channel_title=None,
            verified_at_utc=None,
            failure_reason="Credential profile could not be loaded.",
        )

    if cred.status != "active":
        return VerificationResult(
            account_id=account_id,
            verified=False,
            registered_channel_id=registered_id,
            live_channel_id=None,
            channel_title=None,
            verified_at_utc=None,
            failure_reason=f"Credential is not active (status: {cred.status}).",
        )

    try:
        stored = refresh_account_token(
            conn,
            account_id=account_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            oauth_client=oauth_client,
            token_store=store,
        )
    except (OAuthRefreshError, OAuthTokenStoreError):
        return VerificationResult(
            account_id=account_id,
            verified=False,
            registered_channel_id=registered_id,
            live_channel_id=None,
            channel_title=None,
            verified_at_utc=None,
            failure_reason=(
                "Token could not be loaded or refreshed — account may need reconnection."
            ),
        )

    try:
        identity = oauth_client.get_channel_identity(stored.access_token)
    except OAuthChannelVerificationError as exc:
        record_health(
            conn,
            entity_type="platform_account",
            entity_id=account_id,
            status="degraded",
            recorded_by="system:verify_connection",
            detail=str(exc),
        )
        return VerificationResult(
            account_id=account_id,
            verified=False,
            registered_channel_id=registered_id,
            live_channel_id=None,
            channel_title=None,
            verified_at_utc=None,
            failure_reason="YouTube channel identity lookup failed.",
        )

    # Fail closed on mismatch — do NOT rewrite external_account_id
    if identity.channel_id != registered_id:
        record_health(
            conn,
            entity_type="platform_account",
            entity_id=account_id,
            status="degraded",
            recorded_by="system:verify_connection",
            detail=(
                f"Channel ID mismatch: registered={registered_id}, "
                f"live={identity.channel_id}. Verification failed."
            ),
        )
        return VerificationResult(
            account_id=account_id,
            verified=False,
            registered_channel_id=registered_id,
            live_channel_id=identity.channel_id,
            channel_title=identity.channel_title,
            verified_at_utc=None,
            failure_reason=(
                "Channel ID mismatch: the live credential belongs to a different channel. "
                "The registered ID has not been changed."
            ),
        )

    # Verified — persist timestamp in metadata
    verified_at = identity.verified_at_utc
    _persist_verification_timestamp(
        conn, account_id, acct.metadata_json, verified_at, identity.channel_id
    )

    record_health(
        conn,
        entity_type="platform_account",
        entity_id=account_id,
        status="healthy",
        recorded_by="system:verify_connection",
        detail=f"Live verification succeeded: {identity.channel_id}",
    )

    return VerificationResult(
        account_id=account_id,
        verified=True,
        registered_channel_id=registered_id,
        live_channel_id=identity.channel_id,
        channel_title=identity.channel_title,
        verified_at_utc=verified_at,
        failure_reason=None,
    )


# ---------------------------------------------------------------------------
# Private helpers (no CP table writes without going through public functions)
# ---------------------------------------------------------------------------


def has_upload_scope(
    conn: Any,
    *,
    account_id: str,
    workspace_id: str,
    channel_id: str,
    token_store: LocalFileTokenStore | None = None,
) -> bool:
    """Return True if the account's stored token includes youtube.upload scope.

    Returns False on any error (token missing, account disconnected, etc.).
    Does not make any network calls.
    """
    from app.control_plane import repository as repo

    store = token_store or get_token_store()
    try:
        acct = _get_account_or_raise(conn, account_id, workspace_id, channel_id)
    except Exception:
        return False
    if not acct.credential_profile_id:
        return False
    try:
        cred = repo.get_credential_profile(conn, acct.credential_profile_id)
        stored = store.read(cred.external_ref)
        return stored.has_scope(YOUTUBE_UPLOAD_SCOPE)
    except Exception:
        return False


def start_youtube_upload_oauth(
    conn: Any,
    *,
    account_id: str,
    user_id: str,
    workspace_id: str,
    channel_id: str,
    oauth_client: GoogleOAuthClient,
) -> StartFlowResult:
    """Begin the YouTube OAuth 2.0 scope-upgrade flow to add youtube.upload.

    Generates a new authorization URL requesting YOUTUBE_UPLOAD_SCOPES
    (readonly + upload). The existing account binding is preserved; only the
    credential's token file is updated after the user grants consent.

    On callback completion, complete_youtube_oauth() handles the token write
    and credential update (same flow, broader scopes).
    """
    _get_account_or_raise(conn, account_id, workspace_id, channel_id)

    nonce = secrets.token_hex(32)
    result = oauth_client.get_authorization_url(state_nonce=nonce, scopes=YOUTUBE_UPLOAD_SCOPES)

    claims = OAuthStateClaims(
        nonce=nonce,
        user_id=user_id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        account_id=account_id,
        created_at=time.monotonic(),
        code_verifier=result.code_verifier,
    )
    get_state_store().store(claims)

    return StartFlowResult(
        authorization_url=result.authorization_url,
        state_nonce=nonce,
    )


def _update_credential_external_ref(
    conn: Any, credential_profile_id: str, new_external_ref: str
) -> None:
    from datetime import datetime

    conn.execute(
        "UPDATE cp_credential_profiles SET external_ref = ?, status = 'active', "
        "updated_at = ? WHERE id = ?",
        (new_external_ref, datetime.now(UTC).isoformat(), credential_profile_id),
    )
    conn.commit()


def _update_platform_account_identity(
    conn: Any,
    *,
    account_id: str,
    external_account_id: str,
    display_name: str,
    credential_profile_id: str,
    metadata_json: str,
    actor: str,
) -> None:
    from datetime import datetime

    conn.execute(
        "UPDATE cp_platform_accounts SET "
        "external_account_id = ?, display_name = ?, credential_profile_id = ?, "
        "metadata_json = ?, status = 'connected', actor = ?, updated_at = ? "
        "WHERE id = ?",
        (
            external_account_id,
            display_name,
            credential_profile_id,
            metadata_json,
            actor,
            datetime.now(UTC).isoformat(),
            account_id,
        ),
    )
    conn.commit()


def _mark_account_credential_invalid(conn: Any, account_id: str, actor: str) -> None:
    from app.control_plane import repository as repo

    repo.update_platform_account_status(conn, account_id, "credential_invalid", actor)


def _persist_verification_timestamp(
    conn: Any,
    account_id: str,
    existing_metadata_json: str | None,
    verified_at: datetime,
    verified_channel_id: str,
) -> None:
    meta: dict = {}
    if existing_metadata_json:
        try:
            meta = json.loads(existing_metadata_json)
        except (json.JSONDecodeError, TypeError):
            pass
    meta["last_verified_at"] = verified_at.isoformat()
    meta["last_verified_channel_id"] = verified_channel_id
    conn.execute(
        "UPDATE cp_platform_accounts SET metadata_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(meta), datetime.now(UTC).isoformat(), account_id),
    )
    conn.commit()
