"""Tests for src/app/captions/validation.py."""

from __future__ import annotations

from app.captions.models import CaptionCueDraft
from app.captions.validation import ValidationResult, validate_caption_cues


def _make_cue(
    cue_index: int,
    segment_cue_index: int,
    segment_id: int = 1,
    asset_id: int = 10,
    text_hash: str = "a" * 64,
    audio_sha256: str = "b" * 64,
    text: str = "Hello world",
    start_ms: int = 0,
    end_ms: int = 2000,
    timing_source: str = "estimated",
    warnings: list[str] | None = None,
) -> CaptionCueDraft:
    return CaptionCueDraft(
        segment_id=segment_id,
        narration_asset_id=asset_id,
        narration_text_hash=text_hash,
        audio_sha256=audio_sha256,
        cue_index=cue_index,
        segment_cue_index=segment_cue_index,
        lines=text.split("\n"),
        start_ms=start_ms,
        end_ms=end_ms,
        timing_source=timing_source,
        warnings=warnings or [],
    )


def _base_maps(text: str = "Hello world") -> dict:
    return {
        "segment_id_to_narration_text": {1: text},
        "segment_id_to_duration_ms": {1: 5000},
        "segment_id_to_asset_id": {1: 10},
        "segment_id_to_text_hash": {1: "a" * 64},
        "segment_id_to_audio_sha256": {1: "b" * 64},
    }


