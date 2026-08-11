"""Tests for OAuth state management (CSRF protection)."""

from __future__ import annotations

import time

import pytest

from app.oauth.errors import OAuthStateExpiredError, OAuthStateNotFoundError
from app.oauth.state import (
    STATE_TTL_SECONDS,
    InMemoryOAuthStateStore,
    OAuthStateClaims,
    generate_state,
    reset_state_store,
    set_state_store,
    validate_state,
)


@pytest.fixture(autouse=True)
def fresh_state_store():
    store = InMemoryOAuthStateStore()
    set_state_store(store)
    yield store
    reset_state_store()


# ---------------------------------------------------------------------------
# OAuthStateClaims
# ---------------------------------------------------------------------------


def test_claims_not_expired_when_fresh():
    claims = OAuthStateClaims(
        nonce="abc",
        user_id="u1",
        workspace_id="w1",
        channel_id="c1",
        account_id="a1",
        created_at=time.monotonic(),
    )
    assert not claims.is_expired()


def test_claims_expired_when_old():
    claims = OAuthStateClaims(
        nonce="abc",
        user_id="u1",
        workspace_id="w1",
        channel_id="c1",
        account_id="a1",
        created_at=time.monotonic() - (STATE_TTL_SECONDS + 1),
    )
    assert claims.is_expired()


def test_claims_round_trip_dict():
    claims = OAuthStateClaims(
        nonce="abc123",
        user_id="u1",
        workspace_id="w1",
        channel_id="c1",
        account_id="a1",
        created_at=123.456,
    )
    restored = OAuthStateClaims.from_dict(claims.to_dict())
    assert restored == claims


# ---------------------------------------------------------------------------
# InMemoryOAuthStateStore
# ---------------------------------------------------------------------------


def test_store_and_pop_returns_claims(fresh_state_store):
    claims = OAuthStateClaims(
        nonce="n1",
        user_id="u1",
        workspace_id="w1",
        channel_id="c1",
        account_id="a1",
        created_at=time.monotonic(),
    )
    fresh_state_store.store(claims)
    retrieved = fresh_state_store.pop("n1")
    assert retrieved.account_id == "a1"


def test_pop_deletes_key_one_time_use(fresh_state_store):
    claims = OAuthStateClaims(
        nonce="n2",
        user_id="u1",
        workspace_id="w1",
        channel_id="c1",
        account_id="a1",
        created_at=time.monotonic(),
    )
    fresh_state_store.store(claims)
    fresh_state_store.pop("n2")
    with pytest.raises(OAuthStateNotFoundError):
        fresh_state_store.pop("n2")


def test_pop_unknown_nonce_raises(fresh_state_store):
    with pytest.raises(OAuthStateNotFoundError):
        fresh_state_store.pop("nonexistent-nonce")


def test_pop_expired_raises(fresh_state_store):
    old_claims = OAuthStateClaims(
        nonce="n3",
        user_id="u1",
        workspace_id="w1",
        channel_id="c1",
        account_id="a1",
        created_at=time.monotonic() - (STATE_TTL_SECONDS + 1),
    )
    fresh_state_store.store(old_claims)
    with pytest.raises(OAuthStateExpiredError):
        fresh_state_store.pop("n3")


def test_clear_all_removes_all_state(fresh_state_store):
    for i in range(3):
        claims = OAuthStateClaims(
            nonce=f"n{i}",
            user_id="u",
            workspace_id="w",
            channel_id="c",
            account_id="a",
            created_at=time.monotonic(),
        )
        fresh_state_store.store(claims)
    fresh_state_store.clear_all()
    with pytest.raises(OAuthStateNotFoundError):
        fresh_state_store.pop("n0")


# ---------------------------------------------------------------------------
# generate_state / validate_state (public API)
# ---------------------------------------------------------------------------


def test_generate_state_returns_nonce():
    nonce = generate_state(user_id="u1", workspace_id="w1", channel_id="c1", account_id="a1")
    assert isinstance(nonce, str)
    assert len(nonce) == 64  # 32 bytes hex = 64 chars


def test_generate_state_nonce_is_random():
    n1 = generate_state(user_id="u", workspace_id="w", channel_id="c", account_id="a")
    n2 = generate_state(user_id="u", workspace_id="w", channel_id="c", account_id="a")
    assert n1 != n2


def test_validate_state_returns_correct_claims():
    nonce = generate_state(
        user_id="user42", workspace_id="ws99", channel_id="ch77", account_id="acct55"
    )
    claims = validate_state(nonce)
    assert claims.user_id == "user42"
    assert claims.workspace_id == "ws99"
    assert claims.channel_id == "ch77"
    assert claims.account_id == "acct55"


def test_validate_state_one_time_use():
    nonce = generate_state(user_id="u", workspace_id="w", channel_id="c", account_id="a")
    validate_state(nonce)
    with pytest.raises(OAuthStateNotFoundError):
        validate_state(nonce)


def test_validate_state_unknown_nonce_raises():
    with pytest.raises(OAuthStateNotFoundError):
        validate_state("completely-unknown-nonce-value")


def test_generate_multiple_independent_states():
    """Multi-account: two pending OAuth flows must not interfere."""
    nonce_a = generate_state(user_id="u", workspace_id="w", channel_id="c", account_id="acct_A")
    nonce_b = generate_state(user_id="u", workspace_id="w", channel_id="c", account_id="acct_B")

    claims_b = validate_state(nonce_b)
    assert claims_b.account_id == "acct_B"

    claims_a = validate_state(nonce_a)
    assert claims_a.account_id == "acct_A"


def test_state_binds_all_required_fields():
    nonce = generate_state(user_id="u1", workspace_id="ws1", channel_id="ch1", account_id="ac1")
    claims = validate_state(nonce)
    assert claims.nonce == nonce
    assert claims.user_id == "u1"
    assert claims.workspace_id == "ws1"
    assert claims.channel_id == "ch1"
    assert claims.account_id == "ac1"
