"""Backend API tests for topic routes.

Covers:
- GET /workspaces/{ws}/topics  — listing, empty, populated
- POST /workspaces/{ws}/topics — creation, validation, duplicate handling
- Workspace isolation: workspace A cannot see workspace B topics
- RBAC: pipeline:view required for listing; pipeline:create for creation
- Dev-auth bypass
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth.tokens import create_access_token
from app.control_plane import repository as cp_repo
from app.control_plane.models import WorkspaceDraft
from app.core.config import reset_config
from app.core.database import open_db

_SECRET = "test-secret-topics-32byteslong!!!"


def _uid() -> str:
    return str(uuid.uuid4())


def _jwt(workspace_id: str, role: str) -> str:
    return create_access_token(
        1, "test@test.com", {workspace_id: role}, secret_key=_SECRET, expire_seconds=3600
    )


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "topics_test.db"


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


def _seed_workspace(db_path: Path) -> str:
    conn = open_db(db_path)
    ws_id = _uid()
    draft = WorkspaceDraft(id=ws_id, name="Test WS", slug=f"test-{ws_id[:8]}", actor="test")
    cp_repo.create_workspace(conn, draft)
    conn.commit()
    conn.close()
    return ws_id


# ---------------------------------------------------------------------------
# Dev-mode listing
# ---------------------------------------------------------------------------


class TestListTopicsDev:
    def test_empty_returns_list(self, dev_client, db_path):
        ws_id = _seed_workspace(db_path)
        r = dev_client.get(
            f"/api/v1/workspaces/{ws_id}/topics",
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 200
        assert r.json() == []

    def test_lists_workspace_topics(self, dev_client, db_path):
        ws_id = _seed_workspace(db_path)
        # Create a topic via POST
        dev_client.post(
            f"/api/v1/workspaces/{ws_id}/topics",
            json={"title": "My Topic", "angle": "focus area"},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        r = dev_client.get(
            f"/api/v1/workspaces/{ws_id}/topics",
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["title"] == "My Topic"
        assert data[0]["angle"] == "focus area"
        assert data[0]["workspace_id"] == ws_id
        assert data[0]["status"] == "active"

    def test_newest_first_ordering(self, dev_client, db_path):
        ws_id = _seed_workspace(db_path)
        for title in ["First", "Second", "Third"]:
            dev_client.post(
                f"/api/v1/workspaces/{ws_id}/topics",
                json={"title": title},
                headers={"X-Dev-Actor": "dev:studio-user"},
            )
        r = dev_client.get(
            f"/api/v1/workspaces/{ws_id}/topics",
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        titles = [t["title"] for t in r.json()]
        assert titles == ["Third", "Second", "First"]


# ---------------------------------------------------------------------------
# Topic creation
# ---------------------------------------------------------------------------


class TestCreateTopicDev:
    def test_creates_topic(self, dev_client, db_path):
        ws_id = _seed_workspace(db_path)
        r = dev_client.post(
            f"/api/v1/workspaces/{ws_id}/topics",
            json={"title": "AI in Healthcare", "angle": "practical applications"},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 201
        data = r.json()
        assert data["title"] == "AI in Healthcare"
        assert data["angle"] == "practical applications"
        assert data["workspace_id"] == ws_id
        assert isinstance(data["id"], int)

    def test_empty_title_rejected(self, dev_client, db_path):
        ws_id = _seed_workspace(db_path)
        r = dev_client.post(
            f"/api/v1/workspaces/{ws_id}/topics",
            json={"title": "   "},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 422

    def test_missing_title_rejected(self, dev_client, db_path):
        ws_id = _seed_workspace(db_path)
        r = dev_client.post(
            f"/api/v1/workspaces/{ws_id}/topics",
            json={"angle": "no title provided"},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 422

    def test_title_too_long_rejected(self, dev_client, db_path):
        ws_id = _seed_workspace(db_path)
        r = dev_client.post(
            f"/api/v1/workspaces/{ws_id}/topics",
            json={"title": "x" * 501},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 422

    def test_angle_optional(self, dev_client, db_path):
        ws_id = _seed_workspace(db_path)
        r = dev_client.post(
            f"/api/v1/workspaces/{ws_id}/topics",
            json={"title": "Topic Without Angle"},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 201
        assert r.json()["angle"] == ""


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------


class TestWorkspaceIsolation:
    def test_workspace_a_cannot_see_workspace_b_topics(self, dev_client, db_path):
        ws_a = _seed_workspace(db_path)
        ws_b = _seed_workspace(db_path)

        # Create topic in workspace B
        dev_client.post(
            f"/api/v1/workspaces/{ws_b}/topics",
            json={"title": "Workspace B Topic"},
            headers={"X-Dev-Actor": "dev:studio-user"},
        )

        # Workspace A should see nothing
        r = dev_client.get(
            f"/api/v1/workspaces/{ws_a}/topics",
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert r.status_code == 200
        assert r.json() == []

    def test_workspace_b_topics_not_in_workspace_a_list(self, dev_client, db_path):
        ws_a = _seed_workspace(db_path)
        ws_b = _seed_workspace(db_path)

        for ws_id, title in [(ws_a, "A Topic"), (ws_b, "B Topic")]:
            dev_client.post(
                f"/api/v1/workspaces/{ws_id}/topics",
                json={"title": title},
                headers={"X-Dev-Actor": "dev:studio-user"},
            )

        r_a = dev_client.get(
            f"/api/v1/workspaces/{ws_a}/topics",
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        titles_a = [t["title"] for t in r_a.json()]
        assert "B Topic" not in titles_a
        assert "A Topic" in titles_a


# ---------------------------------------------------------------------------
# RBAC (production JWT mode)
# ---------------------------------------------------------------------------


class TestTopicsRBAC:
    def test_unauthenticated_list_rejected(self, prod_client, db_path):
        ws_id = _seed_workspace(db_path)
        r = prod_client.get(f"/api/v1/workspaces/{ws_id}/topics")
        assert r.status_code == 401

    def test_analyst_can_list_topics(self, prod_client, db_path):
        ws_id = _seed_workspace(db_path)
        token = _jwt(ws_id, "analyst")
        r = prod_client.get(
            f"/api/v1/workspaces/{ws_id}/topics",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

    def test_viewer_cannot_create_topic(self, prod_client, db_path):
        ws_id = _seed_workspace(db_path)
        token = _jwt(ws_id, "reviewer")
        r = prod_client.post(
            f"/api/v1/workspaces/{ws_id}/topics",
            json={"title": "Forbidden Topic"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    def test_operator_can_create_topic(self, prod_client, db_path):
        ws_id = _seed_workspace(db_path)
        token = _jwt(ws_id, "operator")
        r = prod_client.post(
            f"/api/v1/workspaces/{ws_id}/topics",
            json={"title": "Allowed Topic"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201

    def test_wrong_workspace_token_rejected(self, prod_client, db_path):
        ws_a = _seed_workspace(db_path)
        ws_b = _seed_workspace(db_path)
        token = _jwt(ws_a, "operator")  # token for workspace A
        r = prod_client.post(
            f"/api/v1/workspaces/{ws_b}/topics",  # attempt to write to workspace B
            json={"title": "Cross-workspace Attempt"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403
