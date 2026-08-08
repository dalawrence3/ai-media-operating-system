"""Tests for Phase 7 scene-planning Pydantic and dataclass models."""


from datetime import UTC

import pytest

from app.scenes.models import (
    ApprovedSceneManifest,
    ApprovedSceneScene,
    PlannedAssetDraft,
    PlannedSceneDraft,
    SceneManifest,
    SceneManifestDraft,
    SceneManifestScene,
)

# ── Draft models ──────────────────────────────────────────────────────────────


def _make_asset_draft(**kw) -> PlannedAssetDraft:
    defaults = dict(
        scene_index=0,
        asset_index=0,
        category="stock_footage",
        priority="required",
        description="Test asset",
        search_query="test query",
        provider=None,
        source_url=None,
        license_status="unknown",
        license_name=None,
        attribution_required=False,
        attribution_text=None,
        commercial_safe=False,
        verification_status="unverified",
        usage_rights={},
        ai_generation_requested=False,
        ai_generation_prompt=None,
        ai_generation_model=None,
    )
    defaults.update(kw)
    return PlannedAssetDraft(**defaults)


def _make_scene_draft(**kw) -> PlannedSceneDraft:
    defaults = dict(
        scene_index=0,
        segment_id=1,
        narration_asset_id=10,
        caption_cue_ids=[1, 2],
        claim_ids=[3, 4],
        evidence_ids=[],
        script_section_index=0,
        narration_text="This is the narration text.",
        start_ms=0,
        end_ms=5000,
        duration_ms=5000,
        shot_type="medium",
        camera_movement="static",
        transition_in="fade_from_black",
        transition_out="cut",
        visual_objective="Open with context.",
        visual_rationale="Section type 'intro' maps to shot 'medium'.",
        confidence=0.85,
    )
    defaults.update(kw)
    return PlannedSceneDraft(**defaults)


def test_planned_asset_draft_defaults():
    a = _make_asset_draft()
    assert a.claim_ids == []
    assert a.evidence_ids == []


def test_planned_scene_draft_defaults():
    s = _make_scene_draft()
    assert s.assets == []


def test_scene_manifest_draft_properties():
    s1 = _make_scene_draft(scene_index=0, duration_ms=5000)
    s1.assets.append(_make_asset_draft(asset_index=0))
    s1.assets.append(_make_asset_draft(asset_index=1))
    s2 = _make_scene_draft(scene_index=1, duration_ms=3000)
    s2.assets.append(_make_asset_draft(asset_index=0))

    draft = SceneManifestDraft(
        caption_run_id=1,
        narration_run_id=2,
        plan_id=3,
        script_id=4,
        topic_id=5,
        experiment_id=None,
        input_hash="abc123",
        manifest_schema_version="1.0",
        planner_version="1.0",
        scenes=[s1, s2],
    )
    assert draft.total_scene_count == 2
    assert draft.total_asset_count == 3
    assert draft.total_duration_ms == 8000


def test_scene_manifest_draft_empty():
    draft = SceneManifestDraft(
        caption_run_id=1,
        narration_run_id=2,
        plan_id=3,
        script_id=4,
        topic_id=5,
        experiment_id=None,
        input_hash="xyz",
        manifest_schema_version="1.0",
        planner_version="1.0",
    )
    assert draft.total_scene_count == 0
    assert draft.total_asset_count == 0
    assert draft.total_duration_ms == 0


# ── Pydantic frozen models ────────────────────────────────────────────────────


def test_scene_manifest_is_frozen():
    from datetime import datetime

    from pydantic import ValidationError

    m = SceneManifest(
        id=1,
        caption_run_id=1,
        narration_run_id=2,
        plan_id=3,
        script_id=4,
        topic_id=5,
        experiment_id=None,
        input_hash="abc",
        manifest_schema_version="1.0",
        planner_version="1.0",
        status="draft",
        total_scene_count=0,
        total_asset_count=0,
        total_duration_ms=0,
        approved_at=None,
        rejected_at=None,
        superseded_at=None,
        superseded_by_manifest_id=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    with pytest.raises(ValidationError):
        m.status = "approved"  # type: ignore[misc]


def test_scene_manifest_scene_is_frozen():
    from datetime import datetime

    from pydantic import ValidationError

    s = SceneManifestScene(
        id=1,
        manifest_id=1,
        scene_index=0,
        segment_id=1,
        narration_asset_id=None,
        caption_cue_ids=[],
        claim_ids=[],
        evidence_ids=[],
        script_section_index=None,
        narration_text="text",
        start_ms=0,
        end_ms=5000,
        duration_ms=5000,
        shot_type="medium",
        camera_movement="static",
        transition_in="fade_from_black",
        transition_out="cut",
        visual_objective="obj",
        visual_rationale="rat",
        confidence=0.9,
        created_at=datetime.now(UTC),
    )
    with pytest.raises(ValidationError):
        s.shot_type = "wide"  # type: ignore[misc]


def test_approved_scene_manifest_properties():
    from datetime import datetime

    now = datetime.now(UTC)
    manifest = ApprovedSceneManifest(
        manifest_id=1,
        caption_run_id=1,
        narration_run_id=2,
        plan_id=3,
        script_id=4,
        topic_id=5,
        experiment_id=None,
        input_hash="abc",
        manifest_schema_version="1.0",
        planner_version="1.0",
        approved_at=now,
        scenes=[
            ApprovedSceneScene(
                scene_id=1,
                manifest_id=1,
                scene_index=0,
                segment_id=1,
                narration_asset_id=None,
                caption_cue_ids=[],
                claim_ids=[],
                evidence_ids=[],
                script_section_index=None,
                narration_text="text",
                start_ms=0,
                end_ms=5000,
                duration_ms=5000,
                shot_type="medium",
                camera_movement="static",
                transition_in="fade_from_black",
                transition_out="cut",
                visual_objective="obj",
                visual_rationale="rat",
                confidence=0.9,
                assets=[],
            )
        ],
    )
    assert manifest.total_scene_count == 1
    assert manifest.total_duration_ms == 5000
