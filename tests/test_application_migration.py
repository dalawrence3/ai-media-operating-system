"""Phase 13 schema migration tests — SCHEMA_VERSION 19, 3 new app_ tables."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.core.database import SCHEMA_VERSION, open_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_conn():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    conn = open_db(path)
    yield conn
    conn.close()
    path.unlink(missing_ok=True)


@pytest.fixture
def nofk(db_conn):
    """Same connection with FK enforcement disabled — for raw schema structure tests."""
    db_conn.execute("PRAGMA foreign_keys=OFF")
    return db_conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return {r[0] for r in rows}


def _index_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    return {r[0] for r in rows}


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------


class TestSchemaVersion:
    def test_constant_is_19(self):
        assert SCHEMA_VERSION == 20

    def test_db_reports_19(self, db_conn):
        ver = db_conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert ver == 20


# ---------------------------------------------------------------------------
# New application tables exist
# ---------------------------------------------------------------------------


class TestAppTablesExist:
    def test_pipeline_executions_exists(self, db_conn):
        assert "app_pipeline_executions" in _table_names(db_conn)

    def test_pipeline_stage_log_exists(self, db_conn):
        assert "app_pipeline_stage_log" in _table_names(db_conn)

    def test_schedule_definitions_exists(self, db_conn):
        assert "app_schedule_definitions" in _table_names(db_conn)


# ---------------------------------------------------------------------------
# Pipeline executions schema
# ---------------------------------------------------------------------------


class TestPipelineExecutionsSchema:
    EXPECTED_COLS = {
        "id",
        "workspace_id",
        "channel_id",
        "platform_account_id",
        "topic_id",
        "correlation_id",
        "idempotency_key",
        "status",
        "current_stage",
        "end_stage",
        "experiment_id",
        "actor",
        "policy_snapshot_json",
        "error_message",
        "blocked_reason",
        "created_at",
        "updated_at",
    }

    def test_all_columns_present(self, db_conn):
        cols = set(_columns(db_conn, "app_pipeline_executions"))
        assert self.EXPECTED_COLS <= cols

    def test_invalid_status_rejected(self, nofk):
        with pytest.raises(sqlite3.IntegrityError):
            nofk.execute(
                "INSERT INTO app_pipeline_executions "
                "(id, workspace_id, correlation_id, idempotency_key, "
                "status, end_stage, actor, created_at, updated_at) "
                "VALUES ('x','ws','c','k','BAD','learning','cli','n','n')"
            )

    def test_correlation_id_unique(self, nofk):
        nofk.execute(
            "INSERT INTO app_pipeline_executions "
            "(id, workspace_id, correlation_id, idempotency_key, "
            "end_stage, actor, created_at, updated_at) "
            "VALUES ('p1','ws','corr-1','idem-1','learning','cli','n','n')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            nofk.execute(
                "INSERT INTO app_pipeline_executions "
                "(id, workspace_id, correlation_id, idempotency_key, "
                "end_stage, actor, created_at, updated_at) "
                "VALUES ('p2','ws','corr-1','idem-2','learning','cli','n','n')"
            )

    def test_idempotency_key_unique(self, nofk):
        nofk.execute(
            "INSERT INTO app_pipeline_executions "
            "(id, workspace_id, correlation_id, idempotency_key, "
            "end_stage, actor, created_at, updated_at) "
            "VALUES ('p3','ws','corr-3','idem-x','learning','cli','n','n')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            nofk.execute(
                "INSERT INTO app_pipeline_executions "
                "(id, workspace_id, correlation_id, idempotency_key, "
                "end_stage, actor, created_at, updated_at) "
                "VALUES ('p4','ws','corr-4','idem-x','learning','cli','n','n')"
            )

    def test_default_status_is_pending(self, nofk):
        nofk.execute(
            "INSERT INTO app_pipeline_executions "
            "(id, workspace_id, correlation_id, idempotency_key, "
            "end_stage, actor, created_at, updated_at) "
            "VALUES ('pd','ws','corr-d','idem-d','learning','cli','n','n')"
        )
        row = nofk.execute("SELECT status FROM app_pipeline_executions WHERE id='pd'").fetchone()
        assert row[0] == "pending"


# ---------------------------------------------------------------------------
# Pipeline stage log schema
# ---------------------------------------------------------------------------


class TestPipelineStageLogSchema:
    EXPECTED_COLS = {
        "id",
        "pipeline_id",
        "stage",
        "status",
        "artifact_id",
        "artifact_type",
        "error_message",
        "duration_ms",
        "started_at",
        "completed_at",
        "created_at",
    }

    def test_all_columns_present(self, db_conn):
        cols = set(_columns(db_conn, "app_pipeline_stage_log"))
        assert self.EXPECTED_COLS <= cols

    def test_pipeline_stage_unique(self, nofk):
        nofk.execute(
            "INSERT INTO app_pipeline_executions "
            "(id, workspace_id, correlation_id, idempotency_key, "
            "end_stage, actor, created_at, updated_at) "
            "VALUES ('pp','ws','corr-pp','idem-pp','learning','cli','n','n')"
        )
        nofk.execute(
            "INSERT INTO app_pipeline_stage_log "
            "(id, pipeline_id, stage, created_at) VALUES ('s1','pp','research','n')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            nofk.execute(
                "INSERT INTO app_pipeline_stage_log "
                "(id, pipeline_id, stage, created_at) VALUES ('s2','pp','research','n')"
            )

    def test_invalid_stage_status_rejected(self, nofk):
        nofk.execute(
            "INSERT INTO app_pipeline_executions "
            "(id, workspace_id, correlation_id, idempotency_key, "
            "end_stage, actor, created_at, updated_at) "
            "VALUES ('pp2','ws','corr-pp2','idem-pp2','learning','cli','n','n')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            nofk.execute(
                "INSERT INTO app_pipeline_stage_log "
                "(id, pipeline_id, stage, status, created_at) "
                "VALUES ('s3','pp2','research','NOT_VALID','n')"
            )

    def test_default_status_is_pending(self, nofk):
        nofk.execute(
            "INSERT INTO app_pipeline_executions "
            "(id, workspace_id, correlation_id, idempotency_key, "
            "end_stage, actor, created_at, updated_at) "
            "VALUES ('pp3','ws','corr-pp3','idem-pp3','learning','cli','n','n')"
        )
        nofk.execute(
            "INSERT INTO app_pipeline_stage_log "
            "(id, pipeline_id, stage, created_at) VALUES ('s4','pp3','narration','n')"
        )
        row = nofk.execute("SELECT status FROM app_pipeline_stage_log WHERE id='s4'").fetchone()
        assert row[0] == "pending"


# ---------------------------------------------------------------------------
# Schedule definitions schema
# ---------------------------------------------------------------------------


class TestScheduleDefinitionsSchema:
    EXPECTED_COLS = {
        "id",
        "workspace_id",
        "channel_id",
        "name",
        "operation_type",
        "schedule_type",
        "schedule_config_json",
        "timezone",
        "is_active",
        "last_run_at",
        "next_run_at",
        "actor",
        "created_at",
        "updated_at",
    }

    def test_all_columns_present(self, db_conn):
        cols = set(_columns(db_conn, "app_schedule_definitions"))
        assert self.EXPECTED_COLS <= cols

    def test_invalid_schedule_type_rejected(self, nofk):
        with pytest.raises(sqlite3.IntegrityError):
            nofk.execute(
                "INSERT INTO app_schedule_definitions "
                "(id, workspace_id, name, operation_type, schedule_type, "
                "actor, created_at, updated_at) "
                "VALUES ('s1','ws','test','publish','BAD_TYPE','cli','n','n')"
            )

    def test_default_is_active_is_1(self, nofk):
        nofk.execute(
            "INSERT INTO app_schedule_definitions "
            "(id, workspace_id, name, operation_type, schedule_type, "
            "actor, created_at, updated_at) "
            "VALUES ('s2','ws','daily pub','publish','interval','cli','n','n')"
        )
        row = nofk.execute(
            "SELECT is_active FROM app_schedule_definitions WHERE id='s2'"
        ).fetchone()
        assert row[0] == 1

    def test_valid_schedule_types_all_accepted(self, nofk):
        for i, stype in enumerate(["cron", "interval", "once", "after_event"]):
            nofk.execute(
                "INSERT INTO app_schedule_definitions "
                "(id, workspace_id, name, operation_type, schedule_type, "
                "actor, created_at, updated_at) "
                f"VALUES ('st{i}','ws','n','publish','{stype}','cli','n','n')"
            )


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------


class TestAppIndexes:
    REQUIRED = {
        "idx_app_pipeline_workspace",
        "idx_app_pipeline_channel",
        "idx_app_pipeline_corr",
        "idx_app_stage_pipeline",
        "idx_app_schedule_workspace",
    }

    def test_all_required_indexes_exist(self, db_conn):
        found = _index_names(db_conn)
        for idx in self.REQUIRED:
            assert idx in found, f"Missing index: {idx}"


# ---------------------------------------------------------------------------
# Phase 12 tables preserved (regression)
# ---------------------------------------------------------------------------


class TestPhase12TablesPreserved:
    P12 = {
        "cp_organizations",
        "cp_workspaces",
        "cp_channels",
        "cp_platforms",
        "cp_credential_profiles",
        "cp_platform_accounts",
        "cp_automation_policies",
        "cp_events",
        "cp_event_processing",
        "cp_operation_executions",
        "cp_cost_records",
        "cp_budget_policies",
        "cp_health_records",
        "cp_provider_registry",
    }

    def test_all_phase12_tables_still_exist(self, db_conn):
        tables = _table_names(db_conn)
        for t in self.P12:
            assert t in tables, f"Phase 12 table missing after v19 migration: {t}"


# ---------------------------------------------------------------------------
# Incremental migration from v18
# ---------------------------------------------------------------------------


class TestIncrementalMigration:
    def test_v18_db_migrates_to_v19(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = Path(f.name)

        from app.core.database import (
            _DDL_V1,
            _DDL_V2,
            _DDL_V3,
            _DDL_V4_NEW_TABLES,
            _DDL_V5_SCORING,
            _DDL_V6_PROMOTE,
            _DDL_V7_RESEARCH,
            _DDL_V8_CLAIMS,
            _DDL_V9_SCRIPTS,
            _DDL_V10_PRODUCTION,
            _DDL_V11_NARRATION,
            _DDL_V12_CAPTIONS,
            _DDL_V13_SCENES,
            _DDL_V14_RENDERS,
            _DDL_V15_PUBLISHING,
            _DDL_V16_ANALYTICS,
            _DDL_V17_LEARNING,
            _DDL_V18_CONTROL_PLANE,
            _set_version,
        )

        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        for ddl in [
            _DDL_V1,
            _DDL_V2,
            _DDL_V3,
            _DDL_V4_NEW_TABLES,
            _DDL_V5_SCORING,
            _DDL_V6_PROMOTE,
            _DDL_V7_RESEARCH,
            _DDL_V8_CLAIMS,
            _DDL_V9_SCRIPTS,
            _DDL_V10_PRODUCTION,
            _DDL_V11_NARRATION,
            _DDL_V12_CAPTIONS,
            _DDL_V13_SCENES,
            _DDL_V14_RENDERS,
            _DDL_V15_PUBLISHING,
            _DDL_V16_ANALYTICS,
            _DDL_V17_LEARNING,
            _DDL_V18_CONTROL_PLANE,
        ]:
            conn.executescript(ddl)
        _set_version(conn, 18)
        conn.commit()
        conn.close()

        conn = open_db(path)
        ver = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert ver == 20
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "app_pipeline_executions" in tables
        assert "app_pipeline_stage_log" in tables
        assert "app_schedule_definitions" in tables
        conn.close()
        path.unlink(missing_ok=True)
