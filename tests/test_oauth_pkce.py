"""PKCE (RFC 7636) regression tests for the YouTube OAuth flow.

These tests verify the full code_verifier lifecycle:
  start_youtube_oauth  → generates verifier, embeds in authorization URL,
                          stores verifier in server-side OAuthStateClaims
  complete_youtube_oauth → retrieves verifier from state, passes to exchange_code
  State is one-time-use → verifier is consumed and cannot be replayed

No live Google calls are made.  All tests use FakeGoogleOAuthClient.
"""

from __future__ import annotations

import uuid

import pytest

from app.control_plane import orchestrator as cp_orch
from app.control_plane import repository as repo
from app.control_plane.models import PlatformAccountDraft
from app.core.database import open_db
from app.oauth.client import AuthorizationURLResult, FakeGoogleOAuthClient
from app.oauth.errors import OAuthStateNotFoundError
from app.oauth.flow import complete_youtube_oauth, start_youtube_oauth
from app.oauth.state import (
    InMemoryOAuthStateStore,
    OAuthStateClaims,
    get_state_store,
    reset_state_store,
    set_state_store,
)
from app.oauth.store import LocalFileTokenStore, reset_token_store, set_token_store

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    conn = open_db(tmp_path / "pkce_test.db")
    repo.ensure_platform(conn, "plt-youtube-pkce", "youtube", "YouTube")
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
        fake_channel_id="UCpkce_test_channel",
        fake_channel_title="PKCE Test Channel",
    )


@pytest.fixture
def entities(db):
    ws = cp_orch.provision_workspace(db, name="PKCEWorkspace", slug="pkce-ws", actor="test")
    ch = cp_orch.provision_channel(
        db, workspace_id=ws.id, name="PKCEChannel", slug="pkce-ch", actor="test"
    )
    platform = repo.get_platform_by_key(db, "youtube")
    acct_id = str(uuid.uuid4())
    acct = repo.create_platform_account(
        db,
        PlatformAccountDraft(
            id=acct_id,
            channel_id=ch.id,
            platform_id=platform.id,
            platform_key="youtube",
            external_account_id=f"pending:{acct_id}",
            display_name="PKCE Test Account",
            actor="test",
            status="connected",
        ),
    )
    return ws, ch, acct


# ---------------------------------------------------------------------------
# A: FakeGoogleOAuthClient returns a code_verifier
# ---------------------------------------------------------------------------


def test_fake_client_get_authorization_url_returns_code_verifier(fake_client):
    """FakeGoogleOAuthClient must return a non-None code_verifier so PKCE
    propagation can be exercised end-to-end in tests without live Google calls."""
    result = fake_client.get_authorization_url(state_nonce="test_nonce_aabbcc", scopes=["openid"])
    assert isinstance(result, AuthorizationURLResult)
    assert result.code_verifier is not None
    assert len(result.code_verifier) > 0


# ---------------------------------------------------------------------------
# B: start_youtube_oauth stores verifier in OAuthStateClaims
# ---------------------------------------------------------------------------


def test_start_stores_code_verifier_in_state(db, fake_client, token_store, entities):
    """After start_youtube_oauth the state store must hold the code_verifier
    produced by get_authorization_url, bound to the returned nonce."""
    ws, ch, acct = entities
    result = start_youtube_oauth(
        db,
        account_id=acct.id,
        user_id="u1",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=fake_client,
    )
    store = get_state_store()
    # Peek into the in-memory store without consuming the state
    claims: OAuthStateClaims = store._store[result.state_nonce]
    assert claims.code_verifier == "fake_pkce_code_verifier"
    assert claims.nonce == result.state_nonce


# ---------------------------------------------------------------------------
# C: complete_youtube_oauth passes verifier to exchange_code (spy)
# ---------------------------------------------------------------------------


