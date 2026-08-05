"""Tests for Phase 6 M6.2 narration repository."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core.database import open_db
from app.narration.errors import (
    DuplicateNarrationInputHashError,
    IllegalNarrationTransitionError,
    IllegalSegmentTransitionError,
    InvalidNarrationReasonCodeError,
    InvalidNarrationSeverityError,
    NoNarrationRunError,
    NoSegmentAssetError,
    NoVoiceProfileError,
    PendingSegmentsError,
    RejectedSegmentsError,
)
from app.narration.models import (
    NarrationRunDraft,
    NarrationSegmentAssetDraft,
    VoiceProfileCreate,
)
from app.narration.repository import (
    approve_narration_run,
    complete_narration_run,
    create_narration_run,
    create_narration_segment_asset,
    create_voice_profile,
    fail_narration_run,
    finalize_narration_segment_asset,
    get_active_approved_narration_run,
    get_active_segment_asset,
    get_narration_run,
    get_segment_assets_for_run,
    list_narration_review_events,
    list_narration_runs,
    list_voice_profiles,
    record_tts_call,
    reject_narration_run,
    reject_narration_segment_asset,
    require_narration_run,
    require_narration_segment_asset,
    require_voice_profile,
    restart_failed_narration_run,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = open_db(tmp_path / "test.db")
    db.row_factory = sqlite3.Row
    _seed(db)
    return db


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


def _run_draft(voice_profile_id: int = 1, **kwargs) -> NarrationRunDraft:
    defaults = dict(
        plan_id=1,
        plan_input_hash="a" * 64,
        voice_profile_id=voice_profile_id,
        voice_profile_version=1,
        language="en-US",
        speaking_rate=1.0,
        style=None,
        stability=None,
        similarity_boost=None,
        settings_json="{}",
        output_format="wav",
        sample_rate_hz=22050,
    )
    defaults.update(kwargs)
    return NarrationRunDraft(**defaults)


def _asset_draft(run_id: int = 1, segment_id: int = 1, **kwargs) -> NarrationSegmentAssetDraft:
    defaults = dict(
        run_id=run_id,
        segment_id=segment_id,
        narration_text_hash="b" * 64,
        provider="fake",
        model="fake/FAKE",
        voice_id="fake-voice",
        voice_profile_id=1,
        voice_profile_version=1,
        language="en-US",
        speaking_rate=1.0,
        style=None,
        stability=None,
        similarity_boost=None,
        settings_json_hash="c" * 64,
        output_format="wav",
        sample_rate_hz=22050,
        input_hash="d" * 64,
    )
    defaults.update(kwargs)
    return NarrationSegmentAssetDraft(**defaults)


# ── Voice profile tests ───────────────────────────────────────────────────────


def test_create_voice_profile(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    assert vp.id is not None
    assert vp.provider == "fake"
    assert vp.name == "Test Voice"


def test_get_voice_profile_existing(conn: sqlite3.Connection) -> None:
    from app.narration.repository import get_voice_profile as gvp

    vp = create_voice_profile(conn, _vpc())
    result = gvp(conn, vp.id)
    assert result is not None
    assert result.id == vp.id


def test_get_voice_profile_missing(conn: sqlite3.Connection) -> None:
    from app.narration.repository import get_voice_profile as gvp

    assert gvp(conn, 9999) is None


def test_require_voice_profile_raises_on_missing(conn: sqlite3.Connection) -> None:
    with pytest.raises(NoVoiceProfileError):
        require_voice_profile(conn, 9999)


def test_list_voice_profiles(conn: sqlite3.Connection) -> None:
    create_voice_profile(conn, _vpc(name="A"))
    create_voice_profile(conn, _vpc(name="B"))
    profiles = list_voice_profiles(conn)
    assert len(profiles) == 2


def test_create_voice_profile_is_default_clears_previous(conn: sqlite3.Connection) -> None:
    from app.narration.repository import get_voice_profile as gvp

    v1 = create_voice_profile(conn, _vpc(name="A", channel_id=None, is_default=True))
    v2 = create_voice_profile(conn, _vpc(name="B", channel_id=None, is_default=True))
    refreshed_v1 = gvp(conn, v1.id)
    assert refreshed_v1 is not None
    assert refreshed_v1.is_default is False
    assert v2.is_default is True


# ── Narration run tests ───────────────────────────────────────────────────────


def test_create_narration_run(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    assert run.id is not None
    assert run.status == "running"
    assert run.plan_id == 1


def test_create_narration_run_duplicate_hash_raises(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    create_narration_run(conn, _run_draft(vp.id))
    with pytest.raises(DuplicateNarrationInputHashError):
        create_narration_run(conn, _run_draft(vp.id))


def test_get_narration_run_missing(conn: sqlite3.Connection) -> None:
    assert get_narration_run(conn, 9999) is None


def test_require_narration_run_raises_on_missing(conn: sqlite3.Connection) -> None:
    with pytest.raises(NoNarrationRunError):
        require_narration_run(conn, 9999)


def test_list_narration_runs(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    create_narration_run(conn, _run_draft(vp.id))
    runs = list_narration_runs(conn, plan_id=1)
    assert len(runs) == 1


def test_complete_narration_run(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    completed = complete_narration_run(conn, run.id)
    assert completed.status == "completed"
    assert completed.completed_at is not None


def test_complete_narration_run_wrong_status_raises(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    complete_narration_run(conn, run.id)
    with pytest.raises(IllegalNarrationTransitionError):
        complete_narration_run(conn, run.id)


def test_fail_narration_run(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    failed = fail_narration_run(conn, run.id, error_message="oops")
    assert failed.status == "failed"
    assert failed.error_message == "oops"


def test_restart_failed_narration_run(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    fail_narration_run(conn, run.id, error_message="oops")
    restarted = restart_failed_narration_run(conn, run.id)
    assert restarted.status == "running"
    assert restarted.error_message is None


def test_restart_non_failed_raises(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    with pytest.raises(IllegalNarrationTransitionError):
        restart_failed_narration_run(conn, run.id)


# ── Approve / reject run ──────────────────────────────────────────────────────


def _make_completed_run_with_assets(conn: sqlite3.Connection) -> int:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    for seg_id in (1, 2):
        asset = create_narration_segment_asset(
            conn, _asset_draft(run.id, seg_id, input_hash=("x" * 63 + str(seg_id)))
        )
        finalize_narration_segment_asset(
            conn, asset.id,
            audio_path=f"narration/plan_1/run_{run.id}/segment_{seg_id}.wav",
            audio_sha256="e" * 64,
            duration_seconds=2.0,
            characters_billed=10,
            cost_usd=0.0,
        )
    complete_narration_run(conn, run.id)
    return run.id


def test_approve_narration_run(conn: sqlite3.Connection) -> None:
    run_id = _make_completed_run_with_assets(conn)
    approved = approve_narration_run(conn, run_id, actor="operator")
    assert approved.status == "approved"
    assert approved.approved_at is not None


def test_approve_run_creates_review_event(conn: sqlite3.Connection) -> None:
    run_id = _make_completed_run_with_assets(conn)
    approve_narration_run(conn, run_id)
    events = list_narration_review_events(conn, run_id)
    assert any(e.event_type == "run_approved" for e in events)


def test_approve_run_with_pending_segments_raises(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    complete_narration_run(conn, run.id)
    with pytest.raises(PendingSegmentsError):
        approve_narration_run(conn, run.id)


def test_approve_run_with_rejected_segments_raises(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    asset = create_narration_segment_asset(conn, _asset_draft(run.id, 1))
    finalize_narration_segment_asset(
        conn, asset.id,
        audio_path="x.wav", audio_sha256="e" * 64,
        duration_seconds=1.0, characters_billed=5, cost_usd=0.0,
    )
    reject_narration_segment_asset(conn, asset.id, reason_code="pacing")
    complete_narration_run(conn, run.id)
    with pytest.raises(RejectedSegmentsError):
        approve_narration_run(conn, run.id)


def test_get_active_approved_narration_run(conn: sqlite3.Connection) -> None:
    run_id = _make_completed_run_with_assets(conn)
    approve_narration_run(conn, run_id)
    active = get_active_approved_narration_run(conn, plan_id=1)
    assert active is not None
    assert active.id == run_id


def test_reject_narration_run(conn: sqlite3.Connection) -> None:
    run_id = _make_completed_run_with_assets(conn)
    rejected = reject_narration_run(conn, run_id, reason_code="voice_mismatch")
    assert rejected.status == "rejected"
    events = list_narration_review_events(conn, run_id)
    assert any(e.event_type == "run_rejected" for e in events)


# ── Segment asset tests ───────────────────────────────────────────────────────


def test_create_segment_asset(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    asset = create_narration_segment_asset(conn, _asset_draft(run.id, 1))
    assert asset.id is not None
    assert asset.status == "pending"


def test_require_segment_asset_raises_on_missing(conn: sqlite3.Connection) -> None:
    with pytest.raises(NoSegmentAssetError):
        require_narration_segment_asset(conn, 9999)


def test_get_active_segment_asset(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    create_narration_segment_asset(conn, _asset_draft(run.id, 1))
    active = get_active_segment_asset(conn, run.id, 1)
    assert active is not None


def test_finalize_segment_asset(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    asset = create_narration_segment_asset(conn, _asset_draft(run.id, 1))
    done = finalize_narration_segment_asset(
        conn, asset.id,
        audio_path="narration/plan_1/run_1/segment_1.wav",
        audio_sha256="e" * 64,
        duration_seconds=2.5,
        characters_billed=11,
        cost_usd=0.0,
    )
    assert done.status == "synthesized"
    assert done.audio_path == "narration/plan_1/run_1/segment_1.wav"
    assert done.duration_seconds == 2.5


def test_finalize_non_pending_raises(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    asset = create_narration_segment_asset(conn, _asset_draft(run.id, 1))
    finalize_narration_segment_asset(
        conn, asset.id,
        audio_path="x.wav", audio_sha256="e" * 64,
        duration_seconds=1.0, characters_billed=5, cost_usd=0.0,
    )
    with pytest.raises(IllegalSegmentTransitionError):
        finalize_narration_segment_asset(
            conn, asset.id,
            audio_path="x.wav", audio_sha256="e" * 64,
            duration_seconds=1.0, characters_billed=5, cost_usd=0.0,
        )


def test_reject_segment_asset(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    asset = create_narration_segment_asset(conn, _asset_draft(run.id, 1))
    finalize_narration_segment_asset(
        conn, asset.id,
        audio_path="x.wav", audio_sha256="e" * 64,
        duration_seconds=1.0, characters_billed=5, cost_usd=0.0,
    )
    rejected = reject_narration_segment_asset(conn, asset.id, reason_code="pacing")
    assert rejected.status == "rejected"


def test_reject_segment_invalid_reason_code_raises(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    asset = create_narration_segment_asset(conn, _asset_draft(run.id, 1))
    with pytest.raises(InvalidNarrationReasonCodeError):
        reject_narration_segment_asset(conn, asset.id, reason_code="bad_code")


def test_reject_pending_asset_raises(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    asset = create_narration_segment_asset(conn, _asset_draft(run.id, 1))
    with pytest.raises(IllegalSegmentTransitionError):
        reject_narration_segment_asset(conn, asset.id, reason_code="pacing")


def test_other_reason_code_requires_notes(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    asset = create_narration_segment_asset(conn, _asset_draft(run.id, 1))
    finalize_narration_segment_asset(
        conn, asset.id,
        audio_path="x.wav", audio_sha256="e" * 64,
        duration_seconds=1.0, characters_billed=5, cost_usd=0.0,
    )
    with pytest.raises(InvalidNarrationReasonCodeError):
        reject_narration_segment_asset(conn, asset.id, reason_code="other")


def test_finalize_after_rejection_creates_review_event(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    asset1 = create_narration_segment_asset(conn, _asset_draft(run.id, 1, input_hash="x" * 64))
    finalize_narration_segment_asset(
        conn, asset1.id,
        audio_path="x.wav", audio_sha256="e" * 64,
        duration_seconds=1.0, characters_billed=5, cost_usd=0.0,
    )
    reject_narration_segment_asset(conn, asset1.id, reason_code="pacing")
    asset2 = create_narration_segment_asset(conn, _asset_draft(run.id, 1, input_hash="y" * 64))
    finalize_narration_segment_asset(
        conn, asset2.id,
        audio_path="y.wav", audio_sha256="f" * 64,
        duration_seconds=1.5, characters_billed=5, cost_usd=0.0,
    )
    events = list_narration_review_events(conn, run.id)
    assert any(e.event_type == "segment_regenerated" for e in events)
    assert asset1.id != asset2.id


def test_get_segment_assets_for_run(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    create_narration_segment_asset(conn, _asset_draft(run.id, 1))
    create_narration_segment_asset(conn, _asset_draft(run.id, 2, input_hash="y" * 64))
    assets = get_segment_assets_for_run(conn, run.id)
    assert len(assets) == 2


# ── TTS call tests ────────────────────────────────────────────────────────────


def test_record_tts_call(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    call_id = record_tts_call(
        conn,
        run_id=run.id,
        segment_id=1,
        provider="fake",
        model="fake/FAKE",
        voice_id="fake-voice",
        input_characters=50,
        characters_billed=50,
        output_format="wav",
        sample_rate_hz=22050,
        duration_seconds=2.0,
        cost_usd=0.0,
        success=True,
    )
    assert call_id is not None and call_id > 0


def test_record_tts_call_failure(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    call_id = record_tts_call(
        conn,
        run_id=run.id,
        segment_id=None,
        provider="fake",
        model="fake/FAKE",
        voice_id="fake-voice",
        input_characters=50,
        characters_billed=0,
        output_format="wav",
        sample_rate_hz=22050,
        duration_seconds=None,
        cost_usd=0.0,
        success=False,
        error_message="TTS failure",
    )
    assert call_id > 0


# ── Correction 1: Supersession ────────────────────────────────────────────────


def test_approve_supersedes_prior_approved_run(conn: sqlite3.Connection) -> None:
    run1_id = _make_completed_run_with_assets(conn)
    approve_narration_run(conn, run1_id)

    vp = create_voice_profile(conn, _vpc())
    run2 = create_narration_run(conn, _run_draft(vp.id))
    for seg_id in (1, 2):
        asset = create_narration_segment_asset(
            conn, _asset_draft(run2.id, seg_id, input_hash=("z" * 63 + str(seg_id)))
        )
        finalize_narration_segment_asset(
            conn, asset.id,
            audio_path=f"x_{seg_id}.wav", audio_sha256="e" * 64,
            duration_seconds=2.0, characters_billed=10, cost_usd=0.0,
        )
    complete_narration_run(conn, run2.id)
    approve_narration_run(conn, run2.id)

    prior = get_narration_run(conn, run1_id)
    assert prior is not None
    assert prior.status == "approved"
    assert prior.superseded_at is not None
    assert prior.superseded_by_run_id == run2.id


def test_approve_prior_run_retains_approved_status(conn: sqlite3.Connection) -> None:
    run1_id = _make_completed_run_with_assets(conn)
    approve_narration_run(conn, run1_id)

    vp = create_voice_profile(conn, _vpc())
    run2 = create_narration_run(conn, _run_draft(vp.id))
    for seg_id in (1, 2):
        asset = create_narration_segment_asset(
            conn, _asset_draft(run2.id, seg_id, input_hash=("z" * 63 + str(seg_id)))
        )
        finalize_narration_segment_asset(
            conn, asset.id,
            audio_path=f"x_{seg_id}.wav", audio_sha256="e" * 64,
            duration_seconds=2.0, characters_billed=10, cost_usd=0.0,
        )
    complete_narration_run(conn, run2.id)
    approve_narration_run(conn, run2.id)

    prior = get_narration_run(conn, run1_id)
    assert prior is not None
    assert prior.status == "approved"


def test_approve_no_rejection_event_for_superseded(conn: sqlite3.Connection) -> None:
    run1_id = _make_completed_run_with_assets(conn)
    approve_narration_run(conn, run1_id)

    vp = create_voice_profile(conn, _vpc())
    run2 = create_narration_run(conn, _run_draft(vp.id))
    for seg_id in (1, 2):
        asset = create_narration_segment_asset(
            conn, _asset_draft(run2.id, seg_id, input_hash=("z" * 63 + str(seg_id)))
        )
        finalize_narration_segment_asset(
            conn, asset.id,
            audio_path=f"x_{seg_id}.wav", audio_sha256="e" * 64,
            duration_seconds=2.0, characters_billed=10, cost_usd=0.0,
        )
    complete_narration_run(conn, run2.id)
    approve_narration_run(conn, run2.id)

    events_run1 = list_narration_review_events(conn, run1_id)
    assert not any(e.event_type == "run_rejected" for e in events_run1)


def test_active_approved_returns_replacement_after_supersession(conn: sqlite3.Connection) -> None:
    run1_id = _make_completed_run_with_assets(conn)
    approve_narration_run(conn, run1_id)

    vp = create_voice_profile(conn, _vpc())
    run2 = create_narration_run(conn, _run_draft(vp.id))
    for seg_id in (1, 2):
        asset = create_narration_segment_asset(
            conn, _asset_draft(run2.id, seg_id, input_hash=("z" * 63 + str(seg_id)))
        )
        finalize_narration_segment_asset(
            conn, asset.id,
            audio_path=f"x_{seg_id}.wav", audio_sha256="e" * 64,
            duration_seconds=2.0, characters_billed=10, cost_usd=0.0,
        )
    complete_narration_run(conn, run2.id)
    approve_narration_run(conn, run2.id)

    active = get_active_approved_narration_run(conn, plan_id=1)
    assert active is not None
    assert active.id == run2.id


def test_historical_approved_run_still_queryable(conn: sqlite3.Connection) -> None:
    run1_id = _make_completed_run_with_assets(conn)
    approve_narration_run(conn, run1_id)

    vp = create_voice_profile(conn, _vpc())
    run2 = create_narration_run(conn, _run_draft(vp.id))
    for seg_id in (1, 2):
        asset = create_narration_segment_asset(
            conn, _asset_draft(run2.id, seg_id, input_hash=("z" * 63 + str(seg_id)))
        )
        finalize_narration_segment_asset(
            conn, asset.id,
            audio_path=f"x_{seg_id}.wav", audio_sha256="e" * 64,
            duration_seconds=2.0, characters_billed=10, cost_usd=0.0,
        )
    complete_narration_run(conn, run2.id)
    approve_narration_run(conn, run2.id)

    historical = get_narration_run(conn, run1_id)
    assert historical is not None
    assert historical.status == "approved"
    assert historical.superseded_at is not None


def test_approve_non_completed_run_raises(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    with pytest.raises(IllegalNarrationTransitionError):
        approve_narration_run(conn, run.id)


def test_approve_event_has_context(conn: sqlite3.Connection) -> None:
    run_id = _make_completed_run_with_assets(conn)
    approve_narration_run(conn, run_id, actor="reviewer-1")
    events = list_narration_review_events(conn, run_id)
    ev = next(e for e in events if e.event_type == "run_approved")
    assert ev.plan_id == 1
    assert ev.script_id == 1
    assert ev.topic_id == 1
    assert ev.actor == "reviewer-1"


# ── Correction 5: Severity validation ────────────────────────────────────────


def _make_synthesized_asset(conn: sqlite3.Connection) -> int:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    asset = create_narration_segment_asset(conn, _asset_draft(run.id, 1))
    finalize_narration_segment_asset(
        conn, asset.id,
        audio_path="x.wav", audio_sha256="e" * 64,
        duration_seconds=1.0, characters_billed=5, cost_usd=0.0,
    )
    return asset.id


def test_severity_none_accepted(conn: sqlite3.Connection) -> None:
    asset_id = _make_synthesized_asset(conn)
    rejected = reject_narration_segment_asset(conn, asset_id, reason_code="pacing", severity=None)
    assert rejected.status == "rejected"


def test_severity_1_accepted(conn: sqlite3.Connection) -> None:
    asset_id = _make_synthesized_asset(conn)
    rejected = reject_narration_segment_asset(conn, asset_id, reason_code="pacing", severity=1)
    assert rejected.status == "rejected"


def test_severity_5_accepted(conn: sqlite3.Connection) -> None:
    asset_id = _make_synthesized_asset(conn)
    rejected = reject_narration_segment_asset(conn, asset_id, reason_code="pacing", severity=5)
    assert rejected.status == "rejected"


def test_severity_0_raises(conn: sqlite3.Connection) -> None:
    asset_id = _make_synthesized_asset(conn)
    with pytest.raises(InvalidNarrationSeverityError):
        reject_narration_segment_asset(conn, asset_id, reason_code="pacing", severity=0)


def test_severity_6_raises(conn: sqlite3.Connection) -> None:
    asset_id = _make_synthesized_asset(conn)
    with pytest.raises(InvalidNarrationSeverityError):
        reject_narration_segment_asset(conn, asset_id, reason_code="pacing", severity=6)


def test_severity_negative_raises(conn: sqlite3.Connection) -> None:
    asset_id = _make_synthesized_asset(conn)
    with pytest.raises(InvalidNarrationSeverityError):
        reject_narration_segment_asset(conn, asset_id, reason_code="pacing", severity=-1)


def test_severity_validated_before_savepoint(conn: sqlite3.Connection) -> None:
    asset_id = _make_synthesized_asset(conn)
    with pytest.raises(InvalidNarrationSeverityError):
        reject_narration_segment_asset(conn, asset_id, reason_code="pacing", severity=99)
    # Asset must still be synthesized (no partial DB change).
    from app.narration.repository import require_narration_segment_asset
    asset = require_narration_segment_asset(conn, asset_id)
    assert asset.status == "synthesized"


# ── Correction 3 & 4: Review event context fields ────────────────────────────


def test_segment_rejection_event_has_context(conn: sqlite3.Connection) -> None:
    asset_id = _make_synthesized_asset(conn)
    reject_narration_segment_asset(
        conn, asset_id, reason_code="pacing", severity=2, actor="qa-1",
        expected_correction="slow down",
    )
    conn.row_factory = sqlite3.Row
    ev_row = conn.execute(
        "SELECT * FROM narration_review_events WHERE event_type='segment_rejected'"
    ).fetchone()
    assert ev_row["plan_id"] == 1
    assert ev_row["script_id"] == 1
    assert ev_row["topic_id"] == 1
    assert ev_row["provider"] == "fake"
    assert ev_row["asset_id"] == asset_id
    assert ev_row["replacement_asset_id"] is None
    assert ev_row["actor"] == "qa-1"
    assert ev_row["expected_correction"] == "slow down"
    assert ev_row["severity"] == 2


def test_regeneration_event_identifies_both_assets(conn: sqlite3.Connection) -> None:
    vp = create_voice_profile(conn, _vpc())
    run = create_narration_run(conn, _run_draft(vp.id))
    asset1 = create_narration_segment_asset(conn, _asset_draft(run.id, 1, input_hash="x" * 64))
    finalize_narration_segment_asset(
        conn, asset1.id,
        audio_path="x.wav", audio_sha256="e" * 64,
        duration_seconds=1.0, characters_billed=5, cost_usd=0.0,
    )
    reject_narration_segment_asset(conn, asset1.id, reason_code="pacing")
    asset2 = create_narration_segment_asset(conn, _asset_draft(run.id, 1, input_hash="y" * 64))
    finalize_narration_segment_asset(
        conn, asset2.id,
        audio_path="y.wav", audio_sha256="f" * 64,
        duration_seconds=1.5, characters_billed=5, cost_usd=0.0,
        actor="qa-1",
    )
    conn.row_factory = sqlite3.Row
    ev_row = conn.execute(
        "SELECT * FROM narration_review_events WHERE event_type='segment_regenerated'"
    ).fetchone()
    assert ev_row is not None
    assert ev_row["asset_id"] == asset1.id
    assert ev_row["replacement_asset_id"] == asset2.id
    assert ev_row["plan_id"] == 1
    assert ev_row["actor"] == "qa-1"


def test_review_event_context_unchanged_after_upstream_change(conn: sqlite3.Connection) -> None:
    asset_id = _make_synthesized_asset(conn)
    reject_narration_segment_asset(conn, asset_id, reason_code="pacing", actor="qa-1")
    conn.row_factory = sqlite3.Row
    ev_before = conn.execute(
        "SELECT plan_id, actor FROM narration_review_events WHERE event_type='segment_rejected'"
    ).fetchone()
    plan_id_snapshot = ev_before["plan_id"]
    actor_snapshot = ev_before["actor"]

    # Mutate a topic title (upstream change).
    conn.execute("UPDATE topics SET title='Changed Title' WHERE id=1")
    conn.commit()

    ev_after = conn.execute(
        "SELECT plan_id, actor FROM narration_review_events WHERE event_type='segment_rejected'"
    ).fetchone()
    assert ev_after["plan_id"] == plan_id_snapshot
    assert ev_after["actor"] == actor_snapshot
