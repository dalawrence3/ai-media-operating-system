"""Phase 8 render manifest and job repository.

Function-based, sqlite3.Connection first parameter.
No ORM. _row_to_X converters via model.from_row().
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from app.media.constants import (
    RENDER_JOB_STATUS_COMPLETED,
    RENDER_JOB_STATUS_FAILED,
    RENDER_JOB_STATUS_RENDERING,
    RENDER_JOB_VALID_TRANSITIONS,
    RENDER_REVIEW_EVENT_TYPES,
    RENDER_STATUS_APPROVED,
    RENDER_STATUS_REJECTED,
    RENDER_STATUS_SUPERSEDED,
    RENDER_VALID_TRANSITIONS,
)
from app.media.errors import (
    AssetLicenseRejectedError,
    IllegalRenderJobTransitionError,
    IllegalRenderTransitionError,
    RenderJobNotFoundError,
    RenderManifestAlreadyExistsError,
    RenderManifestNotFoundError,
)
from app.media.models import (
    ApprovedRender,
    RenderJob,
    RenderManifest,
    RenderManifestDraft,
    RenderManifestScene,
    RenderReviewEvent,
    RenderSceneDraft,
    ResolvedAsset,
    ResolvedAssetRecord,
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


_VALID_LICENSE_STATUSES = frozenset({"unverified", "verified", "rejected", "not_required"})


def _normalize_license(status: str) -> str:
    return status if status in _VALID_LICENSE_STATUSES else "unverified"


# ---------------------------------------------------------------------------
# RenderManifest — create / read / approve / reject
# ---------------------------------------------------------------------------


def create_render_manifest(
    conn: sqlite3.Connection,
    draft: RenderManifestDraft,
) -> RenderManifest:
    """Insert a new render manifest. Raises RenderManifestAlreadyExistsError on hash clash.

    Also validates asset licenses and persists render_manifest_scenes and resolved_assets.
    """
    # Pre-flight: reject manifests containing a license-rejected primary asset.
    for scene in draft.scenes:
        if scene.primary_asset is not None:
            norm = _normalize_license(scene.primary_asset.license_status)
            if norm == "rejected":
                raise AssetLicenseRejectedError(
                    f"Scene {scene.scene_index} primary asset has rejected license "
                    f"(status={scene.primary_asset.license_status!r}). "
                    "Resolve the license before creating a render manifest."
                )

    try:
        row_id = conn.execute(
            """
            INSERT INTO render_manifests (
                scene_manifest_id, narration_run_id, caption_run_id,
                topic_id, plan_id, script_id,
                experiment_id, input_hash, render_schema_version, compositor_version,
                total_scene_count, total_duration_ms,
                width, height, fps, caption_burn_in,
                status, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                draft.scene_manifest_id,
                draft.narration_run_id,
                draft.caption_run_id,
                draft.topic_id,
                draft.plan_id,
                draft.script_id,
                draft.experiment_id,
                draft.input_hash,
                draft.render_schema_version,
                draft.compositor_version,
                draft.total_scene_count,
                draft.total_duration_ms,
                draft.width,
                draft.height,
                draft.fps,
                1 if draft.caption_burn_in else 0,
                "draft",
                _now(),
                _now(),
            ),
        ).lastrowid
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" in str(exc).upper():
            raise RenderManifestAlreadyExistsError(
                f"Render manifest already exists for input_hash={draft.input_hash!r}"
            ) from exc
        raise

    # Persist resolved assets and render scenes.
    for scene in draft.scenes:
        asset_db_id: int | None = None
        if scene.primary_asset is not None:
            asset_rec = create_resolved_asset(
                conn, scene.primary_asset, render_manifest_id=row_id
            )
            asset_db_id = asset_rec.id
        create_render_manifest_scene(conn, row_id, scene, primary_asset_id=asset_db_id)

    manifest = get_render_manifest(conn, row_id)  # type: ignore[arg-type]
    assert manifest is not None
    return manifest


def get_or_create_render_manifest(
    conn: sqlite3.Connection,
    draft: RenderManifestDraft,
) -> tuple[RenderManifest, bool]:
    """Return (manifest, created). Idempotent on input_hash."""
    existing = conn.execute(
        "SELECT * FROM render_manifests WHERE input_hash = ?",
        (draft.input_hash,),
    ).fetchone()
    if existing:
        return RenderManifest.from_row(existing), False
    manifest = create_render_manifest(conn, draft)
    return manifest, True


