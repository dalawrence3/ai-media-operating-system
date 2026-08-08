"""Tests for Phase 6 M6.2 narration orchestrator."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core.database import open_db
from app.narration.errors import SynthesisError
from app.narration.fake import FakeTTSProvider
from app.narration.models import VoiceProfileCreate
from app.narration.orchestrator import narrate_plan
from app.narration.repository import (
    create_voice_profile,
    get_narration_run,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = open_db(tmp_path / "test.db")
    db.row_factory = sqlite3.Row
    _seed(db)
    return db


@pytest.fixture()
def artifacts(tmp_path: Path) -> Path:
    return tmp_path / "artifacts"


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
    db.execute(
        "INSERT INTO production_segments "
        "(id, plan_id, segment_index, section_index, section_type, narration_text, "
        "created_at) "
        "VALUES (2, 1, 1, 1, 'narration', 'Second segment', ?)",
        (now,),
    )
    db.commit()


def _vpc(**kwargs) -> VoiceProfileCreate:
    defaults = dict(
        channel_id=None,
        provider="fake",
        model="fake/FAKE",
        voice_id="fake-voice",
        name="Test Voice",
        language="en-US",
        speaking_rate=1.0,
        style=None,
        stability=None,
        similarity_boost=None,
        settings_json="{}",
    )
    defaults.update(kwargs)
    return VoiceProfileCreate(**defaults)


# ── Golden path ───────────────────────────────────────────────────────────────


def test_narrate_plan_returns_result(conn: sqlite3.Connection, artifacts: Path) -> None:
    vp = create_voice_profile(conn, _vpc())
    result = narrate_plan(
        conn,
        plan_id=1,
        plan_input_hash="a" * 64,
        voice_profile_id=vp.id,
        artifacts_path=artifacts,
        provider=FakeTTSProvider(),
    )
    assert result.run_id is not None
    assert len(result.assets) == 2


def test_narrate_plan_assets_synthesized(conn: sqlite3.Connection, artifacts: Path) -> None:
    vp = create_voice_profile(conn, _vpc())
    result = narrate_plan(
        conn,
        plan_id=1,
        plan_input_hash="a" * 64,
        voice_profile_id=vp.id,
        artifacts_path=artifacts,
        provider=FakeTTSProvider(),
    )
    for asset in result.assets:
        assert asset.status == "synthesized"
        assert asset.audio_path is not None
        assert asset.audio_sha256 is not None


def test_narrate_plan_audio_files_exist(conn: sqlite3.Connection, artifacts: Path) -> None:
    vp = create_voice_profile(conn, _vpc())
    narrate_plan(
        conn,
        plan_id=1,
        plan_input_hash="a" * 64,
        voice_profile_id=vp.id,
        artifacts_path=artifacts,
        provider=FakeTTSProvider(),
    )
    assert any(artifacts.rglob("*.wav"))


def test_narrate_plan_run_completed(conn: sqlite3.Connection, artifacts: Path) -> None:
    vp = create_voice_profile(conn, _vpc())
    result = narrate_plan(
        conn,
        plan_id=1,
        plan_input_hash="a" * 64,
        voice_profile_id=vp.id,
        artifacts_path=artifacts,
        provider=FakeTTSProvider(),
    )
    run = get_narration_run(conn, result.run_id)
    assert run is not None
    assert run.status == "completed"


# ── Idempotency ───────────────────────────────────────────────────────────────


def test_narrate_plan_idempotent(conn: sqlite3.Connection, artifacts: Path) -> None:
    vp = create_voice_profile(conn, _vpc())
    result1 = narrate_plan(
        conn,
        plan_id=1,
        plan_input_hash="a" * 64,
        voice_profile_id=vp.id,
        artifacts_path=artifacts,
        provider=FakeTTSProvider(),
    )
    result2 = narrate_plan(
        conn,
        plan_id=1,
        plan_input_hash="a" * 64,
        voice_profile_id=vp.id,
        artifacts_path=artifacts,
        provider=FakeTTSProvider(),
    )
    assert result1.run_id == result2.run_id
    assert len(result2.assets) == 2


# ── Dry run ───────────────────────────────────────────────────────────────────


def test_narrate_plan_dry_run_skips_all(conn: sqlite3.Connection, artifacts: Path) -> None:
    vp = create_voice_profile(conn, _vpc())
    result = narrate_plan(
        conn,
        plan_id=1,
        plan_input_hash="a" * 64,
        voice_profile_id=vp.id,
        artifacts_path=artifacts,
        provider=FakeTTSProvider(),
        dry_run=True,
    )
    assert result.assets == []
    assert len(result.skipped_segment_ids) == 2


# ── Error handling ────────────────────────────────────────────────────────────


def test_narrate_plan_tts_failure_marks_run_failed(
    conn: sqlite3.Connection, artifacts: Path
) -> None:
    vp = create_voice_profile(conn, _vpc())
    with pytest.raises(SynthesisError):
        narrate_plan(
            conn,
            plan_id=1,
            plan_input_hash="a" * 64,
            voice_profile_id=vp.id,
            artifacts_path=artifacts,
            provider=FakeTTSProvider(fail_on={1}),
        )
    runs = conn.execute("SELECT status FROM narration_runs WHERE plan_id=1").fetchall()
    assert any(r[0] == "failed" for r in runs)


def test_narrate_plan_missing_voice_profile_raises(
    conn: sqlite3.Connection, artifacts: Path
) -> None:
    from app.narration.errors import NoVoiceProfileError

    with pytest.raises(NoVoiceProfileError):
        narrate_plan(
            conn,
            plan_id=1,
            plan_input_hash="a" * 64,
            voice_profile_id=9999,
            artifacts_path=artifacts,
            provider=FakeTTSProvider(),
        )


# ── TTS call logging ──────────────────────────────────────────────────────────


def test_narrate_plan_logs_tts_calls(conn: sqlite3.Connection, artifacts: Path) -> None:
    vp = create_voice_profile(conn, _vpc())
    result = narrate_plan(
        conn,
        plan_id=1,
        plan_input_hash="a" * 64,
        voice_profile_id=vp.id,
        artifacts_path=artifacts,
        provider=FakeTTSProvider(),
    )
    calls = conn.execute(
        "SELECT COUNT(*) FROM tts_calls WHERE run_id=?", (result.run_id,)
    ).fetchone()[0]
    assert calls == 2


def test_narrate_plan_logs_failed_tts_call(conn: sqlite3.Connection, artifacts: Path) -> None:
    vp = create_voice_profile(conn, _vpc())
    with pytest.raises(SynthesisError):
        narrate_plan(
            conn,
            plan_id=1,
            plan_input_hash="a" * 64,
            voice_profile_id=vp.id,
            artifacts_path=artifacts,
            provider=FakeTTSProvider(fail_on={1}),
        )
    calls = conn.execute("SELECT success FROM tts_calls").fetchall()
    assert any(c[0] == 0 for c in calls)
