"""Tests for the analytics CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from app.analytics.cli import analytics_app
from app.analytics.providers.fake import FakeAnalyticsProvider
from app.core.database import open_db

runner = CliRunner()


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    conn = open_db(path)
    # Pre-insert publications + render_manifest so eligibility check passes.
    # FK constraints disabled because upstream rows are not needed here.
    conn.execute("PRAGMA foreign_keys = OFF")
    # Render manifest id=1: approved, not superseded
    conn.execute(
        "INSERT OR IGNORE INTO render_manifests "
        "(id, scene_manifest_id, narration_run_id, caption_run_id, topic_id, "
        "plan_id, script_id, input_hash, render_schema_version, compositor_version, "
        "status, created_at, updated_at) "
        "VALUES (1, 1, 1, 1, 1, 1, 1, 'rmh1', '1.0.0', '1.0.0', 'approved', "
        "datetime('now'), datetime('now'))",
    )
    for pub_id, vid in [(1, "vid123"), (2, "vid456")]:
        conn.execute(
            "INSERT OR IGNORE INTO publications "
            "(id, publishing_plan_id, publishing_job_id, provider, provider_version, "
            "provider_video_id, status, publishing_engine_version, input_hash, "
            "output_sha256, created_at, updated_at) "
            "VALUES (?, 1, 1, 'fake', '1.0.0', ?, 'published', '1.0.0', ?, ?, "
            "datetime('now'), datetime('now'))",
            (pub_id, vid, f"h{pub_id}", f"s{pub_id}"),
        )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    conn.close()
    return path


def _run(args, db_path: Path):
    with patch("app.core.config.get_config") as mock_cfg:
        cfg = MagicMock()
        cfg.db_path = db_path
        cfg.log_level = "WARNING"
        mock_cfg.return_value = cfg
        with patch("app.analytics.cli._default_provider", return_value=FakeAnalyticsProvider()):
            return runner.invoke(analytics_app, args, catch_exceptions=False)


def _ingest_args(publication_id=1):
    return [
        "ingest",
        str(publication_id),
        "vid123",
        "--plan",
        "1",
        "--job",
        "1",
        "--render",
        "1",
        "--scene",
        "1",
        "--production",
        "1",
        "--script",
        "1",
        "--topic",
        "1",
        "--narration",
        "1",
        "--caption",
        "1",
    ]


class TestIngestCommand:
    def test_ingest_succeeds(self, db_path):
        result = _run(_ingest_args(), db_path)
        assert result.exit_code == 0
        assert "Snapshot" in result.output

    def test_ingest_shows_metric_count(self, db_path):
        result = _run(_ingest_args(), db_path)
        assert "metrics=" in result.output

    def test_ingest_idempotent(self, db_path):
        r1 = _run(_ingest_args(), db_path)
        r2 = _run(_ingest_args(), db_path)
        assert r1.exit_code == 0
        assert r2.exit_code == 0


class TestSnapshotCommand:
    def test_snapshot_shows_details(self, db_path):
        _run(_ingest_args(), db_path)
        result = _run(["snapshot", "1"], db_path)
        assert result.exit_code == 0
        assert "publication_id" in result.output

    def test_snapshot_missing_exits_1(self, db_path):
        result = _run(["snapshot", "99999"], db_path)
        assert result.exit_code == 1


class TestMetricsCommand:
    def test_metrics_lists_after_ingest(self, db_path):
        _run(_ingest_args(), db_path)
        result = _run(["metrics", "1"], db_path)
        assert result.exit_code == 0

    def test_metrics_filter_by_name(self, db_path):
        _run(_ingest_args(), db_path)
        result = _run(["metrics", "1", "--metric", "views"], db_path)
        assert result.exit_code == 0

    def test_metrics_empty(self, db_path):
        result = _run(["metrics", "99999"], db_path)
        assert result.exit_code == 0
        assert "No metrics" in result.output


class TestAggregateCommand:
    def test_aggregate_succeeds(self, db_path):
        _run(_ingest_args(), db_path)
        result = _run(["aggregate", "1", "--topic", "1"], db_path)
        assert result.exit_code == 0
        assert "Aggregated" in result.output


class TestShowCommand:
    def test_show_after_aggregate(self, db_path):
        _run(_ingest_args(), db_path)
        _run(["aggregate", "1", "--topic", "1"], db_path)
        result = _run(["show", "1"], db_path)
        assert result.exit_code == 0

    def test_show_empty_before_aggregate(self, db_path):
        result = _run(["show", "99999"], db_path)
        assert result.exit_code == 0
        assert "No aggregates" in result.output


class TestListCommand:
    def test_list_empty(self, db_path):
        result = _run(["list"], db_path)
        assert result.exit_code == 0
        assert "No snapshots" in result.output

    def test_list_after_ingest(self, db_path):
        _run(_ingest_args(), db_path)
        result = _run(["list"], db_path)
        assert result.exit_code == 0
        assert "fake" in result.output

    def test_list_filter_by_publication(self, db_path):
        _run(_ingest_args(publication_id=1), db_path)
        _run(_ingest_args(publication_id=2), db_path)
        result = _run(["list", "--publication", "1"], db_path)
        assert result.exit_code == 0


class TestEventsCommand:
    def test_events_empty(self, db_path):
        result = _run(["events"], db_path)
        assert result.exit_code == 0
        assert "No events" in result.output

    def test_events_after_review(self, db_path):
        from app.analytics.orchestrator import AnalyticsOrchestrator

        conn = open_db(db_path)
        orch = AnalyticsOrchestrator(conn, FakeAnalyticsProvider())
        snap, _ = orch.ingest(
            provider_video_id="vid123",
            publication_id=1,
            publishing_plan_id=1,
            publishing_job_id=1,
            render_manifest_id=1,
            scene_manifest_id=1,
            production_plan_id=1,
            script_id=1,
            topic_id=1,
            narration_run_id=1,
            caption_run_id=1,
        )
        orch.record_review(snapshot_id=snap.id, severity="info", notes="ok")
        conn.close()

        result = _run(["events", str(snap.id)], db_path)
        assert result.exit_code == 0


class TestDoctorCommand:
    def test_doctor_ok_for_fake(self, db_path):
        result = _run(["doctor"], db_path)
        assert result.exit_code == 0
        assert "OK" in result.output