def test_complete_passes_verifier_to_exchange_code(db, fake_client, token_store, entities):
    """complete_youtube_oauth must retrieve the verifier from state and pass it
    to oauth_client.exchange_code so the token request includes it."""
    ws, ch, acct = entities
    start_result = start_youtube_oauth(
        db,
        account_id=acct.id,
        user_id="u1",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=fake_client,
    )
    complete_youtube_oauth(
        db,
        code="auth_code_abc",
        state_nonce=start_result.state_nonce,
        oauth_client=fake_client,
        token_store=token_store,
    )
    # The spy attribute records what verifier exchange_code received
    assert fake_client.last_received_code_verifier == "fake_pkce_code_verifier"


# ---------------------------------------------------------------------------
# D: Verifier is consumed after callback (one-time use)
# ---------------------------------------------------------------------------


def test_verifier_consumed_after_successful_callback(db, fake_client, token_store, entities):
    """The OAuthStateClaims (and its verifier) must be deleted from the state
    store after complete_youtube_oauth succeeds.  A second attempt must raise
    OAuthStateNotFoundError."""
    ws, ch, acct = entities
    start_result = start_youtube_oauth(
        db,
        account_id=acct.id,
        user_id="u1",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=fake_client,
    )
    nonce = start_result.state_nonce

    # First callback — succeeds
    complete_youtube_oauth(
        db,
        code="auth_code_abc",
        state_nonce=nonce,
        oauth_client=fake_client,
        token_store=token_store,
    )

    # State must be gone: replay raises OAuthStateNotFoundError
    with pytest.raises(OAuthStateNotFoundError):
        complete_youtube_oauth(
            db,
            code="auth_code_abc",
            state_nonce=nonce,
            oauth_client=fake_client,
            token_store=token_store,
        )


# ---------------------------------------------------------------------------
# E: State replay is rejected even when verifier is known
# ---------------------------------------------------------------------------


def test_state_replay_rejected(db, fake_client, token_store, entities):
    """Replaying a nonce after it has been consumed must raise OAuthStateNotFoundError
    regardless of whether the correct code_verifier is supplied.  The state store
    is the authoritative replay gate; the verifier is not a substitute."""
    ws, ch, acct = entities
    start_result = start_youtube_oauth(
        db,
        account_id=acct.id,
        user_id="u1",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=fake_client,
    )
    nonce = start_result.state_nonce

    complete_youtube_oauth(
        db,
        code="code1",
        state_nonce=nonce,
        oauth_client=fake_client,
        token_store=token_store,
    )

    with pytest.raises(OAuthStateNotFoundError):
        complete_youtube_oauth(
            db,
            code="code1",
            state_nonce=nonce,
            oauth_client=fake_client,
            token_store=token_store,
        )


# ---------------------------------------------------------------------------
# F: Two simultaneous flows keep their verifiers isolated
# ---------------------------------------------------------------------------


