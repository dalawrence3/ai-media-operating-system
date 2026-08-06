"""Phase 7 scene manifest repository.

Function-based, sqlite3.Connection first parameter.
No ORM. _row_to_X converters via model.from_row().
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from app.scenes.errors import (
    IllegalManifestTransitionError,
    ManifestNotFoundError,
)
from app.scenes.models import (
    ApprovedSceneManifest,
    ApprovedSceneScene,
    PlannedAssetDraft,
    PlannedSceneDraft,
    SceneManifest,
    SceneManifestAsset,
    SceneManifestDraft,
    SceneManifestReviewEvent,
    SceneManifestScene,
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def create_scene_manifest(
    conn: sqlite3.Connection,
    draft: SceneManifestDraft,
) -> SceneManifest:
    """Insert a new scene manifest and all its scenes/assets. Does not commit."""
    row = conn.execute(
        """
        INSERT INTO scene_manifests (
            caption_run_id, narration_run_id, plan_id, script_id, topic_id,
            experiment_id, input_hash, manifest_schema_version, planner_version,
            status, total_scene_count, total_asset_count, total_duration_ms,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            draft.caption_run_id,
            draft.narration_run_id,
            draft.plan_id,
            draft.script_id,
            draft.topic_id,
            draft.experiment_id,
            draft.input_hash,
            draft.manifest_schema_version,
            draft.planner_version,
            "draft",
            draft.total_scene_count,
            draft.total_asset_count,
            draft.total_duration_ms,
            _now(),
            _now(),
        ),
    ).lastrowid

    manifest_id = row  # type: ignore[assignment]

    # Index scenes by scene_index so assets can be assigned the DB scene id
    scene_id_by_index: dict[int, int] = {}
    for scene in draft.scenes:
        scene_id = _insert_scene(conn, manifest_id, scene)
        scene_id_by_index[scene.scene_index] = scene_id

    for scene in draft.scenes:
        scene_id = scene_id_by_index[scene.scene_index]
        for asset in scene.assets:
            _insert_asset(conn, scene_id, manifest_id, asset)

    return get_scene_manifest_by_id(conn, manifest_id)  # type: ignore[return-value]


def _insert_scene(
    conn: sqlite3.Connection,
    manifest_id: int,
    scene: PlannedSceneDraft,
) -> int:
    row = conn.execute(
        """
        INSERT INTO scene_manifest_scenes (
            manifest_id, scene_index, segment_id, narration_asset_id,
            caption_cue_ids_json, claim_ids_json, evidence_ids_json,
            script_section_index, narration_text,
            start_ms, end_ms, duration_ms,
            shot_type, camera_movement, transition_in, transition_out,
            visual_objective, visual_rationale, confidence, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            manifest_id,
            scene.scene_index,
            scene.segment_id,
            scene.narration_asset_id,
            json.dumps(scene.caption_cue_ids),
            json.dumps(scene.claim_ids),
            json.dumps(scene.evidence_ids),
            scene.script_section_index,
            scene.narration_text,
            scene.start_ms,
            scene.end_ms,
            scene.duration_ms,
            scene.shot_type,
            scene.camera_movement,
            scene.transition_in,
            scene.transition_out,
            scene.visual_objective,
            scene.visual_rationale,
            scene.confidence,
            _now(),
        ),
    ).lastrowid
    return row  # type: ignore[return-value]


def _insert_asset(
    conn: sqlite3.Connection,
    scene_id: int,
    manifest_id: int,
    asset: PlannedAssetDraft,
) -> int:
    row = conn.execute(
        """
        INSERT INTO scene_manifest_assets (
            scene_id, manifest_id, asset_index, category, priority,
            description, search_query, provider, source_url,
            license_status, license_name, attribution_required, attribution_text,
            commercial_safe, verification_status, usage_rights_json,
            ai_generation_requested, ai_generation_prompt, ai_generation_model,
            claim_ids_json, evidence_ids_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            scene_id,
            manifest_id,
            asset.asset_index,
            asset.category,
            asset.priority,
            asset.description,
            asset.search_query,
            asset.provider,
            asset.source_url,
            asset.license_status,
            asset.license_name,
            int(asset.attribution_required),
            asset.attribution_text,
            int(asset.commercial_safe),
            asset.verification_status,
            json.dumps(asset.usage_rights),
            int(asset.ai_generation_requested),
            asset.ai_generation_prompt,
            asset.ai_generation_model,
            json.dumps(asset.claim_ids),
            json.dumps(asset.evidence_ids),
            _now(),
        ),
    ).lastrowid
    return row  # type: ignore[return-value]


