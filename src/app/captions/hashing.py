"""Phase 6 M6.3A caption hashing.

The caption input hash is a SHA-256 of compact sorted JSON.  It covers every
field that influences caption output: the narration run, per-segment asset
provenance, all five version strings, language, and experiment context.
Changing any of these fields produces a different hash and therefore a new
caption run row.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class NarrationSegmentHashInput:
    """Minimal asset provenance fields that enter the caption input hash."""

    segment_id: int
    asset_id: int
    audio_sha256: str
    narration_text_hash: str
    duration_ms: int


def compute_caption_input_hash(
    *,
    narration_run_id: int,
    narration_run_input_hash: str,
    segments: list[NarrationSegmentHashInput],
    caption_schema_version: str,
    segmentation_version: str,
    timing_algorithm_version: str,
    style_version: str,
    exporter_version: str,
    language: str,
    experiment_id: str | None,
) -> str:
    """Return the SHA-256 caption input hash for a deterministic caption run.

    The segment list is sorted by segment_id before serialisation so that
    ordering differences in the caller produce the same hash.
    """
    sorted_segments = sorted(segments, key=lambda s: s.segment_id)
    payload: dict = {
        "narration_run_id": narration_run_id,
        "narration_run_input_hash": narration_run_input_hash,
        "segments": [
            {
                "segment_id": s.segment_id,
                "asset_id": s.asset_id,
                "audio_sha256": s.audio_sha256,
                "narration_text_hash": s.narration_text_hash,
                "duration_ms": s.duration_ms,
            }
            for s in sorted_segments
        ],
        "caption_schema_version": caption_schema_version,
        "segmentation_version": segmentation_version,
        "timing_algorithm_version": timing_algorithm_version,
        "style_version": style_version,
        "exporter_version": exporter_version,
        "language": language,
        "experiment_id": experiment_id,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
