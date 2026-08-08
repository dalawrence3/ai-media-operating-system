"""Tests for Phase 11 ace learn CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from app.core.database import open_db
from app.learning.cli import learn_app

runner = CliRunner()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    conn = open_db(path)
    conn.execute("INSERT INTO topics (title, angle) VALUES ('Test Topic', 'test angle')")
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def db_with_snapshot(db_path: Path) -> Path:
    """DB with one analytics snapshot and aggregate for CTR."""
    conn = open_db(db_path)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """
        INSERT INTO analytics_snapshots
            (publication_id, publishing_plan_id, publishing_job_id,
             render_manifest_id, scene_manifest_id, production_plan_id,
             script_id, topic_id, narration_run_id, caption_run_id,
             provider, provider_version, adapter_version,
             engine_version, analytics_schema_version, db_schema_version,
             input_hash, raw_metrics_json, ingested_at, created_at)
        VALUES (1,1,1,1,1,1,2,1,3,4,'fake','1.0.0','1.0.0',
                '1.0.0','1.0.0',17,
                'snap_hash','{}',
                strftime('%Y-%m-%dT%H:%M:%S','now'),
                strftime('%Y-%m-%dT%H:%M:%S','now'))
        """
    )
    conn.execute(
        """
        INSERT INTO analytics_aggregates
            (publication_id, topic_id, provider, period_type, period_key,
             metric_name, metric_value, snapshot_count, calculation_method,
             currency_code, source_snapshot_ids_json, input_hash, created_at)
        VALUES (1,1,'fake','lifetime','lifetime','ctr',0.01,1,'sum',NULL,'[1]',
                'agg_ctr_hash',strftime('%Y-%m-%dT%H:%M:%S','now'))
        """
    )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    conn.close()
    return db_path


def _run(args, db_path: Path):
    with patch("app.core.config.get_config") as mock_cfg:
        cfg = MagicMock()
        cfg.db_path = db_path
        cfg.log_level = "WARNING"
        mock_cfg.return_value = cfg
        return runner.invoke(learn_app, args, catch_exceptions=False)


class TestLearnRuns:
    def test_runs_empty(self, db_path):
        result = _run(["runs"], db_path)
        assert result.exit_code == 0
        assert "No learning runs" in result.output

    def test_runs_shows_after_analyze(self, db_with_snapshot):
        _run(["analyze", "1", "--topic", "1"], db_with_snapshot)
        result = _run(["runs", "--topic", "1"], db_with_snapshot)
        assert result.exit_code == 0
        assert "completed" in result.output

    def test_runs_without_topic_filter(self, db_with_snapshot):
        _run(["analyze", "1", "--topic", "1"], db_with_snapshot)
        result = _run(["runs"], db_with_snapshot)
        assert result.exit_code == 0


class TestLearnAnalyze:
    def test_analyze_with_snapshot_succeeds(self, db_with_snapshot):
        result = _run(["analyze", "1", "--topic", "1"], db_with_snapshot)
        assert result.exit_code == 0
        assert "completed" in result.output

    def test_analyze_without_snapshot_fails(self, db_path):
        result = runner.invoke(
            learn_app,
            ["analyze", "999", "--topic", "1"],
            catch_exceptions=False,
        )
        # No mock — will fail because DB doesn't exist at default path,
        # or we use the mock but with no snapshots
        with patch("app.core.config.get_config") as mock_cfg:
            cfg = MagicMock()
            cfg.db_path = db_path
            cfg.log_level = "WARNING"
            mock_cfg.return_value = cfg
            result = runner.invoke(learn_app, ["analyze", "999", "--topic", "1"])
        assert result.exit_code == 1

    def test_analyze_shows_recommendation_count(self, db_with_snapshot):
        result = _run(["analyze", "1", "--topic", "1"], db_with_snapshot)
        assert "Recommendations:" in result.output

    def test_analyze_produces_ctr_recommendations(self, db_with_snapshot):
        _run(["analyze", "1", "--topic", "1"], db_with_snapshot)
        # Verify recommendations were stored
        conn = open_db(db_with_snapshot)
        row = conn.execute("SELECT COUNT(*) FROM optimization_recommendations").fetchone()
        assert row[0] > 0


class TestLearnList:
    def test_list_empty(self, db_path):
        result = _run(["list"], db_path)
        assert result.exit_code == 0
        assert "No recommendations" in result.output

    def test_list_shows_after_analyze(self, db_with_snapshot):
        _run(["analyze", "1", "--topic", "1"], db_with_snapshot)
        result = _run(["list"], db_with_snapshot)
        assert result.exit_code == 0
        assert "pending" in result.output

    def test_list_filter_by_status(self, db_with_snapshot):
        _run(["analyze", "1", "--topic", "1"], db_with_snapshot)
        result = _run(["list", "--status", "pending"], db_with_snapshot)
        assert result.exit_code == 0

    def test_list_filter_by_domain(self, db_with_snapshot):
        _run(["analyze", "1", "--topic", "1"], db_with_snapshot)
        result = _run(["list", "--domain", "scripts"], db_with_snapshot)
        assert result.exit_code == 0


class TestLearnShow:
    def test_show_missing_recommendation_fails(self, db_path):
        result = _run(["show", "999"], db_path)
        assert result.exit_code == 1

    def test_show_valid_recommendation(self, db_with_snapshot):
        _run(["analyze", "1", "--topic", "1"], db_with_snapshot)
        conn = open_db(db_with_snapshot)
        row = conn.execute("SELECT id FROM optimization_recommendations LIMIT 1").fetchone()
        rec_id = row[0]
        conn.close()

        result = _run(["show", str(rec_id)], db_with_snapshot)
        assert result.exit_code == 0
        assert "Recommendation #" in result.output
        assert "Evidence" in result.output
        assert "Why this recommendation exists" in result.output


class TestLearnAccept:
    def _get_first_rec_id(self, db_path: Path) -> int:
        conn = open_db(db_path)
        row = conn.execute("SELECT id FROM optimization_recommendations LIMIT 1").fetchone()
        conn.close()
        return row[0]

    def test_accept_with_reviewer_succeeds(self, db_with_snapshot):
        _run(["analyze", "1", "--topic", "1"], db_with_snapshot)
        rec_id = self._get_first_rec_id(db_with_snapshot)
        result = _run(["accept", str(rec_id), "--reviewer", "operator"], db_with_snapshot)
        assert result.exit_code == 0
        assert "accepted" in result.output

    def test_accept_changes_status_in_db(self, db_with_snapshot):
        _run(["analyze", "1", "--topic", "1"], db_with_snapshot)
        rec_id = self._get_first_rec_id(db_with_snapshot)
        _run(["accept", str(rec_id), "--reviewer", "operator"], db_with_snapshot)
        conn = open_db(db_with_snapshot)
        row = conn.execute(
            "SELECT status FROM optimization_recommendations WHERE id = ?", (rec_id,)
        ).fetchone()
        conn.close()
        assert row[0] == "accepted"


class TestLearnReject:
    def _get_first_rec_id(self, db_path: Path) -> int:
        conn = open_db(db_path)
        row = conn.execute("SELECT id FROM optimization_recommendations LIMIT 1").fetchone()
        conn.close()
        return row[0]

    def test_reject_with_notes_succeeds(self, db_with_snapshot):
        _run(["analyze", "1", "--topic", "1"], db_with_snapshot)
        rec_id = self._get_first_rec_id(db_with_snapshot)
        result = _run(
            ["reject", str(rec_id), "--reviewer", "operator", "--notes", "Not relevant"],
            db_with_snapshot,
        )
        assert result.exit_code == 0
        assert "rejected" in result.output

    def test_reject_changes_status_in_db(self, db_with_snapshot):
        _run(["analyze", "1", "--topic", "1"], db_with_snapshot)
        rec_id = self._get_first_rec_id(db_with_snapshot)
        _run(
            ["reject", str(rec_id), "--reviewer", "operator", "--notes", "Not relevant"],
            db_with_snapshot,
        )
        conn = open_db(db_with_snapshot)
        row = conn.execute(
            "SELECT status FROM optimization_recommendations WHERE id = ?", (rec_id,)
        ).fetchone()
        conn.close()
        assert row[0] == "rejected"


class TestLearnEvents:
    def test_events_empty(self, db_path):
        result = _run(["events"], db_path)
        assert result.exit_code == 0
        assert "No review events" in result.output

    def test_events_shows_after_accept(self, db_with_snapshot):
        _run(["analyze", "1", "--topic", "1"], db_with_snapshot)
        conn = open_db(db_with_snapshot)
        row = conn.execute("SELECT id FROM optimization_recommendations LIMIT 1").fetchone()
        rec_id = row[0]
        conn.close()
        _run(["accept", str(rec_id), "--reviewer", "operator"], db_with_snapshot)
        result = _run(["events"], db_with_snapshot)
        assert result.exit_code == 0
        assert "accepted" in result.output

    def test_events_filter_by_recommendation(self, db_with_snapshot):
        _run(["analyze", "1", "--topic", "1"], db_with_snapshot)
        conn = open_db(db_with_snapshot)
        row = conn.execute("SELECT id FROM optimization_recommendations LIMIT 1").fetchone()
        rec_id = row[0]
        conn.close()
        _run(["accept", str(rec_id), "--reviewer", "operator"], db_with_snapshot)
        result = _run(["events", str(rec_id)], db_with_snapshot)
        assert result.exit_code == 0
