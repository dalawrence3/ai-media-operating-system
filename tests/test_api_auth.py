"""Security regression tests for the JWT API authentication layer.

Covers: missing token → 401, expired token → 401, invalid signature → 401,
valid token → 200, wrong workspace → 403, account disabled → 403,
revoked refresh token → 401, all RBAC roles, and dev mode behaviour.

No real DB migrations are needed — open_db() creates the schema.
Tests run against a real in-process SQLite DB and the FastAPI TestClient.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth.service import AuthService
from app.auth.tokens import create_access_token, decode_access_token
from app.core.config import reset_config

_SECRET = "test-secret-key-for-tests-32bytes-long"
_ACCESS_EXPIRE = 900
_REFRESH_EXPIRE = 3600 * 24 * 7


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(user_id: int, email: str, roles: dict[str, str], expire: int = 900) -> str:
    return create_access_token(
        user_id=user_id,
        email=email,
        workspace_roles=roles,
        secret_key=_SECRET,
        expire_seconds=expire,
    )


def _make_expired_token(user_id: int, email: str, roles: dict[str, str]) -> str:
    return _make_token(user_id, email, roles, expire=-1)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_config():
    """Reset the config singleton between tests."""
    reset_config()
    yield
    reset_config()


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "api_auth_test.db"


@pytest.fixture()
def prod_client(db_path: Path, monkeypatch):
    """TestClient with ACE_ENV=production and ACE_SECRET_KEY set."""
    monkeypatch.setenv("ACE_ENV", "production")
    monkeypatch.setenv("ACE_SECRET_KEY", _SECRET)
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    reset_config()

    from app.api.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


@pytest.fixture()
def dev_client(db_path: Path, monkeypatch):
    """TestClient with ACE_ENV=development (dev auth enabled)."""
    monkeypatch.setenv("ACE_ENV", "development")
    monkeypatch.setenv("ACE_DEV_AUTH", "enabled")
    monkeypatch.setenv("ACE_SECRET_KEY", _SECRET)
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    reset_config()

    from app.api.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


@pytest.fixture()
def db_conn(db_path: Path, monkeypatch):
    """Open the same SQLite DB used by the TestClient."""
    from app.core.database import open_db

    conn = open_db(db_path)
    yield conn
    conn.close()


@pytest.fixture()
def svc():
    return AuthService(
        secret_key=_SECRET,
        access_expire=_ACCESS_EXPIRE,
        refresh_expire=_REFRESH_EXPIRE,
    )


@pytest.fixture()
def registered_user(db_conn, svc) -> dict:
    """Create a test user and return {user_id, email, password}."""
    email = "alice@example.com"
    password = "hunter2-long-enough"
    user_id = svc.register_user(db_conn, email, password)
    svc.assign_workspace_role(db_conn, user_id, "ws-001", "operator")
    return {"user_id": user_id, "email": email, "password": password}


# ---------------------------------------------------------------------------
# Production mode — unauthenticated requests
# ---------------------------------------------------------------------------


class TestProductionUnauthenticated:
    def test_missing_token_returns_401(self, prod_client):
        r = prod_client.get("/api/v1/auth/me")
        assert r.status_code == 401
        assert "WWW-Authenticate" in r.headers

    def test_missing_token_on_data_endpoint_returns_401(self, prod_client):
        r = prod_client.get("/api/v1/workspaces/ws-001/pipelines")
        assert r.status_code == 401

    def test_x_dev_actor_ignored_in_production(self, prod_client):
        r = prod_client.get(
            "/api/v1/auth/me",
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 401

    def test_invalid_authorization_format_returns_401(self, prod_client):
        r = prod_client.get("/api/v1/auth/me", headers={"Authorization": "Token abc123"})
        assert r.status_code == 401

    def test_empty_bearer_returns_401(self, prod_client):
        r = prod_client.get("/api/v1/auth/me", headers={"Authorization": "Bearer "})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Production mode — expired / invalid token
# ---------------------------------------------------------------------------


class TestInvalidTokens:
    def test_expired_token_returns_401(self, prod_client):
        token = _make_expired_token(1, "bob@example.com", {"ws-001": "operator"})
        r = prod_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
        assert "expired" in r.json()["detail"].lower()

    def test_wrong_signature_returns_401(self, prod_client):
        token = create_access_token(
            user_id=1,
            email="mallory@example.com",
            workspace_roles={},
            secret_key="completely-different-secret-key-x",
            expire_seconds=900,
        )
        r = prod_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401

    def test_truncated_token_returns_401(self, prod_client):
        token = _make_token(1, "eve@example.com", {})[:30]
        r = prod_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401

    def test_garbage_token_returns_401(self, prod_client):
        r = prod_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer not.a.valid.jwt.at.all"},
        )
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Production mode — valid token
# ---------------------------------------------------------------------------


class TestValidTokens:
    def test_valid_token_returns_200_on_me(self, prod_client):
        token = _make_token(42, "charlie@example.com", {"ws-001": "admin"})
        r = prod_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        data = r.json()
        assert data["user_id"] == 42
        assert data["email"] == "charlie@example.com"
        assert data["actor"] == "user:42"
        assert data["workspace_roles"] == {"ws-001": "admin"}

    def test_valid_token_grants_access_to_data_endpoint(self, prod_client, db_conn):
        token = _make_token(1, "dave@example.com", {"ws-001": "operator"})
        r = prod_client.get(
            "/api/v1/workspaces/ws-001/pipelines",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_health_endpoint_never_requires_auth(self, prod_client):
        r = prod_client.get("/health")
        assert r.status_code == 200

    def test_meta_endpoint_reports_jwt_mode(self, prod_client):
        r = prod_client.get("/api/meta")
        assert r.status_code == 200
        assert r.json()["auth_mode"] == "jwt"


# ---------------------------------------------------------------------------
# Login endpoint
# ---------------------------------------------------------------------------


class TestLogin:
    def test_login_returns_tokens(self, prod_client, db_conn, svc, registered_user):
        r = prod_client.post(
            "/api/v1/auth/login",
            json={"email": registered_user["email"], "password": registered_user["password"]},
        )
        assert r.status_code == 200
        data = r.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    def test_login_wrong_password_returns_401(self, prod_client, db_conn, svc, registered_user):
        r = prod_client.post(
            "/api/v1/auth/login",
            json={"email": registered_user["email"], "password": "wrong-password"},
        )
        assert r.status_code == 401
        # Unified message — must not reveal which of email/password was wrong.
        assert r.json()["detail"] == "Invalid email or password"

    def test_login_unknown_email_returns_401_same_message(self, prod_client):
        r = prod_client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "anypassword"},
        )
        assert r.status_code == 401
        assert r.json()["detail"] == "Invalid email or password"

    def test_login_missing_fields_returns_422(self, prod_client):
        r = prod_client.post("/api/v1/auth/login", json={})
        assert r.status_code == 422

    def test_login_disabled_account_returns_403(self, prod_client, db_conn, svc, registered_user):
        db_conn.execute(
            "UPDATE auth_users SET is_active = 0 WHERE email = ?",
            (registered_user["email"],),
        )
        db_conn.commit()
        r = prod_client.post(
            "/api/v1/auth/login",
            json={"email": registered_user["email"], "password": registered_user["password"]},
        )
        assert r.status_code == 403

    def test_login_access_token_is_verifiable(self, prod_client, db_conn, svc, registered_user):
        r = prod_client.post(
            "/api/v1/auth/login",
            json={"email": registered_user["email"], "password": registered_user["password"]},
        )
        token = r.json()["access_token"]
        claims = decode_access_token(token, secret_key=_SECRET)
        assert claims["email"] == registered_user["email"]
        assert "ws-001" in claims["roles"]


# ---------------------------------------------------------------------------
# Token refresh endpoint
# ---------------------------------------------------------------------------


class TestRefresh:
    def test_refresh_returns_new_access_token(self, prod_client, db_conn, svc, registered_user):
        login_r = prod_client.post(
            "/api/v1/auth/login",
            json={"email": registered_user["email"], "password": registered_user["password"]},
        )
        refresh_token = login_r.json()["refresh_token"]

        r = prod_client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert r.status_code == 200
        assert "access_token" in r.json()

    def test_refresh_with_invalid_token_returns_401(self, prod_client):
        r = prod_client.post("/api/v1/auth/refresh", json={"refresh_token": "not-a-real-token"})
        assert r.status_code == 401

    def test_refresh_with_revoked_token_returns_401(
        self, prod_client, db_conn, svc, registered_user
    ):
        login_r = prod_client.post(
            "/api/v1/auth/login",
            json={"email": registered_user["email"], "password": registered_user["password"]},
        )
        refresh_token = login_r.json()["refresh_token"]

        # Revoke the token via logout.
        access_token = login_r.json()["access_token"]
        prod_client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": refresh_token},
            headers={"Authorization": f"Bearer {access_token}"},
        )

        # Refresh with revoked token → 401.
        r = prod_client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert r.status_code == 401

    def test_refresh_missing_token_returns_422(self, prod_client):
        r = prod_client.post("/api/v1/auth/refresh", json={})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Logout endpoint
# ---------------------------------------------------------------------------


class TestLogout:
    def test_logout_requires_authentication(self, prod_client):
        r = prod_client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": "some-token"},
        )
        assert r.status_code == 401

    def test_logout_revokes_refresh_token(self, prod_client, db_conn, svc, registered_user):
        login_r = prod_client.post(
            "/api/v1/auth/login",
            json={"email": registered_user["email"], "password": registered_user["password"]},
        )
        access_token = login_r.json()["access_token"]
        refresh_token = login_r.json()["refresh_token"]

        r = prod_client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": refresh_token},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert r.status_code == 200
        assert r.json()["revoked"] is True


# ---------------------------------------------------------------------------
# RBAC — workspace membership and role enforcement
# ---------------------------------------------------------------------------


class TestRBACEnforcement:
    def test_no_workspace_membership_returns_403_on_mutation(self, prod_client):
        # Token for user with no membership in ws-999.
        token = _make_token(99, "stranger@example.com", {})
        r = prod_client.post(
            "/api/v1/workspaces/ws-999/pipelines",
            json={
                "channel_id": "ch-1",
                "end_stage": "learning",
                "idempotency_key": "k1",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    def test_wrong_workspace_membership_returns_403(self, prod_client):
        # User is member of ws-other, but requests ws-target.
        token = _make_token(1, "partial@example.com", {"ws-other": "admin"})
        r = prod_client.post(
            "/api/v1/workspaces/ws-target/pipelines",
            json={
                "channel_id": "ch-1",
                "end_stage": "learning",
                "idempotency_key": "k2",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    def test_operator_can_start_pipeline(self, prod_client, db_conn):
        """Operator role satisfies the 'pipeline:create' action (operator level)."""
        token = _make_token(10, "op@example.com", {"ws-001": "operator"})
        # A real DB is needed to actually start a pipeline; here we just verify
        # auth passes (error would be 400 or 422 from missing business data, not 401/403).
        r = prod_client.post(
            "/api/v1/workspaces/ws-001/pipelines",
            json={
                "channel_id": "ch-1",
                "end_stage": "learning",
                "idempotency_key": "k3",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code in {200, 400}  # Not 401 or 403

    def test_analyst_cannot_start_pipeline(self, prod_client):
        """Analyst role must be denied pipeline:create (requires operator)."""
        token = _make_token(20, "analyst@example.com", {"ws-001": "analyst"})
        r = prod_client.post(
            "/api/v1/workspaces/ws-001/pipelines",
            json={
                "channel_id": "ch-1",
                "end_stage": "learning",
                "idempotency_key": "k4",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    def test_reviewer_cannot_start_pipeline(self, prod_client):
        """Reviewer role must be denied pipeline:create (requires operator)."""
        token = _make_token(21, "reviewer@example.com", {"ws-001": "reviewer"})
        r = prod_client.post(
            "/api/v1/workspaces/ws-001/pipelines",
            json={
                "channel_id": "ch-1",
                "end_stage": "learning",
                "idempotency_key": "k5",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    def test_operator_can_approve_review(self, prod_client):
        """Operator role satisfies publish:approve (requires OPERATOR or higher)."""
        token = _make_token(22, "operator2@example.com", {"ws-001": "operator"})
        r = prod_client.post(
            "/api/v1/workspaces/ws-001/reviews/script/item-1/approve",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        # 400 = business error (item not found); not 401/403.
        assert r.status_code in {200, 400}

    def test_reviewer_cannot_approve_review(self, prod_client):
        """Reviewer role must be denied publish:approve (matrix requires operator)."""
        token = _make_token(23, "reviewer2@example.com", {"ws-001": "reviewer"})
        r = prod_client.post(
            "/api/v1/workspaces/ws-001/reviews/script/item-2/approve",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    def test_analyst_cannot_approve_review(self, prod_client):
        """Analyst role must be denied publish:approve (matrix requires operator)."""
        token = _make_token(24, "analyst2@example.com", {"ws-001": "analyst"})
        r = prod_client.post(
            "/api/v1/workspaces/ws-001/reviews/script/item-3/approve",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    def test_reviewer_can_reject_review(self, prod_client):
        """Reviewer role satisfies publish:reject (matrix requires reviewer or higher)."""
        token = _make_token(25, "reviewer3@example.com", {"ws-001": "reviewer"})
        r = prod_client.post(
            "/api/v1/workspaces/ws-001/reviews/script/item-4/reject",
            json={"reason": "needs revision"},
            headers={"Authorization": f"Bearer {token}"},
        )
        # 400 = business error (item not found); not 401/403.
        assert r.status_code in {200, 400}

    def test_read_endpoint_accessible_to_operator(self, prod_client):
        token = _make_token(11, "reader@example.com", {"ws-001": "operator"})
        r = prod_client.get(
            "/api/v1/workspaces/ws-001/pipelines",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Development mode
# ---------------------------------------------------------------------------


class TestDevMode:
    def test_dev_actor_header_accepted_without_token(self, dev_client):
        r = dev_client.get("/api/v1/auth/me", headers={"X-Dev-Actor": "dev:studio-user"})
        assert r.status_code == 200
        assert r.json()["actor"] == "dev:studio-user"

    def test_default_dev_actor_used_when_header_absent(self, dev_client):
        r = dev_client.get("/api/v1/auth/me")
        assert r.status_code == 200
        assert r.json()["actor"] == "dev:studio-user"

    def test_bearer_token_overrides_dev_actor_in_dev_mode(self, dev_client):
        token = _make_token(99, "jwt-user@example.com", {"ws-dev": "admin"})
        r = dev_client.get(
            "/api/v1/auth/me",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Dev-Actor": "dev:studio-user",
            },
        )
        assert r.status_code == 200
        assert r.json()["user_id"] == 99

    def test_meta_reports_dev_mode(self, dev_client):
        r = dev_client.get("/api/meta")
        assert r.status_code == 200
        assert r.json()["auth_mode"] == "dev"
        assert r.json()["environment"] == "development"
