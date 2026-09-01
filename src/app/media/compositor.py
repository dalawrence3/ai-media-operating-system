"""Phase 8 render compositor.

build_render_manifest() is a pure function — no DB access.
SceneInputBuilder requires DB access and resolves narration audio paths.
The caller (CLI / repository) is responsible for persistence.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

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
    RenderVisualBeat,
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

    scene_tuples = [(s.scene_index, s.segment_id, s.audio_sha256) for s in render_scenes]
    beat_tuples = [
        (beat.beat_index, beat.duration_ms, beat.asset_key)
        for scene in render_scenes
        for beat in scene.visual_beats
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
        beat_tuples=beat_tuples,
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
        "visual_beats",
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
        visual_beats: list[RenderVisualBeat] | None = None,
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
        self.visual_beats = visual_beats or []


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
        visual_beats=list(s.visual_beats),
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
        from app.narration.repository import (
            get_approved_narration_run_full,
            get_narration_segment_asset,
        )

        # approved is ApprovedSceneManifest — avoid circular import by duck-typing.
        #
        # Prefer the persisted narration_asset_id on each scene.  It is the
        # strongest lineage reference and avoids losing experiment-linked audio
        # when a plan-level narration lookup cannot resolve the experiment.
        experiment_id = getattr(approved, "experiment_id", None)
        narration_run = get_approved_narration_run_full(
            self._conn,
            approved.plan_id,
            experiment_id=experiment_id,
        )
        self._narration_input_hash = narration_run.input_hash if narration_run else ""

        # Backward-compatible segment lookup for older manifests that do not
        # persist narration_asset_id.
        audio_by_segment: dict[int, tuple[str | None, str | None]] = {}
        if narration_run:
            for seg in narration_run.segments:
                audio_by_segment[seg.segment_id] = (seg.audio_path, seg.audio_sha256)

        inputs: list[_SceneInput] = []
        for scene in approved.scenes:
            audio_path: str | None = None
            audio_sha256: str | None = None

            if scene.narration_asset_id is not None:
                narration_asset = get_narration_segment_asset(
                    self._conn,
                    scene.narration_asset_id,
                )
                if narration_asset is not None:
                    audio_path = narration_asset.audio_path
                    audio_sha256 = narration_asset.audio_sha256

                    # Narration asset paths are persisted relative to the
                    # configured artifacts root. FFmpeg concat files live in a
                    # temporary directory, so relative paths would otherwise be
                    # resolved relative to that temp directory and fail.
                    if audio_path:
                        resolved_audio_path = Path(audio_path)
                        if not resolved_audio_path.is_absolute():
                            from app.core.config import get_config

                            resolved_audio_path = (
                                Path(get_config().artifacts_path) / resolved_audio_path
                            ).resolve()
                        audio_path = str(resolved_audio_path)

            if audio_path is None:
                audio_path, audio_sha256 = audio_by_segment.get(
                    scene.segment_id,
                    (None, None),
                )
            resolved_assets = [
                ResolvedAsset(
                    asset_id=a.id,
                    scene_id=a.scene_id,
                    segment_id=scene.segment_id,
                    asset_index=a.asset_index,
                    category=a.category,
                    priority=a.priority,
                    local_path=None,  # not yet downloaded — placeholder render
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
