"""Tests for the YouTube OAuth flow orchestrator (no live Google calls)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.control_plane import orchestrator as cp_orch
from app.control_plane import repository as repo
from app.control_plane.models import PlatformAccountDraft
from app.core.database import open_db
from app.oauth.client import FakeGoogleOAuthClient
from app.oauth.errors import (
    OAuthAccountNotFoundError,
    OAuthChannelMismatchError,
    OAuthChannelVerificationError,
    OAuthCodeExchangeError,
    OAuthProviderError,
    OAuthRefreshError,
    OAuthStateNotFoundError,
    OAuthTokenStoreError,
)
from app.oauth.flow import (
    complete_youtube_oauth,
    disconnect_youtube_account,
    get_connection_status,
    refresh_account_token,
    start_youtube_oauth,
)
from app.oauth.state import InMemoryOAuthStateStore, reset_state_store, set_state_store
from app.oauth.store import LocalFileTokenStore, reset_token_store, set_token_store

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    conn = open_db(tmp_path / "test.db")
    repo.ensure_platform(conn, "plt-youtube-1", "youtube", "YouTube")
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
        fake_channel_id="UCtest_channel_123",
        fake_channel_title="My Test Channel",
    )


@pytest.fixture
def entities(db):
    """Return (workspace, channel, platform_account) with a pending OAuth account."""
    ws = cp_orch.provision_workspace(db, name="TestWS", slug="test-ws", actor="test")
    ch = cp_orch.provision_channel(
        db, workspace_id=ws.id, name="TestCh", slug="test-ch", actor="test"
    )
    acct = _create_pending_account(db, channel_id=ch.id)
    return ws, ch, acct


def _create_pending_account(db, *, channel_id: str):
    platform = repo.get_platform_by_key(db, "youtube")
    acct_id = str(uuid.uuid4())
    draft = PlatformAccountDraft(
        id=acct_id,
        channel_id=channel_id,
        platform_id=platform.id,
        platform_key="youtube",
        external_account_id=f"pending:{acct_id}",
        display_name="Pending YouTube Account",
        actor="test",
        status="connected",
    )
    return repo.create_platform_account(db, draft)


def _do_full_connect(db, ws, ch, acct, client, token_store):
    """Helper: start + complete OAuth in one call."""
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
# start_youtube_oauth
# ---------------------------------------------------------------------------


def test_start_flow_returns_authorization_url(db, fake_client, token_store, entities):
    ws, ch, acct = entities
    result = start_youtube_oauth(
        db,
        account_id=acct.id,
        user_id="user1",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=fake_client,
    )
    assert "accounts.google.com" in result.authorization_url
    assert len(result.state_nonce) == 64  # 32-byte hex


def test_start_flow_rejects_wrong_channel(db, fake_client, entities):
    ws, ch, acct = entities
    with pytest.raises(OAuthAccountNotFoundError):
        start_youtube_oauth(
            db,
            account_id=acct.id,
            user_id="user1",
            workspace_id=ws.id,
            channel_id="wrong_channel_id",
            oauth_client=fake_client,
        )


def test_start_flow_rejects_nonexistent_account(db, fake_client, entities):
    ws, ch, _ = entities
    with pytest.raises(OAuthAccountNotFoundError):
        start_youtube_oauth(
            db,
            account_id="nonexistent-id",
            user_id="user1",
            workspace_id=ws.id,
            channel_id=ch.id,
            oauth_client=fake_client,
        )


# ---------------------------------------------------------------------------
# complete_youtube_oauth
# ---------------------------------------------------------------------------


def test_complete_flow_connects_account(db, fake_client, token_store, entities):
    ws, ch, acct = entities
    r = start_youtube_oauth(
        db,
        account_id=acct.id,
        user_id="u",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=fake_client,
    )
    conn_result = complete_youtube_oauth(
        db,
        code="fake_auth_code",
        state_nonce=r.state_nonce,
        oauth_client=fake_client,
        token_store=token_store,
    )
    assert conn_result.provider_channel_id == "UCtest_channel_123"
    assert conn_result.channel_title == "My Test Channel"
    assert conn_result.account_id == acct.id


def test_complete_flow_updates_platform_account(db, fake_client, token_store, entities):
    ws, ch, acct = entities
    r = start_youtube_oauth(
        db,
        account_id=acct.id,
        user_id="u",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=fake_client,
    )
    complete_youtube_oauth(
        db,
        code="code",
        state_nonce=r.state_nonce,
        oauth_client=fake_client,
        token_store=token_store,
    )
    updated = repo.get_platform_account(db, acct.id)
    assert updated.external_account_id == "UCtest_channel_123"
    assert updated.display_name == "My Test Channel"
    assert updated.credential_profile_id is not None


def test_complete_flow_stores_token(db, fake_client, token_store, entities):
    ws, ch, acct = entities
    r = start_youtube_oauth(
        db,
        account_id=acct.id,
        user_id="u",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=fake_client,
    )
    complete_youtube_oauth(
        db,
        code="code",
        state_nonce=r.state_nonce,
        oauth_client=fake_client,
        token_store=token_store,
    )
    updated = repo.get_platform_account(db, acct.id)
    cred = repo.get_credential_profile(db, updated.credential_profile_id)
    assert cred.external_ref.startswith("file://")
    stored = token_store.read(cred.external_ref)
    assert stored.access_token == "fake_access_token_aaa"
    assert stored.refresh_token == "fake_refresh_token_bbb"


def test_complete_flow_rejects_invalid_state(db, fake_client, token_store):
    with pytest.raises(OAuthStateNotFoundError):
        complete_youtube_oauth(
            db,
            code="code",
            state_nonce="invalid-state-nonce",
            oauth_client=fake_client,
            token_store=token_store,
        )


def test_complete_flow_rejects_code_exchange_failure(db, token_store, entities):
    ws, ch, acct = entities
    bad_client = FakeGoogleOAuthClient(
        fail_exchange=OAuthCodeExchangeError("Google rejected the code")
    )
    r = start_youtube_oauth(
        db,
        account_id=acct.id,
        user_id="u",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=bad_client,
    )
    with pytest.raises(OAuthCodeExchangeError):
        complete_youtube_oauth(
            db,
            code="bad_code",
            state_nonce=r.state_nonce,
            oauth_client=bad_client,
            token_store=token_store,
        )


def test_complete_flow_rejects_missing_youtube_channel(db, token_store, entities):
    ws, ch, acct = entities
    no_channel_client = FakeGoogleOAuthClient(
        fail_identity=OAuthChannelVerificationError("no YouTube channel")
    )
    r = start_youtube_oauth(
        db,
        account_id=acct.id,
        user_id="u",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=no_channel_client,
    )
    with pytest.raises(OAuthChannelVerificationError):
        complete_youtube_oauth(
            db,
            code="code",
            state_nonce=r.state_nonce,
            oauth_client=no_channel_client,
            token_store=token_store,
        )


def test_complete_flow_rejects_channel_mismatch(db, token_store, entities):
    """Account already bound to one channel cannot be rebound to a different one."""
    ws, ch, acct = entities
    client1 = FakeGoogleOAuthClient(fake_channel_id="UCoriginal_channel")
    client2 = FakeGoogleOAuthClient(fake_channel_id="UCdifferent_channel")

    r1 = start_youtube_oauth(
        db,
        account_id=acct.id,
        user_id="u",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=client1,
    )
    complete_youtube_oauth(
        db, code="c", state_nonce=r1.state_nonce, oauth_client=client1, token_store=token_store
    )

    r2 = start_youtube_oauth(
        db,
        account_id=acct.id,
        user_id="u",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=client2,
    )
    with pytest.raises(OAuthChannelMismatchError):
        complete_youtube_oauth(
            db, code="c", state_nonce=r2.state_nonce, oauth_client=client2, token_store=token_store
        )


# ---------------------------------------------------------------------------
# disconnect_youtube_account
# ---------------------------------------------------------------------------


def test_disconnect_marks_account_disconnected(db, fake_client, token_store, entities):
    ws, ch, acct = entities
    _do_full_connect(db, ws, ch, acct, fake_client, token_store)
    disconnect_youtube_account(
        db,
        account_id=acct.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        actor="test",
        oauth_client=fake_client,
        token_store=token_store,
    )
    updated = repo.get_platform_account(db, acct.id)
    assert updated.status == "disconnected"


def test_disconnect_deletes_token_file(db, fake_client, token_store, entities):
    ws, ch, acct = entities
    _do_full_connect(db, ws, ch, acct, fake_client, token_store)

    updated_acct = repo.get_platform_account(db, acct.id)
    cred = repo.get_credential_profile(db, updated_acct.credential_profile_id)
    ext_ref = cred.external_ref

    disconnect_youtube_account(
        db,
        account_id=acct.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        actor="test",
        oauth_client=fake_client,
        token_store=token_store,
    )
    with pytest.raises(OAuthTokenStoreError):
        token_store.read(ext_ref)


def test_disconnect_attempts_token_revocation(db, fake_client, token_store, entities):
    ws, ch, acct = entities
    _do_full_connect(db, ws, ch, acct, fake_client, token_store)
    disconnect_youtube_account(
        db,
        account_id=acct.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        actor="test",
        oauth_client=fake_client,
        token_store=token_store,
    )
    assert len(fake_client.revoke_calls) == 1


def test_disconnect_succeeds_even_if_revocation_fails(db, token_store, entities):
    ws, ch, acct = entities
    client_with_bad_revoke = FakeGoogleOAuthClient(
        fail_revoke=OAuthProviderError("revoke endpoint unreachable")
    )
    _do_full_connect(db, ws, ch, acct, client_with_bad_revoke, token_store)

    disconnect_youtube_account(
        db,
        account_id=acct.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        actor="test",
        oauth_client=client_with_bad_revoke,
        token_store=token_store,
    )
    updated = repo.get_platform_account(db, acct.id)
    assert updated.status == "disconnected"


# ---------------------------------------------------------------------------
# Multi-account isolation
# ---------------------------------------------------------------------------


def test_connecting_account_b_does_not_affect_account_a(db, token_store, entities):
    ws, ch, acct_a = entities
    acct_b = _create_pending_account(db, channel_id=ch.id)

    client_a = FakeGoogleOAuthClient(fake_channel_id="UCA_channel", fake_refresh_token="refresh_A")
    client_b = FakeGoogleOAuthClient(fake_channel_id="UCB_channel", fake_refresh_token="refresh_B")

    _do_full_connect(db, ws, ch, acct_a, client_a, token_store)
    _do_full_connect(db, ws, ch, acct_b, client_b, token_store)

    acct_a_updated = repo.get_platform_account(db, acct_a.id)
    assert acct_a_updated.external_account_id == "UCA_channel"

    cred_a = repo.get_credential_profile(db, acct_a_updated.credential_profile_id)
    stored_a = token_store.read(cred_a.external_ref)
    assert stored_a.refresh_token == "refresh_A"


def test_disconnecting_account_a_does_not_affect_account_b(db, token_store, entities):
    ws, ch, acct_a = entities
    acct_b = _create_pending_account(db, channel_id=ch.id)

    client_a = FakeGoogleOAuthClient(fake_channel_id="UCA_channel", fake_refresh_token="refresh_A")
    client_b = FakeGoogleOAuthClient(fake_channel_id="UCB_channel", fake_refresh_token="refresh_B")

    _do_full_connect(db, ws, ch, acct_a, client_a, token_store)
    _do_full_connect(db, ws, ch, acct_b, client_b, token_store)

    disconnect_youtube_account(
        db,
        account_id=acct_a.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        actor="test",
        oauth_client=client_a,
        token_store=token_store,
    )

    acct_b_updated = repo.get_platform_account(db, acct_b.id)
    assert acct_b_updated.status == "connected"
    cred_b = repo.get_credential_profile(db, acct_b_updated.credential_profile_id)
    stored_b = token_store.read(cred_b.external_ref)
    assert stored_b.refresh_token == "refresh_B"


# ---------------------------------------------------------------------------
# get_connection_status
# ---------------------------------------------------------------------------


def test_connection_status_disconnected_before_oauth(db, token_store, entities):
    ws, ch, acct = entities
    status = get_connection_status(
        db,
        account_id=acct.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        token_store=token_store,
    )
    assert not status.connected


def test_connection_status_connected_after_oauth(db, fake_client, token_store, entities):
    ws, ch, acct = entities
    _do_full_connect(db, ws, ch, acct, fake_client, token_store)

    status = get_connection_status(
        db,
        account_id=acct.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        token_store=token_store,
    )
    assert status.connected
    assert status.provider_channel_id == "UCtest_channel_123"
    assert status.channel_title == "My Test Channel"


def test_connection_status_disconnected_after_disconnect(db, fake_client, token_store, entities):
    ws, ch, acct = entities
    _do_full_connect(db, ws, ch, acct, fake_client, token_store)
    disconnect_youtube_account(
        db,
        account_id=acct.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        actor="test",
        oauth_client=fake_client,
        token_store=token_store,
    )
    status = get_connection_status(
        db,
        account_id=acct.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        token_store=token_store,
    )
    assert not status.connected


# ---------------------------------------------------------------------------
# refresh_account_token
# ---------------------------------------------------------------------------


def test_refresh_returns_stored_token_if_not_expired(db, fake_client, token_store, entities):
    ws, ch, acct = entities
    _do_full_connect(db, ws, ch, acct, fake_client, token_store)

    stored = refresh_account_token(
        db,
        account_id=acct.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=fake_client,
        token_store=token_store,
    )
    assert stored.access_token == "fake_access_token_aaa"


def test_refresh_raises_when_token_expired_and_no_refresh_token(db, token_store, entities):
    import json
    import os
    from pathlib import Path

    ws, ch, acct = entities
    client = FakeGoogleOAuthClient(fake_channel_id="UCtest")
    _do_full_connect(db, ws, ch, acct, client, token_store)

    # Directly overwrite the token file: set refresh_token=null and expire access_token.
    # (update_tokens preserves existing refresh_token when None is passed, so we bypass it.)
    updated_acct = repo.get_platform_account(db, acct.id)
    cred = repo.get_credential_profile(db, updated_acct.credential_profile_id)
    file_path = cred.external_ref[len("file://") :]
    payload = json.loads(Path(file_path).read_text())
    payload["refresh_token"] = None
    payload["expires_at_utc"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    tmp = file_path + ".tmp"
    Path(tmp).write_text(json.dumps(payload))
    os.rename(tmp, file_path)

    with pytest.raises(OAuthRefreshError, match="reconnected"):
        refresh_account_token(
            db,
            account_id=acct.id,
            workspace_id=ws.id,
            channel_id=ch.id,
            oauth_client=client,
            token_store=token_store,
        )


def test_refresh_no_credential_profile_raises(db, token_store, entities):
    ws, ch, acct = entities
    client = FakeGoogleOAuthClient()
    # Account has no credential profile (pre-OAuth state)
    with pytest.raises(OAuthRefreshError):
        refresh_account_token(
            db,
            account_id=acct.id,
            workspace_id=ws.id,
            channel_id=ch.id,
            oauth_client=client,
            token_store=token_store,
        )
