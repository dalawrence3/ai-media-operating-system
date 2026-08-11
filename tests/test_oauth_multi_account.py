"""Explicit multi-account isolation integration tests for YouTube OAuth.

Specification requirement: Each cp_platform_account has its own
cp_credential_profile row and token file. Operations on Account B must
never touch Account A's token file or credential_profile record.

This file is intentionally separate from test_oauth_flow.py so the isolation
guarantee has a dedicated, searchable test surface.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.control_plane import orchestrator as cp_orch
from app.control_plane import repository as repo
from app.control_plane.models import PlatformAccountDraft
from app.core.database import open_db
from app.oauth.client import FakeGoogleOAuthClient
from app.oauth.errors import OAuthTokenStoreError
from app.oauth.flow import (
    complete_youtube_oauth,
    disconnect_youtube_account,
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
    conn = open_db(tmp_path / "multi.db")
    repo.ensure_platform(conn, "plt-yt-multi", "youtube", "YouTube")
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def fresh_state_store():
    set_state_store(InMemoryOAuthStateStore())
    yield
    reset_state_store()


@pytest.fixture
def token_store(tmp_path):
    store = LocalFileTokenStore(tmp_path / "tokens")
    set_token_store(store)
    yield store
    reset_token_store()


@pytest.fixture
def env(db):
    ws = cp_orch.provision_workspace(db, name="WS", slug="ws", actor="t")
    ch = cp_orch.provision_channel(db, workspace_id=ws.id, name="Ch", slug="ch", actor="t")
    return ws, ch


def _make_account(db, channel_id):
    platform = repo.get_platform_by_key(db, "youtube")
    acct_id = str(uuid.uuid4())
    draft = PlatformAccountDraft(
        id=acct_id,
        channel_id=channel_id,
        platform_id=platform.id,
        platform_key="youtube",
        external_account_id=f"pending:{acct_id}",
        display_name="Pending",
        actor="t",
        status="connected",
    )
    return repo.create_platform_account(db, draft)


def _connect(db, ws, ch, acct, yt_channel_id, refresh_token, token_store):
    client = FakeGoogleOAuthClient(fake_channel_id=yt_channel_id, fake_refresh_token=refresh_token)
    r = start_youtube_oauth(
        db,
        account_id=acct.id,
        user_id="u",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=client,
    )
    complete_youtube_oauth(
        db, code="code", state_nonce=r.state_nonce, oauth_client=client, token_store=token_store
    )
    return client


# ---------------------------------------------------------------------------
# Token file isolation
# ---------------------------------------------------------------------------


def test_two_accounts_produce_separate_token_files(db, token_store, env):
    ws, ch = env
    acct_a = _make_account(db, ch.id)
    acct_b = _make_account(db, ch.id)

    _connect(db, ws, ch, acct_a, "UCA", "refresh_A", token_store)
    _connect(db, ws, ch, acct_b, "UCB", "refresh_B", token_store)

    a = repo.get_platform_account(db, acct_a.id)
    b = repo.get_platform_account(db, acct_b.id)

    cred_a = repo.get_credential_profile(db, a.credential_profile_id)
    cred_b = repo.get_credential_profile(db, b.credential_profile_id)

    assert cred_a.external_ref != cred_b.external_ref
    assert token_store.read(cred_a.external_ref).refresh_token == "refresh_A"
    assert token_store.read(cred_b.external_ref).refresh_token == "refresh_B"


def test_two_accounts_have_separate_credential_profile_rows(db, token_store, env):
    ws, ch = env
    acct_a = _make_account(db, ch.id)
    acct_b = _make_account(db, ch.id)

    _connect(db, ws, ch, acct_a, "UCA", "refresh_A", token_store)
    _connect(db, ws, ch, acct_b, "UCB", "refresh_B", token_store)

    a = repo.get_platform_account(db, acct_a.id)
    b = repo.get_platform_account(db, acct_b.id)

    assert a.credential_profile_id != b.credential_profile_id


# ---------------------------------------------------------------------------
# Cross-account write isolation
# ---------------------------------------------------------------------------


def test_connecting_b_leaves_a_external_id_unchanged(db, token_store, env):
    ws, ch = env
    acct_a = _make_account(db, ch.id)
    acct_b = _make_account(db, ch.id)

    _connect(db, ws, ch, acct_a, "UCA_chan", "refresh_A", token_store)
    _connect(db, ws, ch, acct_b, "UCB_chan", "refresh_B", token_store)

    a_after = repo.get_platform_account(db, acct_a.id)
    assert a_after.external_account_id == "UCA_chan"


def test_connecting_b_leaves_a_token_unchanged(db, token_store, env):
    ws, ch = env
    acct_a = _make_account(db, ch.id)
    acct_b = _make_account(db, ch.id)

    _connect(db, ws, ch, acct_a, "UCA", "refresh_A", token_store)
    _connect(db, ws, ch, acct_b, "UCB", "refresh_B_clobber", token_store)

    a = repo.get_platform_account(db, acct_a.id)
    cred_a = repo.get_credential_profile(db, a.credential_profile_id)
    assert token_store.read(cred_a.external_ref).refresh_token == "refresh_A"


def test_reconnecting_a_does_not_touch_b_token(db, token_store, env):
    ws, ch = env
    acct_a = _make_account(db, ch.id)
    acct_b = _make_account(db, ch.id)

    _connect(db, ws, ch, acct_a, "UCA", "refresh_A_v1", token_store)
    _connect(db, ws, ch, acct_b, "UCB", "refresh_B", token_store)

    # Reconnect A (same YouTube channel, new tokens)
    client_a2 = FakeGoogleOAuthClient(fake_channel_id="UCA", fake_refresh_token="refresh_A_v2")
    r = start_youtube_oauth(
        db,
        account_id=acct_a.id,
        user_id="u",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=client_a2,
    )
    complete_youtube_oauth(
        db, code="code", state_nonce=r.state_nonce, oauth_client=client_a2, token_store=token_store
    )

    b = repo.get_platform_account(db, acct_b.id)
    cred_b = repo.get_credential_profile(db, b.credential_profile_id)
    assert token_store.read(cred_b.external_ref).refresh_token == "refresh_B"


# ---------------------------------------------------------------------------
# Disconnect isolation
# ---------------------------------------------------------------------------


def test_disconnect_a_removes_only_a_token_file(db, token_store, env):
    ws, ch = env
    acct_a = _make_account(db, ch.id)
    acct_b = _make_account(db, ch.id)

    client_a = _connect(db, ws, ch, acct_a, "UCA", "refresh_A", token_store)
    _connect(db, ws, ch, acct_b, "UCB", "refresh_B", token_store)

    a = repo.get_platform_account(db, acct_a.id)
    cred_a = repo.get_credential_profile(db, a.credential_profile_id)
    ref_a = cred_a.external_ref

    disconnect_youtube_account(
        db,
        account_id=acct_a.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        actor="t",
        oauth_client=client_a,
        token_store=token_store,
    )

    # A token file is gone
    with pytest.raises(OAuthTokenStoreError):
        token_store.read(ref_a)

    # B token file still readable
    b = repo.get_platform_account(db, acct_b.id)
    cred_b = repo.get_credential_profile(db, b.credential_profile_id)
    assert token_store.read(cred_b.external_ref).refresh_token == "refresh_B"


def test_disconnect_a_leaves_b_status_connected(db, token_store, env):
    ws, ch = env
    acct_a = _make_account(db, ch.id)
    acct_b = _make_account(db, ch.id)

    client_a = _connect(db, ws, ch, acct_a, "UCA", "refresh_A", token_store)
    _connect(db, ws, ch, acct_b, "UCB", "refresh_B", token_store)

    disconnect_youtube_account(
        db,
        account_id=acct_a.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        actor="t",
        oauth_client=client_a,
        token_store=token_store,
    )

    assert repo.get_platform_account(db, acct_b.id).status == "connected"


def test_disconnect_a_leaves_b_credential_profile_active(db, token_store, env):
    ws, ch = env
    acct_a = _make_account(db, ch.id)
    acct_b = _make_account(db, ch.id)

    client_a = _connect(db, ws, ch, acct_a, "UCA", "refresh_A", token_store)
    _connect(db, ws, ch, acct_b, "UCB", "refresh_B", token_store)

    disconnect_youtube_account(
        db,
        account_id=acct_a.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        actor="t",
        oauth_client=client_a,
        token_store=token_store,
    )

    b = repo.get_platform_account(db, acct_b.id)
    cred_b = repo.get_credential_profile(db, b.credential_profile_id)
    assert cred_b.status == "active"


# ---------------------------------------------------------------------------
# Refresh isolation
# ---------------------------------------------------------------------------


def test_refresh_token_for_a_does_not_read_b_file(db, token_store, env):
    ws, ch = env
    acct_a = _make_account(db, ch.id)
    acct_b = _make_account(db, ch.id)

    _connect(db, ws, ch, acct_a, "UCA", "refresh_A", token_store)
    _connect(db, ws, ch, acct_b, "UCB", "refresh_B", token_store)

    # Force-expire A's token
    a = repo.get_platform_account(db, acct_a.id)
    cred_a = repo.get_credential_profile(db, a.credential_profile_id)
    token_store.update_tokens(
        cred_a.external_ref,
        access_token="old_a",
        refresh_token="refresh_A",
        expires_at_utc=datetime.now(UTC) - timedelta(seconds=1),
    )

    client_a = FakeGoogleOAuthClient(fake_channel_id="UCA", fake_access_token="new_access_A")
    refreshed = refresh_account_token(
        db,
        account_id=acct_a.id,
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=client_a,
        token_store=token_store,
    )
    assert refreshed.access_token == "new_access_A_refreshed"

    # B's token must not be modified
    b = repo.get_platform_account(db, acct_b.id)
    cred_b = repo.get_credential_profile(db, b.credential_profile_id)
    stored_b = token_store.read(cred_b.external_ref)
    assert stored_b.refresh_token == "refresh_B"


# ---------------------------------------------------------------------------
# Concurrent OAuth flows (two pending flows at once)
# ---------------------------------------------------------------------------


def test_two_simultaneous_start_flows_use_separate_states(db, env):
    ws, ch = env
    acct_a = _make_account(db, ch.id)
    acct_b = _make_account(db, ch.id)

    client = FakeGoogleOAuthClient()

    result_a = start_youtube_oauth(
        db,
        account_id=acct_a.id,
        user_id="u",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=client,
    )
    result_b = start_youtube_oauth(
        db,
        account_id=acct_b.id,
        user_id="u",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=client,
    )

    assert result_a.state_nonce != result_b.state_nonce


def test_completing_flow_a_with_b_nonce_fails(db, token_store, env):
    """Using B's nonce to complete A's flow must bind to B's account, not A's."""
    ws, ch = env
    acct_a = _make_account(db, ch.id)
    acct_b = _make_account(db, ch.id)

    client = FakeGoogleOAuthClient(fake_channel_id="UCboth")

    start_youtube_oauth(
        db,
        account_id=acct_a.id,
        user_id="u",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=client,
    )
    r_b = start_youtube_oauth(
        db,
        account_id=acct_b.id,
        user_id="u",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=client,
    )

    # Complete using B's nonce — must bind to B, not A
    complete_youtube_oauth(
        db, code="code", state_nonce=r_b.state_nonce, oauth_client=client, token_store=token_store
    )

    a_after = repo.get_platform_account(db, acct_a.id)
    b_after = repo.get_platform_account(db, acct_b.id)

    # A is still pending
    assert a_after.external_account_id.startswith("pending:")
    # B is connected
    assert b_after.external_account_id == "UCboth"
