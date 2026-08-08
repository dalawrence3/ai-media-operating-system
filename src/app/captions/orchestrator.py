"""Phase 6 M6.3A: Caption orchestrator.

generate_captions() is the public entry point.  It:
  1. Loads the approved narration run handoff from the narration repository.
  2. Validates the handoff (approved, not superseded, all segments complete).
  3. Computes the caption input hash and checks for an existing idempotent run.
  4. Creates a caption run in the DB (status='running').
  5. For each segment: segments text into cues, assigns cumulative proportional timing.
  6. Validates all cues (per-cue, overlap, index gaps, text integrity).
  7. Writes SRT, VTT, JSON exports atomically to disk.
  8. Persists cues to DB, marks run completed.
  9. Commits and returns the completed CaptionRun.

No network access. No TTS provider calls. No WAV audio access.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.captions.constants import (
    CAPTION_EXPORTER_VERSION,
    CAPTION_SCHEMA_VERSION,
    CAPTION_SEGMENTATION_VERSION,
    CAPTION_STYLE_VERSION,
    CAPTION_TIMING_ALGORITHM_VERSION,
    CAPTION_TIMING_SOURCE_ESTIMATED,
)
from app.captions.errors import (
    CaptionValidationError,
    DuplicateNarrationSegmentAssetError,
    FailedCaptionRunError,
    IncompleteNarrationProvenanceError,
    MissingNarrationAudioFileError,
    MissingNarrationSegmentAssetError,
    NoApprovedNarrationRunError,
)
from app.captions.exporters import render_json, render_srt, render_vtt
from app.captions.hashing import compute_caption_input_hash
from app.captions.models import CaptionCueDraft, CaptionRun, CaptionRunDraft
from app.captions.repository import (
    complete_caption_run,
    create_caption_run,
    fail_caption_run,
    find_caption_run_by_input_hash,
    persist_caption_cues,
    require_caption_run,
)
from app.captions.segmentation import segment_narration_text
from app.captions.storage import (
    cleanup_stale_temp_files,
    compute_export_sha256,
    relative_artifact_path,
    resolve_artifacts_path,
    write_export_atomic,
)
from app.captions.storage import (
    json_path as _json_path,
)
from app.captions.storage import (
    srt_path as _srt_path,
)
from app.captions.storage import (
    vtt_path as _vtt_path,
)
from app.captions.timing import allocate_timing
from app.captions.validation import validate_caption_cues
from app.narration.models import ApprovedNarrationRun, ApprovedNarrationSegment
from app.narration.repository import get_approved_narration_run_full


def generate_captions(
    conn: sqlite3.Connection,
    *,
    plan_id: int,
    artifacts_path: Path,
    language: str = "en-US",
    experiment_id: str | None = None,
) -> CaptionRun:
    """Generate caption and timing artifacts for an approved narration run.

    Returns the completed CaptionRun.  Raises on any validation failure.
    Idempotent: returns the existing run if the input hash matches a prior run.
    """
    arts = resolve_artifacts_path(artifacts_path)
    cleanup_stale_temp_files(arts, max_age_seconds=3600)

    # ── 1. Load and validate the narration run handoff ────────────────────────
    handoff = get_approved_narration_run_full(conn, plan_id, experiment_id=experiment_id)
    if handoff is None:
        raise NoApprovedNarrationRunError(
            f"No active approved narration run for plan {plan_id}"
            + (f" (experiment={experiment_id!r})" if experiment_id else "")
        )

    _validate_handoff(handoff, arts)

    # ── 2. Compute input hash and check idempotency ───────────────────────────
    from app.captions.hashing import NarrationSegmentHashInput

    seg_inputs = [
        NarrationSegmentHashInput(
            segment_id=seg.segment_id,
            asset_id=seg.asset_id,
            audio_sha256=seg.audio_sha256,
            narration_text_hash=seg.narration_text_hash,
            duration_ms=seg.duration_ms,
        )
        for seg in handoff.segments
    ]
    input_hash = compute_caption_input_hash(
        narration_run_id=handoff.run_id,
        narration_run_input_hash=handoff.input_hash,
        segments=seg_inputs,
        caption_schema_version=CAPTION_SCHEMA_VERSION,
        segmentation_version=CAPTION_SEGMENTATION_VERSION,
        timing_algorithm_version=CAPTION_TIMING_ALGORITHM_VERSION,
        style_version=CAPTION_STYLE_VERSION,
        exporter_version=CAPTION_EXPORTER_VERSION,
        language=language,
        experiment_id=experiment_id,
    )

    existing = find_caption_run_by_input_hash(conn, handoff.run_id, input_hash)
    if existing is not None and existing.status in ("completed", "approved"):
        return existing
    if existing is not None and existing.status == "failed":
        raise FailedCaptionRunError(
            f"Caption run {existing.id} with matching input_hash previously failed: "
            f"{existing.failure_reason!r}. Create a new run with a different input."
        )
    if existing is not None and existing.status == "running":
        from app.captions.errors import IllegalCaptionTransitionError
        raise IllegalCaptionTransitionError(
            f"Caption run {existing.id} with the same input_hash is already running. "
            "Wait for it to complete or fail before retrying."
        )

    # ── 3. Create the caption run ─────────────────────────────────────────────
    draft = CaptionRunDraft(
        narration_run_id=handoff.run_id,
        plan_id=handoff.plan_id,
        script_id=handoff.script_id,
        topic_id=handoff.topic_id,
        experiment_id=handoff.experiment_id,
        input_hash=input_hash,
        caption_schema_version=CAPTION_SCHEMA_VERSION,
        segmentation_version=CAPTION_SEGMENTATION_VERSION,
        timing_algorithm_version=CAPTION_TIMING_ALGORITHM_VERSION,
        style_version=CAPTION_STYLE_VERSION,
        exporter_version=CAPTION_EXPORTER_VERSION,
        language=language,
    )
    run = create_caption_run(conn, draft)

    try:
        # ── 4. Build cue drafts ───────────────────────────────────────────────
        all_cues: list[CaptionCueDraft] = []
        global_cue_index = 0
        for seg in handoff.segments:
            seg_cues = _build_cues_for_segment(seg, global_cue_index, run.id)
            all_cues.extend(seg_cues)
            global_cue_index += len(seg_cues)

        # ── 5. Validate ───────────────────────────────────────────────────────
        seg_id_to_text = {s.segment_id: s.narration_text for s in handoff.segments}
        seg_id_to_dur = {s.segment_id: s.duration_ms for s in handoff.segments}
        seg_id_to_asset = {s.segment_id: s.asset_id for s in handoff.segments}
        seg_id_to_hash = {s.segment_id: s.narration_text_hash for s in handoff.segments}
        seg_id_to_audio = {s.segment_id: s.audio_sha256 for s in handoff.segments}

        result = validate_caption_cues(
            cues=all_cues,
            segment_id_to_narration_text=seg_id_to_text,
            segment_id_to_duration_ms=seg_id_to_dur,
            segment_id_to_asset_id=seg_id_to_asset,
            segment_id_to_text_hash=seg_id_to_hash,
            segment_id_to_audio_sha256=seg_id_to_audio,
        )
        if not result.ok:
            raise CaptionValidationError(
                "Caption validation failed:\n" + "\n".join(f"  - {e}" for e in result.errors)
            )

        # ── 6. Render exports ─────────────────────────────────────────────────
        srt_content = render_srt(all_cues)
        vtt_content = render_vtt(all_cues)
        json_content = render_json(
            all_cues,
            caption_run_id=run.id,
            narration_run_id=handoff.run_id,
            plan_id=handoff.plan_id,
            script_id=handoff.script_id,
            topic_id=handoff.topic_id,
            experiment_id=handoff.experiment_id,
            caption_schema_version=CAPTION_SCHEMA_VERSION,
            segmentation_version=CAPTION_SEGMENTATION_VERSION,
            timing_algorithm_version=CAPTION_TIMING_ALGORITHM_VERSION,
            style_version=CAPTION_STYLE_VERSION,
            exporter_version=CAPTION_EXPORTER_VERSION,
            language=language,
        )

        srt_dest = _srt_path(arts, handoff.plan_id, handoff.run_id, run.id)
        vtt_dest = _vtt_path(arts, handoff.plan_id, handoff.run_id, run.id)
        json_dest = _json_path(arts, handoff.plan_id, handoff.run_id, run.id)

        write_export_atomic(srt_dest, srt_content)
        write_export_atomic(vtt_dest, vtt_content)
        write_export_atomic(json_dest, json_content)

        srt_sha = compute_export_sha256(srt_content)
        vtt_sha = compute_export_sha256(vtt_content)
        json_sha = compute_export_sha256(json_content)

        # ── 7. Persist cues and complete run ──────────────────────────────────
        persist_caption_cues(conn, run.id, all_cues)

        total_duration_ms = sum(s.duration_ms for s in handoff.segments)
        complete_caption_run(
            conn, run.id,
            total_cue_count=len(all_cues),
            total_duration_ms=total_duration_ms,
            srt_path=relative_artifact_path(arts, srt_dest),
            vtt_path=relative_artifact_path(arts, vtt_dest),
            json_path=relative_artifact_path(arts, json_dest),
            srt_sha256=srt_sha,
            vtt_sha256=vtt_sha,
            json_sha256=json_sha,
        )

    except Exception:
        # Mark run failed; do not re-raise FailedCaptionRunError (already handled above)
        try:
            fail_caption_run(conn, run.id, failure_reason="orchestrator exception")
        except Exception:
            pass
        raise

    conn.commit()
    return require_caption_run(conn, run.id)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _validate_handoff(handoff: ApprovedNarrationRun, arts: Path) -> None:
    """Raise a descriptive error for any handoff integrity violation."""
    if not handoff.segments:
        raise MissingNarrationSegmentAssetError(
            f"Narration run {handoff.run_id} has no segments"
        )

    seen_segment_ids: set[int] = set()
    seen_asset_ids: set[int] = set()

    for seg in handoff.segments:
        if seg.segment_id in seen_segment_ids:
            raise DuplicateNarrationSegmentAssetError(
                f"Segment {seg.segment_id} appears more than once in narration run {handoff.run_id}"
            )
        if seg.asset_id in seen_asset_ids:
            raise DuplicateNarrationSegmentAssetError(
                f"Asset {seg.asset_id} appears more than once in narration run {handoff.run_id}"
            )
        seen_segment_ids.add(seg.segment_id)
        seen_asset_ids.add(seg.asset_id)

        if not seg.audio_path:
            raise MissingNarrationAudioFileError(
                f"Segment {seg.segment_id} has no audio_path in narration run {handoff.run_id}"
            )
        if not seg.audio_sha256:
            raise IncompleteNarrationProvenanceError(
                f"Segment {seg.segment_id} has no audio_sha256"
            )
        if not seg.narration_text_hash:
            raise IncompleteNarrationProvenanceError(
                f"Segment {seg.segment_id} has no narration_text_hash"
            )
        if seg.duration_ms <= 0:
            raise IncompleteNarrationProvenanceError(
                f"Segment {seg.segment_id} has non-positive duration_ms: {seg.duration_ms}"
            )
        if not seg.narration_text.strip():
            raise MissingNarrationSegmentAssetError(
                f"Segment {seg.segment_id} has empty narration_text"
            )


def _build_cues_for_segment(
    seg: ApprovedNarrationSegment,
    global_cue_offset: int,
    run_id: int,
) -> list[CaptionCueDraft]:
    """Segment text and assign timing for one narration segment."""
    segmented = segment_narration_text(seg.narration_text)
    if not segmented:
        return []

    timing = allocate_timing(segmented, seg.duration_ms)
    cues: list[CaptionCueDraft] = []

    for seg_cue_index, (seg_text, (start_ms, end_ms, timing_warnings)) in enumerate(
        zip(segmented, timing, strict=False)
    ):
        all_warnings = list(seg_text.warnings) + list(timing_warnings)
        cue = CaptionCueDraft(
            segment_id=seg.segment_id,
            narration_asset_id=seg.asset_id,
            narration_text_hash=seg.narration_text_hash,
            audio_sha256=seg.audio_sha256,
            cue_index=global_cue_offset + seg_cue_index,
            segment_cue_index=seg_cue_index,
            lines=seg_text.lines,
            start_ms=start_ms,
            end_ms=end_ms,
            timing_source=CAPTION_TIMING_SOURCE_ESTIMATED,
            warnings=all_warnings,
        )
        cues.append(cue)

    return cues
