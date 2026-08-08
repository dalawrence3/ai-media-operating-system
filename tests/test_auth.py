"""Tests for M15.5 Authentication (passwords, tokens, RBAC, AuthService).

No real network connections or external services required.
All tests use an in-process SQLite DB seeded by open_db().
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.auth.passwords import hash_password, needs_rehash, verify_password
from app.auth.rbac import Role, has_permission, require_role
from app.auth.service import (
    AccountDisabledError,
    AuthService,
    InvalidCredentialsError,
    RefreshTokenError,
    UserNotFoundError,
)
from app.auth.tokens import (
    AuthConfigurationError,
    TokenExpiredError,
    TokenInvalidError,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)

_SECRET = "a" * 32  # 32-byte dev key — never use in production


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path: Path):
    from app.core.database import open_db

    conn = open_db(tmp_path / "auth_test.db")
    yield conn
    conn.close()


@pytest.fixture()
def svc():
    return AuthService(secret_key=_SECRET, access_expire=900, refresh_expire=604800)


@pytest.fixture()
def user_id(db, svc):
    return svc.register_user(db, "alice@example.com", "correct-horse-battery-staple")


# ── Password hashing ──────────────────────────────────────────────────────


def test_hash_password_produces_argon2id_hash():
    h = hash_password("test-password")
    assert h.startswith("$argon2id$")


def test_hash_password_different_each_time():
    h1 = hash_password("same-password")
    h2 = hash_password("same-password")
    assert h1 != h2  # different salts


def test_verify_password_matches():
    h = hash_password("my-password")
    assert verify_password("my-password", h) is True


def test_verify_password_mismatch():
    h = hash_password("correct")
    assert verify_password("wrong", h) is False


def test_verify_password_empty_inputs_return_false():
    h = hash_password("valid")
    assert verify_password("", h) is False
    assert verify_password("valid", "") is False


def test_hash_password_rejects_empty():
    with pytest.raises(ValueError):
        hash_password("")


def test_needs_rehash_fresh_hash_returns_false():
    h = hash_password("password")
    assert needs_rehash(h) is False


# ── Token generation ──────────────────────────────────────────────────────


def test_create_and_decode_access_token():
    token = create_access_token(
        user_id=1,
        email="test@example.com",
        workspace_roles={"ws-1": "operator"},
        secret_key=_SECRET,
        expire_seconds=900,
    )
    claims = decode_access_token(token, secret_key=_SECRET)
    assert claims["sub"] == "1"
    assert claims["email"] == "test@example.com"
    assert claims["roles"] == {"ws-1": "operator"}


def test_decode_token_wrong_secret_raises():
    token = create_access_token(1, "t@t.com", {}, secret_key=_SECRET)
    with pytest.raises(TokenInvalidError):
        decode_access_token(token, secret_key="b" * 32)


def test_decode_expired_token_raises():
    token = create_access_token(
        1, "t@t.com", {}, secret_key=_SECRET, expire_seconds=-1
    )
    with pytest.raises(TokenExpiredError):
        decode_access_token(token, secret_key=_SECRET)


def test_short_secret_key_raises_config_error():
    with pytest.raises(AuthConfigurationError):
        create_access_token(1, "t@t.com", {}, secret_key="short")


def test_generate_refresh_token_returns_pair():
    raw, hashed = generate_refresh_token()
    assert len(raw) == 64  # 32 bytes = 64 hex chars
    assert len(hashed) == 64  # SHA-256 hex digest
    assert raw != hashed


def test_hash_refresh_token_is_deterministic():
    raw, h1 = generate_refresh_token()
    h2 = hash_refresh_token(raw)
    assert h1 == h2


def test_different_raw_tokens_produce_different_hashes():
    _, h1 = generate_refresh_token()
    _, h2 = generate_refresh_token()
    assert h1 != h2


# ── RBAC ──────────────────────────────────────────────────────────────────


def test_has_permission_owner_can_delete_workspace():
    assert has_permission("workspace:delete", "ws-1", {"ws-1": "owner"}) is True


def test_has_permission_analyst_cannot_delete_workspace():
    assert has_permission("workspace:delete", "ws-1", {"ws-1": "analyst"}) is False


def test_has_permission_analyst_can_view_analytics():
    assert has_permission("analytics:view", "ws-1", {"ws-1": "analyst"}) is True


def test_has_permission_operator_can_run_pipeline():
    assert has_permission("pipeline:run", "ws-1", {"ws-1": "operator"}) is True


def test_has_permission_reviewer_cannot_run_pipeline():
    assert has_permission("pipeline:run", "ws-1", {"ws-1": "reviewer"}) is False


def test_has_permission_wrong_workspace_returns_false():
    assert has_permission("pipeline:view", "ws-2", {"ws-1": "operator"}) is False


def test_has_permission_unknown_action_returns_false():
    assert has_permission("nonexistent:action", "ws-1", {"ws-1": "owner"}) is False


def test_require_role_passes_for_sufficient_role():
    require_role(Role.OPERATOR, "ws-1", {"ws-1": "owner"})  # owner ≥ operator
    require_role(Role.ANALYST, "ws-1", {"ws-1": "analyst"})  # exact match


def test_require_role_raises_for_insufficient_role():
    with pytest.raises(PermissionError, match="requires role"):
        require_role(Role.ADMIN, "ws-1", {"ws-1": "operator"})


def test_require_role_raises_for_non_member():
    with pytest.raises(PermissionError, match="not a member"):
        require_role(Role.ANALYST, "ws-2", {"ws-1": "owner"})


# ── AuthService ────────────────────────────────────────────────────────────


def test_auth_service_requires_32_byte_secret():
    with pytest.raises(AuthConfigurationError):
        AuthService(secret_key="too-short")


def test_register_user_returns_int_id(db, svc):
    uid = svc.register_user(db, "bob@example.com", "password123!")
    assert isinstance(uid, int)
    assert uid > 0


def test_register_duplicate_email_raises(db, svc):
    svc.register_user(db, "dup@example.com", "pass1!")
    with pytest.raises(sqlite3.IntegrityError):
        svc.register_user(db, "dup@example.com", "pass2!")


def test_register_empty_email_raises(db, svc):
    with pytest.raises(ValueError):
        svc.register_user(db, "", "password!")


def test_register_empty_password_raises(db, svc):
    with pytest.raises(ValueError):
        svc.register_user(db, "x@x.com", "")


def test_login_returns_tokens(db, svc, user_id):
    result = svc.login(db, "alice@example.com", "correct-horse-battery-staple")
    assert "access_token" in result
    assert "refresh_token" in result
    assert result["token_type"] == "bearer"


def test_login_access_token_has_correct_claims(db, svc, user_id):
    result = svc.login(db, "alice@example.com", "correct-horse-battery-staple")
    claims = svc.decode_token(result["access_token"])
    assert claims["email"] == "alice@example.com"
    assert str(user_id) == claims["sub"]


def test_login_refresh_token_is_not_stored_plaintext(db, svc, user_id):
    result = svc.login(db, "alice@example.com", "correct-horse-battery-staple")
    raw = result["refresh_token"]
    # Raw token must NOT appear in the DB
    rows = db.execute("SELECT token_hash FROM auth_refresh_tokens").fetchall()
    hashes = [r["token_hash"] for r in rows]
    assert raw not in hashes
    # But its hash must appear
    assert hash_refresh_token(raw) in hashes


def test_login_wrong_password_raises(db, svc, user_id):
    with pytest.raises(InvalidCredentialsError):
        svc.login(db, "alice@example.com", "wrong-password")


def test_login_unknown_email_raises(db, svc):
    with pytest.raises(UserNotFoundError):
        svc.login(db, "nobody@example.com", "password")


def test_login_disabled_account_raises(db, svc, user_id):
    db.execute("UPDATE auth_users SET is_active = 0 WHERE id = ?", (user_id,))
    db.commit()
    with pytest.raises(AccountDisabledError):
        svc.login(db, "alice@example.com", "correct-horse-battery-staple")


def test_refresh_issues_new_access_token(db, svc, user_id):
    result = svc.login(db, "alice@example.com", "correct-horse-battery-staple")
    refreshed = svc.refresh(db, result["refresh_token"])
    assert "access_token" in refreshed
    assert refreshed["token_type"] == "bearer"


def test_refresh_with_revoked_token_raises(db, svc, user_id):
    result = svc.login(db, "alice@example.com", "correct-horse-battery-staple")
    svc.revoke_refresh_token(db, result["refresh_token"])
    with pytest.raises(RefreshTokenError, match="revoked"):
        svc.refresh(db, result["refresh_token"])


def test_refresh_with_unknown_token_raises(db, svc):
    with pytest.raises(RefreshTokenError, match="not found"):
        svc.refresh(db, "a" * 64)


def test_revoke_refresh_token_returns_true_on_success(db, svc, user_id):
    result = svc.login(db, "alice@example.com", "correct-horse-battery-staple")
    assert svc.revoke_refresh_token(db, result["refresh_token"]) is True


def test_revoke_refresh_token_idempotent_returns_false(db, svc, user_id):
    result = svc.login(db, "alice@example.com", "correct-horse-battery-staple")
    svc.revoke_refresh_token(db, result["refresh_token"])
    assert svc.revoke_refresh_token(db, result["refresh_token"]) is False


def test_revoke_all_user_tokens(db, svc, user_id):
    svc.login(db, "alice@example.com", "correct-horse-battery-staple")
    svc.login(db, "alice@example.com", "correct-horse-battery-staple")
    revoked = svc.revoke_all_user_tokens(db, user_id)
    assert revoked == 2


def test_assign_and_remove_workspace_role(db, svc, user_id):
    svc.assign_workspace_role(db, user_id, "ws-1", "operator")
    roles = svc._get_workspace_roles(db, user_id)
    assert roles["ws-1"] == "operator"

    removed = svc.remove_workspace_role(db, user_id, "ws-1")
    assert removed is True
    roles = svc._get_workspace_roles(db, user_id)
    assert "ws-1" not in roles


def test_assign_workspace_role_upsert(db, svc, user_id):
    svc.assign_workspace_role(db, user_id, "ws-1", "operator")
    svc.assign_workspace_role(db, user_id, "ws-1", "admin")  # upgrade
    roles = svc._get_workspace_roles(db, user_id)
    assert roles["ws-1"] == "admin"


def test_login_includes_workspace_roles_in_token(db, svc, user_id):
    svc.assign_workspace_role(db, user_id, "ws-1", "operator")
    svc.assign_workspace_role(db, user_id, "ws-2", "analyst")
    result = svc.login(db, "alice@example.com", "correct-horse-battery-staple")
    claims = svc.decode_token(result["access_token"])
    assert claims["roles"]["ws-1"] == "operator"
    assert claims["roles"]["ws-2"] == "analyst"


def test_get_user_by_email_excludes_password_hash(db, svc, user_id):
    user = svc.get_user_by_email(db, "alice@example.com")
    assert user is not None
    assert "password_hash" not in user
    assert user["email"] == "alice@example.com"


def test_get_user_by_email_returns_none_for_missing(db, svc):
    assert svc.get_user_by_email(db, "nobody@example.com") is None
