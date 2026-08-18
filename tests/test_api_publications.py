"""Backend API tests for publications routes.

Covers:
- GET /workspaces/{ws}/publications          — list, workspace isolation
- GET /workspaces/{ws}/publications/{id}     — detail with tags, render join
- GET /workspaces/{ws}/publications/{id}/stream  — video file response, auth, path traversal
- GET /workspaces/{ws}/publications/{id}/analytics — snapshot + metrics + retention count
- Authentication enforcement (401 without credentials)
- Workspace isolation (404 for cross-workspace access)
- Path traversal protection (403 for out-of-artifacts paths)
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

_SECRET = "test-secret-publications-32bytes!"
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
    return tmp_path / "publications_test.db"


@pytest.fixture()
def artifacts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "artifacts"
    d.mkdir(parents=True)
    return d


@pytest.fixture()
def dev_client(db_path: Path, artifacts_dir: Path, monkeypatch):
    monkeypatch.setenv("ACE_ENV", "development")
    monkeypatch.setenv("ACE_DEV_AUTH", "enabled")
    monkeypatch.setenv("ACE_SECRET_KEY", _SECRET)
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    monkeypatch.setenv("ACE_ARTIFACTS_PATH", str(artifacts_dir))
    reset_config()
    from app.api.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture()
def prod_client(db_path: Path, artifacts_dir: Path, monkeypatch):
    monkeypatch.setenv("ACE_ENV", "production")
    monkeypatch.setenv("ACE_SECRET_KEY", _SECRET)
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    monkeypatch.setenv("ACE_ARTIFACTS_PATH", str(artifacts_dir))
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
        WorkspaceDraft(id=_uid(), name="Test Workspace", slug=f"test-ws-{_uid()[:8]}", actor="cli"),
    )
    db_conn.commit()
    return ws


def _create_workspace(conn) -> str:
    ws = cp_repo.create_workspace(
        conn,
        WorkspaceDraft(id=_uid(), name="Test Workspace", slug=f"test-ws-{_uid()[:8]}", actor="cli"),
    )
    conn.commit()
    return ws.id


def _seed_workspace_with_publication(
    conn,
    workspace_id: str,
    *,
    render_manifest_id: int = 1,
    publication_id: int = 1,
    output_path: str | None = None,
    tags_json: str = '["energy", "tech"]',
    with_analytics: bool = False,
) -> int:
    """Seed a minimal publication row with all related records (FK constraints off).

    Publications are owned by workspace_id via the explicit workspace_id column (v23).
    Returns the topic_id created.
    """
    conn.execute("PRAGMA foreign_keys = OFF")

    cur = conn.execute(
        "INSERT INTO topics (title, angle, status, created_at, updated_at) "
        "VALUES ('Test Topic', '', 'active', ?, ?)",
        (NOW, NOW),
    )
    topic_id: int = cur.lastrowid
    conn.execute(
        "INSERT INTO render_manifests "
        "(id, scene_manifest_id, narration_run_id, caption_run_id, topic_id, plan_id, "
        " script_id, input_hash, render_schema_version, compositor_version, "
        " total_scene_count, total_duration_ms, width, height, fps, caption_burn_in, "
        " status, created_at, updated_at) "
        "VALUES (?, 1, 1, 1, ?, 1, 1, 'hash-rm', '1', '1',"
        " 5, 58607, 1080, 1920, 30, 0, 'approved', ?, ?)",
        (render_manifest_id, topic_id, NOW, NOW),
    )
    conn.execute(
        "INSERT INTO publishing_plans "
        "(id, render_manifest_id, topic_id, production_plan_id, script_id, scene_manifest_id, "
        " narration_run_id, caption_run_id, input_hash, publishing_engine_version, "
        " metadata_version, provider, provider_version, title, description, tags_json, "
        " visibility, created_at, updated_at) "
        "VALUES (?, ?, ?, 1, 1, 1, 1, 1, 'hash-pp', '1.0', '1', 'youtube', '1.0', "
        " 'Why Renewable Energy Is Getting Cheap', 'A great video.', ?, 'private', ?, ?)",
        (publication_id, render_manifest_id, topic_id, tags_json, NOW, NOW),
    )
    conn.execute(
        "INSERT INTO publishing_jobs "
        "(id, publishing_plan_id, attempt_number, provider, provider_version, status, "
        " retry_count, created_at, updated_at) "
        "VALUES (?, ?, 1, 'youtube', '1.0', 'completed', 0, ?, ?)",
        (publication_id, publication_id, NOW, NOW),
    )
    conn.execute(
        "INSERT INTO publications "
        "(id, publishing_plan_id, publishing_job_id, provider, provider_version, "
        " provider_video_id, provider_url, visibility, status, "
        " publishing_engine_version, input_hash, output_sha256, published_at, "
        " workspace_id, created_at, updated_at) "
        "VALUES (?, ?, ?, 'youtube', '1.0', 'kQH88nXdiRY', "
        " 'https://www.youtube.com/watch?v=kQH88nXdiRY', 'private', 'published', "
        " '1.0', 'hash-pub', 'sha256-pub', ?, ?, ?, ?)",
        (publication_id, publication_id, publication_id, NOW, workspace_id, NOW, NOW),
    )
    if output_path:
        conn.execute(
            "INSERT INTO render_jobs "
            "(id, render_manifest_id, backend, backend_version, output_path, status, "
            " width, height, fps, video_codec, audio_codec, crf, audio_bitrate, "
            " caption_burn_in, ffmpeg_cmd_json, validated, created_at, updated_at) "
            "VALUES (?, ?, 'ffmpeg', '1.0', ?, 'completed', 1080, 1920, 30, "
            " 'h264', 'aac', 23, '128k', 0, '[]', 1, ?, ?)",
            (publication_id * 10, render_manifest_id, output_path, NOW, NOW),
        )
    if with_analytics:
        conn.execute(
            "INSERT INTO analytics_snapshots "
            "(id, publication_id, publishing_plan_id, publishing_job_id, render_manifest_id, "
            " scene_manifest_id, production_plan_id, script_id, topic_id, narration_run_id, "
            " caption_run_id, provider, provider_version, adapter_version, engine_version, "
            " analytics_schema_version, db_schema_version, input_hash, "
            " ingested_at, period_start, period_end, created_at) "
            "VALUES (1, ?, ?, ?, ?, 1, 1, 1, 1, 1, 1, "
            " 'youtube', '1.0', '1.0', '1.0', '1', 23, 'hash-snap', "
            " ?, '2026-01-01', '2026-01-07', ?)",
            (publication_id, publication_id, publication_id, render_manifest_id, NOW, NOW),
        )
        conn.execute(
            "INSERT INTO analytics_metrics "
            "(snapshot_id, publication_id, topic_id, provider,"
            " metric_name, metric_value, input_hash, created_at) "
            "VALUES (1, ?, 1, 'youtube', 'views', 1234.0, 'hash-m', ?)",
            (publication_id, NOW),
        )
    conn.commit()
    return topic_id


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


def test_list_publications_empty_when_no_owned_publications(dev_client, workspace):
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    assert res.json() == []


def test_list_publications_returns_publications(dev_client, db_conn, workspace):
    _seed_workspace_with_publication(db_conn, workspace.id)
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["id"] == 1
    assert data[0]["title"] == "Why Renewable Energy Is Getting Cheap"
    assert data[0]["visibility"] == "private"
    assert data[0]["status"] == "published"
    assert data[0]["provider_video_id"] == "kQH88nXdiRY"


def test_list_publications_workspace_isolation(dev_client, db_conn, workspace):
    ws_b = _uid()
    _seed_workspace_with_publication(db_conn, workspace.id, render_manifest_id=2, publication_id=2)
    res = dev_client.get(
        f"/api/v1/workspaces/{ws_b}/publications",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    assert res.json() == []


def test_list_publications_requires_auth(prod_client, workspace):
    res = prod_client.get(f"/api/v1/workspaces/{workspace.id}/publications")
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# Detail endpoint
# ---------------------------------------------------------------------------


def test_get_publication_detail(dev_client, db_conn, workspace):
    _seed_workspace_with_publication(db_conn, workspace.id)
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == 1
    assert data["title"] == "Why Renewable Energy Is Getting Cheap"
    assert data["description"] == "A great video."
    assert data["tags"] == ["energy", "tech"]
    assert data["render_width"] == 1080
    assert data["render_height"] == 1920
    assert data["render_fps"] == 30
    assert data["render_status"] == "approved"


def test_get_publication_detail_tags_json_empty(dev_client, db_conn, workspace):
    _seed_workspace_with_publication(db_conn, workspace.id, tags_json="[]")
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    assert res.json()["tags"] == []


def test_get_publication_detail_404_wrong_workspace(dev_client, db_conn, workspace):
    _seed_workspace_with_publication(db_conn, workspace.id)
    ws_b = _uid()
    res = dev_client.get(
        f"/api/v1/workspaces/{ws_b}/publications/1",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 404


def test_get_publication_detail_404_unknown_id(dev_client, db_conn, workspace):
    _seed_workspace_with_publication(db_conn, workspace.id)
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/999",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 404


def test_get_publication_rbac(prod_client, db_conn, workspace):
    _seed_workspace_with_publication(db_conn, workspace.id)
    token = _jwt(workspace.id, "analyst")
    res = prod_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200


def test_get_publication_rbac_insufficient_role(prod_client, db_conn, workspace):
    _seed_workspace_with_publication(db_conn, workspace.id)
    other_ws = _uid()
    token = _jwt(other_ws, "analyst")
    res = prod_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# Stream endpoint
# ---------------------------------------------------------------------------


def test_stream_returns_video(dev_client, db_conn, workspace, artifacts_dir):
    mp4_file = artifacts_dir / "renders" / "1" / "render_1.mp4"
    mp4_file.parent.mkdir(parents=True)
    mp4_file.write_bytes(b"\x00\x01\x02fakemp4")
    _seed_workspace_with_publication(db_conn, workspace.id, output_path=str(mp4_file))
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1/stream",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == "video/mp4"
    assert res.content == b"\x00\x01\x02fakemp4"


def test_stream_path_traversal_rejected(dev_client, db_conn, workspace, tmp_path, artifacts_dir):
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"top secret")
    _seed_workspace_with_publication(db_conn, workspace.id, output_path=str(secret))
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1/stream",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 403


def test_stream_missing_file_404(dev_client, db_conn, workspace, artifacts_dir):
    ghost = artifacts_dir / "renders" / "1" / "ghost.mp4"
    _seed_workspace_with_publication(db_conn, workspace.id, output_path=str(ghost))
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1/stream",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 404


def test_stream_no_render_job_404(dev_client, db_conn, workspace):
    _seed_workspace_with_publication(db_conn, workspace.id, output_path=None)
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1/stream",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 404


def test_stream_requires_auth(prod_client, workspace):
    res = prod_client.get(f"/api/v1/workspaces/{workspace.id}/publications/1/stream")
    assert res.status_code == 401


def test_stream_cross_workspace_404(dev_client, db_conn, workspace, artifacts_dir):
    mp4_file = artifacts_dir / "renders" / "2" / "render_2.mp4"
    mp4_file.parent.mkdir(parents=True)
    mp4_file.write_bytes(b"fakemp4")
    _seed_workspace_with_publication(
        db_conn, workspace.id, render_manifest_id=2, publication_id=2, output_path=str(mp4_file)
    )
    ws_b = _uid()
    res = dev_client.get(
        f"/api/v1/workspaces/{ws_b}/publications/2/stream",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Analytics endpoint
# ---------------------------------------------------------------------------


def test_get_analytics_no_snapshot(dev_client, db_conn, workspace):
    _seed_workspace_with_publication(db_conn, workspace.id)
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1/analytics",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["snapshot_id"] is None
    assert data["metrics"] == {}
    assert data["retention_point_count"] == 0


def test_get_analytics_with_snapshot(dev_client, db_conn, workspace):
    _seed_workspace_with_publication(db_conn, workspace.id, with_analytics=True)
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1/analytics",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["snapshot_id"] == 1
    assert data["metrics"] == {"views": 1234.0}
    assert data["retention_point_count"] == 0
    assert data["period_start"] == "2026-01-01"
    assert data["period_end"] == "2026-01-07"


def test_get_analytics_cross_workspace_404(dev_client, db_conn, workspace):
    _seed_workspace_with_publication(db_conn, workspace.id)
    ws_b = _uid()
    res = dev_client.get(
        f"/api/v1/workspaces/{ws_b}/publications/1/analytics",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Schema v23 migration
# ---------------------------------------------------------------------------


def test_schema_v23_columns_present_on_fresh_db(db_conn):
    cols = {row[1] for row in db_conn.execute("PRAGMA table_info(publications)").fetchall()}
    assert "workspace_id" in cols
    assert "channel_id" in cols
    assert "platform_account_id" in cols


def test_schema_v23_index_present_on_fresh_db(db_conn):
    idx_names = {
        row[1]
        for row in db_conn.execute(
            "SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='publications'"
        ).fetchall()
    }
    assert "idx_publications_workspace_id" in idx_names


def test_schema_v23_upgrade_from_v22(tmp_path):
    """Simulate a v22 database and verify that opening it upgrades to v23."""
    import sqlite3 as _sqlite3

    from app.core.database import open_db

    db_file = tmp_path / "v22_upgrade_test.db"

    # Build a v22-era database manually (no ownership columns on publications).
    raw = _sqlite3.connect(str(db_file))
    raw.execute(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL)
        """
    )
    raw.execute("INSERT INTO schema_version (version) VALUES (22)")
    raw.execute(
        """
        CREATE TABLE publications (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            publishing_plan_id          INTEGER NOT NULL,
            publishing_job_id           INTEGER NOT NULL,
            provider                    TEXT NOT NULL,
            provider_version            TEXT NOT NULL,
            provider_video_id           TEXT,
            provider_url                TEXT,
            provider_status_json        TEXT NOT NULL DEFAULT '{}',
            status                      TEXT NOT NULL DEFAULT 'uploading',
            error_message               TEXT,
            visibility                  TEXT NOT NULL DEFAULT 'private',
            scheduled_at                TEXT,
            published_at                TEXT,
            deleted_at                  TEXT,
            publishing_engine_version   TEXT NOT NULL,
            input_hash                  TEXT NOT NULL,
            output_sha256               TEXT NOT NULL,
            created_at                  TEXT NOT NULL,
            updated_at                  TEXT NOT NULL
        )
        """
    )
    # Stub cp_workspaces/cp_channels/cp_platform_accounts so FK refs resolve.
    raw.execute("CREATE TABLE cp_workspaces (id TEXT PRIMARY KEY)")
    raw.execute("CREATE TABLE cp_channels (id TEXT PRIMARY KEY)")
    raw.execute("CREATE TABLE cp_platform_accounts (id TEXT PRIMARY KEY)")
    raw.commit()
    raw.close()

    conn = open_db(db_file)
    try:
        version = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()[0]
        assert version == 23
        cols = {row[1] for row in conn.execute("PRAGMA table_info(publications)").fetchall()}
        assert "workspace_id" in cols
        assert "channel_id" in cols
        assert "platform_account_id" in cols
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Ownership persistence and validation (orchestrator-level)
# ---------------------------------------------------------------------------


