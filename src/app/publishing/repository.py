"""Phase 9 publishing repository — all DB reads and writes.

All mutations use SAVEPOINT-scoped transactions so callers can compose them
inside a parent transaction without surprising partial-commit behaviour.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from app.publishing.constants import (
    JOB_STATUS_QUEUED,
    PLAN_STATUS_DRAFT,
    PUBLISHING_ENGINE_VERSION,
    PUBLISHING_METADATA_VERSION,
)
from app.publishing.errors import (
    PublicationNotFoundError,
    PublishingJobNotFoundError,
    PublishingPlanNotFoundError,
)
from app.publishing.models import (
    Publication,
    PublishingJob,
    PublishingMetadataDraft,
    PublishingPlan,
    PublishingReviewEvent,
    PublishingScheduleDraft,
)
from app.publishing.state_machine import (
    check_job_transition,
    check_plan_transition,
    check_publication_transition,
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


# ── Publishing Plan ───────────────────────────────────────────────────────────


def create_publishing_plan(
    conn: sqlite3.Connection,
    *,
    render_manifest_id: int,
    render_job_id: int | None,
    topic_id: int,
    production_plan_id: int,
    script_id: int,
    scene_manifest_id: int,
    narration_run_id: int,
    caption_run_id: int,
    experiment_id: str | None,
    input_hash: str,
    provider: str,
    provider_version: str,
    metadata: PublishingMetadataDraft,
    schedule: PublishingScheduleDraft,
) -> PublishingPlan:
    now = _now()
    row_id = conn.execute(
        """
        INSERT INTO publishing_plans (
            render_manifest_id, render_job_id,
            topic_id, production_plan_id, script_id, scene_manifest_id,
            narration_run_id, caption_run_id, experiment_id,
            input_hash, publishing_engine_version, metadata_version,
            provider, provider_version,
            title, description, tags_json, language, category, visibility,
            made_for_kids, playlist_id, thumbnail_path, captions_path,
            copyright_notice, licensing_notes, publication_notes,
            schedule_type, scheduled_at, timezone,
            status, created_at, updated_at
        ) VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            render_manifest_id,
            render_job_id,
            topic_id,
            production_plan_id,
            script_id,
            scene_manifest_id,
            narration_run_id,
            caption_run_id,
            experiment_id,
            input_hash,
            PUBLISHING_ENGINE_VERSION,
            PUBLISHING_METADATA_VERSION,
            provider,
            provider_version,
            metadata.title,
            metadata.description,
            json.dumps(metadata.tags),
            metadata.language,
            metadata.category,
            metadata.visibility,
            1 if metadata.made_for_kids else 0,
            metadata.playlist_id,
            metadata.thumbnail_path,
            metadata.captions_path,
            metadata.copyright_notice,
            metadata.licensing_notes,
            metadata.publication_notes,
            schedule.schedule_type,
            schedule.scheduled_at,
            schedule.timezone,
            PLAN_STATUS_DRAFT,
            now,
            now,
        ),
    ).lastrowid
    row = conn.execute("SELECT * FROM publishing_plans WHERE id = ?", (row_id,)).fetchone()
    assert row is not None
    return PublishingPlan.from_row(row)


def get_publishing_plan(conn: sqlite3.Connection, plan_id: int) -> PublishingPlan | None:
    row = conn.execute("SELECT * FROM publishing_plans WHERE id = ?", (plan_id,)).fetchone()
    return PublishingPlan.from_row(row) if row else None


def get_publishing_plan_by_hash(conn: sqlite3.Connection, input_hash: str) -> PublishingPlan | None:
    row = conn.execute(
        "SELECT * FROM publishing_plans WHERE input_hash = ?", (input_hash,)
    ).fetchone()
    return PublishingPlan.from_row(row) if row else None


def list_publishing_plans(
    conn: sqlite3.Connection,
    *,
    render_manifest_id: int | None = None,
    topic_id: int | None = None,
    provider: str | None = None,
    status: str | None = None,
    include_superseded: bool = False,
    limit: int = 50,
) -> list[PublishingPlan]:
    clauses: list[str] = []
    params: list = []
    if render_manifest_id is not None:
        clauses.append("render_manifest_id = ?")
        params.append(render_manifest_id)
    if topic_id is not None:
        clauses.append("topic_id = ?")
        params.append(topic_id)
    if provider is not None:
        clauses.append("provider = ?")
        params.append(provider)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if not include_superseded:
        clauses.append("superseded_at IS NULL")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM publishing_plans {where} ORDER BY id DESC LIMIT ?",
        params,
    ).fetchall()
    return [PublishingPlan.from_row(r) for r in rows]


