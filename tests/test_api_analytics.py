"""Backend API tests for analytics routes.

Covers:
- GET /workspaces/{ws}/analytics/aggregates
- GET /workspaces/{ws}/analytics/snapshots
- Workspace isolation (only returns data for topic_ids linked to the workspace)
- Authentication enforcement
- RBAC: analytics:view requires ANALYST role; insufficient role → 403
- Empty results when no pipeline executions exist
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

_SECRET = "test-secret-analytics-32bytes!!!"
NOW = "2026-01-01T00:00:00"


def _uid() -> str:
    return str(uuid.uuid4())


def _jwt(workspace_id: str, role: str) -> str:
    return create_access_token(
        1, "test@test.com", {workspace_id: role}, secret_key=_SECRET, expire_seconds=3600
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_config():
    reset_config()
    yield
    reset_config()


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "analytics_test.db"


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
def workspace(db_conn):
    ws = cp_repo.create_workspace(
        db_conn,
        WorkspaceDraft(id=_uid(), name="Test Workspace", slug="test-ws", actor="cli"),
    )
    db_conn.commit()
    return ws


@pytest.fixture()
def workspace_with_topic(db_conn, workspace):
    """Create a topic and pipeline execution linking the workspace to that topic."""
    cur = db_conn.execute(
        "INSERT INTO topics (title, angle, status, created_at, updated_at) VALUES (?,?,?,?,?)",
        ("Test Topic", "", "active", NOW, NOW),
    )
    topic_id = cur.lastrowid
    exec_id = _uid()
    db_conn.execute(
        """INSERT INTO app_pipeline_executions
               (id, workspace_id, channel_id, platform_account_id, topic_id,
                correlation_id, idempotency_key, status, end_stage, actor,
                created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            exec_id,
            workspace.id,
            None,
            None,
            topic_id,
            f"corr-{exec_id[:8]}",
            f"idem-{exec_id[:8]}",
            "completed",
            "learning",
            "test",
            NOW,
            NOW,
        ),
    )
    db_conn.commit()
    return workspace, topic_id


@pytest.fixture()
def seeded_aggregates(db_conn, workspace_with_topic):
    ws, topic_id = workspace_with_topic
    rows = [
        ("lifetime", "lifetime", "views", 42000.0, 5, "sum", None),
        ("lifetime", "lifetime", "ctr", 0.047, 5, "latest_observation", None),
        ("monthly", "2026-01", "views", 8400.0, 2, "sum", None),
    ]
    for i, (ptype, pkey, mname, mval, sc, calc, curr) in enumerate(rows):
        db_conn.execute(
            """INSERT INTO analytics_aggregates
                   (publication_id, topic_id, provider, period_type, period_key,
                    metric_name, metric_value, snapshot_count, calculation_method,
                    currency_code, source_snapshot_ids_json, input_hash, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                1,
                topic_id,
                "test_provider",
                ptype,
                pkey,
                mname,
                mval,
                sc,
                calc,
                curr,
                "[]",
                f"h-{i}",
                NOW,
            ),
        )
    db_conn.commit()
    return ws, topic_id


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def test_aggregates_requires_auth(prod_client, workspace):
    r = prod_client.get(f"/api/v1/workspaces/{workspace.id}/analytics/aggregates")
    assert r.status_code == 401


def test_snapshots_requires_auth(prod_client, workspace):
    r = prod_client.get(f"/api/v1/workspaces/{workspace.id}/analytics/snapshots")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Empty state (no pipeline executions)
# ---------------------------------------------------------------------------


def test_aggregates_empty_when_no_executions(dev_client, workspace):
    r = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/analytics/aggregates",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert r.status_code == 200
    assert r.json() == []


def test_snapshots_empty_when_no_executions(dev_client, workspace):
    r = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/analytics/snapshots",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# Populated state
# ---------------------------------------------------------------------------


def test_aggregates_returns_workspace_data(dev_client, seeded_aggregates):
    ws, _topic_id = seeded_aggregates
    r = dev_client.get(
        f"/api/v1/workspaces/{ws.id}/analytics/aggregates",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 3
    metric_names = {row["metric_name"] for row in data}
    assert "views" in metric_names
    assert "ctr" in metric_names


def test_aggregates_filter_by_period_type(dev_client, seeded_aggregates):
    ws, _ = seeded_aggregates
    r = dev_client.get(
        f"/api/v1/workspaces/{ws.id}/analytics/aggregates",
        params={"period_type": "lifetime"},
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert r.status_code == 200
    data = r.json()
    assert all(row["period_type"] == "lifetime" for row in data)
    assert len(data) == 2


def test_aggregates_filter_by_metric_name(dev_client, seeded_aggregates):
    ws, _ = seeded_aggregates
    r = dev_client.get(
        f"/api/v1/workspaces/{ws.id}/analytics/aggregates",
        params={"metric_name": "views"},
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert r.status_code == 200
    data = r.json()
    assert all(row["metric_name"] == "views" for row in data)
    assert len(data) == 2  # lifetime + monthly


def test_aggregates_workspace_isolation(dev_client, db_conn, seeded_aggregates):
    """A second workspace cannot see the first workspace's aggregates."""
    ws2 = cp_repo.create_workspace(
        db_conn,
        WorkspaceDraft(id=_uid(), name="Other Workspace", slug="other-ws", actor="cli"),
    )
    db_conn.commit()
    r = dev_client.get(
        f"/api/v1/workspaces/{ws2.id}/analytics/aggregates",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert r.status_code == 200
    assert r.json() == []


def test_aggregate_calculation_method_preserved(dev_client, seeded_aggregates):
    ws, _ = seeded_aggregates
    r = dev_client.get(
        f"/api/v1/workspaces/{ws.id}/analytics/aggregates",
        params={"metric_name": "ctr"},
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["calculation_method"] == "latest_observation"


# ---------------------------------------------------------------------------
# RBAC — workspace role enforcement (production JWT mode)
# ---------------------------------------------------------------------------


def test_aggregates_analyst_role_succeeds(prod_client, seeded_aggregates):
    """ANALYST role satisfies analytics:view permission."""
    ws, _ = seeded_aggregates
    token = _jwt(ws.id, "analyst")
    r = prod_client.get(
        f"/api/v1/workspaces/{ws.id}/analytics/aggregates",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert len(r.json()) == 3


def test_aggregates_no_workspace_role_is_403(prod_client, seeded_aggregates):
    """A valid JWT with no role in this workspace is rejected with 403."""
    ws, _ = seeded_aggregates
    other_ws = _uid()
    token = _jwt(other_ws, "analyst")  # role is in a different workspace
    r = prod_client.get(
        f"/api/v1/workspaces/{ws.id}/analytics/aggregates",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


def test_snapshots_analyst_role_succeeds(prod_client, workspace_with_topic):
    """ANALYST role satisfies analytics:view for snapshots endpoint."""
    ws, _ = workspace_with_topic
    token = _jwt(ws.id, "analyst")
    r = prod_client.get(
        f"/api/v1/workspaces/{ws.id}/analytics/snapshots",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200


def test_snapshots_no_workspace_role_is_403(prod_client, workspace_with_topic):
    """Snapshots endpoint rejects callers with no role in the target workspace."""
    ws, _ = workspace_with_topic
    token = _jwt(_uid(), "analyst")
    r = prod_client.get(
        f"/api/v1/workspaces/{ws.id}/analytics/snapshots",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403
