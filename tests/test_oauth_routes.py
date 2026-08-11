"""API route tests for YouTube OAuth endpoints (no live Google calls).

Tests: start, callback (success + error cases), disconnect, connection status.
RBAC: unauthenticated rejected, insufficient role rejected.
All tests use FakeGoogleOAuthClient injected via FastAPI dependency override.
"""

from __future__ import annotations

import uuid
from pathlib import Path

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
from app.oauth.errors import OAuthCodeExchangeError
from app.oauth.state import InMemoryOAuthStateStore, reset_state_store, set_state_store
from app.oauth.store import LocalFileTokenStore, reset_token_store, set_token_store

_SECRET = "test-secret-oauth-routes-32-bytes"
_ACCESS_EXPIRE = 900


def _uid() -> str:
    return str(uuid.uuid4())


def _token(user_id: int, email: str, roles: dict[str, str]) -> str:
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
def fresh_state_store():
    store = InMemoryOAuthStateStore()
    set_state_store(store)
    yield store
    reset_state_store()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "oauth_routes_test.db"


@pytest.fixture
def token_store(tmp_path):
    store = LocalFileTokenStore(tmp_path / "tokens")
    set_token_store(store)
    yield store
    reset_token_store()


@pytest.fixture
def svc():
    return AuthService(
        secret_key=_SECRET,
        access_expire=_ACCESS_EXPIRE,
        refresh_expire=3600 * 24 * 7,
    )


@pytest.fixture
def db_conn(db_path: Path, monkeypatch):
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    conn = open_db(db_path)
    cp_repo.ensure_platform(conn, "plt-yt-1", "youtube", "YouTube")
    yield conn
    conn.close()


@pytest.fixture
def workspace(db_conn):
    org = cp_repo.create_organization(
        db_conn, OrganizationDraft(id=_uid(), name="Test Org", slug="test-org", actor="cli")
    )
    ws = cp_repo.create_workspace(
        db_conn,
        WorkspaceDraft(
            id=_uid(), name="Test WS", slug="test-ws", actor="cli", organization_id=org.id
        ),
    )
    db_conn.commit()
    return ws


@pytest.fixture
def channel(db_conn, workspace):
    ch = cp_repo.create_channel(
        db_conn,
        ChannelDraft(
            id=_uid(), workspace_id=workspace.id, name="Test Ch", slug="test-ch", actor="cli"
        ),
    )
    db_conn.commit()
    return ch


@pytest.fixture
def pending_account(db_conn, channel):
    platform = cp_repo.get_platform_by_key(db_conn, "youtube")
    acct_id = _uid()
    draft = PlatformAccountDraft(
        id=acct_id,
        channel_id=channel.id,
        platform_id=platform.id,
        platform_key="youtube",
        external_account_id=f"pending:{acct_id}",
        display_name="Pending YouTube Account",
        actor="test",
        status="connected",
    )
    acct = cp_repo.create_platform_account(db_conn, draft)
    db_conn.commit()
    return acct


@pytest.fixture
def fake_oauth_client():
    return FakeGoogleOAuthClient(
        fake_channel_id="UCroute_test_channel",
        fake_channel_title="Route Test Channel",
    )


def _make_client(db_path, monkeypatch, fake_client, token_store):
    monkeypatch.setenv("ACE_ENV", "production")
    monkeypatch.setenv("ACE_SECRET_KEY", _SECRET)
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    reset_config()
    from app.api.main import app
    from app.api.routes.oauth import get_oauth_client

    # Inject FakeGoogleOAuthClient and real token_store via dependency override
    app.dependency_overrides[get_oauth_client] = lambda: fake_client

    # Also inject token_store into the flow via the module singleton
    # (already set by the token_store fixture via set_token_store)

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
def client(db_path, monkeypatch, fake_oauth_client, token_store):
    yield from _make_client(db_path, monkeypatch, fake_oauth_client, token_store)


def _admin_token(svc, db_conn, workspace_id: str) -> str:
    user_id = svc.register_user(db_conn, "admin@example.com", "hunter2-long-enough-pass")
    svc.assign_workspace_role(db_conn, user_id, workspace_id, "admin")
    return _token(user_id, "admin@example.com", {workspace_id: "admin"})


def _operator_token(svc, db_conn, workspace_id: str) -> str:
    user_id = svc.register_user(db_conn, "operator@example.com", "hunter2-long-enough-pass")
    svc.assign_workspace_role(db_conn, user_id, workspace_id, "operator")
    return _token(user_id, "operator@example.com", {workspace_id: "operator"})


