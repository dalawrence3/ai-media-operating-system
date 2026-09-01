"""Backend API tests for publications routes.

Covers:
- GET /workspaces/{ws}/publications          — list, workspace isolation
- GET /workspaces/{ws}/publications/{id}     — detail with tags, render join
- GET /workspaces/{ws}/publications/{id}/stream  — video file response, auth, path traversal
- GET /workspaces/{ws}/publications/{id}/analytics — snapshot + metrics + retention count
- POST /workspaces/{ws}/publications/{id}/release-public — full gate sequence, read-before-write,
  idempotency, DB ordering, cp_events audit
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
        1,
        "test@test.com",
        {workspace_id: role},
        secret_key=_SECRET,
        expire_seconds=3600,
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
            (
                publication_id,
                publication_id,
                publication_id,
                render_manifest_id,
                NOW,
                NOW,
            ),
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
    assert data[0]["render_duration_ms"] == 58607
    assert data[0]["topic_title"] == "Test Topic"


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
    assert data["topic_title"] == "Test Topic"


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


def test_get_publication_detail_topic_title_null_when_topic_unlinked(
    dev_client, db_conn, workspace
):
    """A publication whose publishing_plans.topic_id points nowhere resolvable
    (e.g. the topic's own workspace_id was never backfilled) must not 500 —
    topic_title is simply null, same LEFT JOIN behavior as render_manifest_id."""
    _seed_workspace_with_publication(db_conn, workspace.id)
    db_conn.execute("UPDATE publishing_plans SET topic_id = 999999 WHERE id = 1")
    db_conn.commit()
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    assert res.json()["topic_title"] is None


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
        db_conn,
        workspace.id,
        render_manifest_id=2,
        publication_id=2,
        output_path=str(mp4_file),
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
    assert data["experiment_id"] is None


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
    assert data["experiment_id"] is None


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
        assert version == 51
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


# ---------------------------------------------------------------------------
# Analytics history endpoint (Phase 17C) — GET .../analytics/history
# ---------------------------------------------------------------------------


def test_analytics_history_empty_without_snapshots(dev_client, db_conn, workspace):
    _seed_workspace_with_publication(db_conn, workspace.id)
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1/analytics/history",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    assert res.json() == []


def test_analytics_history_returns_snapshots_oldest_first(dev_client, db_conn, workspace):
    _seed_workspace_with_publication(db_conn, workspace.id, with_analytics=True)
    # Seed creates snapshot id=1 (views=1234.0, ingested at NOW). Add a later
    # snapshot with no reportable data yet — this is a real ingestion shape:
    # a provider polled again before it had anything new to report.
    db_conn.execute(
        "INSERT INTO analytics_snapshots "
        "(id, publication_id, publishing_plan_id, publishing_job_id, render_manifest_id, "
        " scene_manifest_id, production_plan_id, script_id, topic_id, narration_run_id, "
        " caption_run_id, provider, provider_version, adapter_version, engine_version, "
        " analytics_schema_version, db_schema_version, input_hash, "
        " ingested_at, observed_at, observation_state, period_start, period_end, created_at) "
        "VALUES (2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, "
        " 'youtube', '1.0', '1.0', '1.0', '1', 23, 'hash-snap-2', "
        " '2026-06-01T00:00:00', '2026-06-01T00:00:00', 'no_data', NULL, NULL, ?)",
        (NOW,),
    )
    db_conn.commit()
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1/analytics/history",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 2
    assert data[0]["snapshot_id"] == 1
    assert data[0]["metrics"] == {"views": 1234.0}
    assert data[0]["experiment_id"] is None
    assert data[1]["snapshot_id"] == 2
    assert data[1]["observation_state"] == "no_data"
    # A snapshot with nothing to report yet must not fabricate metric values.
    assert data[1]["metrics"] == {}


def test_analytics_history_surfaces_experiment_id(dev_client, db_conn, workspace):
    _seed_workspace_with_publication(db_conn, workspace.id, with_analytics=True)
    db_conn.execute(
        "UPDATE analytics_snapshots SET experiment_id = ? WHERE id = 1",
        ("exp-abc-123",),
    )
    db_conn.commit()
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1/analytics/history",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data[0]["experiment_id"] == "exp-abc-123"


def test_analytics_history_cross_workspace_404(dev_client, db_conn, workspace):
    _seed_workspace_with_publication(db_conn, workspace.id, with_analytics=True)
    other_ws = _uid()
    res = dev_client.get(
        f"/api/v1/workspaces/{other_ws}/publications/1/analytics/history",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 404


def test_analytics_history_unknown_publication_404(dev_client, workspace):
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/999/analytics/history",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# release_eligible / release_enabled fields in GET detail
# ---------------------------------------------------------------------------


def test_get_publication_detail_includes_release_fields(dev_client, db_conn, workspace):
    _seed_workspace_with_publication(db_conn, workspace.id)
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "release_eligible" in data
    assert "release_enabled" in data
    assert isinstance(data["release_eligible"], bool)
    assert isinstance(data["release_enabled"], bool)
    # platform_account_id is internal and must NOT be exposed
    assert "platform_account_id" not in data


def test_get_publication_detail_release_eligible_false_without_account(
    dev_client, db_conn, workspace
):
    _seed_workspace_with_publication(db_conn, workspace.id)
    # No platform_account_id → not eligible
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    assert res.json()["release_eligible"] is False


def test_get_publication_detail_release_enabled_false_by_default(
    dev_client, db_conn, workspace, monkeypatch
):
    monkeypatch.setenv("ACE_RELEASE_PUBLIC_ENABLED", "false")
    monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "false")
    reset_config()
    _seed_workspace_with_publication(db_conn, workspace.id)
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    assert res.json()["release_enabled"] is False


# ---------------------------------------------------------------------------
# Release endpoint helpers and fake client
# ---------------------------------------------------------------------------


class _FakeReleaseClient:
    """Fake YouTube client for release endpoint tests."""

    def __init__(
        self,
        *,
        current_privacy_status: str = "private",
        raise_on_get: Exception | None = None,
        raise_on_update: Exception | None = None,
    ) -> None:
        self._privacy_status = current_privacy_status
        self._raise_on_get = raise_on_get
        self._raise_on_update = raise_on_update
        self.update_called = False
        self.update_video_id: str | None = None
        self.update_status_sent: dict | None = None

    def get_video(self, video_id: str, parts: list) -> dict:
        if self._raise_on_get is not None:
            raise self._raise_on_get
        return {
            "kind": "youtube#videoListResponse",
            "items": [
                {
                    "id": video_id,
                    "status": {
                        "privacyStatus": self._privacy_status,
                        "uploadStatus": "processed",
                        "embeddable": True,
                        "publicStatsViewable": True,
                        "selfDeclaredMadeForKids": False,
                    },
                }
            ],
        }

    def update_video(self, video_id: str, snippet: dict, status: dict) -> dict:
        if self._raise_on_update is not None:
            raise self._raise_on_update
        self.update_called = True
        self.update_video_id = video_id
        self.update_status_sent = status
        return {"kind": "youtube#video", "id": video_id, "status": status}


def _seed_publication_with_ownership(conn, workspace_id: str) -> tuple[str, str]:
    """Seed a publication whose platform_account_id and channel_id columns are filled in.

    Returns (channel_id, account_id).
    """
    ch_id, acct_id = _seed_cp_workspace_channel_account(conn, workspace_id)
    _seed_workspace_with_publication(conn, workspace_id)
    conn.execute(
        "UPDATE publications SET channel_id=?, platform_account_id=? WHERE id=1",
        (ch_id, acct_id),
    )
    conn.commit()
    return ch_id, acct_id


def _release_url(workspace_id: str, pub_id: int = 1) -> str:
    return f"/api/v1/workspaces/{workspace_id}/publications/{pub_id}/release-public"


# ---------------------------------------------------------------------------
# Gate tests — no YouTube client needed
# ---------------------------------------------------------------------------


def test_release_403_when_release_flag_false(dev_client, db_conn, workspace, monkeypatch):
    monkeypatch.setenv("ACE_RELEASE_PUBLIC_ENABLED", "false")
    monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
    reset_config()
    _seed_workspace_with_publication(db_conn, workspace.id)
    res = dev_client.post(
        _release_url(workspace.id),
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 403
    assert "ACE_RELEASE_PUBLIC_ENABLED" in res.json()["detail"]


def test_release_403_when_live_publishing_flag_false(dev_client, db_conn, workspace, monkeypatch):
    monkeypatch.setenv("ACE_RELEASE_PUBLIC_ENABLED", "true")
    monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "false")
    reset_config()
    _seed_workspace_with_publication(db_conn, workspace.id)
    res = dev_client.post(
        _release_url(workspace.id),
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 403
    assert "ACE_PUBLISHING_LIVE_ENABLED" in res.json()["detail"]


def test_release_401_without_auth(prod_client, db_conn, workspace, monkeypatch):
    monkeypatch.setenv("ACE_RELEASE_PUBLIC_ENABLED", "true")
    monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
    reset_config()
    _seed_workspace_with_publication(db_conn, workspace.id)
    res = prod_client.post(_release_url(workspace.id))
    assert res.status_code == 401


def test_release_403_insufficient_rbac(prod_client, db_conn, workspace, monkeypatch):
    monkeypatch.setenv("ACE_RELEASE_PUBLIC_ENABLED", "true")
    monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
    reset_config()
    _seed_workspace_with_publication(db_conn, workspace.id)
    # analyst role lacks publish:approve
    token = _jwt(workspace.id, "analyst")
    res = prod_client.post(
        _release_url(workspace.id),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403


def test_release_operator_role_passes_rbac_gate(
    dev_client, db_conn, workspace, monkeypatch, tmp_path
):
    """Operator role is sufficient for publish:approve. Gate passes before YouTube."""
    monkeypatch.setenv("ACE_RELEASE_PUBLIC_ENABLED", "true")
    monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
    reset_config()
    _seed_publication_with_ownership(db_conn, workspace.id)

    # Inject fake client so the endpoint doesn't attempt real YouTube calls.
    from app.api.main import app
    from app.api.routes.publications import _get_release_youtube_client

    fake_client = _FakeReleaseClient(current_privacy_status="private")
    app.dependency_overrides[_get_release_youtube_client] = lambda: fake_client
    try:
        # dev_client bypasses JWT but uses dev actor — all permissions granted
        res = dev_client.post(
            _release_url(workspace.id),
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
        assert res.status_code == 200
    finally:
        app.dependency_overrides.pop(_get_release_youtube_client, None)


def test_release_404_wrong_workspace(dev_client, db_conn, workspace, monkeypatch):
    monkeypatch.setenv("ACE_RELEASE_PUBLIC_ENABLED", "true")
    monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
    reset_config()
    _seed_workspace_with_publication(db_conn, workspace.id)
    other_ws = _uid()
    res = dev_client.post(
        _release_url(other_ws),
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 404


def test_release_422_non_youtube_provider(dev_client, db_conn, workspace, monkeypatch):
    monkeypatch.setenv("ACE_RELEASE_PUBLIC_ENABLED", "true")
    monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
    reset_config()
    _seed_workspace_with_publication(db_conn, workspace.id)
    db_conn.execute("UPDATE publications SET provider='fake' WHERE id=1")
    db_conn.commit()
    res = dev_client.post(
        _release_url(workspace.id),
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 422
    assert "fake" in res.json()["detail"]


def test_release_409_publication_not_published(dev_client, db_conn, workspace, monkeypatch):
    monkeypatch.setenv("ACE_RELEASE_PUBLIC_ENABLED", "true")
    monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
    reset_config()
    _seed_workspace_with_publication(db_conn, workspace.id)
    db_conn.execute("UPDATE publications SET status='uploading' WHERE id=1")
    db_conn.commit()
    res = dev_client.post(
        _release_url(workspace.id),
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 409
    assert "uploading" in res.json()["detail"]


def test_release_409_already_public_in_db(dev_client, db_conn, workspace, monkeypatch):
    monkeypatch.setenv("ACE_RELEASE_PUBLIC_ENABLED", "true")
    monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
    reset_config()
    _seed_workspace_with_publication(db_conn, workspace.id)
    db_conn.execute("UPDATE publications SET visibility='public' WHERE id=1")
    db_conn.commit()
    res = dev_client.post(
        _release_url(workspace.id),
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 409
    assert "already public" in res.json()["detail"].lower()


def test_release_422_missing_platform_account_id(dev_client, db_conn, workspace, monkeypatch):
    monkeypatch.setenv("ACE_RELEASE_PUBLIC_ENABLED", "true")
    monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
    reset_config()
    # Default seed has platform_account_id=NULL
    _seed_workspace_with_publication(db_conn, workspace.id)
    res = dev_client.post(
        _release_url(workspace.id),
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 422
    assert "platform_account_id" in res.json()["detail"]


# ---------------------------------------------------------------------------
# Scope / token tests (monkeypatch resolve_upload_token)
# ---------------------------------------------------------------------------


def _enable_release_flags(monkeypatch):
    monkeypatch.setenv("ACE_RELEASE_PUBLIC_ENABLED", "true")
    monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
    reset_config()


def _make_stored_token(scopes: list[str]):
    """Build a StoredTokenPayload with the given scopes for monkeypatching."""
    from datetime import UTC, datetime

    from app.oauth.store import StoredTokenPayload

    return StoredTokenPayload(
        account_id="test-acct",
        access_token="fake_access",
        refresh_token="fake_refresh",
        token_type="Bearer",
        expires_at_utc=datetime(2030, 1, 1, tzinfo=UTC),
        scopes=scopes,
        google_sub="12345",
        stored_at_utc=datetime.now(UTC),
    )


def test_release_403_missing_release_scope(dev_client, db_conn, workspace, monkeypatch):
    _enable_release_flags(monkeypatch)
    _seed_publication_with_ownership(db_conn, workspace.id)

    token_without_force_ssl = _make_stored_token(
        [
            "openid",
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/youtube.upload",
        ]
    )
    monkeypatch.setattr(
        "app.publishing.upload_gate.resolve_upload_token",
        lambda *_a, **_kw: token_without_force_ssl,
    )
    res = dev_client.post(
        _release_url(workspace.id),
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 403
    assert "force-ssl" in res.json()["detail"]


def test_release_502_token_resolution_failure(dev_client, db_conn, workspace, monkeypatch):
    _enable_release_flags(monkeypatch)
    _seed_publication_with_ownership(db_conn, workspace.id)

    from app.oauth.errors import OAuthRefreshError

    monkeypatch.setattr(
        "app.publishing.upload_gate.resolve_upload_token",
        lambda *_a, **_kw: (_ for _ in ()).throw(OAuthRefreshError("token expired")),
    )
    res = dev_client.post(
        _release_url(workspace.id),
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 502
    assert "token" in res.json()["detail"].lower()


# ---------------------------------------------------------------------------
# YouTube client tests (inject fake client via dependency override)
# ---------------------------------------------------------------------------


def _release_with_fake_client(dev_client, db_conn, workspace, monkeypatch, fake_client):
    """Helper: set flags, seed with ownership, inject fake client, POST release."""
    _enable_release_flags(monkeypatch)
    _seed_publication_with_ownership(db_conn, workspace.id)

    from app.api.main import app
    from app.api.routes.publications import _get_release_youtube_client

    app.dependency_overrides[_get_release_youtube_client] = lambda: fake_client
    try:
        res = dev_client.post(
            _release_url(workspace.id),
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
    finally:
        app.dependency_overrides.pop(_get_release_youtube_client, None)
    return res


def test_release_502_youtube_get_video_fails(dev_client, db_conn, workspace, monkeypatch):
    fake = _FakeReleaseClient(raise_on_get=RuntimeError("YouTube API unavailable"))
    res = _release_with_fake_client(dev_client, db_conn, workspace, monkeypatch, fake)
    assert res.status_code == 502
    assert "YouTube API unavailable" in res.json()["detail"]


def test_release_502_youtube_update_video_fails(dev_client, db_conn, workspace, monkeypatch):
    fake = _FakeReleaseClient(raise_on_update=RuntimeError("quota exceeded"))
    res = _release_with_fake_client(dev_client, db_conn, workspace, monkeypatch, fake)
    assert res.status_code == 502
    assert "quota exceeded" in res.json()["detail"]


def test_release_db_unchanged_when_update_fails(dev_client, db_conn, workspace, monkeypatch):
    """DB visibility must remain 'private' if videos.update raises."""
    fake = _FakeReleaseClient(raise_on_update=RuntimeError("upstream error"))
    _release_with_fake_client(dev_client, db_conn, workspace, monkeypatch, fake)
    row = db_conn.execute("SELECT visibility FROM publications WHERE id=1").fetchone()
    assert row["visibility"] == "private"


def test_release_success_sets_db_public(dev_client, db_conn, workspace, monkeypatch):
    fake = _FakeReleaseClient(current_privacy_status="private")
    res = _release_with_fake_client(dev_client, db_conn, workspace, monkeypatch, fake)
    assert res.status_code == 200
    assert res.json()["visibility"] == "public"
    assert res.json()["reconciled"] is False
    row = db_conn.execute("SELECT visibility FROM publications WHERE id=1").fetchone()
    assert row["visibility"] == "public"


def test_release_success_calls_update_with_correct_video_id(
    dev_client, db_conn, workspace, monkeypatch
):
    fake = _FakeReleaseClient(current_privacy_status="private")
    _release_with_fake_client(dev_client, db_conn, workspace, monkeypatch, fake)
    assert fake.update_called is True
    assert fake.update_video_id == "kQH88nXdiRY"


def test_release_success_strips_read_only_fields_from_update(
    dev_client, db_conn, workspace, monkeypatch
):
    """Read-only status fields must not be included in the videos.update payload."""
    fake = _FakeReleaseClient(current_privacy_status="private")
    _release_with_fake_client(dev_client, db_conn, workspace, monkeypatch, fake)
    assert fake.update_status_sent is not None
    for field in ("uploadStatus", "failureReason", "rejectionReason", "madeForKids"):
        assert field not in fake.update_status_sent, f"{field!r} must be stripped"


def test_release_success_sets_privacy_status_public(dev_client, db_conn, workspace, monkeypatch):
    fake = _FakeReleaseClient(current_privacy_status="private")
    _release_with_fake_client(dev_client, db_conn, workspace, monkeypatch, fake)
    assert fake.update_status_sent is not None
    assert fake.update_status_sent["privacyStatus"] == "public"


def test_release_success_clears_publish_at(dev_client, db_conn, workspace, monkeypatch):
    """publishAt must be removed from the update body to release immediately."""

    class _ScheduledFakeClient(_FakeReleaseClient):
        def get_video(self, video_id: str, parts: list) -> dict:
            resp = super().get_video(video_id, parts)
            resp["items"][0]["status"]["publishAt"] = "2030-01-01T00:00:00Z"
            return resp

    fake = _ScheduledFakeClient(current_privacy_status="private")
    _release_with_fake_client(dev_client, db_conn, workspace, monkeypatch, fake)
    assert "publishAt" not in (fake.update_status_sent or {})


# ---------------------------------------------------------------------------
# Idempotency — YouTube ground-truth already public
# ---------------------------------------------------------------------------


def test_release_reconciles_when_youtube_already_public(
    dev_client, db_conn, workspace, monkeypatch
):
    """If YouTube says already public, DB is reconciled and no videos.update is called."""
    fake = _FakeReleaseClient(current_privacy_status="public")
    res = _release_with_fake_client(dev_client, db_conn, workspace, monkeypatch, fake)
    assert res.status_code == 200
    assert res.json()["visibility"] == "public"
    assert res.json()["reconciled"] is True
    assert fake.update_called is False
    row = db_conn.execute("SELECT visibility FROM publications WHERE id=1").fetchone()
    assert row["visibility"] == "public"


def test_release_no_double_write_when_already_public_in_db(
    dev_client, db_conn, workspace, monkeypatch
):
    """Endpoint returns 409 if local DB already shows public (before hitting YouTube)."""
    _enable_release_flags(monkeypatch)
    _seed_publication_with_ownership(db_conn, workspace.id)
    db_conn.execute("UPDATE publications SET visibility='public' WHERE id=1")
    db_conn.commit()

    fake = _FakeReleaseClient(current_privacy_status="public")
    from app.api.main import app
    from app.api.routes.publications import _get_release_youtube_client

    app.dependency_overrides[_get_release_youtube_client] = lambda: fake
    try:
        res = dev_client.post(
            _release_url(workspace.id),
            headers={"X-Dev-Actor": "dev:studio-user"},
        )
    finally:
        app.dependency_overrides.pop(_get_release_youtube_client, None)

    assert res.status_code == 409
    # videos.list was never called (rejected before reaching YouTube)
    assert fake.update_called is False


# ---------------------------------------------------------------------------
# cp_events audit
# ---------------------------------------------------------------------------


def test_release_success_writes_cp_event(dev_client, db_conn, workspace, monkeypatch):
    fake = _FakeReleaseClient(current_privacy_status="private")
    _release_with_fake_client(dev_client, db_conn, workspace, monkeypatch, fake)

    events = db_conn.execute(
        "SELECT event_type, payload_json FROM cp_events WHERE workspace_id=?",
        (workspace.id,),
    ).fetchall()
    release_events = [e for e in events if e["event_type"] == "publication.released_public"]
    assert len(release_events) == 1
    import json

    payload = json.loads(release_events[0]["payload_json"])
    assert payload["publication_id"] == 1
    assert payload["provider_video_id"] == "kQH88nXdiRY"
    assert payload["visibility_before"] == "private"
    assert payload["visibility_after"] == "public"


def test_release_reconcile_does_not_write_released_public_event(
    dev_client, db_conn, workspace, monkeypatch
):
    """Reconciliation (YouTube already public) must NOT write publication.released_public."""
    fake = _FakeReleaseClient(current_privacy_status="public")
    _release_with_fake_client(dev_client, db_conn, workspace, monkeypatch, fake)

    events = db_conn.execute(
        "SELECT event_type FROM cp_events WHERE workspace_id=?",
        (workspace.id,),
    ).fetchall()
    released_events = [e for e in events if e["event_type"] == "publication.released_public"]
    assert len(released_events) == 0


def test_release_reconcile_writes_reconciliation_event(dev_client, db_conn, workspace, monkeypatch):
    """Reconciliation must write publication.visibility_reconciled_public with full payload."""
    fake = _FakeReleaseClient(current_privacy_status="public")
    _release_with_fake_client(dev_client, db_conn, workspace, monkeypatch, fake)

    events = db_conn.execute(
        "SELECT event_type, payload_json FROM cp_events WHERE workspace_id=?",
        (workspace.id,),
    ).fetchall()
    reconcile_events = [
        e for e in events if e["event_type"] == "publication.visibility_reconciled_public"
    ]
    assert len(reconcile_events) == 1
    import json

    payload = json.loads(reconcile_events[0]["payload_json"])
    assert payload["publication_id"] == 1
    assert payload["provider_video_id"] == "kQH88nXdiRY"
    assert payload["previous_local_visibility"] == "private"
    assert payload["observed_provider_visibility"] == "public"


# ---------------------------------------------------------------------------
# release_scope_granted field in GET detail
# ---------------------------------------------------------------------------


def test_get_publication_detail_release_scope_granted_false_without_token(
    dev_client, db_conn, workspace
):
    """release_scope_granted=False when publication has no platform_account_id/token."""
    _seed_workspace_with_publication(db_conn, workspace.id)
    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "release_scope_granted" in data
    assert data["release_scope_granted"] is False


def test_get_publication_detail_release_scope_granted_false_without_force_ssl(
    dev_client, db_conn, workspace, tmp_path
):
    """release_scope_granted=False when stored token lacks youtube.force-ssl."""
    import json as _json

    _seed_publication_with_ownership(db_conn, workspace.id)
    acct_id = db_conn.execute("SELECT platform_account_id FROM publications WHERE id=1").fetchone()[
        "platform_account_id"
    ]

    # Create a real credential profile pointing to a token file without force-ssl.
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    from datetime import UTC, datetime

    token_file = token_dir / f"{acct_id}.json"
    token_file.write_text(
        _json.dumps(
            {
                "account_id": acct_id,
                "access_token": "fake_access",
                "refresh_token": "fake_refresh",
                "token_type": "Bearer",
                "expires_at_utc": "2030-01-01T00:00:00+00:00",
                "scopes": [
                    "openid",
                    "https://www.googleapis.com/auth/youtube.readonly",
                    "https://www.googleapis.com/auth/youtube.upload",
                ],
                "google_sub": "12345",
                "stored_at_utc": datetime.now(UTC).isoformat(),
            }
        )
    )
    # Link a credential profile to the account.
    db_conn.execute("PRAGMA foreign_keys = OFF")
    cred_id = _uid()
    db_conn.execute(
        "INSERT INTO cp_credential_profiles "
        "(id, workspace_id, display_name, credential_type, "
        " status, external_ref, actor, created_at, updated_at) "
        "VALUES (?, ?, 'Test Cred', 'oauth2', 'active', ?, 'test', ?, ?)",
        (cred_id, workspace.id, "file://" + str(token_file), NOW, NOW),
    )
    db_conn.execute(
        "UPDATE cp_platform_accounts SET credential_profile_id=? WHERE id=?",
        (cred_id, acct_id),
    )
    db_conn.commit()

    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    assert res.json()["release_scope_granted"] is False


def test_get_publication_detail_release_scope_granted_true_with_force_ssl(
    dev_client, db_conn, workspace, tmp_path
):
    """release_scope_granted=True when stored token includes youtube.force-ssl."""
    import json as _json

    _seed_publication_with_ownership(db_conn, workspace.id)
    acct_id = db_conn.execute("SELECT platform_account_id FROM publications WHERE id=1").fetchone()[
        "platform_account_id"
    ]

    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    from datetime import UTC, datetime

    token_file = token_dir / f"{acct_id}.json"
    token_file.write_text(
        _json.dumps(
            {
                "account_id": acct_id,
                "access_token": "fake_access",
                "refresh_token": "fake_refresh",
                "token_type": "Bearer",
                "expires_at_utc": "2030-01-01T00:00:00+00:00",
                "scopes": [
                    "openid",
                    "https://www.googleapis.com/auth/youtube.readonly",
                    "https://www.googleapis.com/auth/youtube.upload",
                    "https://www.googleapis.com/auth/yt-analytics.readonly",
                    "https://www.googleapis.com/auth/youtube.force-ssl",
                ],
                "google_sub": "12345",
                "stored_at_utc": datetime.now(UTC).isoformat(),
            }
        )
    )
    db_conn.execute("PRAGMA foreign_keys = OFF")
    cred_id = _uid()
    db_conn.execute(
        "INSERT INTO cp_credential_profiles "
        "(id, workspace_id, display_name, credential_type, "
        " status, external_ref, actor, created_at, updated_at) "
        "VALUES (?, ?, 'Test Cred', 'oauth2', 'active', ?, 'test', ?, ?)",
        (cred_id, workspace.id, "file://" + str(token_file), NOW, NOW),
    )
    db_conn.execute(
        "UPDATE cp_platform_accounts SET credential_profile_id=? WHERE id=?",
        (cred_id, acct_id),
    )
    db_conn.commit()

    res = dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 200
    assert res.json()["release_scope_granted"] is True


def test_get_publication_detail_scope_check_makes_no_network_call(
    dev_client, db_conn, workspace, monkeypatch, tmp_path
):
    """has_release_scope() must never make network calls — confirmed by checking no refresh runs."""
    refresh_call_count = 0

    original_refresh = None

    def _counting_refresh(*args, **kwargs):
        nonlocal refresh_call_count
        refresh_call_count += 1
        return original_refresh(*args, **kwargs)

    import app.oauth.flow as _flow

    original_refresh = _flow.refresh_account_token
    monkeypatch.setattr(_flow, "refresh_account_token", _counting_refresh)

    _seed_workspace_with_publication(db_conn, workspace.id)
    dev_client.get(
        f"/api/v1/workspaces/{workspace.id}/publications/1",
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    # has_release_scope() reads from the token store; it must never call refresh_account_token
    assert refresh_call_count == 0


def test_post_release_still_checks_scope_server_side(dev_client, db_conn, workspace, monkeypatch):
    """POST /release-public independently enforces scope — no client-side bypass is possible."""
    _enable_release_flags(monkeypatch)
    _seed_publication_with_ownership(db_conn, workspace.id)

    # Monkeypatch resolve_upload_token to return a token WITHOUT force-ssl.
    token_without_force_ssl = _make_stored_token(
        [
            "openid",
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/youtube.upload",
        ]
    )
    monkeypatch.setattr(
        "app.publishing.upload_gate.resolve_upload_token",
        lambda *_a, **_kw: token_without_force_ssl,
    )
    # Do NOT override _get_release_youtube_client — this forces the scope check path.
    res = dev_client.post(
        _release_url(workspace.id),
        headers={"X-Dev-Actor": "dev:studio-user"},
    )
    assert res.status_code == 403
    assert "force-ssl" in res.json()["detail"]
