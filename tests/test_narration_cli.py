"""Tests for Phase 6 M6.2 narration CLI commands."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import app
from app.core.config import reset_config
from app.core.database import open_db

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACE_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ACE_ARTIFACTS_PATH", str(tmp_path / "artifacts"))
    reset_config()
    yield
    reset_config()


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    db = open_db(path)
    _seed(db)
    db.close()
    return path


def _seed(db: sqlite3.Connection) -> None:
    now = "2026-01-01T00:00:00"
    db.execute(
        "INSERT INTO topics (id, title, angle, created_at, updated_at) VALUES (1, 'T', '', ?, ?)",
        (now, now),
    )
    db.execute(
        "INSERT INTO scripts (id, topic_id, body, version, created_at, updated_at) "
        "VALUES (1, 1, 'Script body', 1, ?, ?)",
        (now, now),
    )
    db.execute(
        "INSERT INTO production_plans "
        "(id, topic_id, script_id, script_version, input_hash, script_body_hash, "
        "plan_schema_version, renderer_version, duration_algorithm_version, status, "
        "created_at, updated_at) "
        "VALUES (1, 1, 1, 1, ?, ?, 'v1', 'v1', 'v1', 'approved', ?, ?)",
        ("a" * 64, "b" * 64, now, now),
    )
    db.execute(
        "INSERT INTO production_segments "
        "(id, plan_id, segment_index, section_index, section_type, narration_text, "
        "created_at) "
        "VALUES (1, 1, 0, 0, 'narration', 'Hello world', ?)",
        (now,),
    )
    db.commit()


# ── narration voices ──────────────────────────────────────────────────────────


def test_voices_empty(db_path: Path) -> None:
    result = runner.invoke(app, ["narration", "voices"])
    assert result.exit_code == 0
    assert "No voice profiles" in result.output


def test_voices_lists_profiles(db_path: Path) -> None:
    runner.invoke(app, ["narration", "add-voice", "My Voice", "--provider", "fake"])
    result = runner.invoke(app, ["narration", "voices"])
    assert result.exit_code == 0
    assert "My Voice" in result.output


# ── narration add-voice ───────────────────────────────────────────────────────


def test_add_voice_success(db_path: Path) -> None:
    result = runner.invoke(app, ["narration", "add-voice", "Test Voice"])
    assert result.exit_code == 0
    assert "Created voice profile" in result.output


def test_add_voice_default_flag(db_path: Path) -> None:
    result = runner.invoke(app, ["narration", "add-voice", "Default Voice", "--default"])
    assert result.exit_code == 0
    voices_result = runner.invoke(app, ["narration", "voices"])
    assert "[default]" in voices_result.output


# ── narration narrate ─────────────────────────────────────────────────────────


def test_narrate_dry_run(db_path: Path) -> None:
    runner.invoke(app, ["narration", "add-voice", "V1"])
    result = runner.invoke(app, ["narration", "narrate", "1", "--voice", "1", "--dry-run"])
    assert result.exit_code == 0
    assert "Dry-run" in result.output


def test_narrate_success(db_path: Path) -> None:
    runner.invoke(app, ["narration", "add-voice", "V1"])
    result = runner.invoke(app, ["narration", "narrate", "1", "--voice", "1"])
    assert result.exit_code == 0
    assert "synthesised" in result.output


def test_narrate_no_plan(db_path: Path) -> None:
    runner.invoke(app, ["narration", "add-voice", "V1"])
    result = runner.invoke(app, ["narration", "narrate", "999", "--voice", "1"])
    assert result.exit_code != 0


# ── narration runs ────────────────────────────────────────────────────────────


def test_runs_no_runs(db_path: Path) -> None:
    result = runner.invoke(app, ["narration", "runs", "1"])
    assert result.exit_code == 0
    assert "No narration runs" in result.output


def test_runs_lists_runs(db_path: Path) -> None:
    runner.invoke(app, ["narration", "add-voice", "V1"])
    runner.invoke(app, ["narration", "narrate", "1", "--voice", "1"])
    result = runner.invoke(app, ["narration", "runs", "1"])
    assert result.exit_code == 0
    assert "status=" in result.output


# ── narration approve ─────────────────────────────────────────────────────────


def _create_and_get_run_id(db_path: Path) -> int:
    runner.invoke(app, ["narration", "add-voice", "V1"])
    runner.invoke(app, ["narration", "narrate", "1", "--voice", "1"])
    db = open_db(db_path)
    row = db.execute("SELECT id FROM narration_runs LIMIT 1").fetchone()
    db.close()
    return row[0]


def test_approve_run(db_path: Path) -> None:
    run_id = _create_and_get_run_id(db_path)
    result = runner.invoke(app, ["narration", "approve", str(run_id)])
    assert result.exit_code == 0
    assert "approved" in result.output


# ── narration events ──────────────────────────────────────────────────────────


def test_events_empty(db_path: Path) -> None:
    runner.invoke(app, ["narration", "add-voice", "V1"])
    runner.invoke(app, ["narration", "narrate", "1", "--voice", "1"])
    db = open_db(db_path)
    row = db.execute("SELECT id FROM narration_runs LIMIT 1").fetchone()
    db.close()
    result = runner.invoke(app, ["narration", "events", str(row[0])])
    assert result.exit_code == 0
    assert "No review events" in result.output


def test_events_after_approve(db_path: Path) -> None:
    run_id = _create_and_get_run_id(db_path)
    runner.invoke(app, ["narration", "approve", str(run_id)])
    result = runner.invoke(app, ["narration", "events", str(run_id)])
    assert result.exit_code == 0
    assert "run_approved" in result.output


# ── narration regenerate-segment ──────────────────────────────────────────────


def _create_and_get_rejected_asset_id(db_path: Path) -> int:
    """Narrate (single segment plan), then reject segment 1, return its asset id."""
    runner.invoke(app, ["narration", "add-voice", "V1"])
    runner.invoke(app, ["narration", "narrate", "1", "--voice", "1"])
    db = open_db(db_path)
    row = db.execute(
        "SELECT id FROM narration_segment_assets WHERE status='synthesized' LIMIT 1"
    ).fetchone()
    asset_id = row[0]
    db.close()
    runner.invoke(app, ["narration", "reject-segment", str(asset_id), "--reason-code", "pacing"])
    return asset_id


def test_regenerate_segment_success(db_path: Path) -> None:
    rejected_id = _create_and_get_rejected_asset_id(db_path)
    result = runner.invoke(app, ["narration", "regenerate-segment", str(rejected_id)])
    assert result.exit_code == 0, result.output
    assert "Regenerated" in result.output


def test_regenerate_segment_non_rejected_raises(db_path: Path) -> None:
    runner.invoke(app, ["narration", "add-voice", "V1"])
    runner.invoke(app, ["narration", "narrate", "1", "--voice", "1"])
    db = open_db(db_path)
    row = db.execute(
        "SELECT id FROM narration_segment_assets WHERE status='synthesized' LIMIT 1"
    ).fetchone()
    asset_id = row[0]
    db.close()
    result = runner.invoke(app, ["narration", "regenerate-segment", str(asset_id)])
    assert result.exit_code != 0
