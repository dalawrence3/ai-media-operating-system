"""Phase 8 render compositor.

build_render_manifest() is a pure function — no DB access.
SceneInputBuilder requires DB access and resolves narration audio paths.
The caller (CLI / repository) is responsible for persistence.
"""

from __future__ import annotations

import sqlite3

from app.media.constants import (
    COMPOSITOR_VERSION,
    DEFAULT_FPS,
    DEFAULT_RESOLUTION,
    RENDER_SCHEMA_VERSION,
)
from app.media.hashing import RenderHashInput, compute_render_input_hash
from app.media.models import (
    RenderManifestDraft,
    RenderSceneDraft,
    ResolvedAsset,
)


def build_render_manifest(
    *,
    scene_manifest_id: int,
    scene_manifest_input_hash: str,
    narration_run_id: int,
    narration_input_hash: str,
    caption_run_id: int,
    topic_id: int,
    plan_id: int,
    script_id: int,
    scenes: list[_SceneInput],
    width: int = DEFAULT_RESOLUTION[0],
    height: int = DEFAULT_RESOLUTION[1],
    fps: int = DEFAULT_FPS,
    caption_burn_in: bool = False,
    experiment_id: str | None = None,
) -> RenderManifestDraft:
    """Assemble a RenderManifestDraft from pre-resolved upstream data.

    ``scenes`` must be ordered by scene_index ascending.
    """
    render_scenes: list[RenderSceneDraft] = [_resolve_scene(s) for s in scenes]

    scene_tuples = [
        (s.scene_index, s.segment_id, s.audio_sha256) for s in render_scenes
    ]
    hash_input = RenderHashInput(
        scene_manifest_id=scene_manifest_id,
        narration_run_id=narration_run_id,
        caption_run_id=caption_run_id,
        topic_id=topic_id,
        plan_id=plan_id,
        script_id=script_id,
        scene_manifest_input_hash=scene_manifest_input_hash,
        narration_input_hash=narration_input_hash,
        render_schema_version=RENDER_SCHEMA_VERSION,
        compositor_version=COMPOSITOR_VERSION,
        width=width,
        height=height,
        fps=fps,
        caption_burn_in=caption_burn_in,
        experiment_id=experiment_id,
        scene_tuples=scene_tuples,
    )

    total_duration_ms = sum(s.duration_ms for s in render_scenes)

    return RenderManifestDraft(
        scene_manifest_id=scene_manifest_id,
        narration_run_id=narration_run_id,
        caption_run_id=caption_run_id,
        topic_id=topic_id,
        plan_id=plan_id,
        script_id=script_id,
        experiment_id=experiment_id,
        input_hash=compute_render_input_hash(hash_input),
        render_schema_version=RENDER_SCHEMA_VERSION,
        compositor_version=COMPOSITOR_VERSION,
        total_scene_count=len(render_scenes),
        total_duration_ms=total_duration_ms,
        width=width,
        height=height,
        fps=fps,
        caption_burn_in=caption_burn_in,
        scenes=render_scenes,
    )


# ── Input struct for each scene ───────────────────────────────────────────────


class _SceneInput:
    """Data carrier for one scene passed into build_render_manifest."""

    __slots__ = (
        "scene_index",
        "scene_id",
        "segment_id",
        "narration_asset_id",
        "audio_path",
        "audio_sha256",
        "start_ms",
        "end_ms",
        "duration_ms",
        "shot_type",
        "camera_movement",
        "visual_objective",
        "caption_cue_ids",
        "resolved_assets",
    )

    def __init__(
        self,
        *,
        scene_index: int,
        scene_id: int,
        segment_id: int,
        narration_asset_id: int | None,
        audio_path: str | None,
        audio_sha256: str | None,
        start_ms: int,
        end_ms: int,
        duration_ms: int,
        shot_type: str,
        camera_movement: str,
        visual_objective: str,
        caption_cue_ids: list[int],
        resolved_assets: list[ResolvedAsset] | None = None,
    ) -> None:
        self.scene_index = scene_index
        self.scene_id = scene_id
        self.segment_id = segment_id
        self.narration_asset_id = narration_asset_id
        self.audio_path = audio_path
        self.audio_sha256 = audio_sha256
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.duration_ms = duration_ms
        self.shot_type = shot_type
        self.camera_movement = camera_movement
        self.visual_objective = visual_objective
        self.caption_cue_ids = caption_cue_ids
        self.resolved_assets = resolved_assets or []


