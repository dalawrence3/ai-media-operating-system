"""Tests for verify_youtube_connection — live channel identity verification.

Security/isolation guarantees verified:
  - workspace A cannot verify workspace B's account
  - one platform account cannot use another account's credential
  - disconnected account cannot verify
  - missing credential fails safely
  - non-YouTube account cannot use YouTube verification
  - returned Channel ID mismatch fails closed — no external_account_id rewrite
  - correct account succeeds and persists verification timestamp
  - two YouTube accounts resolve their own credentials independently
  - RBAC enforced at the route layer (admin/owner only)
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth.service import AuthService
from app.auth.tokens import create_access_token
from app.control_plane import orchestrator as cp_orch
from app.control_plane import repository as repo
from app.control_plane.models import (
    ChannelDraft,
    OrganizationDraft,
    PlatformAccountDraft,
    WorkspaceDraft,
)
from app.core.config import reset_config
from app.core.database import open_db
from app.oauth.client import FakeGoogleOAuthClient
from app.oauth.errors import (
    OAuthAccountNotFoundError,
    OAuthChannelVerificationError,
)
from app.oauth.flow import (
    complete_youtube_oauth,
    start_youtube_oauth,
    verify_youtube_connection,
)
from app.oauth.state import InMemoryOAuthStateStore, reset_state_store, set_state_store
from app.oauth.store import LocalFileTokenStore, reset_token_store, set_token_store

_SECRET = "test-secret-verify-32-bytes-xxxx"
_ACCESS_EXPIRE = 900


# ---------------------------------------------------------------------------
# Fixtures (unit-level — no HTTP layer)
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    conn = open_db(tmp_path / "test.db")
    repo.ensure_platform(conn, "plt-youtube-1", "youtube", "YouTube")
    repo.ensure_platform(conn, "plt-instagram-1", "instagram", "Instagram")
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def fresh_state_store():
    store = InMemoryOAuthStateStore()
    set_state_store(store)
    yield store
    reset_state_store()


@pytest.fixture
def token_store(tmp_path):
    store = LocalFileTokenStore(tmp_path / "tokens")
    set_token_store(store)
    yield store
    reset_token_store()


@pytest.fixture
def fake_client():
    return FakeGoogleOAuthClient(
        fake_channel_id="UCverify_test_001",
        fake_channel_title="Verify Test Channel",
    )


@pytest.fixture
def entities(db):
    ws = cp_orch.provision_workspace(db, name="TestWS", slug="test-ws", actor="test")
    ch = cp_orch.provision_channel(
        db, workspace_id=ws.id, name="TestCh", slug="test-ch", actor="test"
    )
    acct = _create_pending_account(db, channel_id=ch.id, platform_key="youtube")
    return ws, ch, acct


def _uid() -> str:
    return str(uuid.uuid4())


def _create_pending_account(db, *, channel_id: str, platform_key: str = "youtube") -> object:
    platform = repo.get_platform_by_key(db, platform_key)
    acct_id = _uid()
    draft = PlatformAccountDraft(
        id=acct_id,
        channel_id=channel_id,
        platform_id=platform.id,
        platform_key=platform_key,
        external_account_id=f"pending:{acct_id}",
        display_name=f"Pending {platform_key.title()} Account",
        actor="test",
        status="connected",
    )
    return repo.create_platform_account(db, draft)


def _do_full_connect(db, ws, ch, acct, client, token_store) -> object:
    r = start_youtube_oauth(
        db,
        account_id=acct.id,
        user_id="u",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=client,
    )
    return complete_youtube_oauth(
        db,
        code="code",
        state_nonce=r.state_nonce,
        oauth_client=client,
        token_store=token_store,
    )


# ---------------------------------------------------------------------------
# Successful verification
# ---------------------------------------------------------------------------


def test_verification_succeeds_when_channel_ids_match(db, fake_client, token_store, entities):
    ws, ch, acct = entities
    _do_full_connect(db, ws, ch, acct, fake_client, token_store)

    result = verify_youtube_connection(
        db,
        account_id=acct.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=fake_client,
        token_store=token_store,
    )

    assert result.verified is True
    assert result.registered_channel_id == "UCverify_test_001"
    assert result.live_channel_id == "UCverify_test_001"
    assert result.channel_title == "Verify Test Channel"
    assert result.verified_at_utc is not None
    assert result.failure_reason is None


def test_verification_persists_timestamp_in_metadata(db, fake_client, token_store, entities):
    ws, ch, acct = entities
    _do_full_connect(db, ws, ch, acct, fake_client, token_store)

    verify_youtube_connection(
        db,
        account_id=acct.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=fake_client,
        token_store=token_store,
    )

    updated = repo.get_platform_account(db, acct.id)
    meta = json.loads(updated.metadata_json)
    assert "last_verified_at" in meta
    assert meta["last_verified_channel_id"] == "UCverify_test_001"


# ---------------------------------------------------------------------------
# Channel ID mismatch — fail closed
# ---------------------------------------------------------------------------


def test_verification_fails_when_live_channel_id_does_not_match(db, token_store, entities):
    ws, ch, acct = entities
    connect_client = FakeGoogleOAuthClient(fake_channel_id="UCregistered_channel")
    _do_full_connect(db, ws, ch, acct, connect_client, token_store)

    # Verification client returns a DIFFERENT channel ID
    mismatch_client = FakeGoogleOAuthClient(fake_channel_id="UCdifferent_channel")

    result = verify_youtube_connection(
        db,
        account_id=acct.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=mismatch_client,
        token_store=token_store,
    )

    assert result.verified is False
    assert result.live_channel_id == "UCdifferent_channel"
    assert result.registered_channel_id == "UCregistered_channel"
    assert result.failure_reason is not None
    assert "mismatch" in result.failure_reason.lower()


def test_verification_mismatch_does_not_rewrite_external_account_id(db, token_store, entities):
    ws, ch, acct = entities
    connect_client = FakeGoogleOAuthClient(fake_channel_id="UCoriginal_channel")
    _do_full_connect(db, ws, ch, acct, connect_client, token_store)

    mismatch_client = FakeGoogleOAuthClient(fake_channel_id="UCattacker_channel")
    verify_youtube_connection(
        db,
        account_id=acct.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=mismatch_client,
        token_store=token_store,
    )

    # external_account_id must remain unchanged
    unchanged = repo.get_platform_account(db, acct.id)
    assert unchanged.external_account_id == "UCoriginal_channel"


# ---------------------------------------------------------------------------
# Disconnected account
# ---------------------------------------------------------------------------


def test_verification_fails_for_disconnected_account(db, fake_client, token_store, entities):
    ws, ch, acct = entities
    _do_full_connect(db, ws, ch, acct, fake_client, token_store)

    # Mark account disconnected
    repo.update_platform_account_status(db, acct.id, "disconnected", "test")

    result = verify_youtube_connection(
        db,
        account_id=acct.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=fake_client,
        token_store=token_store,
    )

    assert result.verified is False
    assert "not connected" in result.failure_reason.lower()


# ---------------------------------------------------------------------------
# Pending/pre-OAuth account (no real channel ID yet)
# ---------------------------------------------------------------------------


def test_verification_fails_for_pending_account(db, fake_client, token_store, entities):
    ws, ch, acct = entities
    # Do NOT connect — external_account_id is still "pending:<uuid>"

    result = verify_youtube_connection(
        db,
        account_id=acct.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=fake_client,
        token_store=token_store,
    )

    assert result.verified is False
    assert result.live_channel_id is None
    assert "pending" in result.failure_reason.lower() or "oauth" in result.failure_reason.lower()


# ---------------------------------------------------------------------------
# Non-YouTube account
# ---------------------------------------------------------------------------


def test_verification_fails_for_non_youtube_account(db, fake_client, token_store, entities):
    ws, ch, _ = entities
    instagram_acct = _create_pending_account(db, channel_id=ch.id, platform_key="instagram")

    result = verify_youtube_connection(
        db,
        account_id=instagram_acct.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=fake_client,
        token_store=token_store,
    )

    assert result.verified is False
    assert result.failure_reason is not None
    assert "youtube" in result.failure_reason.lower()


# ---------------------------------------------------------------------------
# Missing credential / token file
# ---------------------------------------------------------------------------


def test_verification_fails_when_credential_profile_missing(db, fake_client, token_store, entities):
    ws, ch, acct = entities
    _do_full_connect(db, ws, ch, acct, fake_client, token_store)

    # Manually delete the token file to simulate a missing credential
    updated = repo.get_platform_account(db, acct.id)
    cred = repo.get_credential_profile(db, updated.credential_profile_id)
    token_path = Path(cred.external_ref[len("file://") :])
    token_path.unlink(missing_ok=True)

    result = verify_youtube_connection(
        db,
        account_id=acct.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=fake_client,
        token_store=token_store,
    )

    assert result.verified is False
    assert result.failure_reason is not None


def test_verification_fails_when_credential_status_not_active(
    db, fake_client, token_store, entities
):
    ws, ch, acct = entities
    _do_full_connect(db, ws, ch, acct, fake_client, token_store)

    updated = repo.get_platform_account(db, acct.id)
    # Mark credential revoked
    repo.update_credential_status(db, updated.credential_profile_id, "revoked", "test")

    result = verify_youtube_connection(
        db,
        account_id=acct.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=fake_client,
        token_store=token_store,
    )

    assert result.verified is False
    reason = result.failure_reason.lower()
    assert "active" in reason or "credential" in reason


# ---------------------------------------------------------------------------
# YouTube API identity lookup failure
# ---------------------------------------------------------------------------


def test_verification_fails_when_channel_identity_lookup_fails(db, token_store, entities):
    ws, ch, acct = entities
    connect_client = FakeGoogleOAuthClient(fake_channel_id="UCsome_channel")
    _do_full_connect(db, ws, ch, acct, connect_client, token_store)

    bad_identity_client = FakeGoogleOAuthClient(
        fake_channel_id="UCsome_channel",
        fail_identity=OAuthChannelVerificationError("no channel found"),
    )

    result = verify_youtube_connection(
        db,
        account_id=acct.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=bad_identity_client,
        token_store=token_store,
    )

    assert result.verified is False
    assert result.live_channel_id is None


# ---------------------------------------------------------------------------
# Channel isolation — wrong channel is rejected at the function level
# Workspace isolation is enforced at the route/JWT layer (see TestVerifyRoute)
# ---------------------------------------------------------------------------


def test_verification_raises_for_wrong_channel(db, fake_client, token_store, entities):
    ws, ch, acct = entities
    _do_full_connect(db, ws, ch, acct, fake_client, token_store)

    with pytest.raises(OAuthAccountNotFoundError):
        verify_youtube_connection(
            db,
            account_id=acct.id,
            workspace_id=ws.id,
            channel_id="wrong-channel-id",
            oauth_client=fake_client,
            token_store=token_store,
        )


def test_verification_raises_for_nonexistent_account(db, fake_client, token_store, entities):
    ws, ch, _ = entities
    with pytest.raises(OAuthAccountNotFoundError):
        verify_youtube_connection(
            db,
            account_id="nonexistent-account-id",
            workspace_id=ws.id,
            channel_id=ch.id,
            oauth_client=fake_client,
            token_store=token_store,
        )


# ---------------------------------------------------------------------------
# Multi-account isolation
# ---------------------------------------------------------------------------


def test_two_connected_accounts_verify_with_own_credentials(db, token_store, entities):
    ws, ch, acct_a = entities
    acct_b = _create_pending_account(db, channel_id=ch.id, platform_key="youtube")

    client_a = FakeGoogleOAuthClient(
        fake_channel_id="UCA_channel",
        fake_channel_title="Channel A",
        fake_refresh_token="refresh_A",
    )
    client_b = FakeGoogleOAuthClient(
        fake_channel_id="UCB_channel",
        fake_channel_title="Channel B",
        fake_refresh_token="refresh_B",
    )

    _do_full_connect(db, ws, ch, acct_a, client_a, token_store)
    _do_full_connect(db, ws, ch, acct_b, client_b, token_store)

    result_a = verify_youtube_connection(
        db,
        account_id=acct_a.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=client_a,
        token_store=token_store,
    )
    result_b = verify_youtube_connection(
        db,
        account_id=acct_b.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=client_b,
        token_store=token_store,
    )

    assert result_a.verified is True
    assert result_a.live_channel_id == "UCA_channel"
    assert result_b.verified is True
    assert result_b.live_channel_id == "UCB_channel"


def test_account_b_cannot_use_account_a_credential_to_verify(db, token_store, entities):
    ws, ch, acct_a = entities
    acct_b = _create_pending_account(db, channel_id=ch.id, platform_key="youtube")

    client_a = FakeGoogleOAuthClient(fake_channel_id="UCA_channel")
    client_b = FakeGoogleOAuthClient(fake_channel_id="UCB_channel")

    _do_full_connect(db, ws, ch, acct_a, client_a, token_store)
    _do_full_connect(db, ws, ch, acct_b, client_b, token_store)

    # Verify account_b using account_a's client — each account has its own
    # token file, so the token resolved is always acct_b's own token.
    # Even if the client returned UCA_channel, the registered ID for acct_b
    # is UCB_channel, so the IDs must mismatch → verified=False.
    mismatch_client = FakeGoogleOAuthClient(fake_channel_id="UCA_channel")

    result = verify_youtube_connection(
        db,
        account_id=acct_b.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=mismatch_client,
        token_store=token_store,
    )

    assert result.verified is False
    assert result.registered_channel_id == "UCB_channel"
    assert result.live_channel_id == "UCA_channel"


# ---------------------------------------------------------------------------
# Token refresh during verification
# ---------------------------------------------------------------------------


def test_verification_refreshes_expired_token(db, token_store, entities):
    ws, ch, acct = entities
    client = FakeGoogleOAuthClient(fake_channel_id="UCsome_channel")
    _do_full_connect(db, ws, ch, acct, client, token_store)

    # Expire the access token in the token file
    updated = repo.get_platform_account(db, acct.id)
    cred = repo.get_credential_profile(db, updated.credential_profile_id)
    token_path = Path(cred.external_ref[len("file://") :])
    payload = json.loads(token_path.read_text())
    payload["expires_at_utc"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    token_path.write_text(json.dumps(payload))

    # Verification should trigger refresh internally and still succeed
    result = verify_youtube_connection(
        db,
        account_id=acct.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=client,
        token_store=token_store,
    )

    assert result.verified is True
    assert result.live_channel_id == "UCsome_channel"


# ===========================================================================
# Route-layer tests (RBAC)
# ===========================================================================


def _token(user_id: int, email: str, roles: dict[str, str]) -> str:
    return create_access_token(
        user_id=user_id,
        email=email,
        workspace_roles=roles,
        secret_key=_SECRET,
        expire_seconds=_ACCESS_EXPIRE,
    )


def _uid_int() -> int:
    import random

    return random.randint(10_000, 999_999)


@pytest.fixture(autouse=True)
def _reset_config():
    reset_config()
    yield
    reset_config()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "verify_routes_test.db"


@pytest.fixture
def db_conn(db_path: Path, monkeypatch):
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    conn = open_db(db_path)
    repo.ensure_platform(conn, "plt-yt-v1", "youtube", "YouTube")
    yield conn
    conn.close()


@pytest.fixture
def svc():
    return AuthService(
        secret_key=_SECRET,
        access_expire=_ACCESS_EXPIRE,
        refresh_expire=3600 * 24 * 7,
    )


@pytest.fixture
def workspace(db_conn):
    org = repo.create_organization(
        db_conn, OrganizationDraft(id=_uid(), name="VOrg", slug="v-org", actor="cli")
    )
    ws = repo.create_workspace(
        db_conn,
        WorkspaceDraft(id=_uid(), name="VWS", slug="v-ws", actor="cli", organization_id=org.id),
    )
    db_conn.commit()
    return ws


@pytest.fixture
def channel(db_conn, workspace):
    ch = repo.create_channel(
        db_conn,
        ChannelDraft(id=_uid(), workspace_id=workspace.id, name="VCh", slug="v-ch", actor="cli"),
    )
    db_conn.commit()
    return ch


@pytest.fixture
def connected_account(db_conn, channel, tmp_path, monkeypatch):
    platform = repo.get_platform_by_key(db_conn, "youtube")
    acct_id = _uid()
    draft = PlatformAccountDraft(
        id=acct_id,
        channel_id=channel.id,
        platform_id=platform.id,
        platform_key="youtube",
        external_account_id=f"pending:{acct_id}",
        display_name="Route Verify Account",
        actor="test",
        status="connected",
    )
    acct = repo.create_platform_account(db_conn, draft)
    db_conn.commit()
    return acct


def _make_test_client(db_path, monkeypatch, fake_client, token_store):
    monkeypatch.setenv("ACE_ENV", "production")
    monkeypatch.setenv("ACE_SECRET_KEY", _SECRET)
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    reset_config()
    from app.api.main import app
    from app.api.routes.oauth import get_oauth_client

    app.dependency_overrides[get_oauth_client] = lambda: fake_client
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def api_client(db_path, monkeypatch, token_store):
    fake = FakeGoogleOAuthClient(
        fake_channel_id="UCroute_verify_channel",
        fake_channel_title="Route Verify Channel",
    )
    yield from _make_test_client(db_path, monkeypatch, fake, token_store)


def _admin_token(svc, db_conn, workspace_id: str) -> str:
    user_id = svc.register_user(db_conn, f"admin{_uid()}@ex.com", "hunter2-long-enough-pass")
    svc.assign_workspace_role(db_conn, user_id, workspace_id, "admin")
    return _token(user_id, "admin@ex.com", {workspace_id: "admin"})


def _operator_token(svc, db_conn, workspace_id: str) -> str:
    user_id = svc.register_user(db_conn, f"op{_uid()}@ex.com", "hunter2-long-enough-pass")
    svc.assign_workspace_role(db_conn, user_id, workspace_id, "operator")
    return _token(user_id, "op@ex.com", {workspace_id: "operator"})


def _verify_url(ws_id, ch_id, acct_id):
    return f"/api/v1/workspaces/{ws_id}/channels/{ch_id}/accounts/{acct_id}/oauth/youtube/verify"


def _start_url(ws_id, ch_id, acct_id):
    return f"/api/v1/workspaces/{ws_id}/channels/{ch_id}/accounts/{acct_id}/oauth/youtube/start"


def _connect_account_via_api(api_client, svc, db_conn, workspace, channel, account):
    """Helper: drive the full OAuth start+callback via the API, returning admin token."""
    import urllib.parse

    token = _admin_token(svc, db_conn, workspace.id)
    r_start = api_client.post(
        _start_url(workspace.id, channel.id, account.id),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r_start.status_code == 200, r_start.text
    state = urllib.parse.parse_qs(urllib.parse.urlparse(r_start.json()["authorization_url"]).query)[
        "state"
    ][0]
    api_client.get(
        f"/api/v1/oauth/youtube/callback?code=fake_code&state={state}",
        follow_redirects=False,
    )
    return token


class TestVerifyRoute:
    def test_unauthenticated_returns_401(self, api_client, workspace, channel, connected_account):
        r = api_client.post(_verify_url(workspace.id, channel.id, connected_account.id))
        assert r.status_code == 401

    def test_operator_returns_403(
        self, api_client, svc, db_conn, workspace, channel, connected_account
    ):
        token = _operator_token(svc, db_conn, workspace.id)
        r = api_client.post(
            _verify_url(workspace.id, channel.id, connected_account.id),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    def test_cross_workspace_returns_403(self, api_client, workspace, channel, connected_account):
        other_ws_id = _uid()
        tok = _token(9999, "other@ex.com", {other_ws_id: "admin"})
        r = api_client.post(
            _verify_url(workspace.id, channel.id, connected_account.id),
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 403

    def test_nonexistent_account_returns_404(self, api_client, svc, db_conn, workspace, channel):
        token = _admin_token(svc, db_conn, workspace.id)
        r = api_client.post(
            _verify_url(workspace.id, channel.id, "no-such-account-id"),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404

    def test_admin_can_verify_connected_account(
        self, api_client, svc, db_conn, workspace, channel, connected_account
    ):
        token = _connect_account_via_api(
            api_client, svc, db_conn, workspace, channel, connected_account
        )
        r = api_client.post(
            _verify_url(workspace.id, channel.id, connected_account.id),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["verified"] is True
        assert body["live_channel_id"] == "UCroute_verify_channel"
        assert body["registered_channel_id"] == "UCroute_verify_channel"
        assert body["verified_at"] is not None
        assert body["failure_reason"] is None

    def test_verify_pending_account_returns_200_not_verified(
        self, api_client, svc, db_conn, workspace, channel, connected_account
    ):
        # Account is pending (not yet OAuth-connected)
        token = _admin_token(svc, db_conn, workspace.id)
        r = api_client.post(
            _verify_url(workspace.id, channel.id, connected_account.id),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["verified"] is False
        assert body["failure_reason"] is not None

    def test_response_contains_no_token_fields(
        self, api_client, svc, db_conn, workspace, channel, connected_account
    ):
        token = _connect_account_via_api(
            api_client, svc, db_conn, workspace, channel, connected_account
        )
        r = api_client.post(
            _verify_url(workspace.id, channel.id, connected_account.id),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        body = r.json()
        forbidden_keys = {"access_token", "refresh_token", "token", "secret"}
        for key in body:
            assert key not in forbidden_keys, f"Unexpected token field in response: {key}"
