"""Tests for src/app/captions/hashing.py."""

from __future__ import annotations

from app.captions.hashing import NarrationSegmentHashInput, compute_caption_input_hash


def _make_segment(segment_id: int = 1, asset_id: int = 10) -> NarrationSegmentHashInput:
    return NarrationSegmentHashInput(
        segment_id=segment_id,
        asset_id=asset_id,
        audio_sha256="a" * 64,
        narration_text_hash="b" * 64,
        duration_ms=3000,
    )


def _base_kwargs() -> dict:
    return {
        "narration_run_id": 7,
        "narration_run_input_hash": "c" * 64,
        "segments": [_make_segment(1, 10), _make_segment(2, 11)],
        "caption_schema_version": "Caption-v1",
        "segmentation_version": "caption-segment-v1",
        "timing_algorithm_version": "caption-timing-estimated-v1",
        "style_version": "caption-style-v1",
        "exporter_version": "caption-exporter-v1",
        "language": "en-US",
        "experiment_id": None,
    }


class TestComputeCaptionInputHash:
    def test_returns_64_char_hex(self):
        h = compute_caption_input_hash(**_base_kwargs())
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self):
        h1 = compute_caption_input_hash(**_base_kwargs())
        h2 = compute_caption_input_hash(**_base_kwargs())
        assert h1 == h2

    def test_segment_order_independent(self):
        kwargs1 = _base_kwargs()
        kwargs2 = _base_kwargs()
        # Reverse the segment order; hash must be identical.
        kwargs2["segments"] = list(reversed(kwargs1["segments"]))
        assert compute_caption_input_hash(**kwargs1) == compute_caption_input_hash(**kwargs2)

    def test_different_narration_run_id_changes_hash(self):
        k1 = _base_kwargs()
        k2 = {**k1, "narration_run_id": 99}
        assert compute_caption_input_hash(**k1) != compute_caption_input_hash(**k2)

    def test_different_narration_run_input_hash_changes_hash(self):
        k1 = _base_kwargs()
        k2 = {**k1, "narration_run_input_hash": "d" * 64}
        assert compute_caption_input_hash(**k1) != compute_caption_input_hash(**k2)

    def test_different_segmentation_version_changes_hash(self):
        k1 = _base_kwargs()
        k2 = {**k1, "segmentation_version": "caption-segment-v2"}
        assert compute_caption_input_hash(**k1) != compute_caption_input_hash(**k2)

    def test_different_timing_algorithm_version_changes_hash(self):
        k1 = _base_kwargs()
        k2 = {**k1, "timing_algorithm_version": "caption-timing-provider-v1"}
        assert compute_caption_input_hash(**k1) != compute_caption_input_hash(**k2)

    def test_different_style_version_changes_hash(self):
        k1 = _base_kwargs()
        k2 = {**k1, "style_version": "caption-style-v2"}
        assert compute_caption_input_hash(**k1) != compute_caption_input_hash(**k2)

    def test_different_exporter_version_changes_hash(self):
        k1 = _base_kwargs()
        k2 = {**k1, "exporter_version": "caption-exporter-v2"}
        assert compute_caption_input_hash(**k1) != compute_caption_input_hash(**k2)

    def test_different_language_changes_hash(self):
        k1 = _base_kwargs()
        k2 = {**k1, "language": "es-ES"}
        assert compute_caption_input_hash(**k1) != compute_caption_input_hash(**k2)

    def test_experiment_id_none_vs_set(self):
        k1 = _base_kwargs()
        k2 = {**k1, "experiment_id": "exp-abc"}
        assert compute_caption_input_hash(**k1) != compute_caption_input_hash(**k2)

    def test_different_audio_sha256_changes_hash(self):
        k1 = _base_kwargs()
        k2 = _base_kwargs()
        k2["segments"] = [
            NarrationSegmentHashInput(
                segment_id=1,
                asset_id=10,
                audio_sha256="e" * 64,  # different
                narration_text_hash="b" * 64,
                duration_ms=3000,
            ),
            _make_segment(2, 11),
        ]
        assert compute_caption_input_hash(**k1) != compute_caption_input_hash(**k2)

    def test_different_narration_text_hash_changes_hash(self):
        k1 = _base_kwargs()
        k2 = _base_kwargs()
        k2["segments"] = [
            NarrationSegmentHashInput(
                segment_id=1,
                asset_id=10,
                audio_sha256="a" * 64,
                narration_text_hash="f" * 64,  # different
                duration_ms=3000,
            ),
            _make_segment(2, 11),
        ]
        assert compute_caption_input_hash(**k1) != compute_caption_input_hash(**k2)

    def test_different_duration_ms_changes_hash(self):
        k1 = _base_kwargs()
        k2 = _base_kwargs()
        k2["segments"] = [
            NarrationSegmentHashInput(
                segment_id=1,
                asset_id=10,
                audio_sha256="a" * 64,
                narration_text_hash="b" * 64,
                duration_ms=5000,  # different
            ),
            _make_segment(2, 11),
        ]
        assert compute_caption_input_hash(**k1) != compute_caption_input_hash(**k2)

    def test_single_segment(self):
        kwargs = _base_kwargs()
        kwargs["segments"] = [_make_segment()]
        h = compute_caption_input_hash(**kwargs)
        assert len(h) == 64
