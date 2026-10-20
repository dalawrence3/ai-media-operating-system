"""Backend API tests for channel and platform-account creation mutations.

Covers:
- POST /workspaces/{ws}/channels
- POST /workspaces/{ws}/channels/{ch}/accounts

Authentication scenarios: dev mode, production JWT, unauthenticated.
RBAC: owner/admin allowed, operator/reviewer/analyst rejected for admin-gated actions.
Workspace isolation: cross-workspace creation rejected.
Workspace state: paused workspace rejects mutations.
Actor attribution: server identity, not client body.
Input validation: missing fields, duplicate slug.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth.service import AuthService
from app.auth.tokens import create_access_token
from app.control_plane import repository as cp_repo
from app.control_plane.models import ChannelDraft, OrganizationDraft, WorkspaceDraft
from app.core.config import reset_config
from app.core.database import open_db

_SECRET = "test-secret-channels-32bytes-ok!!"
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
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_config():
    reset_config()
    yield
    reset_config()


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "channels_test.db"


@pytest.fixture()
def dev_client(db_path: Path, monkeypatch):
    monkeypatch.setenv("ACE_ENV", "development")
    monkeypatch.setenv("ACE_DEV_AUTH", "enabled")
    monkeypatch.setenv("ACE_SECRET_KEY", _SECRET)
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    reset_config()
    from app.api.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture()
def prod_client(db_path: Path, monkeypatch):
    monkeypatch.setenv("ACE_ENV", "production")
    monkeypatch.setenv("ACE_SECRET_KEY", _SECRET)
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    reset_config()
    from app.api.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture()
def db_conn(db_path: Path, monkeypatch):
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    conn = open_db(db_path)
    yield conn
    conn.close()


@pytest.fixture()
def svc():
    return AuthService(
        secret_key=_SECRET,
        access_expire=_ACCESS_EXPIRE,
        refresh_expire=3600 * 24 * 7,
    )


@pytest.fixture()
def workspace(db_conn):
    org = cp_repo.create_organization(
        db_conn, OrganizationDraft(id=_uid(), name="Test Org", slug="test-org", actor="cli")
    )
    ws = cp_repo.create_workspace(
        db_conn,
        WorkspaceDraft(
            id=_uid(), name="Test Workspace", slug="test-ws", actor="cli", organization_id=org.id
        ),
    )
    db_conn.commit()
    return ws


@pytest.fixture()
def channel(db_conn, workspace):
    ch = cp_repo.create_channel(
        db_conn,
        ChannelDraft(
            id=_uid(),
            workspace_id=workspace.id,
            name="Test Channel",
            slug="test-channel",
            actor="cli",
        ),
    )
    db_conn.commit()
    return ch


@pytest.fixture()
def platform(db_conn):
    """Seed the 'youtube' platform so platform-account FK constraints pass."""
    cp_repo.ensure_platform(db_conn, "youtube", "youtube", "YouTube")
    db_conn.commit()


def _admin_token(svc, db_conn, workspace_id: str) -> str:
    user_id = svc.register_user(db_conn, "admin@example.com", "hunter2-long-enough-pass")
    svc.assign_workspace_role(db_conn, user_id, workspace_id, "admin")
    return _token(user_id, "admin@example.com", {workspace_id: "admin"})


def _operator_token(svc, db_conn, workspace_id: str) -> str:
    user_id = svc.register_user(db_conn, "operator@example.com", "hunter2-long-enough-pass")
    svc.assign_workspace_role(db_conn, user_id, workspace_id, "operator")
    return _token(user_id, "operator@example.com", {workspace_id: "operator"})


# ---------------------------------------------------------------------------
# POST /channels — dev mode
# ---------------------------------------------------------------------------


class TestCreateChannelDevMode:
    def test_dev_mode_creates_channel(self, dev_client, workspace):
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels",
            json={"name": "My Channel", "slug": "my-channel"},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "My Channel"
        assert body["slug"] == "my-channel"
        assert body["workspace_id"] == workspace.id

    def test_dev_mode_actor_comes_from_auth_not_body(self, dev_client, workspace):
        """Client cannot inject actor via request body — actor is server-owned."""
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels",
            # body has no actor field; any attempt to smuggle one is ignored
            json={"name": "Spoof Test", "slug": "spoof-test", "actor": "injected:attacker"},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # actor stored in DB must be the authenticated dev actor, not injected value
        assert body["actor"] == "dev:studio-user"
        assert "injected:attacker" not in str(body)

    def test_description_is_optional(self, dev_client, workspace):
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels",
            json={"name": "No Desc", "slug": "no-desc"},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 200

    def test_description_persisted_when_supplied(self, dev_client, workspace):
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels",
            json={"name": "With Desc", "slug": "with-desc", "description": "Hello"},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 200
        assert r.json()["description"] == "Hello"

    def test_duplicate_slug_returns_400(self, dev_client, workspace):
        payload = {"name": "Dup", "slug": "dup-slug"}
        r1 = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels",
            json=payload,
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r1.status_code == 200
        r2 = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels",
            json=payload,
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r2.status_code == 400

    def test_missing_name_returns_400(self, dev_client, workspace):
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels",
            json={"slug": "no-name"},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code in (400, 422)

    def test_missing_slug_returns_400(self, dev_client, workspace):
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels",
            json={"name": "No Slug"},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code in (400, 422)

    def test_channel_id_generated_server_side(self, dev_client, workspace):
        """Browser must not supply or control the channel primary key."""
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels",
            json={"name": "ID Test", "slug": "id-test", "id": "attacker-chosen-id"},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 200
        body = r.json()
        # id must be a uuid4, not the attacker-chosen value
        assert body["id"] != "attacker-chosen-id"
        # valid UUID format
        uuid.UUID(body["id"])

    def test_workspace_scope_is_immutable(self, dev_client, workspace, db_conn):
        """workspace_id in the URL governs the channel; body cannot override it."""
        other_org = cp_repo.create_organization(
            db_conn, OrganizationDraft(id=_uid(), name="Other", slug="other", actor="cli")
        )
        other_ws = cp_repo.create_workspace(
            db_conn,
            WorkspaceDraft(
                id=_uid(),
                name="Other WS",
                slug="other-ws",
                actor="cli",
                organization_id=other_org.id,
            ),
        )
        db_conn.commit()
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels",
            # workspace_id in body targets a different workspace
            json={
                "name": "Scope Test",
                "slug": "scope-test",
                "workspace_id": other_ws.id,
            },
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 200
        body = r.json()
        # The channel must be scoped to the URL workspace, not the body one
        assert body["workspace_id"] == workspace.id

    def test_paused_workspace_rejects_creation(self, dev_client, workspace, db_conn):
        cp_repo.update_workspace_status(db_conn, workspace.id, "suspended", "cli")
        db_conn.commit()
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels",
            json={"name": "Should Fail", "slug": "should-fail"},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 400

    def test_nonexistent_workspace_returns_400_or_404(self, dev_client):
        r = dev_client.post(
            "/api/v1/workspaces/nonexistent-workspace/channels",
            json={"name": "Ghost", "slug": "ghost"},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code in (400, 404)

    def test_result_is_scoped_to_correct_workspace(self, dev_client, workspace):
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels",
            json={"name": "Scope Check", "slug": "scope-check"},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 200
        assert r.json()["workspace_id"] == workspace.id

    def test_created_channel_appears_in_list(self, dev_client, workspace):
        dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels",
            json={"name": "Listed", "slug": "listed"},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        r = dev_client.get(
            f"/api/v1/workspaces/{workspace.id}/channels",
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 200
        slugs = [c["slug"] for c in r.json()]
        assert "listed" in slugs


# ---------------------------------------------------------------------------
# POST /channels — production JWT RBAC
# ---------------------------------------------------------------------------


class TestCreateChannelProductionRBAC:
    def test_unauthenticated_returns_401(self, prod_client, workspace):
        r = prod_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels",
            json={"name": "No Auth", "slug": "no-auth"},
        )
        assert r.status_code == 401

    def test_admin_can_create_channel(self, prod_client, workspace, db_conn, svc):
        token = _admin_token(svc, db_conn, workspace.id)
        r = prod_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels",
            json={"name": "Admin Channel", "slug": "admin-channel"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["slug"] == "admin-channel"

    def test_operator_cannot_create_channel(self, prod_client, workspace, db_conn, svc):
        token = _operator_token(svc, db_conn, workspace.id)
        r = prod_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels",
            json={"name": "Op Channel", "slug": "op-channel"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code in (403, 400)

    def test_no_workspace_membership_returns_403(self, prod_client, workspace, db_conn, svc):
        # User exists but has NO role in this workspace
        user_id = svc.register_user(db_conn, "outsider@example.com", "hunter2-long-enough-pass")
        token = _token(user_id, "outsider@example.com", {"other-workspace-id": "admin"})
        r = prod_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels",
            json={"name": "Outsider", "slug": "outsider"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code in (403, 400)

    def test_actor_is_jwt_identity_not_body(self, prod_client, workspace, db_conn, svc):
        token = _admin_token(svc, db_conn, workspace.id)
        r = prod_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels",
            json={"name": "Actor Test", "slug": "actor-test", "actor": "hacker:injected"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["actor"] != "hacker:injected"
        # must be a user: actor from JWT
        assert body["actor"].startswith("user:")


# ---------------------------------------------------------------------------
# POST /channels/{ch}/accounts — dev mode
# ---------------------------------------------------------------------------


class TestCreatePlatformAccountDevMode:
    def test_creates_platform_account(self, dev_client, workspace, channel, platform):
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={
                "platform_id": "youtube",
                "external_account_id": "UCabc123",
                "display_name": "My YouTube",
            },
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["platform_id"] == "youtube"
        assert body["external_account_id"] == "UCabc123"
        assert body["display_name"] == "My YouTube"
        assert body["channel_id"] == channel.id

    def test_initial_status_is_disconnected(self, dev_client, workspace, channel, platform):
        """Status must be truthful — not 'connected' before any OAuth flow."""
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={
                "platform_id": "youtube",
                "external_account_id": "UCdiscon",
                "display_name": "Disconnected Account",
            },
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "disconnected"

    def test_account_id_is_server_generated(self, dev_client, workspace, channel, platform):
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={
                "platform_id": "youtube",
                "external_account_id": "UCidtest",
                "display_name": "ID Test",
                "id": "client-injected-id",
            },
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["id"] != "client-injected-id"
        uuid.UUID(body["id"])

    def test_actor_is_server_identity_not_body(self, dev_client, workspace, channel, platform):
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={
                "platform_id": "youtube",
                "external_account_id": "UCactor",
                "display_name": "Actor Test",
                "actor": "hacker:injected",
            },
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 200
        assert r.json()["actor"] == "dev:studio-user"

    def test_missing_platform_id_returns_400(self, dev_client, workspace, channel, platform):
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={"external_account_id": "UCx", "display_name": "X"},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code in (400, 422)

    def test_missing_external_account_id_returns_400(
        self, dev_client, workspace, channel, platform
    ):
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={"platform_id": "youtube", "display_name": "X"},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code in (400, 422)

    def test_account_appears_in_channel_list(self, dev_client, workspace, channel, platform):
        dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={
                "platform_id": "youtube",
                "external_account_id": "UClisted",
                "display_name": "Listed Account",
            },
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        r = dev_client.get(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 200
        ext_ids = [a["external_account_id"] for a in r.json()]
        assert "UClisted" in ext_ids

    def test_paused_workspace_rejects_account_creation(
        self, dev_client, workspace, channel, platform, db_conn
    ):
        cp_repo.update_workspace_status(db_conn, workspace.id, "suspended", "cli")
        db_conn.commit()
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={
                "platform_id": "youtube",
                "external_account_id": "UCpaused",
                "display_name": "Paused",
            },
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# POST /channels/{ch}/accounts — production JWT RBAC
# ---------------------------------------------------------------------------


class TestCreatePlatformAccountProductionRBAC:
    def test_unauthenticated_returns_401(self, prod_client, workspace, channel, platform):
        r = prod_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={
                "platform_id": "youtube",
                "external_account_id": "UCx",
                "display_name": "Unauth",
            },
        )
        assert r.status_code == 401

    def test_admin_can_create_account(
        self, prod_client, workspace, channel, platform, db_conn, svc
    ):
        token = _admin_token(svc, db_conn, workspace.id)
        r = prod_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={
                "platform_id": "youtube",
                "external_account_id": "UCadmin",
                "display_name": "Admin Account",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text

    def test_operator_cannot_create_account(
        self, prod_client, workspace, channel, platform, db_conn, svc
    ):
        token = _operator_token(svc, db_conn, workspace.id)
        r = prod_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={
                "platform_id": "youtube",
                "external_account_id": "UCop",
                "display_name": "Op Account",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code in (403, 400)

    def test_cross_workspace_account_creation_rejected(
        self, prod_client, workspace, channel, platform, db_conn, svc
    ):
        """Admin of workspace A cannot add accounts to channels in workspace B."""
        other_org = cp_repo.create_organization(
            db_conn, OrganizationDraft(id=_uid(), name="Other Org", slug="other-org", actor="cli")
        )
        other_ws = cp_repo.create_workspace(
            db_conn,
            WorkspaceDraft(
                id=_uid(),
                name="Other WS",
                slug="other-ws",
                actor="cli",
                organization_id=other_org.id,
            ),
        )
        db_conn.commit()
        # Token has admin in other_ws but NOT in workspace (which owns channel)
        user_id = svc.register_user(db_conn, "other-admin@example.com", "hunter2-long-enough-pass")
        svc.assign_workspace_role(db_conn, user_id, other_ws.id, "admin")
        token = _token(user_id, "other-admin@example.com", {other_ws.id: "admin"})
        r = prod_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={
                "platform_id": "youtube",
                "external_account_id": "UCcross",
                "display_name": "Cross-workspace Account",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code in (403, 400)

    def test_duplicate_external_account_id_on_same_channel_returns_400(
        self, dev_client, workspace, channel, platform
    ):
        """UNIQUE(channel_id, platform_key, external_account_id) prevents duplicates."""
        payload = {
            "platform_id": "youtube",
            "external_account_id": "UCdupacct",
            "display_name": "First Account",
        }
        r1 = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json=payload,
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r1.status_code == 200
        r2 = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={**payload, "display_name": "Duplicate"},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r2.status_code == 400

    def test_two_accounts_same_platform_different_external_id_allowed(
        self, dev_client, workspace, channel, platform
    ):
        """Multiple accounts per platform are allowed when external_account_id differs."""
        r1 = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={
                "platform_id": "youtube",
                "external_account_id": "UCfirst",
                "display_name": "First",
            },
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        r2 = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={
                "platform_id": "youtube",
                "external_account_id": "UCsecond",
                "display_name": "Second",
            },
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r1.status_code == 200
        assert r2.status_code == 200


# ---------------------------------------------------------------------------
# Platform ID canonical enforcement — server-side validation
# ---------------------------------------------------------------------------


class TestPlatformIdValidation:
    """Server must reject unsupported platform IDs before any DB row is written."""

    def test_unsupported_platform_id_rejected(self, dev_client, workspace, channel, platform):
        """An unknown platform_id returns 400."""
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={
                "platform_id": "attacker-platform",
                "external_account_id": "evil-id",
                "display_name": "Evil",
            },
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 400, r.text

    def test_rejected_platform_creates_no_cp_platforms_row(
        self, dev_client, workspace, channel, platform, db_conn
    ):
        """A rejected platform_id must not insert into cp_platforms."""
        before = db_conn.execute(
            "SELECT COUNT(*) FROM cp_platforms WHERE platform_key=?", ("attacker-platform",)
        ).fetchone()[0]
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={
                "platform_id": "attacker-platform",
                "external_account_id": "evil-id",
                "display_name": "Evil",
            },
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 400
        after = db_conn.execute(
            "SELECT COUNT(*) FROM cp_platforms WHERE platform_key=?", ("attacker-platform",)
        ).fetchone()[0]
        assert after == before, "cp_platforms must not be modified for a rejected platform_id"

    def test_youtube_accepted(self, dev_client, workspace, channel, platform):
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={
                "platform_id": "youtube",
                "external_account_id": "UCvalidyt",
                "display_name": "YouTube Account",
            },
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 200, r.text

    def test_instagram_accepted(self, dev_client, workspace, channel, platform):
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={
                "platform_id": "instagram",
                "external_account_id": "igvalidacct",
                "display_name": "Instagram Account",
            },
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 200, r.text

    def test_tiktok_accepted(self, dev_client, workspace, channel, platform):
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={
                "platform_id": "tiktok",
                "external_account_id": "ttvalidacct",
                "display_name": "TikTok Account",
            },
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 200, r.text

    def test_valid_platform_account_starts_disconnected(
        self, dev_client, workspace, channel, platform
    ):
        """Status must be 'disconnected' even for valid platform IDs."""
        r = dev_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={
                "platform_id": "tiktok",
                "external_account_id": "ttdiscon",
                "display_name": "TikTok Disconnected",
            },
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "disconnected"

    def test_rbac_still_enforced_with_platform_validation(
        self, prod_client, workspace, channel, platform, db_conn, svc
    ):
        """Platform validation runs after auth — RBAC is not bypassed for valid platform IDs."""
        user_id = svc.register_user(db_conn, "analyst-pv@example.com", "hunter2-long-enough-pass")
        svc.assign_workspace_role(db_conn, user_id, workspace.id, "analyst")
        token = _token(user_id, "analyst-pv@example.com", {workspace.id: "analyst"})
        r = prod_client.post(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}/accounts",
            json={
                "platform_id": "youtube",
                "external_account_id": "UCrbackeck",
                "display_name": "RBAC Check",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code in (403, 401)


# ── Phase 18C: public-publishing authorization endpoints ─────────────────────


class TestPublishingAuthorizationAPI:
    """The authorization endpoint is deliberately separate from the
    automation-policy endpoint, and granting requires explicit confirmation.
    These tests pin both properties down."""

    def _url(self, workspace_id: str, channel_id: str) -> str:
        return (
            f"/api/v1/workspaces/{workspace_id}/channels/{channel_id}"
            f"/publishing-authorization?workspace_id={workspace_id}"
        )

    def test_unconfigured_channel_reports_not_authorized(self, dev_client, workspace, channel):
        resp = dev_client.get(self._url(workspace.id, channel.id))
        assert resp.status_code == 200
        body = resp.json()
        assert body["authorization"] is None
        assert body["decision"]["allowed"] is False
        assert "channel_not_authorized" in body["decision"]["blocked_by"]

    def test_granting_without_confirm_is_rejected(self, dev_client, workspace, channel):
        resp = dev_client.put(self._url(workspace.id, channel.id), json={"authorized": True})
        assert resp.status_code == 422
        assert "confirm" in resp.json()["detail"]

        # Nothing was persisted by the rejected request.
        state = dev_client.get(self._url(workspace.id, channel.id)).json()
        assert state["authorization"] is None

    def test_granting_with_confirm_records_actor_and_time(self, dev_client, workspace, channel):
        resp = dev_client.put(
            self._url(workspace.id, channel.id),
            json={"authorized": True, "confirm": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["authorized"] is True
        assert body["authorized_by"]
        assert body["authorized_at"]

    def test_revoking_needs_no_confirmation(self, dev_client, workspace, channel):
        dev_client.put(
            self._url(workspace.id, channel.id),
            json={"authorized": True, "confirm": True},
        )
        resp = dev_client.put(self._url(workspace.id, channel.id), json={"authorized": False})
        assert resp.status_code == 200
        assert resp.json()["authorized"] is False
        assert resp.json()["revoked_at"]

    def test_updating_limits_does_not_authorize(self, dev_client, workspace, channel):
        resp = dev_client.put(
            self._url(workspace.id, channel.id), json={"max_publications_per_24h": 3}
        )
        assert resp.status_code == 200
        assert resp.json()["authorized"] is False
        assert resp.json()["max_publications_per_24h"] == 3

    def test_unknown_fields_are_rejected(self, dev_client, workspace, channel):
        resp = dev_client.put(
            self._url(workspace.id, channel.id),
            json={"authorized": True, "confirm": True, "decision_automation_enabled": True},
        )
        assert resp.status_code == 422

    def test_automation_policy_endpoint_cannot_authorize_publishing(
        self, dev_client, workspace, channel
    ):
        """The two controls must stay structurally separate: the automation
        policy endpoint has no vocabulary for publishing authorization."""
        resp = dev_client.put(
            f"/api/v1/workspaces/{workspace.id}/channels/{channel.id}"
            f"/automation-policy?workspace_id={workspace.id}",
            json={"public_publishing_authorized": True},
        )
        assert resp.status_code == 422

        state = dev_client.get(self._url(workspace.id, channel.id)).json()
        assert state["decision"]["allowed"] is False


# ── Automation policy endpoint — persistence round trip ───────────────────────


class TestAutomationPolicyAPI:
    """PUT .../automation-policy followed by a GET on a brand-new request (a
    fresh DB connection each time, exactly like get_db() gives every real
    HTTP request) must return exactly what was saved — this is the contract
    the Channels page's edit form relies on. The live bug traced to the
    frontend never resyncing its form state with this data (see
    Channels.test.tsx); these tests pin down that the backend side of the
    contract holds regardless."""

    def _url(self, workspace_id: str, channel_id: str) -> str:
        return f"/api/v1/workspaces/{workspace_id}/channels/{channel_id}/automation-policy"

    def test_update_then_get_returns_the_saved_policy(self, dev_client, workspace, channel):
        resp = dev_client.put(
            self._url(workspace.id, channel.id),
            json={
                "decision_automation_enabled": True,
                "production_automation_enabled": True,
                "cadence_type": "every_12h",
                "timezone": "America/New_York",
                "preferred_local_hour": 11,
                "queue_target": 1,
            },
        )
        assert resp.status_code == 200, resp.text

        # A brand-new request — get_db() opens an entirely fresh connection,
        # the same as a browser refresh hitting the API again.
        get_resp = dev_client.get(self._url(workspace.id, channel.id))
        policy = get_resp.json()["policy"]
        assert policy["decision_automation_enabled"] is True
        assert policy["production_automation_enabled"] is True
        assert policy["cadence_type"] == "every_12h"
        assert policy["timezone"] == "America/New_York"
        assert policy["preferred_local_hour"] == 11
        assert policy["queue_target"] == 1

    def test_partial_update_does_not_disturb_untouched_fields(self, dev_client, workspace, channel):
        dev_client.put(
            self._url(workspace.id, channel.id),
            json={
                "decision_automation_enabled": True,
                "cadence_type": "every_12h",
                "timezone": "America/New_York",
                "preferred_local_hour": 11,
                "queue_target": 1,
            },
        )
        resp = dev_client.put(
            self._url(workspace.id, channel.id), json={"preferred_local_hour": 14}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["preferred_local_hour"] == 14
        # Everything else survives a partial update untouched.
        assert body["decision_automation_enabled"] is True
        assert body["cadence_type"] == "every_12h"
        assert body["timezone"] == "America/New_York"
        assert body["queue_target"] == 1

    def test_enabling_decision_automation_without_timezone_is_rejected(
        self, dev_client, workspace, channel
    ):
        resp = dev_client.put(
            self._url(workspace.id, channel.id),
            json={"decision_automation_enabled": True},
        )
        assert resp.status_code == 422
        assert "timezone" in resp.json()["detail"]

    def test_unknown_field_is_rejected(self, dev_client, workspace, channel):
        resp = dev_client.put(self._url(workspace.id, channel.id), json={"not_a_real_field": True})
        assert resp.status_code == 422

    def test_two_channels_do_not_share_a_policy(self, dev_client, workspace, channel, db_conn):
        from app.control_plane import repository as cp_repo2
        from app.control_plane.models import ChannelDraft

        other = cp_repo2.create_channel(
            db_conn,
            ChannelDraft(
                id=_uid(),
                workspace_id=workspace.id,
                name="Other Channel",
                slug="other-channel",
                actor="cli",
            ),
        )
        db_conn.commit()

        dev_client.put(
            self._url(workspace.id, channel.id),
            json={
                "decision_automation_enabled": True,
                "cadence_type": "daily",
                "timezone": "America/New_York",
                "preferred_local_hour": 9,
                "queue_target": 1,
            },
        )
        other_policy = dev_client.get(self._url(workspace.id, other.id)).json()["policy"]
        assert other_policy is None


# ── Missed-slot reschedule endpoint (sections 12-14) ──────────────────────────


class TestRescheduleSlotAPI:
    def _seed_missed_ready_slot(self, db_conn, channel_id: str, workspace_id: str) -> int:
        from datetime import UTC, datetime, timedelta

        from app.intelligence.autonomy.repository import fill_slot, reserve_slot

        past = datetime.now(UTC) - timedelta(days=2)
        slot = reserve_slot(
            db_conn,
            channel_id=channel_id,
            workspace_id=workspace_id,
            slot_key="api-missed-slot",
            scheduled_for_local=past.isoformat(),
            timezone="America/New_York",
            scheduled_for_utc=past,
        )
        fill_slot(db_conn, slot.id, brief_id=None, selection_decision_id=None, opportunity_id=None)
        db_conn.execute(
            "UPDATE publishing_slots SET production_status='ready', "
            "publish_status='skipped_missed' WHERE id=?",
            (slot.id,),
        )
        db_conn.commit()
        return slot.id

    def _url(self, workspace_id: str, channel_id: str, slot_id: int) -> str:
        return (
            f"/api/v1/workspaces/{workspace_id}/channels/{channel_id}"
            f"/publishing-slots/{slot_id}/reschedule"
        )

    def test_reschedules_a_missed_ready_slot_to_a_future_time(
        self, dev_client, workspace, channel, db_conn
    ):
        slot_id = self._seed_missed_ready_slot(db_conn, channel.id, workspace.id)
        resp = dev_client.post(
            self._url(workspace.id, channel.id, slot_id),
            json={"scheduled_for_local": "2099-01-01T12:00:00", "timezone": "America/New_York"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] != slot_id
        assert body["rescheduled_from_slot_id"] == slot_id
        assert body["publish_status"] is None
        assert body["scheduled_for_local"].startswith("2099-01-01T12:00:00")

    def test_uses_the_channel_policy_timezone_when_none_is_given(
        self, dev_client, workspace, channel, db_conn
    ):
        from app.intelligence.autonomy.repository import upsert_autonomy_policy

        upsert_autonomy_policy(
            db_conn,
            channel_id=channel.id,
            workspace_id=workspace.id,
            actor="test",
            timezone="America/Los_Angeles",
        )
        slot_id = self._seed_missed_ready_slot(db_conn, channel.id, workspace.id)
        resp = dev_client.post(
            self._url(workspace.id, channel.id, slot_id),
            json={"scheduled_for_local": "2099-01-01T12:00:00"},
        )
        assert resp.status_code == 200, resp.text
        assert (
            "-08:00" in resp.json()["scheduled_for_local"]
            or "-07:00" in resp.json()["scheduled_for_local"]
        )

    def test_missing_timezone_and_no_policy_is_rejected(
        self, dev_client, workspace, channel, db_conn
    ):
        slot_id = self._seed_missed_ready_slot(db_conn, channel.id, workspace.id)
        resp = dev_client.post(
            self._url(workspace.id, channel.id, slot_id),
            json={"scheduled_for_local": "2099-01-01T12:00:00"},
        )
        assert resp.status_code == 422

    def test_a_slot_that_never_missed_is_rejected(self, dev_client, workspace, channel, db_conn):
        from datetime import UTC, datetime, timedelta

        from app.intelligence.autonomy.repository import reserve_slot

        future = datetime.now(UTC) + timedelta(days=1)
        slot = reserve_slot(
            db_conn,
            channel_id=channel.id,
            workspace_id=workspace.id,
            slot_key="api-not-missed",
            scheduled_for_local=future.isoformat(),
            timezone="UTC",
            scheduled_for_utc=future,
        )
        resp = dev_client.post(
            self._url(workspace.id, channel.id, slot.id),
            json={"scheduled_for_local": "2099-01-01T12:00:00", "timezone": "UTC"},
        )
        assert resp.status_code == 409

    def test_a_past_time_is_rejected(self, dev_client, workspace, channel, db_conn):
        slot_id = self._seed_missed_ready_slot(db_conn, channel.id, workspace.id)
        resp = dev_client.post(
            self._url(workspace.id, channel.id, slot_id),
            json={"scheduled_for_local": "2020-01-01T12:00:00", "timezone": "UTC"},
        )
        assert resp.status_code == 422
        assert "future" in resp.json()["detail"]

    def test_calling_it_twice_is_idempotent(self, dev_client, workspace, channel, db_conn):
        slot_id = self._seed_missed_ready_slot(db_conn, channel.id, workspace.id)
        body = {"scheduled_for_local": "2099-01-01T12:00:00", "timezone": "UTC"}
        first = dev_client.post(self._url(workspace.id, channel.id, slot_id), json=body)
        second = dev_client.post(self._url(workspace.id, channel.id, slot_id), json=body)
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]

    def test_unauthenticated_returns_401(self, prod_client, workspace, channel, db_conn):
        resp = prod_client.post(
            self._url(workspace.id, channel.id, 999999),
            json={"scheduled_for_local": "2099-01-01T12:00:00", "timezone": "UTC"},
        )
        assert resp.status_code == 401