def get_or_create_scene_manifest(
    conn: sqlite3.Connection,
    draft: SceneManifestDraft,
) -> tuple[SceneManifest, bool]:
    """Return (manifest, created). Idempotent on input_hash."""
    existing = get_scene_manifest_by_input_hash(conn, draft.input_hash)
    if existing is not None:
        return existing, False
    manifest = create_scene_manifest(conn, draft)
    return manifest, True


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def get_scene_manifest_by_id(
    conn: sqlite3.Connection,
    manifest_id: int,
) -> SceneManifest | None:
    row = conn.execute("SELECT * FROM scene_manifests WHERE id = ?", (manifest_id,)).fetchone()
    return SceneManifest.from_row(row) if row else None


def get_scene_manifest_by_input_hash(
    conn: sqlite3.Connection,
    input_hash: str,
) -> SceneManifest | None:
    row = conn.execute(
        "SELECT * FROM scene_manifests WHERE input_hash = ?", (input_hash,)
    ).fetchone()
    return SceneManifest.from_row(row) if row else None


def get_scene_manifest_by_caption_run(
    conn: sqlite3.Connection,
    caption_run_id: int,
) -> SceneManifest | None:
    row = conn.execute(
        """
        SELECT * FROM scene_manifests
        WHERE caption_run_id = ? AND superseded_at IS NULL
        ORDER BY created_at DESC LIMIT 1
        """,
        (caption_run_id,),
    ).fetchone()
    return SceneManifest.from_row(row) if row else None


def get_scene_manifest_scenes(
    conn: sqlite3.Connection,
    manifest_id: int,
) -> list[SceneManifestScene]:
    rows = conn.execute(
        "SELECT * FROM scene_manifest_scenes WHERE manifest_id = ? ORDER BY scene_index",
        (manifest_id,),
    ).fetchall()
    return [SceneManifestScene.from_row(r) for r in rows]


def get_scene_manifest_scene_by_id(
    conn: sqlite3.Connection,
    scene_id: int,
) -> SceneManifestScene | None:
    row = conn.execute("SELECT * FROM scene_manifest_scenes WHERE id = ?", (scene_id,)).fetchone()
    return SceneManifestScene.from_row(row) if row else None


def get_scene_manifest_assets(
    conn: sqlite3.Connection,
    scene_id: int,
) -> list[SceneManifestAsset]:
    rows = conn.execute(
        "SELECT * FROM scene_manifest_assets WHERE scene_id = ? ORDER BY asset_index",
        (scene_id,),
    ).fetchall()
    return [SceneManifestAsset.from_row(r) for r in rows]


def get_all_scene_manifest_assets(
    conn: sqlite3.Connection,
    manifest_id: int,
) -> list[SceneManifestAsset]:
    rows = conn.execute(
        """
        SELECT a.* FROM scene_manifest_assets a
        WHERE a.manifest_id = ?
        ORDER BY a.scene_id, a.asset_index
        """,
        (manifest_id,),
    ).fetchall()
    return [SceneManifestAsset.from_row(r) for r in rows]


def list_scene_manifests(
    conn: sqlite3.Connection,
    topic_id: int,
) -> list[SceneManifest]:
    rows = conn.execute(
        "SELECT * FROM scene_manifests WHERE topic_id = ? ORDER BY created_at DESC",
        (topic_id,),
    ).fetchall()
    return [SceneManifest.from_row(r) for r in rows]


