"""Tests for OAuth token storage (LocalFileTokenStore)."""

from __future__ import annotations

import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.oauth.errors import OAuthTokenStoreError
from app.oauth.store import LocalFileTokenStore


@pytest.fixture
def token_dir(tmp_path):
    return tmp_path / "oauth_tokens"


@pytest.fixture
def store(token_dir):
    return LocalFileTokenStore(token_dir)


def _write_basic(store, account_id="acct_001"):
    return store.write(
        account_id=account_id,
        access_token="access_aaa",
        refresh_token="refresh_bbb",
        token_type="Bearer",
        expires_at_utc=datetime.now(UTC) + timedelta(hours=1),
        scopes=["https://www.googleapis.com/auth/youtube.readonly"],
        google_sub="1000000001",
    )


# ---------------------------------------------------------------------------
# write / read
# ---------------------------------------------------------------------------


def test_write_returns_external_ref(store):
    ref = _write_basic(store)
    assert ref.startswith("file://")
    assert "acct_001" in ref


def test_write_creates_file(store, token_dir):
    _write_basic(store)
    files = list(token_dir.iterdir())
    assert len(files) == 1
    assert files[0].suffix == ".json"


def test_file_permissions_are_restricted(store, token_dir):
    _write_basic(store)
    path = next(token_dir.iterdir())
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_read_returns_correct_payload(store):
    ref = _write_basic(store)
    payload = store.read(ref)
    assert payload.account_id == "acct_001"
    assert payload.access_token == "access_aaa"
    assert payload.refresh_token == "refresh_bbb"
    assert payload.google_sub == "1000000001"
    assert "youtube.readonly" in " ".join(payload.scopes)


def test_read_missing_file_raises(store):
    with pytest.raises(OAuthTokenStoreError, match="not found"):
        store.read("file:///nonexistent/path/to/token.json")


def test_read_wrong_prefix_raises(store):
    with pytest.raises(OAuthTokenStoreError, match="prefix"):
        store.read("vault://some/path")


def test_read_corrupt_file_raises(store, token_dir):
    ref = _write_basic(store)
    path = Path(ref[len("file://") :])
    path.write_text("NOT JSON", encoding="utf-8")
    with pytest.raises(OAuthTokenStoreError):
        store.read(ref)


# ---------------------------------------------------------------------------
# update_tokens
# ---------------------------------------------------------------------------


def test_update_tokens_replaces_access_token(store):
    ref = _write_basic(store)
    new_expiry = datetime.now(UTC) + timedelta(hours=2)
    store.update_tokens(
        ref, access_token="new_access_ccc", refresh_token=None, expires_at_utc=new_expiry
    )
    payload = store.read(ref)
    assert payload.access_token == "new_access_ccc"
    # Original refresh token preserved when new one is None
    assert payload.refresh_token == "refresh_bbb"


def test_update_tokens_replaces_refresh_token_when_provided(store):
    ref = _write_basic(store)
    store.update_tokens(
        ref,
        access_token="new_access",
        refresh_token="new_refresh_ddd",
        expires_at_utc=datetime.now(UTC) + timedelta(hours=1),
    )
    payload = store.read(ref)
    assert payload.refresh_token == "new_refresh_ddd"


def test_update_tokens_preserves_scopes_and_sub(store):
    ref = _write_basic(store)
    store.update_tokens(
        ref,
        access_token="new",
        refresh_token=None,
        expires_at_utc=datetime.now(UTC) + timedelta(hours=1),
    )
    payload = store.read(ref)
    assert "youtube.readonly" in " ".join(payload.scopes)
    assert payload.google_sub == "1000000001"


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_removes_file(store, token_dir):
    ref = _write_basic(store)
    store.delete(ref)
    assert not list(token_dir.iterdir())


def test_delete_silent_if_file_missing(store):
    store.delete("file:///nonexistent/path.json")  # Should not raise


def test_delete_noop_for_wrong_prefix(store):
    store.delete("vault://some/path")  # Should not raise


# ---------------------------------------------------------------------------
# StoredTokenPayload
# ---------------------------------------------------------------------------


def test_is_expired_false_when_future(store):
    ref = _write_basic(store)
    payload = store.read(ref)
    assert not payload.is_expired()


def test_is_expired_true_when_past(store, token_dir):
    ref = store.write(
        account_id="acct_002",
        access_token="access",
        refresh_token="refresh",
        token_type="Bearer",
        expires_at_utc=datetime.now(UTC) - timedelta(seconds=1),
        scopes=[],
        google_sub=None,
    )
    payload = store.read(ref)
    assert payload.is_expired()


def test_has_scope_true(store):
    ref = _write_basic(store)
    payload = store.read(ref)
    assert payload.has_scope("https://www.googleapis.com/auth/youtube.readonly")


def test_has_scope_false(store):
    ref = _write_basic(store)
    payload = store.read(ref)
    assert not payload.has_scope("https://www.googleapis.com/auth/youtube.upload")


# ---------------------------------------------------------------------------
# Multi-account isolation
# ---------------------------------------------------------------------------


def test_two_accounts_have_independent_token_files(store, token_dir):
    ref_a = store.write(
        account_id="acct_A",
        access_token="access_A",
        refresh_token="refresh_A",
        token_type="Bearer",
        expires_at_utc=datetime.now(UTC) + timedelta(hours=1),
        scopes=["https://www.googleapis.com/auth/youtube.readonly"],
        google_sub="sub_A",
    )
    ref_b = store.write(
        account_id="acct_B",
        access_token="access_B",
        refresh_token="refresh_B",
        token_type="Bearer",
        expires_at_utc=datetime.now(UTC) + timedelta(hours=1),
        scopes=["https://www.googleapis.com/auth/youtube.readonly"],
        google_sub="sub_B",
    )
    assert ref_a != ref_b
    assert store.read(ref_a).access_token == "access_A"
    assert store.read(ref_b).access_token == "access_B"


def test_deleting_account_a_does_not_affect_account_b(store):
    ref_a = _write_basic(store, "acct_A")
    ref_b = _write_basic(store, "acct_B")
    store.delete(ref_a)
    payload_b = store.read(ref_b)
    assert payload_b.account_id == "acct_B"


def test_updating_account_a_does_not_affect_account_b(store):
    ref_a = _write_basic(store, "acct_A")
    ref_b = _write_basic(store, "acct_B")
    store.update_tokens(
        ref_a,
        access_token="new_A",
        refresh_token=None,
        expires_at_utc=datetime.now(UTC) + timedelta(hours=1),
    )
    assert store.read(ref_b).access_token == "access_aaa"
