"""Tests for Phase 7 scenes CLI."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import app
from app.core.config import reset_config
from app.core.database import open_db
from app.scenes.models import PlannedAssetDraft, PlannedSceneDraft, SceneManifestDraft
from app.scenes.repository import (
    approve_scene_manifest,
    create_scene_manifest,
)

runner = CliRunner()


@pytest.fixture()
def isolated_db(tmp_path: Path, monkeypatch) -> sqlite3.Connection:
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ACE_DB_PATH", str(db_path))
    reset_config()
    return open_db(db_path)


def _seed_full(conn: sqlite3.Connection) -> dict:
    """Insert a full pipeline chain with FK checks off."""
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
        " VALUES (1, 1, 'ph', 1, 1, 'en-US', 1.0, '{}', 'wav', 22050,"
        "  'nrh', 'approved', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO narration_segment_assets"
        " (id, run_id, segment_id, narration_text_hash, provider, model, voice_id,"
        "  voice_profile_id, voice_profile_version, language, speaking_rate,"
        "  settings_json_hash, output_format, sample_rate_hz, input_hash, status,"
        "  audio_sha256, duration_seconds, created_at, updated_at)"
        " VALUES (1, 1, 1, 'th1', 'fake', 'fm', 'fv',"
        "  1, 1, 'en-US', 1.0, 'sh', 'wav', 22050, 'ah', 'synthesized',"
        "  'audio1', 3.0, '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO caption_runs"
        " (id, narration_run_id, plan_id, script_id, topic_id,"
        "  input_hash, caption_schema_version, segmentation_version,"
        "  timing_algorithm_version, style_version, exporter_version, language,"
        "  status, total_cue_count, total_duration_ms, created_at, updated_at)"
        " VALUES (1, 1, 1, 1, 1,"
        "  'crh', 'csv1', 'sgv1', 'tav1', 'stv1', 'exv1', 'en-US',"
        "  'approved', 0, 3000, '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    return {"caption_run_id": 1, "narration_run_id": 1, "plan_id": 1, "topic_id": 1}


def _make_minimal_draft(conn, ids, input_hash="test-mh") -> SceneManifestDraft:
    asset = PlannedAssetDraft(
        scene_index=0,
        asset_index=0,
        category="stock_footage",
        priority="required",
        description="Test",
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
    scene = PlannedSceneDraft(
        scene_index=0,
        segment_id=1,
        narration_asset_id=1,
        caption_cue_ids=[],
        claim_ids=[],
        evidence_ids=[],
        script_section_index=0,
        narration_text="Hello.",
        start_ms=0,
        end_ms=3000,
        duration_ms=3000,
        shot_type="close_up",
        camera_movement="zoom_in",
        transition_in="fade_from_black",
        transition_out="fade_to_black",
        visual_objective="Open.",
        visual_rationale="hook",
        confidence=0.9,
        assets=[asset],
    )
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
        scenes=[scene],
    )


# ── scenes plan ───────────────────────────────────────────────────────────────


def test_scenes_plan_no_production_plan(isolated_db):
    result = runner.invoke(app, ["scenes", "plan", "99"])
    assert result.exit_code == 1
    assert "No approved production plan" in result.output


def test_scenes_plan_dry_run(isolated_db):
    _seed_full(isolated_db)
    result = runner.invoke(app, ["scenes", "plan", "1", "--dry-run"])
    assert result.exit_code == 0
    assert "dry-run" in result.output
    assert "scenes=" in result.output


def test_scenes_plan_persists(isolated_db):
    _seed_full(isolated_db)
    result = runner.invoke(app, ["scenes", "plan", "1"])
    assert result.exit_code == 0
    assert "Created scene manifest" in result.output or "already exists" in result.output


def test_scenes_plan_idempotent(isolated_db):
    _seed_full(isolated_db)
    r1 = runner.invoke(app, ["scenes", "plan", "1"])
    r2 = runner.invoke(app, ["scenes", "plan", "1"])
    assert r1.exit_code == 0
    assert r2.exit_code == 0
    assert "already exists" in r2.output


# ── scenes list ───────────────────────────────────────────────────────────────


def test_scenes_list_empty(isolated_db):
    _seed_full(isolated_db)
    result = runner.invoke(app, ["scenes", "list", "1"])
    assert result.exit_code == 0
    assert "No scene manifests" in result.output


def test_scenes_list_shows_manifests(isolated_db):
    ids = _seed_full(isolated_db)
    draft = _make_minimal_draft(isolated_db, ids)
    create_scene_manifest(isolated_db, draft)
    isolated_db.commit()
    result = runner.invoke(app, ["scenes", "list", "1"])
    assert result.exit_code == 0
    assert "draft" in result.output


# ── scenes show ───────────────────────────────────────────────────────────────


def test_scenes_show_not_found(isolated_db):
    _seed_full(isolated_db)
    result = runner.invoke(app, ["scenes", "show", "999"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_scenes_show_displays_details(isolated_db):
    ids = _seed_full(isolated_db)
    draft = _make_minimal_draft(isolated_db, ids)
    manifest = create_scene_manifest(isolated_db, draft)
    isolated_db.commit()
    result = runner.invoke(app, ["scenes", "show", str(manifest.id)])
    assert result.exit_code == 0
    assert "Scene Manifest" in result.output
    assert "draft" in result.output
    assert "stock_footage" in result.output


# ── scenes approve ────────────────────────────────────────────────────────────


def test_scenes_approve_success(isolated_db):
    ids = _seed_full(isolated_db)
    draft = _make_minimal_draft(isolated_db, ids)
    manifest = create_scene_manifest(isolated_db, draft)
    isolated_db.commit()
    result = runner.invoke(app, ["scenes", "approve", str(manifest.id), "--actor", "tester"])
    assert result.exit_code == 0
    assert "Approved" in result.output


def test_scenes_approve_not_found(isolated_db):
    _seed_full(isolated_db)
    result = runner.invoke(app, ["scenes", "approve", "999"])
    assert result.exit_code == 1


def test_scenes_approve_already_rejected(isolated_db):
    ids = _seed_full(isolated_db)
    draft = _make_minimal_draft(isolated_db, ids)
    manifest = create_scene_manifest(isolated_db, draft)
    from app.scenes.repository import reject_scene_manifest
    reject_scene_manifest(isolated_db, manifest.id, reason_code="test")
    isolated_db.commit()
    result = runner.invoke(app, ["scenes", "approve", str(manifest.id)])
    assert result.exit_code == 1


# ── scenes reject ─────────────────────────────────────────────────────────────


def test_scenes_reject_success(isolated_db):
    ids = _seed_full(isolated_db)
    draft = _make_minimal_draft(isolated_db, ids)
    manifest = create_scene_manifest(isolated_db, draft)
    isolated_db.commit()
    result = runner.invoke(
        app,
        ["scenes", "reject", str(manifest.id), "--reason-code", "visual_quality", "--actor", "qa"],
    )
    assert result.exit_code == 0
    assert "Rejected" in result.output


def test_scenes_reject_not_found(isolated_db):
    _seed_full(isolated_db)
    result = runner.invoke(app, ["scenes", "reject", "999"])
    assert result.exit_code == 1


# ── scenes reject-scene ───────────────────────────────────────────────────────


def test_scenes_reject_scene_success(isolated_db):
    ids = _seed_full(isolated_db)
    draft = _make_minimal_draft(isolated_db, ids)
    manifest = create_scene_manifest(isolated_db, draft)
    from app.scenes.repository import get_scene_manifest_scenes
    scenes = get_scene_manifest_scenes(isolated_db, manifest.id)
    isolated_db.commit()
    result = runner.invoke(
        app,
        [
            "scenes", "reject-scene", str(manifest.id), str(scenes[0].id),
            "--reason-code", "wrong_shot", "--actor", "director",
        ],
    )
    assert result.exit_code == 0
    assert "rejection recorded" in result.output


# ── scenes events ─────────────────────────────────────────────────────────────


def test_scenes_events_empty(isolated_db):
    ids = _seed_full(isolated_db)
    draft = _make_minimal_draft(isolated_db, ids)
    manifest = create_scene_manifest(isolated_db, draft)
    isolated_db.commit()
    result = runner.invoke(app, ["scenes", "events", str(manifest.id)])
    assert result.exit_code == 0
    assert "No review events" in result.output


def test_scenes_events_shows_events(isolated_db):
    ids = _seed_full(isolated_db)
    draft = _make_minimal_draft(isolated_db, ids)
    manifest = create_scene_manifest(isolated_db, draft)
    approve_scene_manifest(isolated_db, manifest.id, actor="tester")
    isolated_db.commit()
    result = runner.invoke(app, ["scenes", "events", str(manifest.id)])
    assert result.exit_code == 0
    assert "approved" in result.output


# ── scenes manifest ───────────────────────────────────────────────────────────


def test_scenes_manifest_no_approved(isolated_db):
    _seed_full(isolated_db)
    result = runner.invoke(app, ["scenes", "manifest", "1"])
    assert result.exit_code == 1
    assert "No approved" in result.output


def test_scenes_manifest_shows_details(isolated_db):
    ids = _seed_full(isolated_db)
    draft = _make_minimal_draft(isolated_db, ids)
    manifest = create_scene_manifest(isolated_db, draft)
    approve_scene_manifest(isolated_db, manifest.id)
    isolated_db.commit()
    result = runner.invoke(app, ["scenes", "manifest", "1"])
    assert result.exit_code == 0
    assert "Approved Scene Manifest" in result.output
    assert "total_scenes" in result.output