def _start_url(ws_id, ch_id, acct_id):
    return f"/api/v1/workspaces/{ws_id}/channels/{ch_id}/accounts/{acct_id}/oauth/youtube/start"


def _disconnect_url(ws_id, ch_id, acct_id):
    return f"/api/v1/workspaces/{ws_id}/channels/{ch_id}/accounts/{acct_id}/oauth/youtube"


def _status_url(ws_id, ch_id, acct_id):
    return f"/api/v1/workspaces/{ws_id}/channels/{ch_id}/accounts/{acct_id}/connection"


# ---------------------------------------------------------------------------
# POST /oauth/youtube/start — authentication & RBAC
# ---------------------------------------------------------------------------


class TestStartRoute:
    def test_start_unauthenticated_returns_401(self, client, workspace, channel, pending_account):
        r = client.post(_start_url(workspace.id, channel.id, pending_account.id))
        assert r.status_code == 401

    def test_start_operator_returns_403(
        self, client, svc, db_conn, workspace, channel, pending_account
    ):
        token = _operator_token(svc, db_conn, workspace.id)
        r = client.post(
            _start_url(workspace.id, channel.id, pending_account.id),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    def test_start_admin_returns_authorization_url(
        self, client, svc, db_conn, workspace, channel, pending_account
    ):
        token = _admin_token(svc, db_conn, workspace.id)
        r = client.post(
            _start_url(workspace.id, channel.id, pending_account.id),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "authorization_url" in body
        assert "accounts.google.com" in body["authorization_url"]

    def test_start_cross_workspace_forbidden(
        self, client, svc, db_conn, workspace, channel, pending_account
    ):
        other_ws_id = _uid()
        token = _token(999, "other@example.com", {other_ws_id: "admin"})
        r = client.post(
            _start_url(workspace.id, channel.id, pending_account.id),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    def test_start_nonexistent_account_returns_404(self, client, svc, db_conn, workspace, channel):
        token = _admin_token(svc, db_conn, workspace.id)
        r = client.post(
            _start_url(workspace.id, channel.id, "nonexistent-account-id"),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /oauth/youtube/callback
# ---------------------------------------------------------------------------


class TestCallbackRoute:
    def _get_state_for_account(self, client, svc, db_conn, workspace, channel, account):
        """Helper: call start to generate a state nonce, extract it from the URL."""
        token = _admin_token(svc, db_conn, workspace.id)
        r = client.post(
            _start_url(workspace.id, channel.id, account.id),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        auth_url = r.json()["authorization_url"]
        # Extract state from URL: ?state=<nonce>
        import urllib.parse

        parsed = urllib.parse.urlparse(auth_url)
        params = urllib.parse.parse_qs(parsed.query)
        return params["state"][0]

    def test_callback_success_redirects_to_channel_page(
        self, client, svc, db_conn, workspace, channel, pending_account
    ):
        state = self._get_state_for_account(
            client, svc, db_conn, workspace, channel, pending_account
        )
        r = client.get(
            f"/api/v1/oauth/youtube/callback?code=fake_code&state={state}",
            follow_redirects=False,
        )
        assert r.status_code == 302
        loc = r.headers["location"]
        assert f"/workspaces/{workspace.id}/channels" in loc
        assert "oauth_success=true" in loc
        assert pending_account.id in loc
        assert channel.id in loc

    def test_callback_google_error_redirects_with_error_code(self, client):
        r = client.get(
            "/api/v1/oauth/youtube/callback?error=access_denied",
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "oauth_error=access_denied" in r.headers["location"]

    def test_callback_missing_params_redirects_with_error(self, client):
        r = client.get("/api/v1/oauth/youtube/callback", follow_redirects=False)
        assert r.status_code == 302
        assert "oauth_error=missing_params" in r.headers["location"]

    def test_callback_invalid_state_redirects_with_error(self, client):
        r = client.get(
            "/api/v1/oauth/youtube/callback?code=fake_code&state=invalid_nonce",
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "oauth_error=state_expired" in r.headers["location"]

    def test_callback_error_code_sanitized(self, client):
        r = client.get(
            "/api/v1/oauth/youtube/callback?error=<script>bad</script>",
            follow_redirects=False,
        )
        assert r.status_code == 302
        location = r.headers["location"]
        assert "<script>" not in location
        assert "scriptbadscript" in location  # alphanumeric only

    def test_callback_exchange_failure_redirects_with_error(
        self, db_path, monkeypatch, svc, db_conn, workspace, channel, pending_account, token_store
    ):
        bad_client = FakeGoogleOAuthClient(fail_exchange=OAuthCodeExchangeError("bad code"))
        gen = _make_client(db_path, monkeypatch, bad_client, token_store)
        bad_test_client = next(gen)
        try:
            token = _admin_token(svc, db_conn, workspace.id)
            r = bad_test_client.post(
                _start_url(workspace.id, channel.id, pending_account.id),
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 200
            import urllib.parse

            parsed = urllib.parse.urlparse(r.json()["authorization_url"])
            state = urllib.parse.parse_qs(parsed.query)["state"][0]

            r2 = bad_test_client.get(
                f"/api/v1/oauth/youtube/callback?code=bad_code&state={state}",
                follow_redirects=False,
            )
            assert r2.status_code == 302
            assert "oauth_error=exchange_failed" in r2.headers["location"]
        finally:
            try:
                next(gen)
            except StopIteration:
                pass


# ---------------------------------------------------------------------------
# DELETE /oauth/youtube
# ---------------------------------------------------------------------------


class TestDisconnectRoute:
    def test_disconnect_unauthenticated_returns_401(
        self, client, workspace, channel, pending_account
    ):
        r = client.delete(_disconnect_url(workspace.id, channel.id, pending_account.id))
        assert r.status_code == 401

    def test_disconnect_operator_returns_403(
        self, client, svc, db_conn, workspace, channel, pending_account
    ):
        token = _operator_token(svc, db_conn, workspace.id)
        r = client.delete(
            _disconnect_url(workspace.id, channel.id, pending_account.id),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    def test_disconnect_admin_succeeds(
        self, client, svc, db_conn, workspace, channel, pending_account
    ):
        token = _admin_token(svc, db_conn, workspace.id)
        r = client.delete(
            _disconnect_url(workspace.id, channel.id, pending_account.id),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "disconnected"

    def test_disconnect_nonexistent_account_returns_404(
        self, client, svc, db_conn, workspace, channel
    ):
        token = _admin_token(svc, db_conn, workspace.id)
        r = client.delete(
            _disconnect_url(workspace.id, channel.id, "bad-account-id"),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /connection
# ---------------------------------------------------------------------------


class TestConnectionStatusRoute:
    def test_status_unauthenticated_returns_401(self, client, workspace, channel, pending_account):
        r = client.get(_status_url(workspace.id, channel.id, pending_account.id))
        assert r.status_code == 401

    def test_status_returns_not_connected_before_oauth(
        self, client, svc, db_conn, workspace, channel, pending_account
    ):
        token = _admin_token(svc, db_conn, workspace.id)
        r = client.get(
            _status_url(workspace.id, channel.id, pending_account.id),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["connected"] is False

    def test_status_returns_connected_after_oauth(
        self, client, svc, db_conn, workspace, channel, pending_account
    ):
        token = _admin_token(svc, db_conn, workspace.id)
        # Start flow
        r_start = client.post(
            _start_url(workspace.id, channel.id, pending_account.id),
            headers={"Authorization": f"Bearer {token}"},
        )
        import urllib.parse

        state = urllib.parse.parse_qs(
            urllib.parse.urlparse(r_start.json()["authorization_url"]).query
        )["state"][0]

        # Complete flow via callback
        client.get(
            f"/api/v1/oauth/youtube/callback?code=fake_code&state={state}",
            follow_redirects=False,
        )

        # Check status
        r_status = client.get(
            _status_url(workspace.id, channel.id, pending_account.id),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r_status.status_code == 200
        body = r_status.json()
        assert body["connected"] is True
        assert body["provider_channel_id"] == "UCroute_test_channel"

    def test_status_nonexistent_account_returns_404(self, client, svc, db_conn, workspace, channel):
        token = _admin_token(svc, db_conn, workspace.id)
        r = client.get(
            _status_url(workspace.id, channel.id, "nonexistent-id"),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Callback redirect — ACE_FRONTEND_URL prefix (root cause 3)
# ---------------------------------------------------------------------------


class TestCallbackFrontendRedirect:
    """Verify that ACE_FRONTEND_URL is prepended to all OAuth redirect destinations.

    In local dev the OAuth callback lands on :8000 (FastAPI) while the SPA lives
    on :5173 (Vite).  Without the prefix the browser stays on :8000 where FastAPI
    has no SPA routes, resulting in {"detail": "Not Found"}.
    """

    def _get_state(self, client, svc, db_conn, workspace, channel, account):
        import urllib.parse

        token = _admin_token(svc, db_conn, workspace.id)
        r = client.post(
            _start_url(workspace.id, channel.id, account.id),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        parsed = urllib.parse.urlparse(r.json()["authorization_url"])
        return urllib.parse.parse_qs(parsed.query)["state"][0]

    def test_success_redirect_includes_frontend_url(
        self, db_path, monkeypatch, svc, db_conn, workspace, channel, pending_account, token_store
    ):
        monkeypatch.setenv("ACE_FRONTEND_URL", "http://localhost:5173")
        reset_config()
        gen = _make_client(db_path, monkeypatch, FakeGoogleOAuthClient(), token_store)
        test_client = next(gen)
        try:
            state = self._get_state(test_client, svc, db_conn, workspace, channel, pending_account)
            r = test_client.get(
                f"/api/v1/oauth/youtube/callback?code=fake_code&state={state}",
                follow_redirects=False,
            )
            assert r.status_code == 302
            loc = r.headers["location"]
            assert loc.startswith("http://localhost:5173/")
            assert "oauth_success=true" in loc
        finally:
            try:
                next(gen)
            except StopIteration:
                pass

    def test_error_redirect_includes_frontend_url(
        self, db_path, monkeypatch, svc, db_conn, workspace, channel, pending_account, token_store
    ):
        monkeypatch.setenv("ACE_FRONTEND_URL", "http://localhost:5173")
        reset_config()
        gen = _make_client(db_path, monkeypatch, FakeGoogleOAuthClient(), token_store)
        test_client = next(gen)
        try:
            r = test_client.get(
                "/api/v1/oauth/youtube/callback?error=access_denied",
                follow_redirects=False,
            )
            assert r.status_code == 302
            loc = r.headers["location"]
            assert loc.startswith("http://localhost:5173/")
            assert "oauth_error=access_denied" in loc
        finally:
            try:
                next(gen)
            except StopIteration:
                pass

    def test_success_redirect_is_relative_without_frontend_url(
        self, client, svc, db_conn, workspace, channel, pending_account
    ):
        """Without ACE_FRONTEND_URL the redirect is a relative path (production behaviour)."""
        state = self._get_state(client, svc, db_conn, workspace, channel, pending_account)
        r = client.get(
            f"/api/v1/oauth/youtube/callback?code=fake_code&state={state}",
            follow_redirects=False,
        )
        assert r.status_code == 302
        loc = r.headers["location"]
        # Relative path — starts with /workspaces (no http scheme)
        assert loc.startswith("/workspaces/")
        assert "oauth_success=true" in loc


# ---------------------------------------------------------------------------
# OAUTHLIB_INSECURE_TRANSPORT — localhost redirect URI (root cause 2)
# ---------------------------------------------------------------------------


def test_real_client_sets_insecure_transport_for_localhost(tmp_path):
    """Regression: RealGoogleOAuthClient must set OAUTHLIB_INSECURE_TRANSPORT=1
    when the redirect URI uses HTTP so that fetch_token does not raise
    InsecureTransportError during local development."""
    import json
    import os

    from app.oauth.client_google import RealGoogleOAuthClient

    secrets = {
        "web": {
            "client_id": "fake.apps.googleusercontent.com",
            "client_secret": "fake_secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://localhost:8000/api/v1/oauth/youtube/callback"],
            "javascript_origins": ["http://localhost:8000"],
        }
    }
    p = tmp_path / "client_secret.json"
    p.write_text(json.dumps(secrets))

    # Clear the flag before the test so we detect whether the client sets it
    old = os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
    try:
        RealGoogleOAuthClient(
            client_secrets_path=str(p),
            redirect_uri="http://localhost:8000/api/v1/oauth/youtube/callback",
        )
        assert os.environ.get("OAUTHLIB_INSECURE_TRANSPORT") == "1"
    finally:
        if old is None:
            os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
        else:
            os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = old


def test_real_client_does_not_set_insecure_transport_for_https(tmp_path):
    """Production HTTPS redirect URIs must NOT set OAUTHLIB_INSECURE_TRANSPORT."""
    import json
    import os

    from app.oauth.client_google import RealGoogleOAuthClient

    secrets = {
        "web": {
            "client_id": "fake.apps.googleusercontent.com",
            "client_secret": "fake_secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["https://app.example.com/api/v1/oauth/youtube/callback"],
            "javascript_origins": ["https://app.example.com"],
        }
    }
    p = tmp_path / "client_secret.json"
    p.write_text(json.dumps(secrets))

    old = os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
    try:
        RealGoogleOAuthClient(
            client_secrets_path=str(p),
            redirect_uri="https://app.example.com/api/v1/oauth/youtube/callback",
        )
        assert "OAUTHLIB_INSECURE_TRANSPORT" not in os.environ
    finally:
        if old is None:
            os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
        else:
            os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = old