def _resolve_scene(s: _SceneInput) -> RenderSceneDraft:
    primary = _pick_primary_asset(s.resolved_assets)
    return RenderSceneDraft(
        scene_index=s.scene_index,
        scene_id=s.scene_id,
        segment_id=s.segment_id,
        narration_asset_id=s.narration_asset_id,
        audio_path=s.audio_path,
        audio_sha256=s.audio_sha256,
        start_ms=s.start_ms,
        end_ms=s.end_ms,
        duration_ms=s.duration_ms,
        shot_type=s.shot_type,
        camera_movement=s.camera_movement,
        visual_objective=s.visual_objective,
        caption_cue_ids=s.caption_cue_ids,
        primary_asset=primary,
    )


class SceneInputBuilder:
    """Resolves narration audio paths and builds _SceneInput objects from
    an ApprovedSceneManifest. Requires DB access for narration asset lookup."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._narration_input_hash: str = ""

    @property
    def narration_input_hash(self) -> str:
        return self._narration_input_hash

    def build(self, approved: object) -> list[_SceneInput]:
        """Build scene inputs from an ApprovedSceneManifest."""
        from app.narration.repository import get_approved_narration_run_full

        # approved is ApprovedSceneManifest — avoid circular import by duck-typing
        narration_run = get_approved_narration_run_full(self._conn, approved.plan_id)
        self._narration_input_hash = narration_run.input_hash if narration_run else ""

        # Build segment_id → (audio_path, audio_sha256) lookup
        audio_by_segment: dict[int, tuple[str | None, str | None]] = {}
        if narration_run:
            for seg in narration_run.segments:
                audio_by_segment[seg.segment_id] = (seg.audio_path, seg.audio_sha256)

        inputs: list[_SceneInput] = []
        for scene in approved.scenes:
            audio_path, audio_sha256 = audio_by_segment.get(scene.segment_id, (None, None))
            resolved_assets = [
                ResolvedAsset(
                    asset_id=a.id,
                    scene_id=a.scene_id,
                    asset_index=a.asset_index,
                    category=a.category,
                    priority=a.priority,
                    local_path=None,    # not yet downloaded — placeholder render
                    local_sha256=None,
                    source_url=a.source_url,
                    license_status=a.license_status,
                    commercial_safe=a.commercial_safe,
                )
                for a in scene.assets
            ]
            inputs.append(
                _SceneInput(
                    scene_index=scene.scene_index,
                    scene_id=scene.scene_id,
                    segment_id=scene.segment_id,
                    narration_asset_id=scene.narration_asset_id,
                    audio_path=audio_path,
                    audio_sha256=audio_sha256,
                    start_ms=scene.start_ms,
                    end_ms=scene.end_ms,
                    duration_ms=scene.duration_ms,
                    shot_type=scene.shot_type,
                    camera_movement=scene.camera_movement,
                    visual_objective=scene.visual_objective,
                    caption_cue_ids=scene.caption_cue_ids,
                    resolved_assets=resolved_assets,
                )
            )
        return inputs


def _pick_primary_asset(assets: list[ResolvedAsset]) -> ResolvedAsset | None:
    """Return the highest-priority resolved asset with a local path, or None."""
    # Priority order: required > preferred > optional
    _priority_rank = {"required": 0, "preferred": 1, "optional": 2}

    resolved = [a for a in assets if a.local_path is not None]
    if not resolved:
        return None
    resolved.sort(key=lambda a: _priority_rank.get(a.priority, 99))
    return resolved[0]