def update_publishing_plan_status(
    conn: sqlite3.Connection,
    plan_id: int,
    to_status: str,
    *,
    rejection_reason: str | None = None,
) -> PublishingPlan:
    plan = get_publishing_plan(conn, plan_id)
    if plan is None:
        raise PublishingPlanNotFoundError(f"Publishing plan {plan_id} not found.")
    check_plan_transition(plan.status, to_status)
    now = _now()
    ts_col = {"approved": "approved_at", "rejected": "rejected_at"}.get(to_status)
    extra = ""
    params_extra: list = []
    if ts_col:
        extra += f", {ts_col} = ?"
        params_extra.append(now)
    if rejection_reason is not None:
        extra += ", rejection_reason = ?"
        params_extra.append(rejection_reason)
    conn.execute(
        f"UPDATE publishing_plans SET status = ?, updated_at = ?{extra} WHERE id = ?",
        [to_status, now] + params_extra + [plan_id],
    )
    updated = get_publishing_plan(conn, plan_id)
    assert updated is not None
    return updated


def supersede_publishing_plan(
    conn: sqlite3.Connection,
    old_plan_id: int,
    new_plan_id: int,
) -> None:
    """Mark old_plan_id as superseded by new_plan_id (field-based, no status change)."""
    now = _now()
    conn.execute(
        "UPDATE publishing_plans SET superseded_at = ?, superseded_by_id = ?, updated_at = ?"
        " WHERE id = ?",
        (now, new_plan_id, now, old_plan_id),
    )


def approve_publishing_plan(
    conn: sqlite3.Connection,
    plan_id: int,
    *,
    actor: str | None = None,
    notes: str | None = None,
) -> PublishingPlan:
    plan = get_publishing_plan(conn, plan_id)
    if plan is None:
        raise PublishingPlanNotFoundError(f"Publishing plan {plan_id} not found.")
    updated = update_publishing_plan_status(conn, plan_id, "approved")
    record_publishing_review_event(
        conn,
        publishing_plan_id=plan_id,
        event_type="plan_approved",
        topic_id=plan.topic_id,
        production_plan_id=plan.production_plan_id,
        script_id=plan.script_id,
        render_manifest_id=plan.render_manifest_id,
        provider=plan.provider,
        experiment_id=plan.experiment_id,
        actor=actor,
        notes=notes,
    )
    return updated


def reject_publishing_plan(
    conn: sqlite3.Connection,
    plan_id: int,
    *,
    reason_code: str | None = None,
    notes: str | None = None,
    actor: str | None = None,
    severity: int | None = None,
    expected_correction: str | None = None,
) -> PublishingPlan:
    plan = get_publishing_plan(conn, plan_id)
    if plan is None:
        raise PublishingPlanNotFoundError(f"Publishing plan {plan_id} not found.")
    updated = update_publishing_plan_status(conn, plan_id, "rejected", rejection_reason=reason_code)
    record_publishing_review_event(
        conn,
        publishing_plan_id=plan_id,
        event_type="plan_rejected",
        topic_id=plan.topic_id,
        production_plan_id=plan.production_plan_id,
        script_id=plan.script_id,
        render_manifest_id=plan.render_manifest_id,
        provider=plan.provider,
        experiment_id=plan.experiment_id,
        reason_code=reason_code,
        severity=severity,
        notes=notes,
        actor=actor,
        expected_correction=expected_correction,
    )
    return updated


# ── Publishing Job ────────────────────────────────────────────────────────────