def _seed_cp_workspace_channel_account(conn, workspace_id: str) -> tuple[str, str]:
    """Insert a connected cp_channel and cp_platform_account; return (channel_id, account_id)."""
    conn.execute("PRAGMA foreign_keys = OFF")
    ch_id = _uid()
    acct_id = _uid()
    conn.execute(
        "INSERT OR IGNORE INTO cp_workspaces"
        " (id, name, slug, status, actor, created_at, updated_at) "
        "VALUES (?, 'WS', 'ws', 'active', 'test', ?, ?)",
        (workspace_id, NOW, NOW),
    )
    conn.execute(
        "INSERT INTO cp_channels"
        " (id, workspace_id, name, slug, status, actor, created_at, updated_at) "
        "VALUES (?, ?, 'Chan', 'chan', 'active', 'test', ?, ?)",
        (ch_id, workspace_id, NOW, NOW),
    )
    conn.execute(
        "INSERT INTO cp_platform_accounts "
        "(id, channel_id, platform_id, platform_key, external_account_id, display_name, "
        " status, actor, created_at, updated_at) "
        "VALUES (?, ?, 'youtube', 'youtube', 'UC-test', 'Test Acct', "
        " 'connected', 'test', ?, ?)",
        (acct_id, ch_id, NOW, NOW),
    )
    conn.commit()
    return ch_id, acct_id


