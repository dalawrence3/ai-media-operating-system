"""Visual Intelligence — Scene Planner.

Orchestrates deterministic scene planning from approved upstream artifacts:
caption run → narration run → production plan → SceneManifestDraft.

Visual decisions (shot type, camera movement, transitions, objectives) are
made here. Asset strategy is delegated to asset_strategy.plan_assets().

No AI calls, no external providers, no randomness.
Every output is reproducible from the same inputs.
"""

from __future__ import annotations

import sqlite3
from textwrap import shorten

from app.captions.models import CaptionCue, CaptionRun
from app.captions.repository import get_caption_cues
from app.narration.models import ApprovedNarrationRun
from app.production.models import ApprovedProductionPlan
from app.scenes.asset_strategy import plan_assets
from app.scenes.constants import (
    CAMERA_PAN,
    CAMERA_STATIC,
    CAMERA_TILT,
    DEFAULT_CAMERA_MOVEMENT,
    DEFAULT_SHOT_TYPE,
    MANIFEST_SCHEMA_VERSION,
    PLANNER_VERSION,
    SECTION_CAMERA_MAP,
    SECTION_SHOT_TYPE_MAP,
    TRANSITION_CUT,
    TRANSITION_DISSOLVE,
    TRANSITION_FADE_FROM_BLACK,
    TRANSITION_FADE_TO_BLACK,
)
from app.scenes.hashing import ManifestHashInput, compute_manifest_input_hash
from app.scenes.models import PlannedSceneDraft, SceneManifestDraft

# ---------------------------------------------------------------------------
# Internal helpers — visual grammar decisions
# ---------------------------------------------------------------------------


def _shot_type(section_type: str) -> str:
    return SECTION_SHOT_TYPE_MAP.get(section_type, DEFAULT_SHOT_TYPE)


def _camera_movement(section_type: str, scene_index: int, total_scenes: int) -> str:
    base = SECTION_CAMERA_MAP.get(section_type, DEFAULT_CAMERA_MOVEMENT)
    if base != CAMERA_STATIC:
        return base
    # For static-default sections, add movement to avoid flat video
    if scene_index % 3 == 1:
        return CAMERA_PAN
    if scene_index % 3 == 2:
        return CAMERA_TILT if total_scenes > 2 else CAMERA_STATIC
    return CAMERA_STATIC


def _transition_in(scene_index: int) -> str:
    if scene_index == 0:
        return TRANSITION_FADE_FROM_BLACK
    return TRANSITION_CUT


def _transition_out(scene_index: int, total_scenes: int) -> str:
    if scene_index == total_scenes - 1:
        return TRANSITION_FADE_TO_BLACK
    return TRANSITION_DISSOLVE


def _visual_objective(section_type: str, narration_text: str) -> str:
    snippet = shorten(narration_text, width=80, placeholder="...")
    objectives: dict[str, str] = {
        "hook": f"Open with high-impact visual to hook the viewer: {snippet}",
        "intro": f"Establish context and tone for: {snippet}",
        "body": f"Illustrate and reinforce the narration: {snippet}",
        "evidence": f"Visually support the cited evidence: {snippet}",
        "outro": f"Close with memorable imagery for: {snippet}",
        "cta": f"Drive viewer action with clear visual prompt: {snippet}",
    }
    return objectives.get(section_type, f"Visually complement: {snippet}")


def _visual_rationale(section_type: str, shot_type: str, camera_movement: str) -> str:
    return (
        f"Section type '{section_type}' maps to shot '{shot_type}' with "
        f"'{camera_movement}' movement per planner v{PLANNER_VERSION} defaults."
    )


