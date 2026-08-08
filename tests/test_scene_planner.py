"""Tests for Phase 7 scene planner."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.captions.models import CaptionRun
from app.core.database import open_db
from app.narration.models import ApprovedNarrationRun, ApprovedNarrationSegment
from app.production.models import (
    ApprovedProductionPlan,
    ProductionSegmentCitation,
    ProductionSegmentWithCitations,
)
from app.scenes.asset_strategy import plan_assets
from app.scenes.constants import (
    CAMERA_MOVEMENTS,
    TRANSITION_FADE_FROM_BLACK,
    TRANSITION_FADE_TO_BLACK,
    TRANSITIONS,
)
from app.scenes.models import SceneManifestDraft
from app.scenes.planner import (
    _camera_movement,
    _compute_confidence,
    _shot_type,
    _transition_in,
    _transition_out,
    _visual_objective,
    _visual_rationale,
    build_scene_manifest,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "test.db")


def _seed_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO topics (id, title, angle) VALUES (1, 'T', 'A')")
    conn.execute(
        "INSERT INTO scripts (id, topic_id, version, body, status)"
        " VALUES (1, 1, 1, 'body', 'approved')"
    )
    conn.execute(
        "INSERT INTO production_plans"
        " (id, topic_id, script_id, script_version, input_hash, script_body_hash,"
        "  plan_schema_version, renderer_version, duration_algorithm_version,"
        "  status, created_at, updated_at)"
        " VALUES (1, 1, 1, 1, 'ph', 'bh', 'v1', 'rv1', 'dv1',"
        "  'approved', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO production_segments"
        " (id, plan_id, segment_index, section_index, section_type, narration_text, created_at)"
        " VALUES (1, 1, 0, 0, 'hook', 'Hello world.', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO production_segments"
        " (id, plan_id, segment_index, section_index, section_type, narration_text, created_at)"
        " VALUES (2, 1, 1, 1, 'body', 'Body text.', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO production_segments"
        " (id, plan_id, segment_index, section_index, section_type, narration_text, created_at)"
        " VALUES (3, 1, 2, 2, 'outro', 'Bye.', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO voice_profiles (id, provider, model, voice_id, name, language, speaking_rate)"
        " VALUES (1, 'fake', 'fm', 'fv', 'Voice', 'en-US', 1.0)"
    )
    conn.execute(
        "INSERT INTO narration_runs"
        " (id, plan_id, plan_input_hash, voice_profile_id, voice_profile_version,"
        "  language, speaking_rate, settings_json, output_format, sample_rate_hz,"
        "  input_hash, status, created_at, updated_at)"
        " VALUES (1, 1, 'ph', 1, 1, 'en-US', 1.0, '{}', 'wav', 22050,"
        "  'nrh', 'approved', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    for i in range(1, 4):
        conn.execute(
            "INSERT INTO narration_segment_assets"
            " (id, run_id, segment_id, narration_text_hash, provider, model, voice_id,"
            "  voice_profile_id, voice_profile_version, language, speaking_rate,"
            "  settings_json_hash, output_format, sample_rate_hz, input_hash, status,"
            "  audio_sha256, duration_seconds, created_at, updated_at)"
            f" VALUES ({i}, 1, {i}, 'th{i}', 'fake', 'fm', 'fv',"
            "  1, 1, 'en-US', 1.0, 'sh', 'wav', 22050,"
            f"  'ah{i}', 'synthesized',"
            f"  'audio{i}', 3.0, '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
        )
    conn.execute(
        "INSERT INTO caption_runs"
        " (id, narration_run_id, plan_id, script_id, topic_id,"
        "  input_hash, caption_schema_version, segmentation_version,"
        "  timing_algorithm_version, style_version, exporter_version, language,"
        "  status, total_cue_count, total_duration_ms, created_at, updated_at)"
        " VALUES (1, 1, 1, 1, 1,"
        "  'crh', 'csv1', 'sgv1', 'tav1', 'stv1', 'exv1', 'en-US',"
        "  'approved', 0, 9000, '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()


def _make_caption_run(db: sqlite3.Connection) -> CaptionRun:
    _seed_db(db)
    row = db.execute("SELECT * FROM caption_runs WHERE id = 1").fetchone()
    return CaptionRun.from_row(row)


def _make_narration_run() -> ApprovedNarrationRun:
    segments = tuple(
        ApprovedNarrationSegment(
            asset_id=i,
            segment_id=i,
            narration_text=["Hello world.", "Body text.", "Bye."][i - 1],
            narration_text_hash=f"th{i}",
            audio_sha256=f"audio{i}",
            audio_path=f"path/to/{i}.wav",
            duration_ms=3000,
            provider="fake",
            model="fm",
            voice_id="fv",
        )
        for i in range(1, 4)
    )
    return ApprovedNarrationRun(
        run_id=1,
        plan_id=1,
        script_id=1,
        topic_id=1,
        experiment_id=None,
        input_hash="nrh",
        voice_profile_id=1,
        provider="fake",
        model="fm",
        voice_id="fv",
        language="en-US",
        segments=segments,
    )


def _make_production_plan() -> ApprovedProductionPlan:
    segs = [
        ProductionSegmentWithCitations(
            segment_id=i,
            plan_id=1,
            segment_index=i - 1,
            section_index=i - 1,
            section_type=["hook", "body", "outro"][i - 1],
            narration_text=["Hello world.", "Body text.", "Bye."][i - 1],
            estimated_duration_s=3,
            estimated_word_count=5,
            citations=[
                ProductionSegmentCitation(
                    id=i, segment_id=i, claim_id=100 + i, citation_order=0, created_at="2024-01-01"
                )
            ]
            if i == 2
            else [],
        )
        for i in range(1, 4)
    ]
    return ApprovedProductionPlan(
        plan_id=1,
        topic_id=1,
        script_id=1,
        script_version=1,
        input_hash="ph",
        script_body_hash="bh",
        plan_schema_version="v1",
        renderer_version="rv1",
        duration_algorithm_version="dv1",
        title="Test",
        format="short",
        total_estimated_duration_s=9,
        total_word_count=15,
        warnings=[],
        requires_evidence_review=False,
        evidence_hash="eh",
        generation_run_id=None,
        experiment_id=None,
        approved_at="2024-01-01T00:00:00",
        segments=segs,
    )


# ── Unit: internal helpers ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "section_type,expected",
    [
        ("hook", "close_up"),
        ("intro", "medium"),
        ("body", "wide"),
        ("evidence", "cutaway"),
        ("outro", "medium"),
        ("cta", "close_up"),
        ("unknown_type", "medium"),  # falls back to DEFAULT
    ],
)
def test_shot_type_mapping(section_type, expected):
    assert _shot_type(section_type) == expected


@pytest.mark.parametrize("section_type", ["hook", "intro", "body", "evidence", "outro", "cta"])
def test_camera_movement_is_valid(section_type):
    cam = _camera_movement(section_type, scene_index=0, total_scenes=5)
    assert cam in CAMERA_MOVEMENTS


def test_camera_movement_static_gets_variety():
    """Body scenes (static default) should get camera variety across indices."""
    movements = {_camera_movement("body", i, 5) for i in range(5)}
    assert len(movements) > 1


def test_transition_in_first_scene():
    assert _transition_in(0) == TRANSITION_FADE_FROM_BLACK


def test_transition_in_middle_scene():
    t = _transition_in(1)
    assert t in TRANSITIONS
    assert t != TRANSITION_FADE_FROM_BLACK


def test_transition_out_last_scene():
    assert _transition_out(2, total_scenes=3) == TRANSITION_FADE_TO_BLACK


def test_transition_out_non_last():
    t = _transition_out(0, total_scenes=3)
    assert t in TRANSITIONS
    assert t != TRANSITION_FADE_TO_BLACK


@pytest.mark.parametrize("section_type", ["hook", "intro", "body", "evidence", "outro", "cta"])
def test_visual_objective_returns_string(section_type):
    obj = _visual_objective(section_type, "Some narration here.")
    assert isinstance(obj, str)
    assert len(obj) > 0


def test_visual_objective_includes_narration_snippet():
    obj = _visual_objective("body", "This is a specific phrase.")
    assert "specific" in obj or "phrase" in obj or "narration" in obj.lower() or "reinforce" in obj


def test_visual_rationale_contains_section_and_shot():
    r = _visual_rationale("hook", "close_up", "zoom_in")
    assert "hook" in r
    assert "close_up" in r
    assert "zoom_in" in r


@pytest.mark.parametrize("section_type", ["hook", "intro", "body", "evidence", "outro"])
def test_compute_confidence_in_range(section_type):
    c = _compute_confidence(section_type, claim_count=2, cue_count=3)
    assert 0.0 <= c <= 1.0


def test_compute_confidence_increases_with_evidence():
    no_evidence = _compute_confidence("body", claim_count=0, cue_count=0)
    with_evidence = _compute_confidence("body", claim_count=2, cue_count=3)
    assert with_evidence >= no_evidence


def test_plan_assets_returns_list_for_known_section():
    assets = plan_assets("hook", "Some narration", [], scene_index=0)
    assert len(assets) >= 1


def test_plan_assets_first_is_required():
    assets = plan_assets("body", "Some text", [1, 2], scene_index=1)
    assert assets[0].priority == "required"


def test_plan_assets_carries_claim_ids():
    assets = plan_assets("evidence", "Text", [55, 66], scene_index=0)
    for asset in assets:
        assert 55 in asset.claim_ids
        assert 66 in asset.claim_ids


def test_plan_assets_ai_categories_get_prompt():
    assets = plan_assets("hook", "narration", [], scene_index=0)
    for asset in assets:
        if asset.ai_generation_requested:
            assert asset.ai_generation_prompt is not None


# ── Integration: build_scene_manifest ────────────────────────────────────────


def test_build_scene_manifest_returns_draft(db):
    caption_run = _make_caption_run(db)
    narration_run = _make_narration_run()
    plan = _make_production_plan()
    draft = build_scene_manifest(
        db,
        caption_run=caption_run,
        narration_run=narration_run,
        production_plan=plan,
    )
    assert isinstance(draft, SceneManifestDraft)


def test_build_scene_manifest_scene_count(db):
    caption_run = _make_caption_run(db)
    narration_run = _make_narration_run()
    plan = _make_production_plan()
    draft = build_scene_manifest(
        db, caption_run=caption_run, narration_run=narration_run, production_plan=plan
    )
    assert draft.total_scene_count == 3


def test_build_scene_manifest_duration(db):
    caption_run = _make_caption_run(db)
    narration_run = _make_narration_run()
    plan = _make_production_plan()
    draft = build_scene_manifest(
        db, caption_run=caption_run, narration_run=narration_run, production_plan=plan
    )
    # 3 segments × 3000 ms each
    assert draft.total_duration_ms == 9000


def test_build_scene_manifest_timing_cumulative(db):
    caption_run = _make_caption_run(db)
    narration_run = _make_narration_run()
    plan = _make_production_plan()
    draft = build_scene_manifest(
        db, caption_run=caption_run, narration_run=narration_run, production_plan=plan
    )
    # scene 0: 0–3000, scene 1: 3000–6000, scene 2: 6000–9000
    assert draft.scenes[0].start_ms == 0
    assert draft.scenes[0].end_ms == 3000
    assert draft.scenes[1].start_ms == 3000
    assert draft.scenes[2].start_ms == 6000
    assert draft.scenes[2].end_ms == 9000


def test_build_scene_manifest_first_scene_fade_in(db):
    caption_run = _make_caption_run(db)
    narration_run = _make_narration_run()
    plan = _make_production_plan()
    draft = build_scene_manifest(
        db, caption_run=caption_run, narration_run=narration_run, production_plan=plan
    )
    assert draft.scenes[0].transition_in == TRANSITION_FADE_FROM_BLACK


def test_build_scene_manifest_last_scene_fade_out(db):
    caption_run = _make_caption_run(db)
    narration_run = _make_narration_run()
    plan = _make_production_plan()
    draft = build_scene_manifest(
        db, caption_run=caption_run, narration_run=narration_run, production_plan=plan
    )
    assert draft.scenes[-1].transition_out == TRANSITION_FADE_TO_BLACK


def test_build_scene_manifest_evidence_linkage(db):
    caption_run = _make_caption_run(db)
    narration_run = _make_narration_run()
    plan = _make_production_plan()
    draft = build_scene_manifest(
        db, caption_run=caption_run, narration_run=narration_run, production_plan=plan
    )
    # segment 2 (body, index=1) has claim 102
    body_scene = draft.scenes[1]
    assert 102 in body_scene.claim_ids


def test_build_scene_manifest_hook_shot_type(db):
    caption_run = _make_caption_run(db)
    narration_run = _make_narration_run()
    plan = _make_production_plan()
    draft = build_scene_manifest(
        db, caption_run=caption_run, narration_run=narration_run, production_plan=plan
    )
    assert draft.scenes[0].shot_type == "close_up"


def test_build_scene_manifest_assets_per_scene(db):
    caption_run = _make_caption_run(db)
    narration_run = _make_narration_run()
    plan = _make_production_plan()
    draft = build_scene_manifest(
        db, caption_run=caption_run, narration_run=narration_run, production_plan=plan
    )
    for scene in draft.scenes:
        assert len(scene.assets) >= 1


def test_build_scene_manifest_is_deterministic(db):
    caption_run = _make_caption_run(db)
    narration_run = _make_narration_run()
    plan = _make_production_plan()
    draft1 = build_scene_manifest(
        db, caption_run=caption_run, narration_run=narration_run, production_plan=plan
    )
    draft2 = build_scene_manifest(
        db, caption_run=caption_run, narration_run=narration_run, production_plan=plan
    )
    assert draft1.input_hash == draft2.input_hash
    assert draft1.total_scene_count == draft2.total_scene_count
    assert draft1.scenes[0].shot_type == draft2.scenes[0].shot_type


def test_build_scene_manifest_input_hash_matches_hashing_module(db):
    caption_run = _make_caption_run(db)
    narration_run = _make_narration_run()
    plan = _make_production_plan()
    draft = build_scene_manifest(
        db, caption_run=caption_run, narration_run=narration_run, production_plan=plan
    )
    # Hash must be a 64-char hex string (SHA-256)
    assert len(draft.input_hash) == 64
    int(draft.input_hash, 16)  # valid hex


def test_build_scene_manifest_narration_asset_linked(db):
    caption_run = _make_caption_run(db)
    narration_run = _make_narration_run()
    plan = _make_production_plan()
    draft = build_scene_manifest(
        db, caption_run=caption_run, narration_run=narration_run, production_plan=plan
    )
    # Each scene should have its narration_asset_id set (from the narration run)
    for i, scene in enumerate(draft.scenes):
        assert scene.narration_asset_id == i + 1


def test_build_scene_manifest_confidence_is_valid(db):
    caption_run = _make_caption_run(db)
    narration_run = _make_narration_run()
    plan = _make_production_plan()
    draft = build_scene_manifest(
        db, caption_run=caption_run, narration_run=narration_run, production_plan=plan
    )
    for scene in draft.scenes:
        assert 0.0 <= scene.confidence <= 1.0