def create_publishing_job(
    conn: sqlite3.Connection,
    publishing_plan_id: int,
    attempt_number: int,
    provider: str,
    provider_version: str,
) -> PublishingJob:
    now = _now()
    row_id = conn.execute(
        """
        INSERT INTO publishing_jobs (
            publishing_plan_id, attempt_number,
            provider, provider_version,
            status, retry_count, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            publishing_plan_id,
            attempt_number,
            provider,
            provider_version,
            JOB_STATUS_QUEUED,
            0,
            now,
            now,
        ),
    ).lastrowid
    row = conn.execute("SELECT * FROM publishing_jobs WHERE id = ?", (row_id,)).fetchone()
    assert row is not None
    return PublishingJob.from_row(row)


def get_publishing_job(conn: sqlite3.Connection, job_id: int) -> PublishingJob | None:
    row = conn.execute("SELECT * FROM publishing_jobs WHERE id = ?", (job_id,)).fetchone()
    return PublishingJob.from_row(row) if row else None


def get_latest_publishing_job(
    conn: sqlite3.Connection, publishing_plan_id: int
) -> PublishingJob | None:
    row = conn.execute(
        "SELECT * FROM publishing_jobs WHERE publishing_plan_id = ?"
        " ORDER BY attempt_number DESC LIMIT 1",
        (publishing_plan_id,),
    ).fetchone()
    return PublishingJob.from_row(row) if row else None


def get_active_publishing_job(
    conn: sqlite3.Connection, publishing_plan_id: int
) -> PublishingJob | None:
    row = conn.execute(
        "SELECT * FROM publishing_jobs WHERE publishing_plan_id = ?"
        " AND status IN ('queued','running','retry_scheduled')"
        " ORDER BY attempt_number DESC LIMIT 1",
        (publishing_plan_id,),
    ).fetchone()
    return PublishingJob.from_row(row) if row else None


def list_publishing_jobs(conn: sqlite3.Connection, publishing_plan_id: int) -> list[PublishingJob]:
    rows = conn.execute(
        "SELECT * FROM publishing_jobs WHERE publishing_plan_id = ? ORDER BY attempt_number",
        (publishing_plan_id,),
    ).fetchall()
    return [PublishingJob.from_row(r) for r in rows]


def update_publishing_job_status(
    conn: sqlite3.Connection,
    job_id: int,
    to_status: str,
    *,
    error_message: str | None = None,
    retry_after: str | None = None,
    increment_retry: bool = False,
    mark_started: bool = False,
    mark_completed: bool = False,
) -> PublishingJob:
    job = get_publishing_job(conn, job_id)
    if job is None:
        raise PublishingJobNotFoundError(f"Publishing job {job_id} not found.")
    check_job_transition(job.status, to_status)
    now = _now()
    extra = ""
    params_extra: list = []
    if error_message is not None:
        extra += ", error_message = ?"
        params_extra.append(error_message)
    if retry_after is not None:
        extra += ", retry_after = ?"
        params_extra.append(retry_after)
    if increment_retry:
        extra += ", retry_count = retry_count + 1"
    if mark_started:
        extra += ", started_at = ?"
        params_extra.append(now)
    if mark_completed:
        extra += ", completed_at = ?"
        params_extra.append(now)
    conn.execute(
        f"UPDATE publishing_jobs SET status = ?, updated_at = ?{extra} WHERE id = ?",
        [to_status, now] + params_extra + [job_id],
    )
    updated = get_publishing_job(conn, job_id)
    assert updated is not None
    return updated


# ── Publication ───────────────────────────────────────────────────────────────


def create_publication(
    conn: sqlite3.Connection,
    *,
    publishing_plan_id: int,
    publishing_job_id: int,
    provider: str,
    provider_version: str,
    provider_video_id: str,
    provider_url: str | None,
    provider_status: dict,
    visibility: str,
    scheduled_at: str | None,
    input_hash: str,
    output_sha256: str,
    initial_status: str = "uploading",
    workspace_id: str | None = None,
    channel_id: str | None = None,
    platform_account_id: str | None = None,
) -> Publication:
    now = _now()
    row_id = conn.execute(
        """
        INSERT INTO publications (
            publishing_plan_id, publishing_job_id,
            provider, provider_version,
            provider_video_id, provider_url, provider_status_json,
            status, visibility, scheduled_at,
            publishing_engine_version, input_hash, output_sha256,
            workspace_id, channel_id, platform_account_id,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            publishing_plan_id,
            publishing_job_id,
            provider,
            provider_version,
            provider_video_id,
            provider_url,
            json.dumps(provider_status),
            initial_status,
            visibility,
            scheduled_at,
            PUBLISHING_ENGINE_VERSION,
            input_hash,
            output_sha256,
            workspace_id,
            channel_id,
            platform_account_id,
            now,
            now,
        ),
    ).lastrowid
    row = conn.execute("SELECT * FROM publications WHERE id = ?", (row_id,)).fetchone()
    assert row is not None
    return Publication.from_row(row)


