"""RBAC tests for OAuth endpoints.

Verifies that:
- Unauthenticated requests are rejected (401)
- Insufficient-role requests are rejected (403)
- Cross-workspace tokens are rejected (403)
- Admin-role requests succeed

Tests use FakeGoogleOAuthClient and no live network calls.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.service import AuthService
from app.auth.tokens import create_access_token
from app.control_plane import repository as cp_repo
from app.control_plane.models import (
    ChannelDraft,
    OrganizationDraft,
    PlatformAccountDraft,
    WorkspaceDraft,
)
from app.core.config import reset_config
from app.core.database import open_db
from app.oauth.client import FakeGoogleOAuthClient
from app.oauth.state import InMemoryOAuthStateStore, reset_state_store, set_state_store
from app.oauth.store import LocalFileTokenStore, reset_token_store, set_token_store

_SECRET = "test-secret-rbac-tests-32-bytes!!"
_ACCESS_EXPIRE = 900


def _uid():
    return str(uuid.uuid4())


def _bearer(user_id, email, roles):
    return create_access_token(
        user_id=user_id,
        email=email,
        workspace_roles=roles,
        secret_key=_SECRET,
        expire_seconds=_ACCESS_EXPIRE,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_config():
    reset_config()
    yield
    reset_config()


@pytest.fixture(autouse=True)
def fresh_state():
    set_state_store(InMemoryOAuthStateStore())
    yield
    reset_state_store()


@pytest.fixture
def tmp_token_store(tmp_path):
    store = LocalFileTokenStore(tmp_path / "tokens")
    set_token_store(store)
    yield store
    reset_token_store()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "rbac_test.db"


@pytest.fixture
def db_conn(db_path, monkeypatch):
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    conn = open_db(db_path)
    cp_repo.ensure_platform(conn, "plt-yt-rbac", "youtube", "YouTube")
    yield conn
    conn.close()


@pytest.fixture
def ws(db_conn):
    org = cp_repo.create_organization(
        db_conn, OrganizationDraft(id=_uid(), name="Org", slug="org", actor="t")
    )
    workspace = cp_repo.create_workspace(
        db_conn, WorkspaceDraft(id=_uid(), name="WS", slug="ws", actor="t", organization_id=org.id)
    )
    db_conn.commit()
    return workspace


@pytest.fixture
def ch(db_conn, ws):
    channel = cp_repo.create_channel(
        db_conn, ChannelDraft(id=_uid(), workspace_id=ws.id, name="Ch", slug="ch", actor="t")
    )
    db_conn.commit()
    return channel


@pytest.fixture
def acct(db_conn, ch):
    platform = cp_repo.get_platform_by_key(db_conn, "youtube")
    acct_id = _uid()
    draft = PlatformAccountDraft(
        id=acct_id,
        channel_id=ch.id,
        platform_id=platform.id,
        platform_key="youtube",
        external_account_id=f"pending:{acct_id}",
        display_name="Pending",
        actor="t",
        status="connected",
    )
    account = cp_repo.create_platform_account(db_conn, draft)
    db_conn.commit()
    return account


@pytest.fixture
def svc():
    return AuthService(
        secret_key=_SECRET,
        access_expire=_ACCESS_EXPIRE,
        refresh_expire=86400 * 7,
    )


@pytest.fixture
def api_client(db_path, monkeypatch, tmp_token_store):
    monkeypatch.setenv("ACE_ENV", "production")
    monkeypatch.setenv("ACE_SECRET_KEY", _SECRET)
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    reset_config()
    from app.api.main import app
    from app.api.routes.oauth import get_oauth_client

    fake = FakeGoogleOAuthClient()
    app.dependency_overrides[get_oauth_client] = lambda: fake
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def _start(ws_id, ch_id, acct_id):
    return f"/api/v1/workspaces/{ws_id}/channels/{ch_id}/accounts/{acct_id}/oauth/youtube/start"


def _disconnect(ws_id, ch_id, acct_id):
    return f"/api/v1/workspaces/{ws_id}/channels/{ch_id}/accounts/{acct_id}/oauth/youtube"


def _status(ws_id, ch_id, acct_id):
    return f"/api/v1/workspaces/{ws_id}/channels/{ch_id}/accounts/{acct_id}/connection"


def _make_user(svc, db_conn, email, workspace_id, role):
    uid = svc.register_user(db_conn, email, "password-long-enough")
    svc.assign_workspace_role(db_conn, uid, workspace_id, role)
    return _bearer(uid, email, {workspace_id: role})


# ---------------------------------------------------------------------------
# Start route RBAC
# ---------------------------------------------------------------------------


class TestStartRBAC:
    def test_unauthenticated_rejected(self, api_client, ws, ch, acct):
        r = api_client.post(_start(ws.id, ch.id, acct.id))
        assert r.status_code == 401

    def test_reviewer_rejected(self, api_client, svc, db_conn, ws, ch, acct):
        token = _make_user(svc, db_conn, "reviewer@x.com", ws.id, "reviewer")
        r = api_client.post(
            _start(ws.id, ch.id, acct.id), headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403

    def test_analyst_rejected(self, api_client, svc, db_conn, ws, ch, acct):
        token = _make_user(svc, db_conn, "analyst@x.com", ws.id, "analyst")
        r = api_client.post(
            _start(ws.id, ch.id, acct.id), headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403

    def test_operator_rejected(self, api_client, svc, db_conn, ws, ch, acct):
        token = _make_user(svc, db_conn, "operator@x.com", ws.id, "operator")
        r = api_client.post(
            _start(ws.id, ch.id, acct.id), headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403

    def test_admin_allowed(self, api_client, svc, db_conn, ws, ch, acct):
        token = _make_user(svc, db_conn, "admin@x.com", ws.id, "admin")
        r = api_client.post(
            _start(ws.id, ch.id, acct.id), headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200

    def test_cross_workspace_rejected(self, api_client, svc, db_conn, ws, ch, acct):
        # Token has admin in a DIFFERENT workspace — no access to this workspace
        other_ws = _uid()
        token = _bearer(42, "admin-other@x.com", {other_ws: "admin"})
        r = api_client.post(
            _start(ws.id, ch.id, acct.id), headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Disconnect route RBAC
# ---------------------------------------------------------------------------


class TestDisconnectRBAC:
    def test_unauthenticated_rejected(self, api_client, ws, ch, acct):
        r = api_client.delete(_disconnect(ws.id, ch.id, acct.id))
        assert r.status_code == 401

    def test_operator_rejected(self, api_client, svc, db_conn, ws, ch, acct):
        token = _make_user(svc, db_conn, "op@x.com", ws.id, "operator")
        r = api_client.delete(
            _disconnect(ws.id, ch.id, acct.id), headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403

    def test_admin_allowed(self, api_client, svc, db_conn, ws, ch, acct):
        token = _make_user(svc, db_conn, "admin@x.com", ws.id, "admin")
        r = api_client.delete(
            _disconnect(ws.id, ch.id, acct.id), headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200

    def test_cross_workspace_rejected(self, api_client, ws, ch, acct):
        other_ws = _uid()
        token = _bearer(99, "cross@x.com", {other_ws: "admin"})
        r = api_client.delete(
            _disconnect(ws.id, ch.id, acct.id), headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Connection status route RBAC
# ---------------------------------------------------------------------------


class TestConnectionStatusRBAC:
    def test_unauthenticated_rejected(self, api_client, ws, ch, acct):
        r = api_client.get(_status(ws.id, ch.id, acct.id))
        assert r.status_code == 401

    def test_authenticated_any_role_can_read_status(self, api_client, svc, db_conn, ws, ch, acct):
        # Connection status is readable by any authenticated workspace member
        token = _make_user(svc, db_conn, "reviewer@x.com", ws.id, "reviewer")
        r = api_client.get(
            _status(ws.id, ch.id, acct.id), headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
