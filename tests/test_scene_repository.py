"""Tests for Phase 7 scene manifest repository."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core.database import open_db
from app.scenes.errors import (
    IllegalManifestTransitionError,
    ManifestNotFoundError,
)
from app.scenes.models import (
    PlannedAssetDraft,
    PlannedSceneDraft,
    SceneManifest,
    SceneManifestDraft,
)
from app.scenes.repository import (
    approve_scene_manifest,
    create_scene_manifest,
    get_active_approved_scene_manifest,
    get_all_scene_manifest_assets,
    get_approved_scene_manifest_full,
    get_or_create_scene_manifest,
    get_scene_manifest_assets,
    get_scene_manifest_by_caption_run,
    get_scene_manifest_by_id,
    get_scene_manifest_by_input_hash,
    get_scene_manifest_scene_by_id,
    get_scene_manifest_scenes,
    list_scene_manifest_review_events,
    list_scene_manifests,
    record_scene_rejection,
    reject_scene_manifest,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "test.db")


def _seed(conn: sqlite3.Connection) -> dict:
    """Insert minimal upstream rows (FK checks off)."""
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
        " VALUES (1, 1, 0, 0, 'hook', 'Hello.', '2024-01-01T00:00:00')"
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
        " VALUES (1, 1, 'ph', 1, 1,"
        "  'en-US', 1.0, '{}', 'wav', 22050,"
        "  'nrh', 'approved', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO narration_segment_assets"
        " (id, run_id, segment_id, narration_text_hash, provider, model, voice_id,"
        "  voice_profile_id, voice_profile_version, language, speaking_rate,"
        "  settings_json_hash, output_format, sample_rate_hz, input_hash, status,"
        "  audio_sha256, duration_seconds, created_at, updated_at)"
        " VALUES (1, 1, 1, 'th', 'fake', 'fm', 'fv',"
        "  1, 1, 'en-US', 1.0,"
        "  'sh', 'wav', 22050, 'ah', 'synthesized',"
        "  'audio-sha', 3.0, '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO caption_runs"
        " (id, narration_run_id, plan_id, script_id, topic_id,"
        "  input_hash, caption_schema_version, segmentation_version,"
        "  timing_algorithm_version, style_version, exporter_version, language,"
        "  status, total_cue_count, total_duration_ms, created_at, updated_at)"
        " VALUES (1, 1, 1, 1, 1,"
        "  'crh', 'csv1', 'sgv1', 'tav1', 'stv1', 'exv1', 'en-US',"
        "  'approved', 2, 8000, '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    return {"caption_run_id": 1, "narration_run_id": 1, "plan_id": 1, "topic_id": 1}


def _make_asset(scene_index: int = 0, asset_index: int = 0, **kw) -> PlannedAssetDraft:
    defaults = dict(
        scene_index=scene_index,
        asset_index=asset_index,
        category="stock_footage",
        priority="required",
        description="Test asset",
        search_query="query",
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


def _make_scene(scene_index: int = 0, segment_id: int = 1, **kw) -> PlannedSceneDraft:
    defaults = dict(
        scene_index=scene_index,
        segment_id=segment_id,
        narration_asset_id=1,
        caption_cue_ids=[],
        claim_ids=[10, 11],
        evidence_ids=[],
        script_section_index=0,
        narration_text="Hello world.",
        start_ms=0,
        end_ms=5000,
        duration_ms=5000,
        shot_type="medium",
        camera_movement="static",
        transition_in="fade_from_black",
        transition_out="cut",
        visual_objective="Illustrate.",
        visual_rationale="hook → close_up",
        confidence=0.9,
        assets=[_make_asset(scene_index=scene_index)],
    )
    defaults.update(kw)
    return PlannedSceneDraft(**defaults)


def _make_draft(
    conn,
    *,
    input_hash: str = "manifest-hash-1",
    scenes: list | None = None,
) -> SceneManifestDraft:
    ids = _seed(conn)
    return SceneManifestDraft(
        caption_run_id=ids["caption_run_id"],
        narration_run_id=ids["narration_run_id"],
        plan_id=ids["plan_id"],
        script_id=1,
        topic_id=ids["topic_id"],
        experiment_id=None,
        input_hash=input_hash,
        manifest_schema_version="1.0",
        planner_version="1.0",
        scenes=scenes if scenes is not None else [_make_scene()],
    )


# ── Create ────────────────────────────────────────────────────────────────────


def test_create_scene_manifest_returns_manifest(db):
    draft = _make_draft(db)
    manifest = create_scene_manifest(db, draft)
    assert isinstance(manifest, SceneManifest)
    assert manifest.id > 0
    assert manifest.status == "draft"
    assert manifest.total_scene_count == 1
    assert manifest.total_asset_count == 1


def test_create_scene_manifest_persists_scenes(db):
    draft = _make_draft(db)
    manifest = create_scene_manifest(db, draft)
    scenes = get_scene_manifest_scenes(db, manifest.id)
    assert len(scenes) == 1
    scene = scenes[0]
    assert scene.scene_index == 0
    assert scene.segment_id == 1
    assert scene.shot_type == "medium"
    assert scene.claim_ids == [10, 11]
    assert scene.confidence == pytest.approx(0.9)


def test_create_scene_manifest_persists_assets(db):
    draft = _make_draft(db)
    manifest = create_scene_manifest(db, draft)
    scenes = get_scene_manifest_scenes(db, manifest.id)
    assets = get_scene_manifest_assets(db, scenes[0].id)
    assert len(assets) == 1
    asset = assets[0]
    assert asset.category == "stock_footage"
    assert asset.priority == "required"
    assert not asset.attribution_required
    assert not asset.commercial_safe
    assert not asset.ai_generation_requested


def test_create_scene_manifest_multi_asset(db):
    scene = _make_scene()
    scene.assets.append(_make_asset(asset_index=1, category="photograph"))
    draft = _make_draft(db, scenes=[scene])
    manifest = create_scene_manifest(db, draft)
    scenes = get_scene_manifest_scenes(db, manifest.id)
    assets = get_scene_manifest_assets(db, scenes[0].id)
    assert len(assets) == 2
    categories = {a.category for a in assets}
    assert categories == {"stock_footage", "photograph"}


# ── Get / lookup ──────────────────────────────────────────────────────────────


def test_get_scene_manifest_by_id_returns_none_for_missing(db):
    _seed(db)
    assert get_scene_manifest_by_id(db, 999) is None


def test_get_scene_manifest_by_input_hash(db):
    draft = _make_draft(db, input_hash="unique-hash-42")
    create_scene_manifest(db, draft)
    result = get_scene_manifest_by_input_hash(db, "unique-hash-42")
    assert result is not None
    assert result.input_hash == "unique-hash-42"


def test_get_scene_manifest_by_input_hash_missing(db):
    _seed(db)
    assert get_scene_manifest_by_input_hash(db, "no-such") is None


def test_get_scene_manifest_by_caption_run(db):
    draft = _make_draft(db)
    create_scene_manifest(db, draft)
    result = get_scene_manifest_by_caption_run(db, 1)
    assert result is not None


def test_get_scene_manifest_scene_by_id(db):
    draft = _make_draft(db)
    manifest = create_scene_manifest(db, draft)
    scenes = get_scene_manifest_scenes(db, manifest.id)
    scene_id = scenes[0].id
    scene = get_scene_manifest_scene_by_id(db, scene_id)
    assert scene is not None
    assert scene.id == scene_id


def test_get_scene_manifest_scene_by_id_missing(db):
    _seed(db)
    assert get_scene_manifest_scene_by_id(db, 999) is None


def test_get_all_scene_manifest_assets(db):
    scenes = [
        _make_scene(scene_index=0),
        _make_scene(scene_index=1, segment_id=1),
    ]
    scenes[1].assets.append(_make_asset(scene_index=1, asset_index=1))
    draft = _make_draft(db, input_hash="mh-multi", scenes=scenes)
    manifest = create_scene_manifest(db, draft)
    assets = get_all_scene_manifest_assets(db, manifest.id)
    assert len(assets) == 3


def test_list_scene_manifests_returns_empty_for_unknown_topic(db):
    _seed(db)
    assert list_scene_manifests(db, 999) == []


def test_list_scene_manifests(db):
    draft = _make_draft(db)
    create_scene_manifest(db, draft)
    results = list_scene_manifests(db, 1)
    assert len(results) == 1


# ── Idempotency ───────────────────────────────────────────────────────────────


def test_get_or_create_idempotent(db):
    draft = _make_draft(db, input_hash="idem-hash")
    m1, created1 = get_or_create_scene_manifest(db, draft)
    m2, created2 = get_or_create_scene_manifest(db, draft)
    assert created1 is True
    assert created2 is False
    assert m1.id == m2.id


# ── Approve ───────────────────────────────────────────────────────────────────


def test_approve_scene_manifest(db):
    draft = _make_draft(db)
    manifest = create_scene_manifest(db, draft)
    approved = approve_scene_manifest(db, manifest.id, actor="tester", notes="LGTM")
    db.commit()
    assert approved.status == "approved"
    assert approved.approved_at is not None


def test_approve_creates_review_event(db):
    draft = _make_draft(db)
    manifest = create_scene_manifest(db, draft)
    approve_scene_manifest(db, manifest.id, actor="tester")
    events = list_scene_manifest_review_events(db, manifest.id)
    assert len(events) == 1
    assert events[0].event_type == "approved"
    assert events[0].actor == "tester"


def test_approve_scene_manifest_not_found(db):
    _seed(db)
    with pytest.raises(ManifestNotFoundError):
        approve_scene_manifest(db, 999)


def test_approve_rejected_manifest_raises(db):
    draft = _make_draft(db)
    manifest = create_scene_manifest(db, draft)
    reject_scene_manifest(db, manifest.id, reason_code="quality")
    with pytest.raises(IllegalManifestTransitionError):
        approve_scene_manifest(db, manifest.id)


def test_approve_supersedes_previous_approved(db):
    draft1 = _make_draft(db, input_hash="mh1")
    m1 = create_scene_manifest(db, draft1)
    approve_scene_manifest(db, m1.id)
    db.commit()

    draft2 = SceneManifestDraft(
        caption_run_id=1,
        narration_run_id=1,
        plan_id=1,
        script_id=1,
        topic_id=1,
        experiment_id=None,
        input_hash="mh2",
        manifest_schema_version="1.0",
        planner_version="1.0",
        scenes=[_make_scene()],
    )
    m2 = create_scene_manifest(db, draft2)
    approve_scene_manifest(db, m2.id)
    db.commit()

    m1_refreshed = get_scene_manifest_by_id(db, m1.id)
    assert m1_refreshed.superseded_at is not None
    assert m1_refreshed.superseded_by_manifest_id == m2.id

    active = get_active_approved_scene_manifest(db, 1)
    assert active is not None
    assert active.id == m2.id


# ── Reject ────────────────────────────────────────────────────────────────────


def test_reject_scene_manifest(db):
    draft = _make_draft(db)
    manifest = create_scene_manifest(db, draft)
    rejected = reject_scene_manifest(
        db, manifest.id, reason_code="visual_mismatch", notes="Wrong", severity=3
    )
    db.commit()
    assert rejected.status == "rejected"
    assert rejected.rejected_at is not None


def test_reject_creates_review_event(db):
    draft = _make_draft(db)
    manifest = create_scene_manifest(db, draft)
    reject_scene_manifest(db, manifest.id, reason_code="quality", actor="reviewer")
    events = list_scene_manifest_review_events(db, manifest.id)
    assert any(ev.event_type == "rejected" for ev in events)
    assert any(ev.reason_code == "quality" for ev in events)


def test_reject_not_found(db):
    _seed(db)
    with pytest.raises(ManifestNotFoundError):
        reject_scene_manifest(db, 999)


# ── Scene rejection ───────────────────────────────────────────────────────────


def test_record_scene_rejection(db):
    draft = _make_draft(db)
    manifest = create_scene_manifest(db, draft)
    scenes = get_scene_manifest_scenes(db, manifest.id)
    event = record_scene_rejection(
        db,
        manifest.id,
        scenes[0].id,
        reason_code="wrong_shot",
        notes="Use wide shot",
        severity=2,
        expected_correction="wide",
        actor="director",
    )
    db.commit()
    assert event.event_type == "scene_rejected"
    assert event.reason_code == "wrong_shot"
    assert event.scene_id == scenes[0].id
    assert event.severity == 2
    assert event.expected_correction == "wide"


def test_record_scene_rejection_does_not_change_manifest_status(db):
    draft = _make_draft(db)
    manifest = create_scene_manifest(db, draft)
    scenes = get_scene_manifest_scenes(db, manifest.id)
    record_scene_rejection(db, manifest.id, scenes[0].id, reason_code="test")
    refreshed = get_scene_manifest_by_id(db, manifest.id)
    assert refreshed.status == "draft"


# ── Full handoff ──────────────────────────────────────────────────────────────


def test_get_approved_scene_manifest_full_none_when_no_approved(db):
    _seed(db)
    result = get_approved_scene_manifest_full(db, 1)
    assert result is None


def test_get_approved_scene_manifest_full_returns_manifest(db):
    draft = _make_draft(db)
    manifest = create_scene_manifest(db, draft)
    approve_scene_manifest(db, manifest.id)
    db.commit()

    result = get_approved_scene_manifest_full(db, 1)
    assert result is not None
    assert result.manifest_id == manifest.id
    assert result.total_scene_count == 1
    assert len(result.scenes) == 1
    scene = result.scenes[0]
    assert scene.shot_type == "medium"
    assert len(scene.assets) == 1
    assert scene.assets[0].category == "stock_footage"


def test_get_approved_scene_manifest_full_evidence_linkage(db):
    draft = _make_draft(db)
    manifest = create_scene_manifest(db, draft)
    approve_scene_manifest(db, manifest.id)
    db.commit()

    result = get_approved_scene_manifest_full(db, 1)
    assert result is not None
    scene = result.scenes[0]
    # claim_ids come from the PlannedSceneDraft we set (10, 11)
    assert 10 in scene.claim_ids
    assert 11 in scene.claim_ids


# ── JSON field round-trips ────────────────────────────────────────────────────


def test_usage_rights_dict_round_trip(db):
    asset = _make_asset(usage_rights={"license": "CC0", "attribution": False})
    scene = _make_scene()
    scene.assets = [asset]
    draft = _make_draft(db, scenes=[scene])
    manifest = create_scene_manifest(db, draft)
    scenes = get_scene_manifest_scenes(db, manifest.id)
    assets = get_scene_manifest_assets(db, scenes[0].id)
    assert assets[0].usage_rights == {"license": "CC0", "attribution": False}


def test_caption_cue_ids_round_trip(db):
    scene = _make_scene(caption_cue_ids=[5, 6, 7])
    draft = _make_draft(db, scenes=[scene])
    manifest = create_scene_manifest(db, draft)
    scenes_db = get_scene_manifest_scenes(db, manifest.id)
    assert scenes_db[0].caption_cue_ids == [5, 6, 7]


def test_evidence_ids_round_trip(db):
    scene = _make_scene(evidence_ids=[100, 200])
    draft = _make_draft(db, scenes=[scene])
    manifest = create_scene_manifest(db, draft)
    scenes_db = get_scene_manifest_scenes(db, manifest.id)
    assert scenes_db[0].evidence_ids == [100, 200]


def test_ai_generation_fields_round_trip(db):
    asset = _make_asset(
        ai_generation_requested=True,
        ai_generation_prompt="Generate a wide shot of mountains.",
        ai_generation_model="dall-e-3",
    )
    scene = _make_scene()
    scene.assets = [asset]
    draft = _make_draft(db, scenes=[scene])
    manifest = create_scene_manifest(db, draft)
    scenes_db = get_scene_manifest_scenes(db, manifest.id)
    assets_db = get_scene_manifest_assets(db, scenes_db[0].id)
    a = assets_db[0]
    assert a.ai_generation_requested is True
    assert a.ai_generation_prompt == "Generate a wide shot of mountains."
    assert a.ai_generation_model == "dall-e-3"