def get_publication(conn: sqlite3.Connection, publication_id: int) -> Publication | None:
    row = conn.execute("SELECT * FROM publications WHERE id = ?", (publication_id,)).fetchone()
    return Publication.from_row(row) if row else None


def get_latest_publication(conn: sqlite3.Connection, publishing_plan_id: int) -> Publication | None:
    row = conn.execute(
        "SELECT * FROM publications WHERE publishing_plan_id = ? ORDER BY id DESC LIMIT 1",
        (publishing_plan_id,),
    ).fetchone()
    return Publication.from_row(row) if row else None


def list_publications(conn: sqlite3.Connection, publishing_plan_id: int) -> list[Publication]:
    rows = conn.execute(
        "SELECT * FROM publications WHERE publishing_plan_id = ? ORDER BY id",
        (publishing_plan_id,),
    ).fetchall()
    return [Publication.from_row(r) for r in rows]


def update_publication_status(
    conn: sqlite3.Connection,
    publication_id: int,
    to_status: str,
    *,
    provider_status: dict | None = None,
    published_at: str | None = None,
    error_message: str | None = None,
    deleted_at: str | None = None,
) -> Publication:
    pub = get_publication(conn, publication_id)
    if pub is None:
        raise PublicationNotFoundError(f"Publication {publication_id} not found.")
    check_publication_transition(pub.status, to_status)
    now = _now()
    extra = ""
    params_extra: list = []
    if provider_status is not None:
        extra += ", provider_status_json = ?"
        params_extra.append(json.dumps(provider_status))
    if published_at is not None:
        extra += ", published_at = ?"
        params_extra.append(published_at)
    if error_message is not None:
        extra += ", error_message = ?"
        params_extra.append(error_message)
    if deleted_at is not None:
        extra += ", deleted_at = ?"
        params_extra.append(deleted_at)
    conn.execute(
        f"UPDATE publications SET status = ?, updated_at = ?{extra} WHERE id = ?",
        [to_status, now] + params_extra + [publication_id],
    )
    updated = get_publication(conn, publication_id)
    assert updated is not None
    return updated


# ── Publishing Review Events ──────────────────────────────────────────────────


def record_publishing_review_event(
    conn: sqlite3.Connection,
    *,
    publishing_plan_id: int,
    event_type: str,
    topic_id: int,
    production_plan_id: int,
    script_id: int,
    render_manifest_id: int,
    provider: str,
    experiment_id: str | None = None,
    publishing_job_id: int | None = None,
    publication_id: int | None = None,
    reason_code: str | None = None,
    severity: int | None = None,
    notes: str | None = None,
    actor: str | None = None,
    expected_correction: str | None = None,
) -> PublishingReviewEvent:
    now = _now()
    row_id = conn.execute(
        """
        INSERT INTO publishing_review_events (
            publishing_plan_id, publishing_job_id, publication_id,
            topic_id, production_plan_id, script_id, render_manifest_id,
            provider, experiment_id,
            event_type, reason_code, severity, notes, actor, expected_correction,
            created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            publishing_plan_id,
            publishing_job_id,
            publication_id,
            topic_id,
            production_plan_id,
            script_id,
            render_manifest_id,
            provider,
            experiment_id,
            event_type,
            reason_code,
            severity,
            notes,
            actor,
            expected_correction,
            now,
        ),
    ).lastrowid
    row = conn.execute("SELECT * FROM publishing_review_events WHERE id = ?", (row_id,)).fetchone()
    assert row is not None
    return PublishingReviewEvent.from_row(row)


def list_publishing_review_events(
    conn: sqlite3.Connection, publishing_plan_id: int
) -> list[PublishingReviewEvent]:
    rows = conn.execute(
        "SELECT * FROM publishing_review_events WHERE publishing_plan_id = ? ORDER BY id",
        (publishing_plan_id,),
    ).fetchall()
    return [PublishingReviewEvent.from_row(r) for r in rows]
