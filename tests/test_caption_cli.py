"""Tests for the captions CLI subcommand (Phase 6 M6.3A)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import app
from app.core.config import reset_config

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACE_DB_PATH", str(tmp_path / "test.db"))
    reset_config()
    yield
    reset_config()


def _seed_approved_narration(tmp_path: Path) -> None:
    from app.core.database import open_db

    conn = open_db(tmp_path / "test.db")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO topics (id, title, angle) VALUES (1, 'T', 'A')")
    conn.execute(
        "INSERT INTO scripts (id, topic_id, version, body, status)"
        " VALUES (1, 1, 1, 'body', 'approved')"
    )
    conn.execute(
        "INSERT INTO voice_profiles"
        " (id, provider, model, voice_id, name, language, speaking_rate)"
        " VALUES (1, 'mock', 'm1', 'v1', 'Voice', 'en-US', 1.0)"
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
        "INSERT INTO production_segments"
        " (id, plan_id, segment_index, section_index, section_type,"
        "  narration_text, estimated_duration_s, created_at)"
        " VALUES (1, 1, 0, 0, 'hook',"
        "  'Scientists discovered a new species of fish. It lives very deep.', 4, '2024-01-01')"
    )
    conn.execute(
        "INSERT INTO narration_runs"
        " (id, plan_id, plan_input_hash, voice_profile_id, voice_profile_version,"
        "  language, speaking_rate, settings_json, output_format, sample_rate_hz,"
        "  input_hash, status, approved_at, created_at, updated_at)"
        " VALUES (1, 1, 'ph', 1, 1, 'en-US', 1.0, '{}', 'wav', 22050,"
        "  'nr-hash', 'approved', '2024-01-01T00:00:00', '2024-01-01', '2024-01-01')"
    )
    conn.execute(
        "INSERT INTO narration_segment_assets"
        " (id, run_id, segment_id, narration_text_hash, provider, model, voice_id,"
        "  voice_profile_id, voice_profile_version, language, speaking_rate,"
        "  settings_json_hash, output_format, sample_rate_hz, input_hash, status,"
        "  audio_path, audio_sha256, duration_seconds, created_at, updated_at)"
        " VALUES (1, 1, 1, 'th1', 'mock', 'm1', 'v1', 1, 1, 'en-US', 1.0,"
        "  'sh1', 'wav', 22050, 'ah', 'synthesized',"
        "  'narration/plan_1/run_1/segment_1.wav', 'abc123', 4.0,"
        "  '2024-01-01', '2024-01-01')"
    )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    conn.close()


class TestCaptionsGenerate:
    def test_generate_exits_zero(self, tmp_path: Path) -> None:
        _seed_approved_narration(tmp_path)
        result = runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "1",
             "--artifacts-path", str(tmp_path / "arts")],
        )
        assert result.exit_code == 0, result.output

    def test_generate_outputs_run_info(self, tmp_path: Path) -> None:
        _seed_approved_narration(tmp_path)
        result = runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "1",
             "--artifacts-path", str(tmp_path / "arts")],
        )
        assert "status=completed" in result.output
        assert "SRT" in result.output
        assert "VTT" in result.output
        assert "JSON" in result.output

    def test_generate_unknown_plan_exits_one(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "9999",
             "--artifacts-path", str(tmp_path / "arts")],
        )
        assert result.exit_code == 1


class TestCaptionsRuns:
    def test_no_runs_shows_message(self, tmp_path: Path) -> None:
        _seed_approved_narration(tmp_path)
        result = runner.invoke(app, ["captions", "runs", "1"])
        assert result.exit_code == 0
        assert "No caption runs for plan id=1" in result.output

    def test_lists_run_after_generate(self, tmp_path: Path) -> None:
        _seed_approved_narration(tmp_path)
        runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "1",
             "--artifacts-path", str(tmp_path / "arts")],
        )
        result = runner.invoke(app, ["captions", "runs", "1"])
        assert "status=completed" in result.output


class TestCaptionsApprove:
    def test_approve_completed_run(self, tmp_path: Path) -> None:
        _seed_approved_narration(tmp_path)
        runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "1",
             "--artifacts-path", str(tmp_path / "arts")],
        )
        result = runner.invoke(app, ["captions", "approve", "1"])
        assert result.exit_code == 0
        assert "approved" in result.output

    def test_approve_non_existent_run_exits_one(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["captions", "approve", "9999"])
        assert result.exit_code == 1


class TestCaptionsRejectRun:
    def test_reject_completed_run(self, tmp_path: Path) -> None:
        _seed_approved_narration(tmp_path)
        runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "1",
             "--artifacts-path", str(tmp_path / "arts")],
        )
        result = runner.invoke(app, ["captions", "reject-run", "1", "--reason-code", "timing"])
        assert result.exit_code == 0
        assert "rejected" in result.output

    def test_reject_non_existent_run_exits_one(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["captions", "reject-run", "9999", "--reason-code", "timing"])
        assert result.exit_code == 1


class TestCaptionsRejectCue:
    def test_reject_cue(self, tmp_path: Path) -> None:
        from app.captions.repository import get_caption_cues
        from app.core.database import open_db

        _seed_approved_narration(tmp_path)
        runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "1",
             "--artifacts-path", str(tmp_path / "arts")],
        )
        conn = open_db(tmp_path / "test.db")
        cues = get_caption_cues(conn, run_id=1)
        conn.close()
        assert cues, "No cues to reject"
        result = runner.invoke(
            app,
            ["captions", "reject-cue", "1", str(cues[0].id),
             "--reason-code", "timing"],
        )
        assert result.exit_code == 0
        assert "cue_rejected" in result.output or "rejection recorded" in result.output


class TestCaptionsEvents:
    def test_no_events_shows_message(self, tmp_path: Path) -> None:
        _seed_approved_narration(tmp_path)
        runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "1",
             "--artifacts-path", str(tmp_path / "arts")],
        )
        result = runner.invoke(app, ["captions", "events", "1"])
        assert result.exit_code == 0
        assert "No review events" in result.output

    def test_events_shown_after_approve(self, tmp_path: Path) -> None:
        _seed_approved_narration(tmp_path)
        runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "1",
             "--artifacts-path", str(tmp_path / "arts")],
        )
        runner.invoke(app, ["captions", "approve", "1"])
        result = runner.invoke(app, ["captions", "events", "1"])
        assert "run_approved" in result.output


class TestCaptionsGenerateDryRun:
    def test_dry_run_exits_zero(self, tmp_path: Path) -> None:
        _seed_approved_narration(tmp_path)
        result = runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "1",
             "--artifacts-path", str(tmp_path / "arts"), "--dry-run"],
        )
        assert result.exit_code == 0, result.output

    def test_dry_run_reports_cues_and_no_writes(self, tmp_path: Path) -> None:
        _seed_approved_narration(tmp_path)
        arts = tmp_path / "arts"
        result = runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "1",
             "--artifacts-path", str(arts), "--dry-run"],
        )
        assert "dry-run" in result.output
        assert "cues=" in result.output
        assert "Validation passed" in result.output
        # No files should have been written
        assert not arts.exists() or not any(arts.rglob("*.srt"))

    def test_dry_run_unknown_plan_exits_one(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "9999",
             "--artifacts-path", str(tmp_path / "arts"), "--dry-run"],
        )
        assert result.exit_code == 1

    def test_dry_run_no_db_row_created(self, tmp_path: Path) -> None:
        from app.captions.repository import list_caption_runs_by_plan
        from app.core.database import open_db

        _seed_approved_narration(tmp_path)
        runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "1",
             "--artifacts-path", str(tmp_path / "arts"), "--dry-run"],
        )
        conn = open_db(tmp_path / "test.db")
        runs = list_caption_runs_by_plan(conn, 1)
        conn.close()
        assert runs == [], "dry-run must not create any caption_runs row"


class TestCaptionsShow:
    def test_show_exits_zero(self, tmp_path: Path) -> None:
        _seed_approved_narration(tmp_path)
        runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "1",
             "--artifacts-path", str(tmp_path / "arts")],
        )
        result = runner.invoke(app, ["captions", "show", "1"])
        assert result.exit_code == 0, result.output

    def test_show_displays_metadata(self, tmp_path: Path) -> None:
        _seed_approved_narration(tmp_path)
        runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "1",
             "--artifacts-path", str(tmp_path / "arts")],
        )
        result = runner.invoke(app, ["captions", "show", "1"])
        assert "status" in result.output
        assert "narration_run" in result.output
        assert "plan" in result.output
        assert "input_hash" in result.output
        assert "cues" in result.output

    def test_show_displays_cue_timeline(self, tmp_path: Path) -> None:
        _seed_approved_narration(tmp_path)
        runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "1",
             "--artifacts-path", str(tmp_path / "arts")],
        )
        result = runner.invoke(app, ["captions", "show", "1"])
        # Cue timeline header should appear
        assert "cues" in result.output
        # Each cue line starts with a bracketed index
        assert "[  0]" in result.output or "[ 0]" in result.output or "[0]" in result.output

    def test_show_non_existent_run_exits_one(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["captions", "show", "9999"])
        assert result.exit_code == 1


class TestCaptionsExport:
    def test_export_exits_zero(self, tmp_path: Path) -> None:
        _seed_approved_narration(tmp_path)
        runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "1",
             "--artifacts-path", str(tmp_path / "arts")],
        )
        result = runner.invoke(
            app,
            ["captions", "export", "1", "--artifacts-path", str(tmp_path / "arts")],
        )
        assert result.exit_code == 0, result.output

    def test_export_writes_files(self, tmp_path: Path) -> None:
        _seed_approved_narration(tmp_path)
        runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "1",
             "--artifacts-path", str(tmp_path / "arts")],
        )
        arts = tmp_path / "arts"
        result = runner.invoke(
            app,
            ["captions", "export", "1", "--artifacts-path", str(arts)],
        )
        assert "SRT" in result.output
        assert "VTT" in result.output
        assert "JSON" in result.output
        assert any(arts.rglob("*.srt")), "SRT file not found after export"
        assert any(arts.rglob("*.vtt")), "VTT file not found after export"
        assert any(arts.rglob("*.json")), "JSON file not found after export"

    def test_export_single_format(self, tmp_path: Path) -> None:
        _seed_approved_narration(tmp_path)
        runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "1",
             "--artifacts-path", str(tmp_path / "arts")],
        )
        arts = tmp_path / "arts2"
        result = runner.invoke(
            app,
            ["captions", "export", "1", "--artifacts-path", str(arts), "--format", "srt"],
        )
        assert result.exit_code == 0, result.output
        assert "SRT" in result.output
        assert "VTT" not in result.output

    def test_export_invalid_format_exits_one(self, tmp_path: Path) -> None:
        _seed_approved_narration(tmp_path)
        runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "1",
             "--artifacts-path", str(tmp_path / "arts")],
        )
        result = runner.invoke(
            app,
            ["captions", "export", "1", "--artifacts-path", str(tmp_path / "arts"),
             "--format", "mp4"],
        )
        assert result.exit_code == 1

    def test_export_updates_db_metadata(self, tmp_path: Path) -> None:
        from app.captions.repository import require_caption_run
        from app.core.database import open_db

        _seed_approved_narration(tmp_path)
        runner.invoke(
            app,
            ["captions", "generate", "--plan-id", "1",
             "--artifacts-path", str(tmp_path / "arts")],
        )
        arts = tmp_path / "arts"
        runner.invoke(
            app,
            ["captions", "export", "1", "--artifacts-path", str(arts)],
        )
        conn = open_db(tmp_path / "test.db")
        run = require_caption_run(conn, 1)
        conn.close()
        assert run.srt_sha256 is not None
        assert run.vtt_sha256 is not None
        assert run.json_sha256 is not None

    def test_export_non_existent_run_exits_one(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["captions", "export", "9999", "--artifacts-path", str(tmp_path / "arts")],
        )
        assert result.exit_code == 1
