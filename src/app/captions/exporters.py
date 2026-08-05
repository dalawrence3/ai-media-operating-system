"""Phase 6 M6.3A deterministic caption exporters.

All exporters are pure functions — they accept caption data and return
text/bytes.  No filesystem I/O occurs here.

All output behaviour is governed by CAPTION_EXPORTER_VERSION.  Changing
line formatting, timestamp format, JSON structure, or metadata fields
requires bumping that version constant.

Formats:
  SRT  — SubRip subtitle format (.srt)
  WebVTT — Web Video Text Tracks (.vtt)
  JSON — Canonical provenance export with full metadata
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.captions.models import CaptionCue, CaptionCueDraft

if TYPE_CHECKING:
    pass

# Either draft (pre-persistence) or DB cue rows can be rendered.
_AnyCue = CaptionCueDraft | CaptionCue

# ── Timestamp formatting ────────────────────────────────────────────────────


def _ms_to_srt_timestamp(ms: int) -> str:
    """Format milliseconds as SRT timestamp: HH:MM:SS,mmm."""
    total_s, millis = divmod(ms, 1000)
    total_m, secs = divmod(total_s, 60)
    hours, mins = divmod(total_m, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def _ms_to_vtt_timestamp(ms: int) -> str:
    """Format milliseconds as WebVTT timestamp: HH:MM:SS.mmm."""
    total_s, millis = divmod(ms, 1000)
    total_m, secs = divmod(total_s, 60)
    hours, mins = divmod(total_m, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d}.{millis:03d}"


# ── SRT exporter ─────────────────────────────────────────────────────────────


def render_srt(cues: list[_AnyCue]) -> str:
    """Render cues as a SubRip (.srt) formatted string.

    Each cue block:
        <index>\\n
        <start> --> <end>\\n
        <text>\\n
        \\n

    Index is 1-based.  Text preserves display newlines within the cue.
    """
    blocks: list[str] = []
    for cue in cues:
        idx = cue.cue_index + 1  # SRT uses 1-based indexes
        start = _ms_to_srt_timestamp(cue.start_ms)
        end = _ms_to_srt_timestamp(cue.end_ms)
        text = cue.text
        blocks.append(f"{idx}\n{start} --> {end}\n{text}")
    return "\n\n".join(blocks) + "\n" if blocks else ""


# ── WebVTT exporter ──────────────────────────────────────────────────────────


def render_vtt(cues: list[_AnyCue]) -> str:
    """Render cues as a WebVTT (.vtt) formatted string.

    Header line: "WEBVTT"
    Optional NOTE block with provenance.
    Cue blocks:
        <id>\\n
        <start> --> <end>\\n
        <text>\\n
        \\n

    Cue IDs use the 0-based cue_index.
    """
    lines: list[str] = ["WEBVTT", ""]
    for cue in cues:
        start = _ms_to_vtt_timestamp(cue.start_ms)
        end = _ms_to_vtt_timestamp(cue.end_ms)
        lines.append(str(cue.cue_index))
        lines.append(f"{start} --> {end}")
        lines.append(cue.text)
        lines.append("")
    return "\n".join(lines)


# ── JSON exporter ─────────────────────────────────────────────────────────────


def render_json(
    cues: list[_AnyCue],
    *,
    caption_run_id: int,
    narration_run_id: int,
    plan_id: int,
    script_id: int,
    topic_id: int,
    experiment_id: str | None,
    caption_schema_version: str,
    segmentation_version: str,
    timing_algorithm_version: str,
    style_version: str,
    exporter_version: str,
    language: str,
) -> str:
    """Render cues as a canonical JSON provenance export.

    The JSON document includes all immutable provenance required for later
    rendering, verification, and Platform Analytics attribution.  It is
    encoded as UTF-8 with compact separators for deterministic SHA-256.
    """
    cue_list: list[dict[str, Any]] = []
    for cue in cues:
        cue_list.append(
            {
                "cue_index": cue.cue_index,
                "segment_id": cue.segment_id,
                "segment_cue_index": cue.segment_cue_index,
                "narration_asset_id": cue.narration_asset_id,
                "narration_text_hash": cue.narration_text_hash,
                "audio_sha256": cue.audio_sha256,
                "text": cue.text,
                "lines": cue.lines,
                "start_ms": cue.start_ms,
                "end_ms": cue.end_ms,
                "line_count": cue.line_count,
                "char_count": cue.char_count,
                "timing_source": cue.timing_source,
                "warnings": cue.warnings,
            }
        )

    doc: dict[str, Any] = {
        "caption_run_id": caption_run_id,
        "narration_run_id": narration_run_id,
        "plan_id": plan_id,
        "script_id": script_id,
        "topic_id": topic_id,
        "experiment_id": experiment_id,
        "caption_schema_version": caption_schema_version,
        "segmentation_version": segmentation_version,
        "timing_algorithm_version": timing_algorithm_version,
        "style_version": style_version,
        "exporter_version": exporter_version,
        "language": language,
        "cues": cue_list,
    }
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
