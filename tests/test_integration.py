"""Integration tests for M15.12: N+1 fix, end-to-end auth → pipeline flow,
and production readiness validation.

All tests run against an in-process SQLite DB (no real PostgreSQL required).
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path: Path):
    from app.core.database import open_db

    conn = open_db(tmp_path / "integration.db")
    yield conn
    conn.close()


@pytest.fixture()
def ws_id(db):
    now = "2026-08-07T00:00:00"
    db.execute(
        "INSERT INTO cp_workspaces (id, name, slug, actor, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("ws-int", "Integration WS", "integration-ws", "system", now, now),
    )
    db.commit()
    return "ws-int"


@pytest.fixture()
def channel_id(db, ws_id):
    now = "2026-08-07T00:00:00"
    db.execute(
        "INSERT INTO cp_channels (id, workspace_id, name, slug, actor, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("ch-int", ws_id, "Integration Channel", "integration-ch", "system", now, now),
    )
    db.commit()
    return "ch-int"


def _create_pipeline(db, workspace_id, channel_id):
    """Helper: create a minimal pipeline row and return its ID."""
    import uuid

    from app.application.state import create_pipeline

    return create_pipeline(
        db,
        workspace_id=workspace_id,
        channel_id=channel_id,
        topic_id=None,
        actor="system:test",
        idempotency_key=str(uuid.uuid4()),
        start_stage="research",
        end_stage="research",
        correlation_id=str(uuid.uuid4()),
    )


# ── N+1 fix: list_pipelines bulk query ────────────────────────────────────


class _CountingConn:
    """Wraps a sqlite3 connection to count execute() calls."""

    def __init__(self, conn):
        self._conn = conn
        self.query_count = 0

    def execute(self, sql, params=()):
        self.query_count += 1
        return self._conn.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_list_pipelines_no_n_plus_1(db, ws_id, channel_id):
    """list_pipelines must issue exactly 2 queries regardless of pipeline count."""
    from app.application.state import list_pipelines

    # Create 5 pipelines
    for _ in range(5):
        _create_pipeline(db, ws_id, channel_id)

    counting = _CountingConn(db)
    result = list_pipelines(counting, ws_id)

    assert len(result) == 5
    # Should be exactly 2 queries: 1 for pipelines + 1 bulk stage query
    assert counting.query_count == 2, (
        f"Expected 2 queries, got {counting.query_count} (N+1 regression)"
    )


def test_list_pipelines_empty_returns_empty(db, ws_id):
    from app.application.state import list_pipelines

    result = list_pipelines(db, ws_id)
    assert result == []


def test_list_pipelines_with_stages_populated(db, ws_id, channel_id):
    from app.application.state import create_stage_entries, list_pipelines

    pid = _create_pipeline(db, ws_id, channel_id)
    create_stage_entries(db, pid, ["research", "scripting"], first="research")

    pipelines = list_pipelines(db, ws_id)
    assert len(pipelines) == 1
    stage_names = [s.stage for s in pipelines[0].stages]
    assert "research" in stage_names


def test_list_pipelines_status_filter(db, ws_id, channel_id):
    from app.application.state import list_pipelines

    _create_pipeline(db, ws_id, channel_id)
    _create_pipeline(db, ws_id, channel_id)

    all_pipelines = list_pipelines(db, ws_id)
    pending = list_pipelines(db, ws_id, status="pending")
    assert len(pending) == len(all_pipelines)


def test_list_pipelines_limit(db, ws_id, channel_id):
    from app.application.state import list_pipelines

    for _ in range(10):
        _create_pipeline(db, ws_id, channel_id)

    result = list_pipelines(db, ws_id, limit=3)
    assert len(result) == 3


# ── Auth → pipeline end-to-end ────────────────────────────────────────────


def test_auth_register_login_get_pipeline(db, ws_id, channel_id):
    """End-to-end: register user → login → decode token → create pipeline."""
    from app.application.state import get_pipeline
    from app.auth.service import AuthService

    svc = AuthService(secret_key="a" * 32)
    uid = svc.register_user(db, "e2e@example.com", "pass-phrase-123!")
    svc.assign_workspace_role(db, uid, ws_id, "operator")

    tokens = svc.login(db, "e2e@example.com", "pass-phrase-123!")
    claims = svc.decode_token(tokens["access_token"])

    assert claims["roles"][ws_id] == "operator"

    pid = _create_pipeline(db, ws_id, channel_id)
    pipeline = get_pipeline(db, pid)
    assert pipeline is not None
    assert pipeline.workspace_id == ws_id


# ── Provider boundary enforcement ─────────────────────────────────────────


def test_class_a_stage_dispatches_without_provider(db, ws_id, channel_id):
    """Class A stages run without any provider config."""
    from unittest.mock import MagicMock

    from app.workers.executor import dispatch_stage

    pipeline = {"id": "pipe-1", "workspace_id": ws_id}
    cfg = MagicMock()
    cfg.ai_provider = "fake"
    cfg.anthropic_api_key = ""
    cfg.elevenlabs_api_key = ""
    cfg.tts_live_enabled = False
    cfg.publishing_live_enabled = False

    result = dispatch_stage(
        db, pipeline, "research", actor="system", workspace_id=ws_id, config=cfg
    )
    assert result.status == "dispatched"


def test_class_b_stage_blocked_in_ci(db, ws_id, channel_id):
    """Class B stages must be blocked when no live provider is configured (CI safety)."""
    from unittest.mock import MagicMock

    from app.workers.executor import dispatch_stage

    pipeline = {"id": "pipe-1", "workspace_id": ws_id}
    cfg = MagicMock()
    cfg.ai_provider = "fake"
    cfg.anthropic_api_key = ""
    cfg.elevenlabs_api_key = ""
    cfg.tts_live_enabled = False
    cfg.publishing_live_enabled = False

    result = dispatch_stage(
        db, pipeline, "narration", actor="system", workspace_id=ws_id, config=cfg
    )
    assert result.status == "blocked"


# ── Production readiness checks ───────────────────────────────────────────


def test_schema_version_is_20(db):
    from app.core.database import SCHEMA_VERSION

    assert SCHEMA_VERSION == 21


def test_auth_tables_present(db):
    tables = {
        row["name"]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "auth_users" in tables
    assert "auth_refresh_tokens" in tables
    assert "auth_workspace_roles" in tables
    assert "obj_storage_objects" in tables


def test_sensitive_field_redaction_is_wired():
    """Confirm logging redacts passwords and tokens before emission."""
    from app.observability.logging_config import _redact_sensitive

    event = {"password": "secret", "pipeline_id": "p-1"}
    result = _redact_sensitive(None, "info", event)
    assert result["password"] == "<redacted>"
    assert result["pipeline_id"] == "p-1"


def test_provider_boundary_defaults_to_fail_closed():
    """When no config is set, Class B/C stages must be blocked."""
    from unittest.mock import MagicMock

    from app.workers.executor import dispatch_stage

    cfg = MagicMock()
    cfg.ai_provider = "fake"
    cfg.anthropic_api_key = ""
    cfg.elevenlabs_api_key = ""
    cfg.tts_live_enabled = False
    cfg.publishing_live_enabled = False

    for stage in ("narration", "publish", "upload", "content_generation"):
        result = dispatch_stage(
            None, {"id": "p"}, stage, actor="sys", workspace_id="ws", config=cfg
        )
        assert result.status == "blocked", f"Stage '{stage}' should be blocked by default"


def test_refresh_token_not_stored_plaintext(db, ws_id):
    """Verify the DB never holds a raw refresh token."""
    from app.auth.service import AuthService
    from app.auth.tokens import hash_refresh_token

    svc = AuthService(secret_key="b" * 32)
    svc.register_user(db, "sec@example.com", "strong-passphrase!")
    tokens = svc.login(db, "sec@example.com", "strong-passphrase!")

    raw = tokens["refresh_token"]
    rows = db.execute("SELECT token_hash FROM auth_refresh_tokens").fetchall()
    hashes = {r["token_hash"] for r in rows}

    assert raw not in hashes, "Raw refresh token must never be stored in DB"
    assert hash_refresh_token(raw) in hashes, "Hashed refresh token must be in DB"
