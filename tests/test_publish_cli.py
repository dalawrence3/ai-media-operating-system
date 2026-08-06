"""Tests for the ace publish CLI commands."""

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


def _invoke(args: list[str], db_path: str) -> object:
    with patch("app.core.config.get_config") as mock_cfg:
        cfg = MagicMock()
        cfg.db_path = Path(db_path)
        cfg.log_level = "WARNING"
        cfg.artifacts_path = str(Path(db_path).parent / "artifacts")
        mock_cfg.return_value = cfg
        return runner.invoke(app, args, catch_exceptions=False)


def _seed(conn: sqlite3.Connection) -> None:
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
        "  'smh', 'Mv1', 'pv1',"
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
        "  'Rv1', 'cv1',"
        "  0, 15000, 1080, 1920, 30,"
        "  'approved', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "UPDATE render_manifests SET approved_at='2024-01-01T00:00:00' WHERE id=1"
    )
    conn.execute(
        "INSERT INTO render_jobs"
        " (id, render_manifest_id, backend, backend_version,"
        "  status, output_path, output_sha256, duration_s,"
        "  width, height, fps, video_codec, audio_codec,"
        "  created_at, updated_at)"
        " VALUES (1, 1, 'ffmpeg', '1.0', 'completed',"
        "  '/tmp/out.mp4', 'sha256out', 15.0,"
        "  1080, 1920, 30, 'h264', 'aac',"
        "  '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()


class TestPublishListCommand:
    def test_list_empty(self, tmp_path, db):
        result = _invoke(["publish", "list"], str(tmp_path / "test.db"))
        assert result.exit_code == 0
        assert "No publishing plans" in result.output

    def test_list_after_prepare(self, tmp_path, db):
        _seed(db)
        _invoke(
            ["publish", "prepare", "1", "--title", "Test Video"],
            str(tmp_path / "test.db"),
        )
        result = _invoke(["publish", "list"], str(tmp_path / "test.db"))
        assert result.exit_code == 0
        assert "manifest=1" in result.output


class TestPublishPrepareCommand:
    def test_prepare_succeeds(self, tmp_path, db):
        _seed(db)
        result = _invoke(
            ["publish", "prepare", "1", "--title", "My Video"],
            str(tmp_path / "test.db"),
        )
        assert result.exit_code == 0
        assert "Created publishing plan" in result.output

    def test_prepare_idempotent(self, tmp_path, db):
        _seed(db)
        _invoke(
            ["publish", "prepare", "1", "--title", "My Video"],
            str(tmp_path / "test.db"),
        )
        result = _invoke(
            ["publish", "prepare", "1", "--title", "My Video"],
            str(tmp_path / "test.db"),
        )
        assert result.exit_code == 0
        assert "Idempotent" in result.output

    def test_prepare_nonexistent_manifest(self, tmp_path, db):
        result = _invoke(
            ["publish", "prepare", "999", "--title", "T"],
            str(tmp_path / "test.db"),
        )
        assert result.exit_code == 1

    def test_prepare_with_tags(self, tmp_path, db):
        _seed(db)
        result = _invoke(
            ["publish", "prepare", "1", "--title", "T", "--tags", "a,b,c"],
            str(tmp_path / "test.db"),
        )
        assert result.exit_code == 0

    def test_prepare_with_visibility(self, tmp_path, db):
        _seed(db)
        result = _invoke(
            ["publish", "prepare", "1", "--title", "T", "--visibility", "unlisted"],
            str(tmp_path / "test.db"),
        )
        assert result.exit_code == 0

    def test_prepare_invalid_visibility(self, tmp_path, db):
        _seed(db)
        result = _invoke(
            ["publish", "prepare", "1", "--title", "T", "--visibility", "secret"],
            str(tmp_path / "test.db"),
        )
        assert result.exit_code == 1


class TestPublishShowCommand:
    def test_show_not_found(self, tmp_path, db):
        result = _invoke(["publish", "show", "999"], str(tmp_path / "test.db"))
        assert result.exit_code == 1

    def test_show_existing(self, tmp_path, db):
        _seed(db)
        _invoke(
            ["publish", "prepare", "1", "--title", "My Video"],
            str(tmp_path / "test.db"),
        )
        result = _invoke(["publish", "show", "1"], str(tmp_path / "test.db"))
        assert result.exit_code == 0
        assert "Publishing plan id=1" in result.output
        assert "My Video" in result.output


class TestPublishStartCommand:
    def test_start_dry_run(self, tmp_path, db):
        _seed(db)
        _invoke(
            ["publish", "prepare", "1", "--title", "T"],
            str(tmp_path / "test.db"),
        )
        result = _invoke(
            ["publish", "start", "1", "--dry-run"],
            str(tmp_path / "test.db"),
        )
        assert result.exit_code == 0
        assert "dry-run" in result.output

    def test_start_not_found(self, tmp_path, db):
        result = _invoke(["publish", "start", "999"], str(tmp_path / "test.db"))
        assert result.exit_code == 1

    def test_start_succeeds(self, tmp_path, db):
        _seed(db)
        _invoke(
            ["publish", "prepare", "1", "--title", "T"],
            str(tmp_path / "test.db"),
        )
        result = _invoke(["publish", "start", "1"], str(tmp_path / "test.db"))
        assert result.exit_code == 0
        assert "completed" in result.output


class TestPublishApproveCommand:
    def test_approve_plan(self, tmp_path, db):
        _seed(db)
        _invoke(
            ["publish", "prepare", "1", "--title", "T"],
            str(tmp_path / "test.db"),
        )
        result = _invoke(
            ["publish", "approve", "1", "--actor", "alice"],
            str(tmp_path / "test.db"),
        )
        assert result.exit_code == 0
        assert "approved" in result.output.lower()

    def test_approve_not_found(self, tmp_path, db):
        result = _invoke(["publish", "approve", "999"], str(tmp_path / "test.db"))
        assert result.exit_code == 1

    def test_double_approve_blocked(self, tmp_path, db):
        _seed(db)
        _invoke(
            ["publish", "prepare", "1", "--title", "T"],
            str(tmp_path / "test.db"),
        )
        _invoke(["publish", "approve", "1"], str(tmp_path / "test.db"))
        result = _invoke(["publish", "approve", "1"], str(tmp_path / "test.db"))
        assert result.exit_code == 1


class TestPublishRejectCommand:
    def test_reject_plan(self, tmp_path, db):
        _seed(db)
        _invoke(
            ["publish", "prepare", "1", "--title", "T"],
            str(tmp_path / "test.db"),
        )
        result = _invoke(
            ["publish", "reject", "1", "--reason", "quality"],
            str(tmp_path / "test.db"),
        )
        assert result.exit_code == 0
        assert "rejected" in result.output.lower()

    def test_reject_not_found(self, tmp_path, db):
        result = _invoke(["publish", "reject", "999"], str(tmp_path / "test.db"))
        assert result.exit_code == 1


class TestPublishEventsCommand:
    def test_events_empty(self, tmp_path, db):
        _seed(db)
        _invoke(
            ["publish", "prepare", "1", "--title", "T"],
            str(tmp_path / "test.db"),
        )
        result = _invoke(["publish", "events", "1"], str(tmp_path / "test.db"))
        assert result.exit_code == 0
        # plan_prepared event is recorded on prepare
        assert "plan_prepared" in result.output

    def test_events_after_approve(self, tmp_path, db):
        _seed(db)
        _invoke(
            ["publish", "prepare", "1", "--title", "T"],
            str(tmp_path / "test.db"),
        )
        _invoke(
            ["publish", "approve", "1", "--actor", "qa"],
            str(tmp_path / "test.db"),
        )
        result = _invoke(["publish", "events", "1"], str(tmp_path / "test.db"))
        assert result.exit_code == 0
        assert "plan_approved" in result.output
        assert "qa" in result.output

    def test_events_not_found(self, tmp_path, db):
        result = _invoke(["publish", "events", "999"], str(tmp_path / "test.db"))
        assert result.exit_code == 1


class TestPublishDoctorCommand:
    def test_doctor_fake_ok(self, tmp_path, db):
        result = _invoke(["publish", "doctor"], str(tmp_path / "test.db"))
        assert result.exit_code == 0
        assert "fake" in result.output.lower()
        assert "OK" in result.output

    def test_doctor_youtube_provider(self, tmp_path, db):
        result = _invoke(
            ["publish", "doctor", "--provider", "youtube"],
            str(tmp_path / "test.db"),
        )
        assert result.exit_code == 0
        assert "youtube" in result.output.lower()
        assert "OAuth" in result.output


class TestPublishScheduleCommand:
    def test_schedule_plan(self, tmp_path, db):
        _seed(db)
        _invoke(
            ["publish", "prepare", "1", "--title", "T"],
            str(tmp_path / "test.db"),
        )
        result = _invoke(
            ["publish", "schedule", "1", "--at", "2099-06-01T12:00:00+00:00"],
            str(tmp_path / "test.db"),
        )
        assert result.exit_code == 0
        assert "2099-06-01" in result.output

    def test_schedule_not_found(self, tmp_path, db):
        result = _invoke(
            ["publish", "schedule", "999", "--at", "2099-06-01T12:00:00+00:00"],
            str(tmp_path / "test.db"),
        )
        assert result.exit_code == 1


class TestPublishCancelCommand:
    def test_cancel_no_active_job_raises(self, tmp_path, db):
        _seed(db)
        _invoke(
            ["publish", "prepare", "1", "--title", "T"],
            str(tmp_path / "test.db"),
        )
        result = _invoke(["publish", "cancel", "1"], str(tmp_path / "test.db"))
        assert result.exit_code == 1
        assert "no active job" in result.output.lower()

    def test_cancel_not_found(self, tmp_path, db):
        result = _invoke(["publish", "cancel", "999"], str(tmp_path / "test.db"))
        assert result.exit_code == 1
