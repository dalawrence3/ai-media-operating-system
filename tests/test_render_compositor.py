"""Tests for the render compositor."""

import pytest

from app.media.compositor import (
    SceneInputBuilder,
    _SceneInput,
    _pick_primary_asset,
    _resolve_scene,
    build_render_manifest,
)
from app.media.models import RenderManifestDraft, ResolvedAsset


def _make_scene_input(**overrides) -> _SceneInput:
    defaults = dict(
        scene_index=0,
        scene_id=1,
        segment_id=10,
        narration_asset_id=None,
        audio_path="/tmp/seg_0.wav",
        audio_sha256="sha256_audio",
        start_ms=0,
        end_ms=3000,
        duration_ms=3000,
        shot_type="medium",
        camera_movement="static",
        visual_objective="Intro scene",
        caption_cue_ids=[1, 2, 3],
        resolved_assets=[],
    )
    defaults.update(overrides)
    return _SceneInput(**defaults)


def _make_resolved_asset(priority: str = "required", local_path: str | None = None) -> ResolvedAsset:
    return ResolvedAsset(
        asset_id=1,
        scene_id=1,
        asset_index=0,
        category="b_roll",
        priority=priority,
        local_path=local_path,
        local_sha256="sha" if local_path else None,
        source_url="https://example.com/v.mp4",
        license_status="royalty_free",
        commercial_safe=True,
    )


class TestPickPrimaryAsset:
    def test_returns_none_when_no_assets(self):
        assert _pick_primary_asset([]) is None

    def test_returns_none_when_all_unresolved(self):
        assets = [_make_resolved_asset("required", None)]
        assert _pick_primary_asset(assets) is None

    def test_returns_resolved_asset(self):
        asset = _make_resolved_asset("required", "/tmp/asset.jpg")
        result = _pick_primary_asset([asset])
        assert result is asset

    def test_prefers_required_over_preferred(self):
        preferred = _make_resolved_asset("preferred", "/tmp/p.jpg")
        required = ResolvedAsset(
            asset_id=2, scene_id=1, asset_index=1,
            category="b_roll", priority="required",
            local_path="/tmp/r.jpg", local_sha256="sha",
            source_url=None, license_status="royalty_free", commercial_safe=True,
        )
        result = _pick_primary_asset([preferred, required])
        assert result is required

    def test_skips_unresolved_picks_lower_priority_resolved(self):
        unresolved_required = _make_resolved_asset("required", None)
        resolved_optional = ResolvedAsset(
            asset_id=3, scene_id=1, asset_index=2,
            category="b_roll", priority="optional",
            local_path="/tmp/opt.jpg", local_sha256="sha",
            source_url=None, license_status="royalty_free", commercial_safe=True,
        )
        result = _pick_primary_asset([unresolved_required, resolved_optional])
        assert result is resolved_optional


class TestBuildRenderManifest:
    def _scenes(self, n=2) -> list[_SceneInput]:
        return [
            _make_scene_input(
                scene_index=i,
                scene_id=i + 1,
                segment_id=10 + i,
                audio_sha256=f"sha_{i}",
                start_ms=i * 3000,
                end_ms=(i + 1) * 3000,
                duration_ms=3000,
            )
            for i in range(n)
        ]

    def test_builds_draft_with_correct_counts(self):
        draft = build_render_manifest(
            scene_manifest_id=1,
            scene_manifest_input_hash="sm_hash",
            narration_run_id=2,
            narration_input_hash="nar_hash",
            caption_run_id=3,
            topic_id=4,
            plan_id=5,
            script_id=6,
            scenes=self._scenes(3),
        )
        assert isinstance(draft, RenderManifestDraft)
        assert draft.total_scene_count == 3
        assert draft.total_duration_ms == 9000

    def test_default_resolution_and_fps(self):
        draft = build_render_manifest(
            scene_manifest_id=1,
            scene_manifest_input_hash="h",
            narration_run_id=1,
            narration_input_hash="h2",
            caption_run_id=1,
            topic_id=1,
            plan_id=1,
            script_id=1,
            scenes=self._scenes(1),
        )
        assert draft.width == 1080
        assert draft.height == 1920
        assert draft.fps == 30
        assert draft.caption_burn_in is False

    def test_custom_resolution(self):
        draft = build_render_manifest(
            scene_manifest_id=1,
            scene_manifest_input_hash="h",
            narration_run_id=1,
            narration_input_hash="h2",
            caption_run_id=1,
            topic_id=1,
            plan_id=1,
            script_id=1,
            scenes=self._scenes(1),
            width=720,
            height=1280,
            fps=60,
            caption_burn_in=True,
        )
        assert draft.width == 720
        assert draft.height == 1280
        assert draft.fps == 60
        assert draft.caption_burn_in is True

    def test_input_hash_is_deterministic(self):
        kwargs = dict(
            scene_manifest_id=1,
            scene_manifest_input_hash="sm",
            narration_run_id=2,
            narration_input_hash="nar",
            caption_run_id=3,
            topic_id=4,
            plan_id=5,
            script_id=6,
            scenes=self._scenes(2),
        )
        h1 = build_render_manifest(**kwargs).input_hash
        h2 = build_render_manifest(**kwargs).input_hash
        assert h1 == h2

    def test_different_inputs_different_hash(self):
        kwargs = dict(
            scene_manifest_id=1,
            scene_manifest_input_hash="sm",
            narration_run_id=2,
            narration_input_hash="nar",
            caption_run_id=3,
            topic_id=4,
            plan_id=5,
            script_id=6,
            scenes=self._scenes(2),
        )
        h1 = build_render_manifest(**kwargs).input_hash
        kwargs["narration_run_id"] = 99
        h2 = build_render_manifest(**kwargs).input_hash
        assert h1 != h2

    def test_scenes_are_resolved(self):
        asset = _make_resolved_asset("required", "/tmp/asset.jpg")
        scene = _make_scene_input(resolved_assets=[asset])
        draft = build_render_manifest(
            scene_manifest_id=1,
            scene_manifest_input_hash="sm",
            narration_run_id=2,
            narration_input_hash="nar",
            caption_run_id=3,
            topic_id=4,
            plan_id=5,
            script_id=6,
            scenes=[scene],
        )
        assert draft.scenes[0].primary_asset is not None
        assert draft.scenes[0].primary_asset.local_path == "/tmp/asset.jpg"

    def test_empty_scenes_allowed(self):
        draft = build_render_manifest(
            scene_manifest_id=1,
            scene_manifest_input_hash="sm",
            narration_run_id=2,
            narration_input_hash="nar",
            caption_run_id=3,
            topic_id=4,
            plan_id=5,
            script_id=6,
            scenes=[],
        )
        assert draft.total_scene_count == 0
        assert draft.total_duration_ms == 0

    def test_experiment_id_propagated(self):
        draft = build_render_manifest(
            scene_manifest_id=1,
            scene_manifest_input_hash="sm",
            narration_run_id=2,
            narration_input_hash="nar",
            caption_run_id=3,
            topic_id=4,
            plan_id=5,
            script_id=6,
            scenes=self._scenes(1),
            experiment_id="exp-abc",
        )
        assert draft.experiment_id == "exp-abc"
