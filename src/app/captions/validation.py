"""Phase 6 M6.3A caption cue set validation.

Validates the complete in-memory cue set before database persistence.
Hard failures raise CaptionValidationError.
Warnings are collected and stored per-cue and per-run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.captions.constants import (
    CAPTION_TIMING_ROUNDING_TOLERANCE_MS,
    CAPTION_VALID_TIMING_SOURCES,
)
from app.captions.models import CaptionCueDraft
from app.captions.segmentation import normalize_for_integrity


@dataclass
class ValidationResult:
    """Outcome of the cue-set validation pipeline."""

    ok: bool = True
    errors: list[str] = field(default_factory=list)
    run_warnings: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.ok = False
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.run_warnings.append(message)


def validate_caption_cues(
    *,
    cues: list[CaptionCueDraft],
    segment_id_to_narration_text: dict[int, str],
    segment_id_to_duration_ms: dict[int, int],
    segment_id_to_asset_id: dict[int, int],
    segment_id_to_text_hash: dict[int, str],
    segment_id_to_audio_sha256: dict[int, str],
) -> ValidationResult:
    """Run the full hard-validation pipeline over the assembled cue set.

    Parameters
    ----------
    cues
        The ordered list of CaptionCueDraft objects assembled by the orchestrator.
    segment_id_to_narration_text
        Maps production_segment.id → narration_text from the approved narration handoff.
    segment_id_to_duration_ms
        Maps production_segment.id → measured audio duration in milliseconds.
    segment_id_to_asset_id
        Maps production_segment.id → narration_segment_asset.id.
    segment_id_to_text_hash
        Maps production_segment.id → narration_text_hash stored on the asset.
    segment_id_to_audio_sha256
        Maps production_segment.id → audio_sha256 stored on the asset.
    """
    result = ValidationResult()

    if not cues:
        result.fail("no cues: caption cue set is empty")
        return result

    # ── 1. Individual cue hard checks ─────────────────────────────────────────

    seen_cue_indices: set[int] = set()
    seen_segment_cue_pairs: set[tuple[int, int]] = set()

    for i, cue in enumerate(cues):
        # Empty cue text
        if not cue.text.strip():
            result.fail(f"cue[{i}]: empty cue text")

        # Negative start
        if cue.start_ms < 0:
            result.fail(f"cue[{i}]: negative start_ms={cue.start_ms}")

        # start not < end
        if cue.start_ms >= cue.end_ms:
            result.fail(f"cue[{i}]: start_ms={cue.start_ms} >= end_ms={cue.end_ms}")

        # Cue end beyond segment duration (+ tolerance)
        seg_dur = segment_id_to_duration_ms.get(cue.segment_id)
        if seg_dur is not None:
            tolerance = CAPTION_TIMING_ROUNDING_TOLERANCE_MS
            if cue.end_ms > seg_dur + tolerance:
                result.fail(
                    f"cue[{i}]: end_ms={cue.end_ms} exceeds segment_duration_ms={seg_dur} "
                    f"+ tolerance={tolerance}"
                )

        # Global cue index gap/duplicate
        if cue.cue_index in seen_cue_indices:
            result.fail(f"cue[{i}]: duplicate global cue_index={cue.cue_index}")
        seen_cue_indices.add(cue.cue_index)

        # Segment cue index gap/duplicate
        pair = (cue.segment_id, cue.segment_cue_index)
        if pair in seen_segment_cue_pairs:
            result.fail(
                f"cue[{i}]: duplicate (segment_id={cue.segment_id}, "
                f"segment_cue_index={cue.segment_cue_index})"
            )
        seen_segment_cue_pairs.add(pair)

        # Invalid timing source
        if cue.timing_source not in CAPTION_VALID_TIMING_SOURCES:
            result.fail(f"cue[{i}]: invalid timing_source={cue.timing_source!r}")

        # Malformed warnings JSON
        try:
            json.dumps(cue.warnings)
        except (TypeError, ValueError) as exc:
            result.fail(f"cue[{i}]: malformed warnings: {exc}")

        # Provenance mismatch — asset ID
        expected_asset_id = segment_id_to_asset_id.get(cue.segment_id)
        if expected_asset_id is not None and cue.narration_asset_id != expected_asset_id:
            result.fail(
                f"cue[{i}]: narration_asset_id={cue.narration_asset_id} "
                f"does not match expected={expected_asset_id} for segment {cue.segment_id}"
            )

        # Narration text hash mismatch
        expected_text_hash = segment_id_to_text_hash.get(cue.segment_id)
        if expected_text_hash is not None and cue.narration_text_hash != expected_text_hash:
            result.fail(f"cue[{i}]: narration_text_hash mismatch for segment {cue.segment_id}")

        # Audio SHA-256 mismatch
        expected_audio_sha256 = segment_id_to_audio_sha256.get(cue.segment_id)
        if expected_audio_sha256 is not None and cue.audio_sha256 != expected_audio_sha256:
            result.fail(f"cue[{i}]: audio_sha256 mismatch for segment {cue.segment_id}")

    # ── 2. Timeline overlap within each segment ────────────────────────────────

    by_segment: dict[int, list[CaptionCueDraft]] = {}
    for cue in cues:
        by_segment.setdefault(cue.segment_id, []).append(cue)

    for seg_id, seg_cues in by_segment.items():
        ordered = sorted(seg_cues, key=lambda c: c.start_ms)
        for j in range(len(ordered) - 1):
            if ordered[j].end_ms > ordered[j + 1].start_ms:
                result.fail(
                    f"segment {seg_id}: cue overlap between "
                    f"cue_index={ordered[j].cue_index} (end={ordered[j].end_ms}) "
                    f"and cue_index={ordered[j + 1].cue_index} (start={ordered[j + 1].start_ms})"
                )

    # ── 3. Global cue_index must be gapless 0..N-1 ────────────────────────────

    sorted_indices = sorted(seen_cue_indices)
    for expected, actual in enumerate(sorted_indices):
        if expected != actual:
            result.fail(f"global cue_index gap: expected {expected}, found {actual}")
            break

    # ── 4. Segment cue_indices must be gapless 0..K-1 per segment ─────────────

    for seg_id, seg_cues in by_segment.items():
        seg_indices = sorted(c.segment_cue_index for c in seg_cues)
        for expected, actual in enumerate(seg_indices):
            if expected != actual:
                result.fail(
                    f"segment {seg_id}: segment_cue_index gap: expected {expected}, found {actual}"
                )
                break

    # ── 5. Text integrity per segment ─────────────────────────────────────────

    for seg_id, seg_cues in by_segment.items():
        expected_text = segment_id_to_narration_text.get(seg_id)
        if expected_text is None:
            result.fail(f"segment {seg_id}: no narration text provided for validation")
            continue
        ordered_cues = sorted(seg_cues, key=lambda c: c.cue_index)
        joined = " ".join(c.text for c in ordered_cues)
        if normalize_for_integrity(joined) != normalize_for_integrity(expected_text):
            result.fail(
                f"segment {seg_id}: text integrity violation — "
                f"cue text does not match narration text"
            )

    # ── 6. Run-level warnings ──────────────────────────────────────────────────

    # Check for large duration deviation vs. production segment estimates.
    # (Done in orchestrator with actual estimated_duration_s; stored here if passed.)

    return result
