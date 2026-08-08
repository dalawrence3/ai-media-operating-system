"""Tests for src/app/captions/models.py."""

from __future__ import annotations

from app.captions.models import CaptionCueDraft, CaptionRunDraft


class TestCaptionCueDraft:
    def _make(self, lines: list[str] | None = None) -> CaptionCueDraft:
        return CaptionCueDraft(
            segment_id=1,
            narration_asset_id=10,
            narration_text_hash="a" * 64,
            audio_sha256="b" * 64,
            cue_index=0,
            segment_cue_index=0,
            lines=lines or ["Hello world"],
            start_ms=0,
            end_ms=1500,
            timing_source="estimated",
        )

    def test_text_single_line(self):
        cue = self._make(["Hello world"])
        assert cue.text == "Hello world"

    def test_text_two_lines(self):
        cue = self._make(["Hello", "world"])
        assert cue.text == "Hello\nworld"

    def test_char_count_single_line(self):
        cue = self._make(["Hello"])
        assert cue.char_count == 5

    def test_char_count_two_lines_no_newline(self):
        cue = self._make(["Hello", "world"])
        assert cue.char_count == 10  # 5 + 5, no newline

    def test_line_count(self):
        assert self._make(["a"]).line_count == 1
        assert self._make(["a", "b"]).line_count == 2

    def test_duration_ms(self):
        cue = self._make()
        assert cue.duration_ms == 1500

    def test_default_warnings_empty(self):
        cue = self._make()
        assert cue.warnings == []

    def test_warnings_stored(self):
        cue = self._make()
        cue.warnings.append("short cue")
        assert cue.warnings == ["short cue"]


class TestCaptionRunDraft:
    def _make(self, cues: list[CaptionCueDraft] | None = None) -> CaptionRunDraft:
        return CaptionRunDraft(
            narration_run_id=1,
            plan_id=2,
            script_id=3,
            topic_id=4,
            experiment_id=None,
            input_hash="c" * 64,
            caption_schema_version="Caption-v1",
            segmentation_version="caption-segment-v1",
            timing_algorithm_version="caption-timing-estimated-v1",
            style_version="caption-style-v1",
            exporter_version="caption-exporter-v1",
            language="en-US",
            cues=cues or [],
        )

    def test_total_cue_count_empty(self):
        draft = self._make()
        assert draft.total_cue_count == 0

    def test_total_cue_count_with_cues(self):
        cue = CaptionCueDraft(
            segment_id=1,
            narration_asset_id=10,
            narration_text_hash="a" * 64,
            audio_sha256="b" * 64,
            cue_index=0,
            segment_cue_index=0,
            lines=["test"],
            start_ms=0,
            end_ms=1000,
            timing_source="estimated",
        )
        draft = self._make([cue, cue])
        assert draft.total_cue_count == 2