def _compute_confidence(section_type: str, claim_count: int, cue_count: int) -> float:
    """Deterministic confidence score in [0.0, 1.0].

    Higher confidence when we have more evidence linkage and caption support.
    """
    base: dict[str, float] = {
        "hook": 0.9,
        "intro": 0.85,
        "body": 0.8,
        "evidence": 0.75,
        "outro": 0.85,
        "cta": 0.9,
    }
    score = base.get(section_type, 0.75)
    if claim_count > 0:
        score = min(1.0, score + 0.05)
    if cue_count > 0:
        score = min(1.0, score + 0.02)
    return round(score, 4)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_scene_manifest(
    conn: sqlite3.Connection,
    *,
    caption_run: CaptionRun,
    narration_run: ApprovedNarrationRun,
    production_plan: ApprovedProductionPlan,
) -> SceneManifestDraft:
    """Build an immutable SceneManifestDraft from approved upstream artifacts.

    Every output is deterministic given the same inputs.
    No external calls. No randomness.
    """
    cues: list[CaptionCue] = get_caption_cues(conn, caption_run.id)
    cue_by_segment: dict[int, list[CaptionCue]] = {}
    for cue in cues:
        cue_by_segment.setdefault(cue.segment_id, []).append(cue)

    # Map narration assets by segment_id for duration lookup
    asset_by_segment: dict[int, int] = {
        seg.segment_id: seg.asset_id for seg in narration_run.segments
    }
    duration_by_segment: dict[int, int] = {
        seg.segment_id: seg.duration_ms for seg in narration_run.segments
    }
    text_hash_by_segment: dict[int, str] = {
        seg.segment_id: seg.narration_text_hash for seg in narration_run.segments
    }

    total_scenes = len(production_plan.segments)
    planned_scenes: list[PlannedSceneDraft] = []
    cumulative_ms = 0

    for seg in production_plan.segments:
        seg_id = seg.segment_id
        duration_ms = duration_by_segment.get(seg_id, seg.estimated_duration_s * 1000)
        narration_asset_id = asset_by_segment.get(seg_id)
        seg_cues = cue_by_segment.get(seg_id, [])
        cue_ids = [c.id for c in seg_cues]
        claim_ids = list(seg.citation_claim_ids)

        shot = _shot_type(seg.section_type)
        camera = _camera_movement(seg.section_type, seg.segment_index, total_scenes)
        t_in = _transition_in(seg.segment_index)
        t_out = _transition_out(seg.segment_index, total_scenes)
        obj = _visual_objective(seg.section_type, seg.narration_text)
        rationale = _visual_rationale(seg.section_type, shot, camera)
        confidence = _compute_confidence(seg.section_type, len(claim_ids), len(seg_cues))
        assets = plan_assets(seg.section_type, seg.narration_text, claim_ids, seg.segment_index)

        planned_scenes.append(
            PlannedSceneDraft(
                scene_index=seg.segment_index,
                segment_id=seg_id,
                narration_asset_id=narration_asset_id,
                caption_cue_ids=cue_ids,
                claim_ids=claim_ids,
                evidence_ids=[],
                script_section_index=seg.section_index,
                narration_text=seg.narration_text,
                start_ms=cumulative_ms,
                end_ms=cumulative_ms + duration_ms,
                duration_ms=duration_ms,
                shot_type=shot,
                camera_movement=camera,
                transition_in=t_in,
                transition_out=t_out,
                visual_objective=obj,
                visual_rationale=rationale,
                confidence=confidence,
                assets=assets,
            )
        )
        cumulative_ms += duration_ms

    segment_tuples = [
        (
            seg.segment_id,
            text_hash_by_segment.get(seg.segment_id, ""),
            duration_by_segment.get(seg.segment_id, 0),
        )
        for seg in production_plan.segments
    ]
    hash_input = ManifestHashInput(
        caption_run_id=caption_run.id,
        narration_run_id=narration_run.run_id,
        plan_id=production_plan.plan_id,
        script_id=production_plan.script_id,
        topic_id=production_plan.topic_id,
        caption_input_hash=caption_run.input_hash,
        narration_input_hash=narration_run.input_hash,
        manifest_schema_version=MANIFEST_SCHEMA_VERSION,
        planner_version=PLANNER_VERSION,
        experiment_id=caption_run.experiment_id,
        segment_tuples=segment_tuples,
    )
    input_hash = compute_manifest_input_hash(hash_input)

    return SceneManifestDraft(
        caption_run_id=caption_run.id,
        narration_run_id=narration_run.run_id,
        plan_id=production_plan.plan_id,
        script_id=production_plan.script_id,
        topic_id=production_plan.topic_id,
        experiment_id=caption_run.experiment_id,
        input_hash=input_hash,
        manifest_schema_version=MANIFEST_SCHEMA_VERSION,
        planner_version=PLANNER_VERSION,
        scenes=planned_scenes,
    )