class TestValidateCaptionCues:
    def test_empty_cue_list_fails(self):
        result = validate_caption_cues(cues=[], **_base_maps())
        assert not result.ok
        assert any("empty" in e for e in result.errors)

    def test_valid_single_cue(self):
        cue = _make_cue(0, 0, start_ms=0, end_ms=2000)
        result = validate_caption_cues(cues=[cue], **_base_maps())
        assert result.ok, result.errors

    def test_valid_two_cues_sequential(self):
        cue1 = _make_cue(0, 0, start_ms=0, end_ms=2000, text="Hello")
        cue2 = _make_cue(1, 1, start_ms=2000, end_ms=4000, text="world")
        maps = {
            "segment_id_to_narration_text": {1: "Hello world"},
            "segment_id_to_duration_ms": {1: 5000},
            "segment_id_to_asset_id": {1: 10},
            "segment_id_to_text_hash": {1: "a" * 64},
            "segment_id_to_audio_sha256": {1: "b" * 64},
        }
        result = validate_caption_cues(cues=[cue1, cue2], **maps)
        assert result.ok, result.errors

    def test_negative_start_ms_fails(self):
        cue = _make_cue(0, 0, start_ms=-1, end_ms=1000)
        result = validate_caption_cues(cues=[cue], **_base_maps())
        assert not result.ok
        assert any("negative" in e for e in result.errors)

    def test_start_gte_end_fails(self):
        cue = _make_cue(0, 0, start_ms=1000, end_ms=1000)
        result = validate_caption_cues(cues=[cue], **_base_maps())
        assert not result.ok
        assert any("start_ms" in e and ">=" in e for e in result.errors)

    def test_end_exceeds_duration_plus_tolerance_fails(self):
        cue = _make_cue(0, 0, start_ms=0, end_ms=5100)  # duration=5000, tolerance=1
        result = validate_caption_cues(cues=[cue], **_base_maps())
        assert not result.ok
        assert any("exceeds" in e for e in result.errors)

    def test_end_within_tolerance_passes(self):
        from app.captions.constants import CAPTION_TIMING_ROUNDING_TOLERANCE_MS

        cue = _make_cue(0, 0, start_ms=0, end_ms=5000 + CAPTION_TIMING_ROUNDING_TOLERANCE_MS)
        result = validate_caption_cues(cues=[cue], **_base_maps())
        assert result.ok, result.errors

    def test_duplicate_global_cue_index_fails(self):
        cue1 = _make_cue(0, 0, start_ms=0, end_ms=1000, text="Hello")
        cue2 = _make_cue(0, 1, start_ms=1000, end_ms=2000, text="world")
        maps = {
            "segment_id_to_narration_text": {1: "Hello world"},
            "segment_id_to_duration_ms": {1: 5000},
            "segment_id_to_asset_id": {1: 10},
            "segment_id_to_text_hash": {1: "a" * 64},
            "segment_id_to_audio_sha256": {1: "b" * 64},
        }
        result = validate_caption_cues(cues=[cue1, cue2], **maps)
        assert not result.ok
        assert any("duplicate global cue_index" in e for e in result.errors)

    def test_global_cue_index_gap_fails(self):
        cue1 = _make_cue(0, 0, start_ms=0, end_ms=1000, text="Hello")
        cue2 = _make_cue(2, 1, start_ms=1000, end_ms=2000, text="world")  # gap: missing index 1
        maps = {
            "segment_id_to_narration_text": {1: "Hello world"},
            "segment_id_to_duration_ms": {1: 5000},
            "segment_id_to_asset_id": {1: 10},
            "segment_id_to_text_hash": {1: "a" * 64},
            "segment_id_to_audio_sha256": {1: "b" * 64},
        }
        result = validate_caption_cues(cues=[cue1, cue2], **maps)
        assert not result.ok
        assert any("gap" in e for e in result.errors)

    def test_overlap_within_segment_fails(self):
        cue1 = _make_cue(0, 0, start_ms=0, end_ms=2000, text="Hello")
        cue2 = _make_cue(1, 1, start_ms=1500, end_ms=3000, text="world")  # overlaps
        maps = {
            "segment_id_to_narration_text": {1: "Hello world"},
            "segment_id_to_duration_ms": {1: 5000},
            "segment_id_to_asset_id": {1: 10},
            "segment_id_to_text_hash": {1: "a" * 64},
            "segment_id_to_audio_sha256": {1: "b" * 64},
        }
        result = validate_caption_cues(cues=[cue1, cue2], **maps)
        assert not result.ok
        assert any("overlap" in e for e in result.errors)

    def test_text_integrity_violation_fails(self):
        cue = _make_cue(0, 0, text="Something else entirely", start_ms=0, end_ms=2000)
        result = validate_caption_cues(cues=[cue], **_base_maps("Hello world"))
        assert not result.ok
        assert any("integrity" in e for e in result.errors)

    def test_invalid_timing_source_fails(self):
        cue = _make_cue(0, 0, timing_source="magic")
        result = validate_caption_cues(cues=[cue], **_base_maps())
        assert not result.ok
        assert any("timing_source" in e for e in result.errors)

    def test_asset_id_mismatch_fails(self):
        cue = _make_cue(0, 0, asset_id=99)  # maps says 10
        result = validate_caption_cues(cues=[cue], **_base_maps())
        assert not result.ok
        assert any("narration_asset_id" in e for e in result.errors)

    def test_text_hash_mismatch_fails(self):
        cue = _make_cue(0, 0, text_hash="z" * 64)
        result = validate_caption_cues(cues=[cue], **_base_maps())
        assert not result.ok
        assert any("narration_text_hash" in e for e in result.errors)

    def test_audio_hash_mismatch_fails(self):
        cue = _make_cue(0, 0, audio_sha256="z" * 64)
        result = validate_caption_cues(cues=[cue], **_base_maps())
        assert not result.ok
        assert any("audio_sha256" in e for e in result.errors)

    def test_two_segments_multi_cue_valid(self):
        cue1 = _make_cue(0, 0, segment_id=1, asset_id=10, text="Hello", start_ms=0, end_ms=1500)
        cue2 = _make_cue(
            1,
            0,
            segment_id=2,
            asset_id=20,
            text_hash="c" * 64,
            audio_sha256="d" * 64,
            text="world",
            start_ms=0,
            end_ms=2000,
        )
        maps = {
            "segment_id_to_narration_text": {1: "Hello", 2: "world"},
            "segment_id_to_duration_ms": {1: 1500, 2: 2000},
            "segment_id_to_asset_id": {1: 10, 2: 20},
            "segment_id_to_text_hash": {1: "a" * 64, 2: "c" * 64},
            "segment_id_to_audio_sha256": {1: "b" * 64, 2: "d" * 64},
        }
        result = validate_caption_cues(cues=[cue1, cue2], **maps)
        assert result.ok, result.errors

    def test_validation_result_fail_accumulates_errors(self):
        r = ValidationResult()
        r.fail("first")
        r.fail("second")
        assert not r.ok
        assert len(r.errors) == 2

    def test_validation_result_warn_accumulates_warnings(self):
        r = ValidationResult()
        r.warn("x")
        r.warn("y")
        assert r.ok  # warnings don't fail
        assert len(r.run_warnings) == 2
