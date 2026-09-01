"""Phase 16.1 Section 12 — Channel isolation tests.

Validates that data written under one channel in a workspace is not visible
when querying through another channel's publication in the same workspace.
This guards against the scenario where two channels co-exist in local-dev
and one channel's analytics leak into another channel's API responses.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.core.config import reset_config
from app.core.database import open_db


def _uid() -> str:
    return str(uuid.uuid4())


_NOW = "2025-01-01T00:00:00"


@pytest.fixture(autouse=True)
def _reset_cfg():
    reset_config()
    yield
    reset_config()


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "iso_test.db"


@pytest.fixture()
def dev_client(db_path: Path, monkeypatch):
    monkeypatch.setenv("ACE_ENV", "development")
    monkeypatch.setenv("ACE_DEV_AUTH", "enabled")
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    reset_config()
    open_db(db_path).close()
    from fastapi.testclient import TestClient

    from app.api.main import app

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


DEV_HEADERS = {"X-Dev-Actor": "dev:studio-user"}


def _seed_workspace(db_path: Path, ws_id: str = "ws-iso") -> str:
    conn = open_db(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO cp_workspaces "
        "(id, name, slug, status, actor, created_at, updated_at) "
        "VALUES (?, 'Iso WS', ?, 'active', 'test', ?, ?)",
        (ws_id, f"slug-{ws_id[:6]}", _NOW, _NOW),
    )
    conn.commit()
    conn.close()
    return ws_id


def _seed_channel_and_publication(
    db_path: Path, ws_id: str, channel_suffix: str, pub_id: int, metric_value: int
) -> int:
    """Seed a channel + publication + analytics aggregate. Returns pub_id."""
    conn = open_db(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")

    channel_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO cp_channels "
        "(id, workspace_id, name, slug, status, actor, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'active', 'test', ?, ?)",
        (channel_id, ws_id, f"Chan-{channel_suffix}", f"chan-{channel_suffix}", _NOW, _NOW),
    )

    # topic
    conn.execute(
        "INSERT INTO topics (title, angle, status, created_at, updated_at) "
        "VALUES (?, '', 'active', ?, ?)",
        (f"Topic-{channel_suffix}", _NOW, _NOW),
    )
    topic_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # publication (minimal, FK off)
    conn.execute(
        "INSERT INTO publications "
        "(id, publishing_plan_id, publishing_job_id, provider, provider_version, "
        "provider_video_id, provider_url, visibility, status, "
        "publishing_engine_version, input_hash, output_sha256, published_at, "
        "workspace_id, channel_id, created_at, updated_at) "
        "VALUES (?, 1, 1, 'youtube', '1.0', ?, ?, 'private', 'published', "
        "'1.0', ?, ?, ?, ?, ?, ?, ?)",
        (
            pub_id,
            f"vid_{channel_suffix}",
            f"https://www.youtube.com/watch?v=vid_{channel_suffix}",
            str(uuid.uuid4()),
            f"sha256-{channel_suffix}",
            _NOW,
            ws_id,
            channel_id,
            _NOW,
            _NOW,
        ),
    )

    # analytics aggregate
    conn.execute(
        "INSERT INTO analytics_aggregates "
        "(publication_id, topic_id, provider, period_type, period_key, "
        "metric_name, metric_value, snapshot_count, calculation_method, "
        "source_snapshot_ids_json, input_hash, created_at) "
        "VALUES (?, ?, 'youtube', 'lifetime', 'lifetime', 'views', ?, 1, 'sum', '[]', ?, ?)",
        (pub_id, topic_id, metric_value, str(uuid.uuid4()), _NOW),
    )

    conn.commit()
    conn.close()
    return pub_id


# ---------------------------------------------------------------------------
# Test 1 — Publication A analytics are scoped to publication A only
# ---------------------------------------------------------------------------


def test_publication_scoped_analytics_no_leakage(db_path, dev_client):
    """
    Two publications (A=pub_id=10, B=pub_id=20) in the same workspace.
    Querying aggregates for pub A must return only pub A's metrics (views=111).
    Querying aggregates for pub B must return only pub B's metrics (views=999).
    """
    ws = _seed_workspace(db_path)
    _seed_channel_and_publication(db_path, ws, "A", pub_id=10, metric_value=111)
    _seed_channel_and_publication(db_path, ws, "B", pub_id=20, metric_value=999)

    resp_a = dev_client.get(
        f"/api/v1/workspaces/{ws}/analytics/aggregates",
        params={"publication_id": 10},
        headers=DEV_HEADERS,
    )
    assert resp_a.status_code == 200, resp_a.text
    data_a = resp_a.json()
    assert len(data_a) == 1
    assert data_a[0]["publication_id"] == 10
    assert data_a[0]["metric_value"] == 111

    resp_b = dev_client.get(
        f"/api/v1/workspaces/{ws}/analytics/aggregates",
        params={"publication_id": 20},
        headers=DEV_HEADERS,
    )
    assert resp_b.status_code == 200, resp_b.text
    data_b = resp_b.json()
    assert len(data_b) == 1
    assert data_b[0]["publication_id"] == 20
    assert data_b[0]["metric_value"] == 999


# ---------------------------------------------------------------------------
# Test 2 — Publication from another workspace is rejected (Section 10 ownership)
# ---------------------------------------------------------------------------


def test_publication_from_other_workspace_is_404(db_path, dev_client):
    """
    Publication in workspace ws-iso must not be accessible via workspace other-ws.
    The endpoint checks publications.workspace_id = ? and returns 404.
    """
    ws = _seed_workspace(db_path, "ws-iso")
    _seed_channel_and_publication(db_path, ws, "Leak", pub_id=30, metric_value=42)

    resp = dev_client.get(
        "/api/v1/workspaces/other-ws/analytics/aggregates",
        params={"publication_id": 30},
        headers=DEV_HEADERS,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test 3 — Environment endpoint counts only the queried workspace's publications
# ---------------------------------------------------------------------------


def test_environment_publication_count_is_workspace_scoped(db_path, dev_client):
    """
    Publications in ws-iso must not inflate publication count reported
    for a different workspace (ws-other). Each workspace's environment
    readiness is independently scoped.
    """
    ws_iso = _seed_workspace(db_path, "ws-iso2")
    _seed_channel_and_publication(db_path, ws_iso, "C", pub_id=40, metric_value=77)

    # Seed second workspace with 0 publications
    conn = open_db(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO cp_workspaces "
        "(id, name, slug, status, actor, created_at, updated_at) "
        "VALUES ('ws-other2', 'Other', 'other2', 'active', 'test', ?, ?)",
        (_NOW, _NOW),
    )
    conn.commit()
    conn.close()

    resp = dev_client.get(
        "/api/v1/workspaces/ws-other2/environment",
        headers=DEV_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    # ws-other2 has no publications — must report 0, not 1
    assert data["checks"]["publications"]["ok"] is False
    assert "0 publication" in data["checks"]["publications"]["detail"]
