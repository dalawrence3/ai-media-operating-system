"""Tests for Phase 6 M6.2 narration models."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.narration.models import (
    AudioMetadata,
    NarrationReviewEvent,
    NarrationRun,
    NarrationRunDraft,
    NarrationRunResult,
    NarrationSegmentAsset,
    NarrationSegmentAssetDraft,
    TtsCall,
    VoiceProfile,
    VoiceProfileCreate,
)

NOW = datetime.now(UTC).isoformat()


# ── Creation dataclasses ──────────────────────────────────────────────────────


def test_voice_profile_create_fields() -> None:
    vpc = VoiceProfileCreate(
        channel_id=1,
        provider="fake",
        model="fake/FAKE",
        voice_id="v1",
        name="Test",
        language="en-US",
        speaking_rate=1.0,
        style=None,
        stability=None,
        similarity_boost=None,
        settings_json="{}",
    )
    assert vpc.provider == "fake"
    assert vpc.version == 1
    assert vpc.is_default is False


def test_narration_run_draft_fields() -> None:
    draft = NarrationRunDraft(
        plan_id=1,
        plan_input_hash="a" * 64,
        voice_profile_id=1,
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
    assert draft.experiment_id is None
    assert draft.notes is None


def test_narration_segment_asset_draft_fields() -> None:
    draft = NarrationSegmentAssetDraft(
        run_id=1,
        segment_id=2,
        narration_text_hash="b" * 64,
        provider="fake",
        model="fake/FAKE",
        voice_id="v1",
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
    assert draft.run_id == 1
    assert draft.segment_id == 2


def test_audio_metadata_fields() -> None:
    meta = AudioMetadata(
        duration_seconds=3.5,
        sample_rate_hz=22050,
        num_channels=1,
        num_frames=77175,
        sample_width=2,
    )
    assert meta.duration_seconds == 3.5


def test_narration_run_result_defaults() -> None:
    result = NarrationRunResult(run_id=1)
    assert result.assets == []
    assert result.skipped_segment_ids == []


# ── Pydantic frozen models ────────────────────────────────────────────────────


def _make_row(data: dict) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cols = ", ".join(f"? AS [{k}]" for k in data)
    row = conn.execute(f"SELECT {cols}", list(data.values())).fetchone()
    conn.close()
    return row


def test_voice_profile_from_row() -> None:
    data = dict(
        id=1,
        channel_id=None,
        provider="fake",
        model="fake/FAKE",
        voice_id="v1",
        name="Test",
        language="en-US",
        speaking_rate=1.0,
        style=None,
        stability=None,
        similarity_boost=None,
        settings_json="{}",
        version=1,
        is_default=0,
        superseded_by_id=None,
        created_at=NOW,
        updated_at=NOW,
    )
    vp = VoiceProfile.from_row(_make_row(data))
    assert vp.provider == "fake"
    assert vp.id == 1


def test_voice_profile_is_frozen() -> None:
    data = dict(
        id=1,
        channel_id=None,
        provider="fake",
        model="fake/FAKE",
        voice_id="v1",
        name="Test",
        language="en-US",
        speaking_rate=1.0,
        style=None,
        stability=None,
        similarity_boost=None,
        settings_json="{}",
        version=1,
        is_default=0,
        superseded_by_id=None,
        created_at=NOW,
        updated_at=NOW,
    )
    vp = VoiceProfile.from_row(_make_row(data))
    with pytest.raises(ValidationError):
        vp.provider = "openai"  # type: ignore[misc]


def test_narration_run_from_row() -> None:
    data = dict(
        id=1,
        plan_id=1,
        plan_input_hash="a" * 64,
        voice_profile_id=1,
        voice_profile_version=1,
        language="en-US",
        speaking_rate=1.0,
        style=None,
        stability=None,
        similarity_boost=None,
        settings_json="{}",
        output_format="wav",
        sample_rate_hz=22050,
        input_hash="b" * 64,
        status="running",
        experiment_id=None,
        notes=None,
        error_message=None,
        completed_at=None,
        approved_at=None,
        rejected_at=None,
        superseded_at=None,
        superseded_by_run_id=None,
        created_at=NOW,
        updated_at=NOW,
    )
    run = NarrationRun.from_row(_make_row(data))
    assert run.status == "running"
    assert run.plan_id == 1
    assert run.superseded_at is None
    assert run.superseded_by_run_id is None


def test_narration_segment_asset_from_row() -> None:
    data = dict(
        id=1,
        run_id=1,
        segment_id=2,
        narration_text_hash="b" * 64,
        provider="fake",
        model="fake/FAKE",
        voice_id="v1",
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
        status="pending",
        audio_path=None,
        audio_sha256=None,
        duration_seconds=None,
        characters_billed=None,
        cost_usd=None,
        superseded_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    asset = NarrationSegmentAsset.from_row(_make_row(data))
    assert asset.status == "pending"
    assert asset.segment_id == 2


def test_tts_call_from_row() -> None:
    data = dict(
        id=1,
        run_id=1,
        segment_id=2,
        provider="fake",
        model="fake/FAKE",
        voice_id="v1",
        input_characters=100,
        characters_billed=100,
        output_format="wav",
        sample_rate_hz=22050,
        duration_seconds=2.5,
        cost_usd=0.0,
        success=1,
        error_message=None,
        latency_ms=1,
        request_id="req-1",
        provider_metadata_json=None,
        narration_schema_version="Narration-v1",
        narration_algorithm_version="narration-segment-v1",
        called_at=NOW,
    )
    call = TtsCall.from_row(_make_row(data))
    assert call.success is True
    assert call.cost_usd == 0.0


def test_narration_review_event_from_row() -> None:
    data = dict(
        id=1,
        run_id=1,
        plan_id=1,
        script_id=1,
        topic_id=1,
        voice_profile_id=1,
        provider="fake",
        model="fake/FAKE",
        voice_id="fake-voice",
        experiment_id=None,
        segment_id=None,
        asset_id=None,
        replacement_asset_id=None,
        event_type="run_approved",
        reason_code=None,
        severity=None,
        expected_correction=None,
        notes=None,
        actor="operator",
        created_at=NOW,
    )
    evt = NarrationReviewEvent.from_row(_make_row(data))
    assert evt.event_type == "run_approved"
    assert evt.actor == "operator"
    assert evt.plan_id == 1
    assert evt.replacement_asset_id is None
