"""Tests for Phase 8 render CLI commands."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from app.cli import app
from app.core.database import open_db

runner = CliRunner()


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "test.db")


def _seed(conn: sqlite3.Connection) -> dict:
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO topics (id, title, angle) VALUES (1, 'T', 'A')")
    conn.execute(
        "INSERT INTO scripts (id, topic_id, version, body, status)"
        " VALUES (1, 1, 1, 'body', 'approved')"
    )
    conn.execute(
        "INSERT INTO production_plans"
        " (id, topic_id, script_id, script_version, input_hash, script_body_hash,"
        "  plan_schema_version, renderer_version, duration_algorithm_version,"
        "  status, created_at, updated_at)"
        " VALUES (1, 1, 1, 1, 'ph', 'bh', 'v1', 'rv1', 'dv1',"
        "  'approved', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO narration_runs"
        " (id, plan_id, plan_input_hash, voice_profile_id, voice_profile_version,"
        "  language, speaking_rate, settings_json, output_format, sample_rate_hz,"
        "  input_hash, status, created_at, updated_at)"
        " VALUES (1, 1, 'ph', 1, 1,"
        "  'en-US', 1.0, '{}', 'wav', 22050,"
        "  'nrh', 'approved', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO caption_runs"
        " (id, narration_run_id, plan_id, script_id, topic_id,"
        "  input_hash, caption_schema_version, segmentation_version,"
        "  timing_algorithm_version, style_version, exporter_version, language,"
        "  status, total_cue_count, total_duration_ms, created_at, updated_at)"
        " VALUES (1, 1, 1, 1, 1,"
        "  'crh', 'csv1', 'sgv1', 'tav1', 'stv1', 'exv1', 'en-US',"
        "  'approved', 2, 8000, '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO scene_manifests"
        " (id, caption_run_id, narration_run_id, plan_id, script_id, topic_id,"
        "  input_hash, manifest_schema_version, planner_version,"
        "  status, total_scene_count, total_asset_count, total_duration_ms,"
        "  approved_at, created_at, updated_at)"
        " VALUES (1, 1, 1, 1, 1, 1,"
        "  'smh', 'Manifest-v1', 'planner-1.0.0',"
        "  'approved', 0, 0, 15000,"
        "  '2024-01-01T00:00:00', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO render_manifests"
        " (id, scene_manifest_id, narration_run_id, caption_run_id,"
        "  topic_id, plan_id, script_id, input_hash,"
        "  render_schema_version, compositor_version,"
        "  total_scene_count, total_duration_ms, width, height, fps,"
        "  status, created_at, updated_at)"
        " VALUES (1, 1, 1, 1,"
        "  1, 1, 1, 'rmh',"
        "  'Render-v1', 'compositor-1.0.0',"
        "  3, 15000, 1080, 1920, 30,"
        "  'draft', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()


def _invoke(args: list[str], db_path: str) -> object:
    with patch("app.core.config.get_config") as mock_cfg:
        cfg = MagicMock()
        cfg.db_path = Path(db_path)
        cfg.log_level = "WARNING"
        cfg.artifacts_path = str(Path(db_path).parent / "artifacts")
        mock_cfg.return_value = cfg
        return runner.invoke(app, args, catch_exceptions=False)


class TestRenderListCommand:
    def test_list_empty(self, tmp_path, db):
        result = _invoke(["render", "list"], str(tmp_path / "test.db"))
        assert result.exit_code == 0
        assert "No render manifests" in result.output

    def test_list_with_manifest(self, tmp_path, db):
        _seed(db)
        result = _invoke(["render", "list"], str(tmp_path / "test.db"))
        assert result.exit_code == 0
        assert "id=1" in result.output
        assert "draft" in result.output

    def test_list_filter_by_topic(self, tmp_path, db):
        _seed(db)
        result = _invoke(
            ["render", "list", "--topic-id", "1"], str(tmp_path / "test.db")
        )
        assert result.exit_code == 0
        assert "id=1" in result.output


class TestRenderShowCommand:
    def test_show_missing_manifest(self, tmp_path, db):
        result = _invoke(["render", "show", "999"], str(tmp_path / "test.db"))
        assert result.exit_code == 1

    def test_show_existing_manifest(self, tmp_path, db):
        _seed(db)
        result = _invoke(["render", "show", "1"], str(tmp_path / "test.db"))
        assert result.exit_code == 0
        assert "Render manifest id=1" in result.output
        assert "draft" in result.output
        assert "1080x1920" in result.output


class TestRenderApproveCommand:
    def test_approve_manifest(self, tmp_path, db):
        _seed(db)
        result = _invoke(
            ["render", "approve", "1", "--actor", "alice"],
            str(tmp_path / "test.db"),
        )
        assert result.exit_code == 0
        assert "approved" in result.output.lower()

    def test_approve_nonexistent_manifest(self, tmp_path, db):
        result = _invoke(["render", "approve", "999"], str(tmp_path / "test.db"))
        assert result.exit_code == 1


class TestRenderRejectCommand:
    def test_reject_manifest(self, tmp_path, db):
        _seed(db)
        result = _invoke(
            [
                "render", "reject", "1",
                "--reason", "visual_quality",
                "--severity", "3",
                "--actor", "bob",
            ],
            str(tmp_path / "test.db"),
        )
        assert result.exit_code == 0
        assert "rejected" in result.output.lower()

    def test_reject_already_approved_fails(self, tmp_path, db):
        _seed(db)
        _invoke(["render", "approve", "1"], str(tmp_path / "test.db"))
        result = _invoke(["render", "reject", "1"], str(tmp_path / "test.db"))
        assert result.exit_code == 1


class TestRenderEventsCommand:
    def test_no_events(self, tmp_path, db):
        _seed(db)
        result = _invoke(["render", "events", "1"], str(tmp_path / "test.db"))
        assert result.exit_code == 0
        assert "No review events" in result.output

    def test_events_after_approval(self, tmp_path, db):
        _seed(db)
        _invoke(["render", "approve", "1", "--actor", "alice"], str(tmp_path / "test.db"))
        result = _invoke(["render", "events", "1"], str(tmp_path / "test.db"))
        assert result.exit_code == 0
        assert "render_approved" in result.output
        assert "alice" in result.output


class TestRenderStartCommandNoFFmpeg:
    def test_exits_gracefully_when_ffmpeg_missing(self, tmp_path, db):
        _seed(db)
        with patch("app.media.backend.shutil.which", return_value=None):
            result = _invoke(["render", "start", "1"], str(tmp_path / "test.db"))
        assert result.exit_code == 1
        assert "ffmpeg" in result.output.lower()


class TestRenderValidateCommandNoFFprobe:
    def test_exits_gracefully_when_ffprobe_missing(self, tmp_path, db):
        _seed(db)
        with patch("app.media.validator.shutil.which", return_value=None):
            result = _invoke(["render", "validate", "1"], str(tmp_path / "test.db"))
        assert result.exit_code == 1
        assert "ffprobe" in result.output.lower()