def test_ownership_validation_rejects_channel_not_in_workspace(db_conn):
    from app.publishing.errors import PublicationOwnershipError
    from app.publishing.orchestrator import _validate_publication_ownership

    ws_a = _uid()
    ws_b = _uid()
    ch_id, acct_id = _seed_cp_workspace_channel_account(db_conn, ws_a)
    with pytest.raises(PublicationOwnershipError, match="does not belong to workspace"):
        _validate_publication_ownership(db_conn, ws_b, ch_id, acct_id)


def test_ownership_validation_rejects_account_not_in_channel(db_conn):
    from app.publishing.errors import PublicationOwnershipError
    from app.publishing.orchestrator import _validate_publication_ownership

    ws_id = _uid()
    ch_id, _ = _seed_cp_workspace_channel_account(db_conn, ws_id)
    wrong_acct = _uid()
    with pytest.raises(PublicationOwnershipError, match="does not belong to channel"):
        _validate_publication_ownership(db_conn, ws_id, ch_id, wrong_acct)


def test_ownership_validation_passes_for_consistent_ownership(db_conn):
    from app.publishing.orchestrator import _validate_publication_ownership

    ws_id = _uid()
    ch_id, acct_id = _seed_cp_workspace_channel_account(db_conn, ws_id)
    # Should not raise.
    _validate_publication_ownership(db_conn, ws_id, ch_id, acct_id)


