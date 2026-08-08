"""Tests for src/app/captions/exporters.py."""

from __future__ import annotations

import json

from app.captions.exporters import render_json, render_srt, render_vtt
from app.captions.models import CaptionCueDraft


def _make_cue(
    cue_index: int,
    segment_cue_index: int = 0,
    segment_id: int = 1,
    asset_id: int = 10,
    text: str = "Hello world",
    start_ms: int = 0,
    end_ms: int = 2000,
) -> CaptionCueDraft:
    return CaptionCueDraft(
        segment_id=segment_id,
        narration_asset_id=asset_id,
        narration_text_hash="a" * 64,
        audio_sha256="b" * 64,
        cue_index=cue_index,
        segment_cue_index=segment_cue_index,
        lines=text.split("\n"),
        start_ms=start_ms,
        end_ms=end_ms,
        timing_source="estimated",
    )


def _base_json_kwargs() -> dict:
    return {
        "caption_run_id": 1,
        "narration_run_id": 7,
        "plan_id": 2,
        "script_id": 3,
        "topic_id": 4,
        "experiment_id": None,
        "caption_schema_version": "Caption-v1",
        "segmentation_version": "caption-segment-v1",
        "timing_algorithm_version": "caption-timing-estimated-v1",
        "style_version": "caption-style-v1",
        "exporter_version": "caption-exporter-v1",
        "language": "en-US",
    }


class TestSRTExporter:
    def test_empty_cues_returns_empty_string(self):
        assert render_srt([]) == ""

    def test_single_cue_format(self):
        cue = _make_cue(0, start_ms=0, end_ms=2500)
        srt = render_srt([cue])
        assert "1\n" in srt
        assert "00:00:00,000 --> 00:00:02,500" in srt
        assert "Hello world" in srt

    def test_one_based_index(self):
        cue1 = _make_cue(0, start_ms=0, end_ms=1000)
        cue2 = _make_cue(1, segment_cue_index=1, start_ms=1000, end_ms=2000, text="Second")
        srt = render_srt([cue1, cue2])
        lines = srt.strip().split("\n\n")
        assert lines[0].startswith("1\n")
        assert lines[1].startswith("2\n")

    def test_multi_line_cue(self):
        cue = _make_cue(0, text="Line one\nLine two", start_ms=0, end_ms=3000)
        srt = render_srt([cue])
        assert "Line one\nLine two" in srt

    def test_srt_timestamp_format(self):
        cue = _make_cue(0, start_ms=3723456, end_ms=3725000)  # 1h2m3s456ms
        srt = render_srt([cue])
        assert "01:02:03,456 --> 01:02:05,000" in srt

    def test_deterministic(self):
        cues = [
            _make_cue(0),
            _make_cue(1, segment_cue_index=1, start_ms=2000, end_ms=4000, text="bye"),
        ]
        assert render_srt(cues) == render_srt(cues)

    def test_blocks_separated_by_blank_line(self):
        cue1 = _make_cue(0, start_ms=0, end_ms=1000, text="First")
        cue2 = _make_cue(1, segment_cue_index=1, start_ms=1000, end_ms=2000, text="Second")
        srt = render_srt([cue1, cue2])
        assert "\n\n" in srt


class TestVTTExporter:
    def test_starts_with_webvtt(self):
        cues = [_make_cue(0)]
        vtt = render_vtt(cues)
        assert vtt.startswith("WEBVTT")

    def test_empty_cues_returns_header(self):
        vtt = render_vtt([])
        assert vtt.startswith("WEBVTT")

    def test_vtt_timestamp_format(self):
        cue = _make_cue(0, start_ms=0, end_ms=2500)
        vtt = render_vtt([cue])
        assert "00:00:00.000 --> 00:00:02.500" in vtt

    def test_cue_id_zero_based(self):
        cue = _make_cue(0, start_ms=0, end_ms=1000)
        vtt = render_vtt([cue])
        assert "\n0\n" in vtt

    def test_multi_cue(self):
        cue1 = _make_cue(0, start_ms=0, end_ms=1000, text="First")
        cue2 = _make_cue(1, segment_cue_index=1, start_ms=1000, end_ms=2000, text="Second")
        vtt = render_vtt([cue1, cue2])
        assert "First" in vtt
        assert "Second" in vtt

    def test_vtt_timestamp_hours(self):
        cue = _make_cue(0, start_ms=3_661_000, end_ms=3_662_000)  # 1h1m1s
        vtt = render_vtt([cue])
        assert "01:01:01.000 --> 01:01:02.000" in vtt

    def test_deterministic(self):
        cues = [_make_cue(0), _make_cue(1, segment_cue_index=1, start_ms=2000, end_ms=4000)]
        assert render_vtt(cues) == render_vtt(cues)


class TestJSONExporter:
    def test_valid_json(self):
        cues = [_make_cue(0)]
        raw = render_json(cues, **_base_json_kwargs())
        doc = json.loads(raw)
        assert isinstance(doc, dict)

    def test_provenance_fields_present(self):
        cues = [_make_cue(0)]
        doc = json.loads(render_json(cues, **_base_json_kwargs()))
        for key in (
            "caption_run_id",
            "narration_run_id",
            "plan_id",
            "script_id",
            "topic_id",
            "experiment_id",
            "caption_schema_version",
            "segmentation_version",
            "timing_algorithm_version",
            "style_version",
            "exporter_version",
            "language",
            "cues",
        ):
            assert key in doc, f"Missing key: {key}"

    def test_cue_fields_present(self):
        cues = [_make_cue(0, start_ms=0, end_ms=2000)]
        doc = json.loads(render_json(cues, **_base_json_kwargs()))
        assert len(doc["cues"]) == 1
        cue_doc = doc["cues"][0]
        for key in (
            "cue_index",
            "segment_id",
            "segment_cue_index",
            "narration_asset_id",
            "narration_text_hash",
            "audio_sha256",
            "text",
            "lines",
            "start_ms",
            "end_ms",
            "line_count",
            "char_count",
            "timing_source",
            "warnings",
        ):
            assert key in cue_doc, f"Missing cue key: {key}"

    def test_cue_start_end_ms_stored(self):
        cue = _make_cue(0, start_ms=1234, end_ms=5678)
        doc = json.loads(render_json([cue], **_base_json_kwargs()))
        assert doc["cues"][0]["start_ms"] == 1234
        assert doc["cues"][0]["end_ms"] == 5678

    def test_deterministic(self):
        cues = [_make_cue(0), _make_cue(1, segment_cue_index=1, start_ms=2000, end_ms=4000)]
        r1 = render_json(cues, **_base_json_kwargs())
        r2 = render_json(cues, **_base_json_kwargs())
        assert r1 == r2

    def test_experiment_id_null_preserved(self):
        cues = [_make_cue(0)]
        doc = json.loads(render_json(cues, **_base_json_kwargs()))
        assert doc["experiment_id"] is None

    def test_experiment_id_stored(self):
        cues = [_make_cue(0)]
        kwargs = {**_base_json_kwargs(), "experiment_id": "exp-123"}
        doc = json.loads(render_json(cues, **kwargs))
        assert doc["experiment_id"] == "exp-123"

    def test_empty_cues_valid_json(self):
        doc = json.loads(render_json([], **_base_json_kwargs()))
        assert doc["cues"] == []

    def test_unicode_preserved(self):
        cue = _make_cue(0, text="café München")
        doc = json.loads(render_json([cue], **_base_json_kwargs()))
        assert doc["cues"][0]["text"] == "café München"
