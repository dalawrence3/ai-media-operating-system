"""Tests for src/app/captions/repository.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.captions.errors import (
    CueRejectionBlocksApprovalError,
    DuplicateCaptionInputHashError,
    IllegalCaptionTransitionError,
    IncompleteCaptionRunError,
    InvalidCaptionReasonCodeError,
    InvalidCaptionSeverityError,
    NoCaptionRunError,
)
from app.captions.models import CaptionCueDraft, CaptionRunDraft
from app.captions.repository import (
    approve_caption_run,
    complete_caption_run,
    create_caption_run,
    fail_caption_run,
    find_caption_run_by_input_hash,
    get_active_approved_caption_run,
    get_caption_cues,
    get_caption_run,
    list_caption_review_events,
    list_caption_runs,
    persist_caption_cues,
    record_cue_rejection,
    reject_caption_run,
    require_caption_run,
)
from app.core.database import open_db

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "test.db")


def _seed_db(conn: sqlite3.Connection) -> dict:
    """Insert minimal prerequisite rows; return a dict of inserted IDs."""
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        "INSERT INTO topics (id, title, angle) VALUES (1, 'T', 'A')"
    )
    conn.execute(
        "INSERT INTO scripts (id, topic_id, version, body, status)"
        " VALUES (1, 1, 1, 'body', 'approved')"
    )
    conn.execute(
        "INSERT INTO voice_profiles (id, provider, model, voice_id, name, language, speaking_rate)"
        " VALUES (1, 'mock', 'm1', 'v1', 'Test Voice', 'en-US', 1.0)"
    )
    conn.execute(
        "INSERT INTO production_plans"
        " (id, topic_id, script_id, script_version, input_hash, script_body_hash,"
        "  plan_schema_version, renderer_version, duration_algorithm_version,"
        "  status, created_at, updated_at)"
        " VALUES (1, 1, 1, 1, 'plan-hash', 'body-hash',"
        "  'v1', 'rv1', 'dv1', 'approved', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO production_segments"
        " (id, plan_id, segment_index, section_index, section_type, narration_text, created_at)"
        " VALUES (1, 1, 0, 0, 'hook', 'Hello world.', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO narration_runs"
        " (id, plan_id, plan_input_hash, voice_profile_id, voice_profile_version,"
        "  language, speaking_rate, settings_json, output_format, sample_rate_hz,"
        "  input_hash, status, created_at, updated_at)"
        " VALUES (1, 1, 'plan-hash', 1, 1,"
        "  'en-US', 1.0, '{}', 'wav', 22050,"
        "  'nr-hash-1', 'approved', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO narration_segment_assets"
        " (id, run_id, segment_id, narration_text_hash, provider, model, voice_id,"
        "  voice_profile_id, voice_profile_version, language, speaking_rate,"
        "  settings_json_hash, output_format, sample_rate_hz, input_hash, status,"
        "  audio_sha256, duration_seconds, created_at, updated_at)"
        " VALUES (1, 1, 1, 'th1', 'mock', 'm1', 'v1',"
        "  1, 1, 'en-US', 1.0,"
        "  'sh1', 'wav', 22050, 'asset-hash', 'synthesized',"
        "  'ah1', 2.5, '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    return {"narration_run_id": 1, "plan_id": 1, "script_id": 1, "topic_id": 1}


def _make_draft(**overrides) -> CaptionRunDraft:
    defaults = dict(
        narration_run_id=1,
        plan_id=1,
        script_id=1,
        topic_id=1,
        experiment_id=None,
        input_hash="caption-hash-1",
        caption_schema_version="Caption-v1",
        segmentation_version="caption-segment-v1",
        timing_algorithm_version="caption-timing-estimated-v1",
        style_version="caption-style-v1",
        exporter_version="caption-exporter-v1",
        language="en-US",
    )
    defaults.update(overrides)
    return CaptionRunDraft(**defaults)


def _make_cue(
    cue_index: int,
    segment_cue_index: int = 0,
    text: str = "Hello world",
    start_ms: int = 0,
    end_ms: int = 2000,
) -> CaptionCueDraft:
    return CaptionCueDraft(
        segment_id=1,
        narration_asset_id=1,
        narration_text_hash="th1",
        audio_sha256="ah1",
        cue_index=cue_index,
        segment_cue_index=segment_cue_index,
        lines=text.split("\n"),
        start_ms=start_ms,
        end_ms=end_ms,
        timing_source="estimated",
    )


def _complete_run(conn: sqlite3.Connection, run_id: int) -> None:
    complete_caption_run(
        conn, run_id,
        total_cue_count=1,
        total_duration_ms=2000,
        srt_path="captions/plan_1/run_1/captions.srt",
        vtt_path="captions/plan_1/run_1/captions.vtt",
        json_path="captions/plan_1/run_1/captions.json",
        srt_sha256="a" * 64,
        vtt_sha256="b" * 64,
        json_sha256="c" * 64,
    )


class TestCreateCaptionRun:
    def test_creates_run_with_running_status(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        assert run.status == "running"
        assert run.id is not None

    def test_sets_version_fields(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        assert run.caption_schema_version == "Caption-v1"
        assert run.segmentation_version == "caption-segment-v1"

    def test_duplicate_input_hash_raises(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        create_caption_run(db, _make_draft())
        with pytest.raises(DuplicateCaptionInputHashError):
            create_caption_run(db, _make_draft())

    def test_different_input_hash_allowed(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        r1 = create_caption_run(db, _make_draft(input_hash="hash-1"))
        r2 = create_caption_run(db, _make_draft(input_hash="hash-2"))
        assert r1.id != r2.id


class TestGetCaptionRun:
    def test_get_existing(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        created = create_caption_run(db, _make_draft())
        fetched = get_caption_run(db, created.id)
        assert fetched is not None
        assert fetched.id == created.id

    def test_get_missing_returns_none(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        assert get_caption_run(db, 9999) is None

    def test_require_missing_raises(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        with pytest.raises(NoCaptionRunError):
            require_caption_run(db, 9999)


class TestFindByInputHash:
    def test_found(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        create_caption_run(db, _make_draft())
        found = find_caption_run_by_input_hash(db, 1, "caption-hash-1")
        assert found is not None

    def test_not_found(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        result = find_caption_run_by_input_hash(db, 1, "nonexistent-hash")
        assert result is None


class TestPersistCaptionCues:
    def test_inserts_all_cues(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        cues = [
            _make_cue(0, segment_cue_index=0),
            _make_cue(1, segment_cue_index=1, start_ms=2000, end_ms=4000, text="Goodbye"),
        ]
        result = persist_caption_cues(db, run.id, cues)
        assert len(result) == 2

    def test_cues_ordered_by_cue_index(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        cue0 = _make_cue(0, segment_cue_index=0)
        cue1 = _make_cue(1, segment_cue_index=1, start_ms=2000, end_ms=4000)
        persist_caption_cues(db, run.id, [cue0, cue1])
        fetched = get_caption_cues(db, run.id)
        assert [c.cue_index for c in fetched] == [0, 1]

    def test_warnings_json_round_trips(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        cue = _make_cue(0)
        cue.warnings.append("orphan_word")
        persist_caption_cues(db, run.id, [cue])
        fetched = get_caption_cues(db, run.id)
        assert "orphan_word" in fetched[0].warnings

    def test_empty_cue_list_is_no_op(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        result = persist_caption_cues(db, run.id, [])
        assert result == []


class TestCompleteCaptionRun:
    def test_sets_completed_status(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        _complete_run(db, run.id)
        updated = require_caption_run(db, run.id)
        assert updated.status == "completed"
        assert updated.srt_sha256 == "a" * 64

    def test_cannot_complete_non_running(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        _complete_run(db, run.id)
        with pytest.raises(IllegalCaptionTransitionError):
            _complete_run(db, run.id)


class TestFailCaptionRun:
    def test_sets_failed_status(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        updated = fail_caption_run(db, run.id, failure_reason="segmentation error")
        assert updated.status == "failed"
        assert updated.failure_reason == "segmentation error"

    def test_cannot_fail_non_running(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        fail_caption_run(db, run.id, failure_reason="x")
        with pytest.raises(IllegalCaptionTransitionError):
            fail_caption_run(db, run.id, failure_reason="again")


class TestApproveCaptionRun:
    def test_approves_completed_run(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        persist_caption_cues(db, run.id, [_make_cue(0)])
        _complete_run(db, run.id)
        approved = approve_caption_run(db, run.id)
        assert approved.status == "approved"
        assert approved.approved_at is not None

    def test_inserts_run_approved_event(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        persist_caption_cues(db, run.id, [_make_cue(0)])
        _complete_run(db, run.id)
        approve_caption_run(db, run.id)
        events = list_caption_review_events(db, run.id)
        assert any(e.event_type == "run_approved" for e in events)

    def test_cannot_approve_non_completed(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        with pytest.raises(IllegalCaptionTransitionError):
            approve_caption_run(db, run.id)

    def test_cue_rejection_blocks_approval(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        persist_caption_cues(db, run.id, [_make_cue(0)])
        cues = get_caption_cues(db, run.id)
        _complete_run(db, run.id)
        record_cue_rejection(db, run.id, cues[0].id, reason_code="timing")
        with pytest.raises(CueRejectionBlocksApprovalError):
            approve_caption_run(db, run.id)

    def test_zero_cue_count_blocks_approval(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        complete_caption_run(
            db, run.id,
            total_cue_count=0,
            total_duration_ms=0,
            srt_path="p/c.srt", vtt_path="p/c.vtt", json_path="p/c.json",
            srt_sha256="a" * 64, vtt_sha256="b" * 64, json_sha256="c" * 64,
        )
        with pytest.raises(IncompleteCaptionRunError):
            approve_caption_run(db, run.id)

    def test_supersedes_prior_approved_run(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run1 = create_caption_run(db, _make_draft(input_hash="h1"))
        persist_caption_cues(db, run1.id, [_make_cue(0)])
        _complete_run(db, run1.id)
        approve_caption_run(db, run1.id)

        run2 = create_caption_run(db, _make_draft(input_hash="h2"))
        persist_caption_cues(db, run2.id, [_make_cue(0)])
        _complete_run(db, run2.id)
        approve_caption_run(db, run2.id)

        old = require_caption_run(db, run1.id)
        assert old.superseded_at is not None
        assert old.superseded_by_run_id == run2.id
        assert old.status == "approved"  # supersession does not change status


class TestRejectCaptionRun:
    def test_rejects_completed_run(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        _complete_run(db, run.id)
        rejected = reject_caption_run(db, run.id, reason_code="timing")
        assert rejected.status == "rejected"
        assert rejected.rejected_at is not None

    def test_rejects_approved_run(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        persist_caption_cues(db, run.id, [_make_cue(0)])
        _complete_run(db, run.id)
        approve_caption_run(db, run.id)
        rejected = reject_caption_run(db, run.id, reason_code="style")
        assert rejected.status == "rejected"

    def test_cannot_reject_running(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        with pytest.raises(IllegalCaptionTransitionError):
            reject_caption_run(db, run.id, reason_code="timing")

    def test_invalid_severity_raises(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        _complete_run(db, run.id)
        with pytest.raises(InvalidCaptionSeverityError):
            reject_caption_run(db, run.id, reason_code="timing", severity=99)


class TestGetActiveApproved:
    def test_returns_active_approved(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        persist_caption_cues(db, run.id, [_make_cue(0)])
        _complete_run(db, run.id)
        approve_caption_run(db, run.id)
        found = get_active_approved_caption_run(db, narration_run_id=1)
        assert found is not None
        assert found.id == run.id

    def test_returns_none_when_none_approved(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        create_caption_run(db, _make_draft())
        found = get_active_approved_caption_run(db, narration_run_id=1)
        assert found is None


class TestRecordCueRejection:
    def test_inserts_event(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        persist_caption_cues(db, run.id, [_make_cue(0)])
        cues = get_caption_cues(db, run.id)
        _complete_run(db, run.id)
        event = record_cue_rejection(db, run.id, cues[0].id, reason_code="timing")
        assert event.event_type == "cue_rejected"
        assert event.reason_code == "timing"

    def test_invalid_reason_code_raises(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        persist_caption_cues(db, run.id, [_make_cue(0)])
        cues = get_caption_cues(db, run.id)
        with pytest.raises(InvalidCaptionReasonCodeError):
            record_cue_rejection(db, run.id, cues[0].id, reason_code="not_a_real_code")

    def test_other_reason_requires_notes(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        persist_caption_cues(db, run.id, [_make_cue(0)])
        cues = get_caption_cues(db, run.id)
        with pytest.raises(InvalidCaptionReasonCodeError):
            record_cue_rejection(db, run.id, cues[0].id, reason_code="other")

    def test_other_reason_with_notes_succeeds(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        run = create_caption_run(db, _make_draft())
        persist_caption_cues(db, run.id, [_make_cue(0)])
        cues = get_caption_cues(db, run.id)
        event = record_cue_rejection(
            db, run.id, cues[0].id, reason_code="other", notes="custom note"
        )
        assert event.notes == "custom note"


class TestListCaptionRuns:
    def test_lists_all_runs_for_narration_run(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        create_caption_run(db, _make_draft(input_hash="h1"))
        create_caption_run(db, _make_draft(input_hash="h2"))
        runs = list_caption_runs(db, narration_run_id=1)
        assert len(runs) == 2

    def test_empty_for_unknown_narration_run(self, db: sqlite3.Connection) -> None:
        _seed_db(db)
        assert list_caption_runs(db, narration_run_id=9999) == []