def get_render_manifest(
    conn: sqlite3.Connection, manifest_id: int
) -> RenderManifest | None:
    row = conn.execute(
        "SELECT * FROM render_manifests WHERE id = ?", (manifest_id,)
    ).fetchone()
    return RenderManifest.from_row(row) if row else None


def list_render_manifests(
    conn: sqlite3.Connection,
    *,
    topic_id: int | None = None,
    scene_manifest_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[RenderManifest]:
    clauses = []
    params: list = []
    if topic_id is not None:
        clauses.append("topic_id = ?")
        params.append(topic_id)
    if scene_manifest_id is not None:
        clauses.append("scene_manifest_id = ?")
        params.append(scene_manifest_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM render_manifests {where} ORDER BY created_at DESC, id DESC LIMIT ?",
        params,
    ).fetchall()
    return [RenderManifest.from_row(r) for r in rows]


def approve_render_manifest(
    conn: sqlite3.Connection,
    manifest_id: int,
    *,
    actor: str | None = None,
    notes: str | None = None,
    reason_code: str | None = None,
    severity: int | None = None,
    expected_correction: str | None = None,
    render_job_id: int | None = None,
) -> RenderManifest:
    """Approve a render manifest. Supersedes any previously approved manifest
    for the same scene_manifest_id."""
    manifest = _require_manifest(conn, manifest_id)
    _check_render_transition(manifest.status, RENDER_STATUS_APPROVED)

    now = _now()

    # Supersede any existing approved manifests for this scene_manifest_id
    conn.execute(
        """
        UPDATE render_manifests
        SET status = ?, superseded_at = ?, superseded_by_id = ?, updated_at = ?
        WHERE scene_manifest_id = ? AND status = ? AND superseded_at IS NULL AND id != ?
        """,
        (
            RENDER_STATUS_SUPERSEDED, now, manifest_id, now,
            manifest.scene_manifest_id, RENDER_STATUS_APPROVED, manifest_id,
        ),
    )

    conn.execute(
        """
        UPDATE render_manifests
        SET status = ?, approved_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (RENDER_STATUS_APPROVED, now, now, manifest_id),
    )

    record_render_review_event(
        conn,
        manifest_id=manifest_id,
        render_job_id=render_job_id,
        event_type="render_approved",
        actor=actor,
        notes=notes,
        reason_code=reason_code,
        severity=severity,
        expected_correction=expected_correction,
    )

    updated = get_render_manifest(conn, manifest_id)
    assert updated is not None
    return updated


def reject_render_manifest(
    conn: sqlite3.Connection,
    manifest_id: int,
    *,
    actor: str | None = None,
    notes: str | None = None,
    reason_code: str | None = None,
    severity: int | None = None,
    expected_correction: str | None = None,
    render_job_id: int | None = None,
) -> RenderManifest:
    manifest = _require_manifest(conn, manifest_id)
    _check_render_transition(manifest.status, RENDER_STATUS_REJECTED)

    now = _now()
    conn.execute(
        """
        UPDATE render_manifests
        SET status = ?, rejected_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (RENDER_STATUS_REJECTED, now, now, manifest_id),
    )

    record_render_review_event(
        conn,
        manifest_id=manifest_id,
        render_job_id=render_job_id,
        event_type="render_rejected",
        actor=actor,
        notes=notes,
        reason_code=reason_code,
        severity=severity,
        expected_correction=expected_correction,
    )

    updated = get_render_manifest(conn, manifest_id)
    assert updated is not None
    return updated


# ---------------------------------------------------------------------------
# RenderJob — create / update
# ---------------------------------------------------------------------------


def create_render_job(
    conn: sqlite3.Connection,
    render_manifest_id: int,
    *,
    backend: str,
    backend_version: str,
    width: int,
    height: int,
    fps: int,
    video_codec: str,
    audio_codec: str,
    crf: int,
    audio_bitrate: str,
    caption_burn_in: bool,
) -> RenderJob:
    now = _now()
    row_id = conn.execute(
        """
        INSERT INTO render_jobs (
            render_manifest_id, backend, backend_version,
            width, height, fps, video_codec, audio_codec, crf, audio_bitrate,
            caption_burn_in, status, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            render_manifest_id,
            backend,
            backend_version,
            width,
            height,
            fps,
            video_codec,
            audio_codec,
            crf,
            audio_bitrate,
            1 if caption_burn_in else 0,
            "pending",
            now,
            now,
        ),
    ).lastrowid
    job = get_render_job(conn, row_id)  # type: ignore[arg-type]
    assert job is not None
    return job


def mark_render_job_rendering(
    conn: sqlite3.Connection, job_id: int
) -> RenderJob:
    job = _require_job(conn, job_id)
    _check_job_transition(job.status, RENDER_JOB_STATUS_RENDERING)
    now = _now()
    conn.execute(
        "UPDATE render_jobs SET status=?, started_at=?, updated_at=? WHERE id=?",
        (RENDER_JOB_STATUS_RENDERING, now, now, job_id),
    )
    updated = get_render_job(conn, job_id)
    assert updated is not None
    return updated


def mark_render_job_completed(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    output_path: str,
    output_sha256: str,
    duration_s: float,
    file_size_bytes: int,
    render_time_s: float,
    ffmpeg_cmd: list[str],
) -> RenderJob:
    job = _require_job(conn, job_id)
    _check_job_transition(job.status, RENDER_JOB_STATUS_COMPLETED)
    now = _now()
    conn.execute(
        """
        UPDATE render_jobs
        SET status=?, output_path=?, output_sha256=?, duration_s=?,
            file_size_bytes=?, render_time_s=?, ffmpeg_cmd_json=?,
            completed_at=?, updated_at=?
        WHERE id=?
        """,
        (
            RENDER_JOB_STATUS_COMPLETED,
            output_path,
            output_sha256,
            duration_s,
            file_size_bytes,
            render_time_s,
            json.dumps(ffmpeg_cmd),
            now,
            now,
            job_id,
        ),
    )
    updated = get_render_job(conn, job_id)
    assert updated is not None
    return updated


def mark_render_job_failed(
    conn: sqlite3.Connection, job_id: int, *, error_message: str
) -> RenderJob:
    job = _require_job(conn, job_id)
    _check_job_transition(job.status, RENDER_JOB_STATUS_FAILED)
    now = _now()
    conn.execute(
        "UPDATE render_jobs SET status=?, error_message=?, completed_at=?, updated_at=? WHERE id=?",
        (RENDER_JOB_STATUS_FAILED, error_message, now, now, job_id),
    )
    updated = get_render_job(conn, job_id)
    assert updated is not None
    return updated


def mark_render_job_validated(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    validation_metadata: dict,
) -> RenderJob:
    now = _now()
    conn.execute(
        "UPDATE render_jobs SET validated=1, validation_metadata_json=?, updated_at=? WHERE id=?",
        (json.dumps(validation_metadata), now, job_id),
    )
    updated = get_render_job(conn, job_id)
    assert updated is not None
    return updated


def get_render_job(conn: sqlite3.Connection, job_id: int) -> RenderJob | None:
    row = conn.execute(
        "SELECT * FROM render_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    return RenderJob.from_row(row) if row else None


def list_render_jobs(
    conn: sqlite3.Connection, render_manifest_id: int
) -> list[RenderJob]:
    rows = conn.execute(
        "SELECT * FROM render_jobs WHERE render_manifest_id = ? ORDER BY created_at DESC, id DESC",
        (render_manifest_id,),
    ).fetchall()
    return [RenderJob.from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Review events
# ---------------------------------------------------------------------------


def record_render_review_event(
    conn: sqlite3.Connection,
    manifest_id: int,
    *,
    event_type: str,
    render_job_id: int | None = None,
    actor: str | None = None,
    notes: str | None = None,
    reason_code: str | None = None,
    severity: int | None = None,
    expected_correction: str | None = None,
) -> RenderReviewEvent:
    assert event_type in RENDER_REVIEW_EVENT_TYPES, f"Unknown event_type: {event_type!r}"
    if reason_code == "other" and not notes:
        raise ValueError("reason_code='other' requires notes to be provided.")
    if severity is not None and not (1 <= severity <= 5):
        raise ValueError(f"severity must be between 1 and 5, got {severity!r}.")

    manifest = _require_manifest(conn, manifest_id)
    now = _now()
    row_id = conn.execute(
        """
        INSERT INTO render_review_events (
            render_manifest_id, render_job_id,
            topic_id, plan_id, script_id, scene_manifest_id,
            experiment_id, render_schema_version,
            event_type, reason_code, severity, expected_correction, notes, actor,
            created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            manifest_id,
            render_job_id,
            manifest.topic_id,
            manifest.plan_id,
            manifest.script_id,
            manifest.scene_manifest_id,
            manifest.experiment_id,
            manifest.render_schema_version,
            event_type,
            reason_code,
            severity,
            expected_correction,
            notes,
            actor,
            now,
        ),
    ).lastrowid
    row = conn.execute(
        "SELECT * FROM render_review_events WHERE id = ?", (row_id,)
    ).fetchone()
    assert row is not None
    return RenderReviewEvent.from_row(row)


def list_render_review_events(
    conn: sqlite3.Connection, manifest_id: int
) -> list[RenderReviewEvent]:
    rows = conn.execute(
        "SELECT * FROM render_review_events WHERE render_manifest_id = ? ORDER BY created_at, id",
        (manifest_id,),
    ).fetchall()
    return [RenderReviewEvent.from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Approved render handoff
# ---------------------------------------------------------------------------


def get_approved_render(
    conn: sqlite3.Connection,
    scene_manifest_id: int,
    *,
    experiment_id: str | None = None,
) -> ApprovedRender | None:
    """Return the active approved render for a scene manifest, or None."""
    if experiment_id is None:
        row = conn.execute(
            """
            SELECT rm.*, rj.id AS job_id, rj.output_path, rj.output_sha256,
                   rj.duration_s, rj.video_codec, rj.audio_codec
            FROM render_manifests rm
            JOIN render_jobs rj ON rj.render_manifest_id = rm.id AND rj.status = ?
            WHERE rm.scene_manifest_id = ? AND rm.status = ? AND rm.superseded_at IS NULL
                  AND rm.experiment_id IS NULL
            ORDER BY rm.approved_at DESC LIMIT 1
            """,
            (RENDER_JOB_STATUS_COMPLETED, scene_manifest_id, RENDER_STATUS_APPROVED),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT rm.*, rj.id AS job_id, rj.output_path, rj.output_sha256,
                   rj.duration_s, rj.video_codec, rj.audio_codec
            FROM render_manifests rm
            JOIN render_jobs rj ON rj.render_manifest_id = rm.id AND rj.status = ?
            WHERE rm.scene_manifest_id = ? AND rm.status = ? AND rm.superseded_at IS NULL
                  AND rm.experiment_id = ?
            ORDER BY rm.approved_at DESC LIMIT 1
            """,
            (RENDER_JOB_STATUS_COMPLETED, scene_manifest_id, RENDER_STATUS_APPROVED, experiment_id),
        ).fetchone()

    if row is None:
        return None
    d = dict(row)
    return ApprovedRender(
        render_manifest_id=d["id"],
        render_job_id=d["job_id"],
        scene_manifest_id=d["scene_manifest_id"],
        narration_run_id=d["narration_run_id"],
        caption_run_id=d["caption_run_id"],
        topic_id=d["topic_id"],
        plan_id=d["plan_id"],
        script_id=d["script_id"],
        experiment_id=d["experiment_id"],
        output_path=d["output_path"],
        output_sha256=d["output_sha256"],
        duration_s=d["duration_s"],
        width=d["width"],
        height=d["height"],
        fps=d["fps"],
        video_codec=d["video_codec"],
        audio_codec=d["audio_codec"],
        approved_at=datetime.fromisoformat(d["approved_at"]),
    )


# ---------------------------------------------------------------------------
# RenderManifestScene — create / read
# ---------------------------------------------------------------------------


def create_render_manifest_scene(
    conn: sqlite3.Connection,
    render_manifest_id: int,
    scene: RenderSceneDraft,
    *,
    primary_asset_id: int | None = None,
) -> RenderManifestScene:
    has_placeholder = scene.primary_asset is None or scene.primary_asset.local_path is None
    row_id = conn.execute(
        """
        INSERT INTO render_manifest_scenes (
            render_manifest_id, scene_index, scene_id, segment_id,
            narration_asset_id, audio_path, audio_sha256,
            start_ms, end_ms, duration_ms,
            shot_type, camera_movement, visual_objective,
            caption_cue_ids_json, primary_asset_id, has_placeholder, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            render_manifest_id,
            scene.scene_index,
            scene.scene_id,
            scene.segment_id,
            scene.narration_asset_id,
            scene.audio_path,
            scene.audio_sha256,
            scene.start_ms,
            scene.end_ms,
            scene.duration_ms,
            scene.shot_type,
            scene.camera_movement,
            scene.visual_objective,
            json.dumps(scene.caption_cue_ids),
            primary_asset_id,
            1 if has_placeholder else 0,
            _now(),
        ),
    ).lastrowid
    row = conn.execute(
        "SELECT * FROM render_manifest_scenes WHERE id = ?", (row_id,)
    ).fetchone()
    assert row is not None
    return RenderManifestScene.from_row(row)


def list_render_manifest_scenes(
    conn: sqlite3.Connection, render_manifest_id: int
) -> list[RenderManifestScene]:
    rows = conn.execute(
        "SELECT * FROM render_manifest_scenes"
        " WHERE render_manifest_id = ? ORDER BY scene_index",
        (render_manifest_id,),
    ).fetchall()
    return [RenderManifestScene.from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# ResolvedAsset — create / read
# ---------------------------------------------------------------------------


def create_resolved_asset(
    conn: sqlite3.Connection,
    asset: ResolvedAsset,
    *,
    render_manifest_id: int | None = None,
) -> ResolvedAssetRecord:
    now = _now()
    row_id = conn.execute(
        """
        INSERT INTO resolved_assets (
            planned_asset_id, scene_id, segment_id, render_manifest_id,
            local_path, sha256, license_status, commercial_use_verified,
            warnings_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            asset.asset_id,
            asset.scene_id,
            asset.segment_id,
            render_manifest_id,
            asset.local_path,
            asset.local_sha256,
            _normalize_license(asset.license_status),
            1 if asset.commercial_safe else 0,
            "[]",
            now,
            now,
        ),
    ).lastrowid
    row = conn.execute(
        "SELECT * FROM resolved_assets WHERE id = ?", (row_id,)
    ).fetchone()
    assert row is not None
    return ResolvedAssetRecord.from_row(row)


def get_resolved_asset(
    conn: sqlite3.Connection, asset_id: int
) -> ResolvedAssetRecord | None:
    row = conn.execute(
        "SELECT * FROM resolved_assets WHERE id = ?", (asset_id,)
    ).fetchone()
    return ResolvedAssetRecord.from_row(row) if row else None


def list_resolved_assets_for_manifest(
    conn: sqlite3.Connection, render_manifest_id: int
) -> list[ResolvedAssetRecord]:
    rows = conn.execute(
        "SELECT * FROM resolved_assets"
        " WHERE render_manifest_id = ? ORDER BY scene_id, id",
        (render_manifest_id,),
    ).fetchall()
    return [ResolvedAssetRecord.from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_manifest(conn: sqlite3.Connection, manifest_id: int) -> RenderManifest:
    manifest = get_render_manifest(conn, manifest_id)
    if manifest is None:
        raise RenderManifestNotFoundError(f"Render manifest {manifest_id} not found.")
    return manifest


def _require_job(conn: sqlite3.Connection, job_id: int) -> RenderJob:
    job = get_render_job(conn, job_id)
    if job is None:
        raise RenderJobNotFoundError(f"Render job {job_id} not found.")
    return job


def _check_render_transition(current: str, target: str) -> None:
    if target not in RENDER_VALID_TRANSITIONS.get(current, frozenset()):
        raise IllegalRenderTransitionError(
            f"Cannot transition render manifest from '{current}' to '{target}'."
        )


def _check_job_transition(current: str, target: str) -> None:
    if target not in RENDER_JOB_VALID_TRANSITIONS.get(current, frozenset()):
        raise IllegalRenderJobTransitionError(
            f"Cannot transition render job from '{current}' to '{target}'."
        )