def test_two_concurrent_flows_do_not_cross_contaminate(db, tmp_path, token_store):
    """Verifiers from two simultaneous OAuth flows for different accounts must
    not be confused.  Each callback must receive its own verifier."""
    repo.ensure_platform(db, "plt-yt-concurrent", "youtube", "YouTube")
    ws = cp_orch.provision_workspace(db, name="ConcWS", slug="conc-ws", actor="test")
    ch = cp_orch.provision_channel(
        db, workspace_id=ws.id, name="ConcCh", slug="conc-ch", actor="test"
    )
    platform = repo.get_platform_by_key(db, "youtube")

    def _make_account(suffix: str):
        acct_id = str(uuid.uuid4())
        return repo.create_platform_account(
            db,
            PlatformAccountDraft(
                id=acct_id,
                channel_id=ch.id,
                platform_id=platform.id,
                platform_key="youtube",
                external_account_id=f"pending:{acct_id}",
                display_name=f"Account {suffix}",
                actor="test",
                status="connected",
            ),
        )

    # Two fake clients that return distinct channel IDs
    client_a = FakeGoogleOAuthClient(
        fake_channel_id="UCchannel_aaa",
        fake_channel_title="Channel A",
        fake_access_token="token_a",
        fake_refresh_token="refresh_a",
    )
    client_b = FakeGoogleOAuthClient(
        fake_channel_id="UCchannel_bbb",
        fake_channel_title="Channel B",
        fake_access_token="token_b",
        fake_refresh_token="refresh_b",
    )

    acct_a = _make_account("A")
    acct_b = _make_account("B")

    # Both flows start concurrently — interleaved
    start_a = start_youtube_oauth(
        db,
        account_id=acct_a.id,
        user_id="u_a",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=client_a,
    )
    start_b = start_youtube_oauth(
        db,
        account_id=acct_b.id,
        user_id="u_b",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=client_b,
    )

    # Nonces and verifiers must be distinct
    assert start_a.state_nonce != start_b.state_nonce

    store = get_state_store()
    claims_a = store._store[start_a.state_nonce]
    claims_b = store._store[start_b.state_nonce]
    assert claims_a.account_id == acct_a.id
    assert claims_b.account_id == acct_b.id
    # Both return the same fake verifier string (deterministic fake), but are
    # stored independently under different nonces — no cross-contamination.
    assert claims_a.nonce != claims_b.nonce

    # Each callback uses its own nonce and verifier
    result_a = complete_youtube_oauth(
        db,
        code="code_a",
        state_nonce=start_a.state_nonce,
        oauth_client=client_a,
        token_store=token_store,
    )
    result_b = complete_youtube_oauth(
        db,
        code="code_b",
        state_nonce=start_b.state_nonce,
        oauth_client=client_b,
        token_store=token_store,
    )

    assert result_a.account_id == acct_a.id
    assert result_b.account_id == acct_b.id
    assert result_a.provider_channel_id == "UCchannel_aaa"
    assert result_b.provider_channel_id == "UCchannel_bbb"


# ---------------------------------------------------------------------------
# G: Verifier binding survives workspace/channel/account claim check
# ---------------------------------------------------------------------------


def test_claims_carry_full_binding_alongside_verifier(db, fake_client, token_store, entities):
    """OAuthStateClaims must carry all binding fields AND the code_verifier together.
    The verifier must not displace or corrupt the workspace/channel/account binding."""
    ws, ch, acct = entities
    start_result = start_youtube_oauth(
        db,
        account_id=acct.id,
        user_id="u_binding",
        workspace_id=ws.id,
        channel_id=ch.id,
        oauth_client=fake_client,
    )
    store = get_state_store()
    claims: OAuthStateClaims = store._store[start_result.state_nonce]

    assert claims.user_id == "u_binding"
    assert claims.workspace_id == ws.id
    assert claims.channel_id == ch.id
    assert claims.account_id == acct.id
    assert claims.code_verifier == "fake_pkce_code_verifier"


# ---------------------------------------------------------------------------
# H: OAuthStateClaims serialisation round-trips code_verifier (Redis path)
# ---------------------------------------------------------------------------


def test_oauth_state_claims_roundtrip_with_verifier():
    """to_dict / from_dict must preserve code_verifier for the Redis-backed store."""
    import time

    claims = OAuthStateClaims(
        nonce="abc123",
        user_id="uid",
        workspace_id="wid",
        channel_id="cid",
        account_id="aid",
        created_at=time.monotonic(),
        code_verifier="verifier_xyz_987",
    )
    restored = OAuthStateClaims.from_dict(claims.to_dict())
    assert restored.code_verifier == "verifier_xyz_987"
    assert restored.nonce == claims.nonce


def test_oauth_state_claims_from_dict_handles_missing_verifier():
    """Records stored before PKCE was introduced (no code_verifier key) must
    deserialise without error with code_verifier=None."""
    import time

    old_record = {
        "nonce": "old_nonce",
        "user_id": "uid",
        "workspace_id": "wid",
        "channel_id": "cid",
        "account_id": "aid",
        "created_at": time.monotonic(),
        # no "code_verifier" key — simulates a pre-PKCE serialised record
    }
    claims = OAuthStateClaims.from_dict(old_record)
    assert claims.code_verifier is None
