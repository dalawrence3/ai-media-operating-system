"""Tests for the analytics database migration."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core.database import SCHEMA_VERSION, open_db


class TestSchemaMigration:
    def test_schema_version_is_22(self):
        assert SCHEMA_VERSION == 22

    def test_fresh_db_at_current_version(self, tmp_path: Path):
        conn = open_db(tmp_path / "test.db")
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert version == SCHEMA_VERSION

    def test_analytics_tables_created(self, tmp_path: Path):
        conn = open_db(tmp_path / "test.db")
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "analytics_snapshots" in tables
        assert "analytics_metrics" in tables
        assert "analytics_aggregates" in tables
        assert "analytics_review_events" in tables

    def test_analytics_snapshot_columns(self, tmp_path: Path):
        conn = open_db(tmp_path / "test.db")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(analytics_snapshots)").fetchall()}
        expected = {
            "id",
            "publication_id",
            "publishing_plan_id",
            "publishing_job_id",
            "render_manifest_id",
            "scene_manifest_id",
            "production_plan_id",
            "script_id",
            "topic_id",
            "narration_run_id",
            "caption_run_id",
            "experiment_id",
            "provider",
            "provider_version",
            "adapter_version",
            "engine_version",
            "analytics_schema_version",
            "db_schema_version",
            "input_hash",
            "raw_metrics_json",
            "period_start",
            "period_end",
            "ingested_at",
            "created_at",
        }
        assert expected <= cols

    def test_analytics_metrics_columns(self, tmp_path: Path):
        conn = open_db(tmp_path / "test.db")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(analytics_metrics)").fetchall()}
        expected = {
            "id",
            "snapshot_id",
            "publication_id",
            "topic_id",
            "provider",
            "metric_name",
            "metric_value",
            "period_start",
            "period_end",
            "input_hash",
            "created_at",
        }
        assert expected <= cols

    def test_analytics_aggregates_unique_constraint(self, tmp_path: Path):
        conn = open_db(tmp_path / "test.db")
        conn.execute(
            "INSERT INTO analytics_aggregates "
            "(publication_id,topic_id,provider,period_type,period_key,metric_name,"
            " metric_value,snapshot_count,input_hash,created_at) "
            "VALUES (1,1,'fake','lifetime','all','views',100.0,1,'h1','2026-01-01')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO analytics_aggregates "
                "(publication_id,topic_id,provider,period_type,period_key,metric_name,"
                " metric_value,snapshot_count,input_hash,created_at) "
                "VALUES (1,1,'fake','lifetime','all','views',200.0,2,'h2','2026-01-01')"
            )
            conn.commit()

    def test_analytics_review_events_severity_check(self, tmp_path: Path):
        conn = open_db(tmp_path / "test.db")
        conn.execute(
            "INSERT INTO analytics_snapshots "
            "(publication_id,publishing_plan_id,publishing_job_id,"
            " render_manifest_id,scene_manifest_id,production_plan_id,"
            " script_id,topic_id,narration_run_id,caption_run_id,"
            " provider,provider_version,adapter_version,engine_version,"
            " analytics_schema_version,db_schema_version,"
            " input_hash,raw_metrics_json,ingested_at,created_at) "
            "VALUES (1,1,1,1,1,1,1,1,1,1,"
            " 'fake','1.0.0','1.0.0','1.0.0','1.0.0',16,"
            " 'h1','{}','2026-01-01','2026-01-01')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO analytics_review_events "
                "(snapshot_id,severity,notes,reviewer,input_hash,created_at) "
                "VALUES (1,'invalid_severity','','','rh1','2026-01-01')"
            )
            conn.commit()

    def test_v15_to_v16_migration(self, tmp_path: Path):
        """Simulate existing v15 database being migrated to v16."""
        db_path = tmp_path / "v15.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (15)")
        # Create minimal publishing tables that v15 would have
        conn.execute(
            "CREATE TABLE publishing_plans (id INTEGER PRIMARY KEY, "
            "render_manifest_id INTEGER, render_job_id INTEGER, topic_id INTEGER, "
            "production_plan_id INTEGER, script_id INTEGER, scene_manifest_id INTEGER, "
            "narration_run_id INTEGER, caption_run_id INTEGER, experiment_id TEXT, "
            "input_hash TEXT UNIQUE, publishing_engine_version TEXT, "
            "metadata_version TEXT, provider TEXT, provider_version TEXT, "
            "title TEXT, description TEXT, tags_json TEXT, language TEXT, "
            "category TEXT, visibility TEXT, made_for_kids INTEGER, "
            "playlist_id TEXT, thumbnail_path TEXT, captions_path TEXT, "
            "copyright_notice TEXT, licensing_notes TEXT, publication_notes TEXT, "
            "schedule_type TEXT, scheduled_at TEXT, timezone TEXT, "
            "status TEXT, approved_at TEXT, rejected_at TEXT, rejection_reason TEXT, "
            "superseded_at TEXT, superseded_by_id INTEGER, "
            "created_at TEXT, updated_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE publishing_jobs (id INTEGER PRIMARY KEY, "
            "publishing_plan_id INTEGER, attempt_number INTEGER, provider TEXT, "
            "provider_version TEXT, status TEXT, error_message TEXT, "
            "retry_count INTEGER, retry_after TEXT, started_at TEXT, "
            "completed_at TEXT, created_at TEXT, updated_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE publications (id INTEGER PRIMARY KEY, "
            "publishing_plan_id INTEGER, publishing_job_id INTEGER, "
            "provider TEXT, provider_version TEXT, provider_video_id TEXT, "
            "provider_url TEXT, provider_status_json TEXT, status TEXT, "
            "error_message TEXT, visibility TEXT, scheduled_at TEXT, "
            "published_at TEXT, deleted_at TEXT, publishing_engine_version TEXT, "
            "input_hash TEXT, output_sha256 TEXT, created_at TEXT, updated_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE publishing_review_events (id INTEGER PRIMARY KEY, "
            "publishing_plan_id INTEGER, publishing_job_id INTEGER, "
            "publication_id INTEGER, topic_id INTEGER, production_plan_id INTEGER, "
            "script_id INTEGER, render_manifest_id INTEGER, provider TEXT, "
            "experiment_id TEXT, event_type TEXT, reason_code TEXT, severity INTEGER, "
            "notes TEXT, actor TEXT, expected_correction TEXT, created_at TEXT)"
        )
        conn.commit()
        conn.close()

        # Now open with the current engine — it should migrate
        conn = open_db(db_path)
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert version == SCHEMA_VERSION
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "analytics_snapshots" in tables