def test_start_publishing_job_rejects_partial_ownership(db_conn):
    """Providing only some of the three ownership IDs must be rejected."""
    from app.publishing.errors import PublicationOwnershipError
    from app.publishing.orchestrator import start_publishing_job
    from app.publishing.providers.fake import FakePublishingProvider

    ws_id = _uid()
    with pytest.raises(PublicationOwnershipError, match="must all be provided together"):
        start_publishing_job(
            db_conn,
            plan_id=999,
            provider=FakePublishingProvider(),
            output_path="/tmp/x.mp4",
            output_sha256="abc",
            workspace_id=ws_id,
            channel_id=None,
            platform_account_id=None,
        )


# ---------------------------------------------------------------------------
# Workspace ownership API — publication visible to owner, not to other ws
# ---------------------------------------------------------------------------


def test_owned_publication_visible_to_owning_workspace(dev_client, db_conn, workspace):
    _seed_workspace_with_publication(db_conn, workspace.id)
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["id"] == 1


def test_owned_publication_not_visible_to_other_workspace(dev_client, db_conn, workspace):
    _seed_workspace_with_publication(db_conn, workspace.id)
    other_ws = _uid()
    res = dev_client.get(
        f"/api/v1/workspaces/{other_ws}/publications",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    assert res.json() == []


def test_null_workspace_publication_not_visible_to_any_workspace(dev_client, db_conn, workspace):
    """Publications with workspace_id=NULL (legacy/fake) must not be exposed."""
    db_conn.execute("PRAGMA foreign_keys = OFF")
    cur = db_conn.execute(
        "INSERT INTO topics (title, angle, status, created_at, updated_at) "
        "VALUES ('Legacy Topic', '', 'active', ?, ?)",
        (NOW, NOW),
    )
    topic_id = cur.lastrowid
    db_conn.execute(
        "INSERT INTO publishing_plans "
        "(id, render_manifest_id, topic_id, production_plan_id, script_id, scene_manifest_id, "
        " narration_run_id, caption_run_id, input_hash, publishing_engine_version, "
        " metadata_version, provider, provider_version, title, description, tags_json, "
        " visibility, created_at, updated_at) "
        "VALUES (99, 99, ?, 1, 1, 1, 1, 1, 'hash-legacy', '1.0', '1', 'fake', '1.0', "
        " 'Legacy Video', '', '[]', 'private', ?, ?)",
        (topic_id, NOW, NOW),
    )
    db_conn.execute(
        "INSERT INTO publishing_jobs "
        "(id, publishing_plan_id, attempt_number, provider, provider_version, status, "
        " retry_count, created_at, updated_at) "
        "VALUES (99, 99, 1, 'fake', '1.0', 'completed', 0, ?, ?)",
        (NOW, NOW),
    )
    # workspace_id intentionally omitted → NULL
    db_conn.execute(
        "INSERT INTO publications "
        "(id, publishing_plan_id, publishing_job_id, provider, provider_version, "
        " provider_video_id, provider_url, visibility, status, "
        " publishing_engine_version, input_hash, output_sha256,"
        " published_at, created_at, updated_at) "
        "VALUES (99, 99, 99, 'fake', '1.0', 'fake-vid', NULL, 'private', 'published', "
        " '1.0', 'hash-legacy-pub', 'sha256-legacy', ?, ?, ?)",
        (NOW, NOW, NOW),
    )
    db_conn.commit()

    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    assert all(p["id"] != 99 for p in res.json())


def test_stream_follows_workspace_ownership(dev_client, db_conn, workspace, artifacts_dir):
    mp4 = artifacts_dir / "renders" / "1" / "render_1.mp4"
    mp4.parent.mkdir(parents=True)
    mp4.write_bytes(b"fakemp4")
    _seed_workspace_with_publication(db_conn, workspace.id, output_path=str(mp4))
    other_ws = _uid()
    res = dev_client.get(
        f"/api/v1/workspaces/{other_ws}/publications/1/stream",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 404


def test_analytics_follows_workspace_ownership(dev_client, db_conn, workspace):
    _seed_workspace_with_publication(db_conn, workspace.id, with_analytics=True)
    other_ws = _uid()
    res = dev_client.get(
        f"/api/v1/workspaces/{other_ws}/publications/1/analytics",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 404