def get_active_approved_scene_manifest(
    conn: sqlite3.Connection,
    topic_id: int,
) -> SceneManifest | None:
    row = conn.execute(
        """
        SELECT * FROM scene_manifests
        WHERE topic_id = ? AND status = 'approved' AND superseded_at IS NULL
        ORDER BY approved_at DESC LIMIT 1
        """,
        (topic_id,),
    ).fetchone()
    return SceneManifest.from_row(row) if row else None


def list_scene_manifest_review_events(
    conn: sqlite3.Connection,
    manifest_id: int,
) -> list[SceneManifestReviewEvent]:
    rows = conn.execute(
        "SELECT * FROM scene_manifest_review_events WHERE manifest_id = ? ORDER BY created_at",
        (manifest_id,),
    ).fetchall()
    return [SceneManifestReviewEvent.from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Review: approve / reject
# ---------------------------------------------------------------------------


def approve_scene_manifest(
    conn: sqlite3.Connection,
    manifest_id: int,
    *,
    actor: str | None = None,
    notes: str | None = None,
) -> SceneManifest:
    """Approve a draft manifest; supersede any existing approved manifest."""
    manifest = get_scene_manifest_by_id(conn, manifest_id)
    if manifest is None:
        raise ManifestNotFoundError(f"Scene manifest {manifest_id} not found.")
    if manifest.status not in ("draft",):
        raise IllegalManifestTransitionError(
            f"Cannot approve manifest {manifest_id} with status '{manifest.status}'."
        )

    now = _now()

    # Supersede any currently approved manifest for this topic
    conn.execute(
        """
        UPDATE scene_manifests
        SET superseded_at = ?, superseded_by_manifest_id = ?, updated_at = ?
        WHERE topic_id = ? AND status = 'approved' AND superseded_at IS NULL AND id != ?
        """,
        (now, manifest_id, now, manifest.topic_id, manifest_id),
    )

    conn.execute(
        "UPDATE scene_manifests"
        " SET status = 'approved', approved_at = ?, updated_at = ? WHERE id = ?",
        (now, now, manifest_id),
    )

    _record_review_event(
        conn,
        manifest=manifest,
        event_type="approved",
        actor=actor,
        notes=notes,
    )

    return get_scene_manifest_by_id(conn, manifest_id)  # type: ignore[return-value]


def reject_scene_manifest(
    conn: sqlite3.Connection,
    manifest_id: int,
    *,
    reason_code: str | None = None,
    notes: str | None = None,
    severity: int | None = None,
    expected_correction: str | None = None,
    actor: str | None = None,
) -> SceneManifest:
    """Reject a draft or approved manifest."""
    manifest = get_scene_manifest_by_id(conn, manifest_id)
    if manifest is None:
        raise ManifestNotFoundError(f"Scene manifest {manifest_id} not found.")
    if manifest.status not in ("draft", "approved"):
        raise IllegalManifestTransitionError(
            f"Cannot reject manifest {manifest_id} with status '{manifest.status}'."
        )

    now = _now()
    conn.execute(
        "UPDATE scene_manifests"
        " SET status = 'rejected', rejected_at = ?, updated_at = ? WHERE id = ?",
        (now, now, manifest_id),
    )

    _record_review_event(
        conn,
        manifest=manifest,
        event_type="rejected",
        reason_code=reason_code,
        severity=severity,
        expected_correction=expected_correction,
        notes=notes,
        actor=actor,
    )

    return get_scene_manifest_by_id(conn, manifest_id)  # type: ignore[return-value]


def record_scene_rejection(
    conn: sqlite3.Connection,
    manifest_id: int,
    scene_id: int,
    *,
    reason_code: str | None = None,
    notes: str | None = None,
    severity: int | None = None,
    expected_correction: str | None = None,
    actor: str | None = None,
) -> SceneManifestReviewEvent:
    """Record a scene-level rejection event (does not change manifest status)."""
    manifest = get_scene_manifest_by_id(conn, manifest_id)
    if manifest is None:
        raise ManifestNotFoundError(f"Scene manifest {manifest_id} not found.")

    return _record_review_event(
        conn,
        manifest=manifest,
        event_type="scene_rejected",
        scene_id=scene_id,
        reason_code=reason_code,
        severity=severity,
        expected_correction=expected_correction,
        notes=notes,
        actor=actor,
    )


def _record_review_event(
    conn: sqlite3.Connection,
    *,
    manifest: SceneManifest,
    event_type: str,
    scene_id: int | None = None,
    reason_code: str | None = None,
    severity: int | None = None,
    expected_correction: str | None = None,
    notes: str | None = None,
    actor: str | None = None,
) -> SceneManifestReviewEvent:
    now = _now()
    row = conn.execute(
        """
        INSERT INTO scene_manifest_review_events (
            manifest_id, scene_id, topic_id, plan_id, script_id,
            caption_run_id, narration_run_id, experiment_id,
            manifest_schema_version, event_type,
            reason_code, severity, expected_correction, notes, actor, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            manifest.id,
            scene_id,
            manifest.topic_id,
            manifest.plan_id,
            manifest.script_id,
            manifest.caption_run_id,
            manifest.narration_run_id,
            manifest.experiment_id,
            manifest.manifest_schema_version,
            event_type,
            reason_code,
            severity,
            expected_correction,
            notes,
            actor,
            now,
        ),
    ).lastrowid
    result = conn.execute(
        "SELECT * FROM scene_manifest_review_events WHERE id = ?", (row,)
    ).fetchone()
    return SceneManifestReviewEvent.from_row(result)


# ---------------------------------------------------------------------------
# Handoff
# ---------------------------------------------------------------------------


def _hydrate_manifest(
    conn: sqlite3.Connection,
    manifest: SceneManifest,
) -> ApprovedSceneManifest:
    scenes_raw = get_scene_manifest_scenes(conn, manifest.id)
    resolved_scenes: list[ApprovedSceneScene] = []
    for s in scenes_raw:
        assets = get_scene_manifest_assets(conn, s.id)
        resolved_scenes.append(
            ApprovedSceneScene(
                scene_id=s.id,
                manifest_id=s.manifest_id,
                scene_index=s.scene_index,
                segment_id=s.segment_id,
                narration_asset_id=s.narration_asset_id,
                caption_cue_ids=s.caption_cue_ids,
                claim_ids=s.claim_ids,
                evidence_ids=s.evidence_ids,
                script_section_index=s.script_section_index,
                narration_text=s.narration_text,
                start_ms=s.start_ms,
                end_ms=s.end_ms,
                duration_ms=s.duration_ms,
                shot_type=s.shot_type,
                camera_movement=s.camera_movement,
                transition_in=s.transition_in,
                transition_out=s.transition_out,
                visual_objective=s.visual_objective,
                visual_rationale=s.visual_rationale,
                confidence=s.confidence,
                assets=assets,
            )
        )
    return ApprovedSceneManifest(
        manifest_id=manifest.id,
        caption_run_id=manifest.caption_run_id,
        narration_run_id=manifest.narration_run_id,
        plan_id=manifest.plan_id,
        script_id=manifest.script_id,
        topic_id=manifest.topic_id,
        experiment_id=manifest.experiment_id,
        input_hash=manifest.input_hash,
        manifest_schema_version=manifest.manifest_schema_version,
        planner_version=manifest.planner_version,
        approved_at=manifest.approved_at or datetime.now(UTC),
        scenes=resolved_scenes,
    )


def get_scene_manifest_full_by_id(
    conn: sqlite3.Connection,
    manifest_id: int,
) -> ApprovedSceneManifest | None:
    """Return the fully hydrated manifest for any manifest ID (approved or not)."""
    manifest = get_scene_manifest_by_id(conn, manifest_id)
    if manifest is None or manifest.status not in ("approved",):
        return None
    return _hydrate_manifest(conn, manifest)


def get_approved_scene_manifest_full(
    conn: sqlite3.Connection,
    topic_id: int,
) -> ApprovedSceneManifest | None:
    """Return the fully hydrated approved manifest for a topic."""
    manifest = get_active_approved_scene_manifest(conn, topic_id)
    if manifest is None:
        return None
    return _hydrate_manifest(conn, manifest)
