"""Phase 9 publishing orchestrator.

Coordinates approved-render resolution, plan lifecycle, job execution, and
provider invocation.  No provider-specific logic lives here.
"""

from __future__ import annotations

import sqlite3

from app.media.repository import get_approved_render, get_render_manifest
from app.publishing.constants import (
    EVENT_CANCELLATION_REQUESTED,
    EVENT_JOB_COMPLETED,
    EVENT_JOB_FAILED,
    EVENT_JOB_QUEUED,
    EVENT_JOB_STARTED,
    EVENT_PLAN_PREPARED,
    EVENT_RETRY_REQUESTED,
    EVENT_SCHEDULE_CHANGED,
    EVENT_SUPERSEDED,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    MAX_RETRY_ATTEMPTS,
    PUB_STATUS_FAILED,
    PUB_STATUS_PUBLISHED,
    PUB_STATUS_SCHEDULED,
    PUB_STATUS_UPLOADED,
)
from app.publishing.errors import (
    ActiveJobExistsError,
    JobNotCancellableError,
    JobNotRetryableError,
    MaxRetriesExceededError,
    NoApprovedRenderForManifestError,
    PublishingPlanNotFoundError,
    RenderManifestNotApprovedError,
)
from app.publishing.hashing import PublishingHashInput, compute_publishing_input_hash
from app.publishing.models import (
    Publication,
    PublishingJob,
    PublishingMetadataDraft,
    PublishingPlan,
    PublishingScheduleDraft,
)
from app.publishing.protocol import PublishingProvider, UploadPackage
from app.publishing.repository import (
    create_publication,
    create_publishing_job,
    create_publishing_plan,
    get_active_publishing_job,
    get_latest_publishing_job,
    get_publishing_plan,
    get_publishing_plan_by_hash,
    list_publishing_jobs,
    record_publishing_review_event,
    supersede_publishing_plan,
    update_publication_status,
    update_publishing_job_status,
)
from app.publishing.scheduler import validate_schedule
from app.publishing.validation import (
    validate_approved_render_for_publishing,
    validate_publishing_metadata,
)

# ── Plan preparation ──────────────────────────────────────────────────────────

def prepare_publishing_plan(
    conn: sqlite3.Connection,
    render_manifest_id: int,
    metadata: PublishingMetadataDraft,
    schedule: PublishingScheduleDraft,
    *,
    provider: PublishingProvider,
    supersede_existing: bool = False,
) -> tuple[PublishingPlan, bool]:
    """Validate inputs and create (or retrieve) a publishing plan.

    Returns (plan, created).  If created=False the plan was already persisted
    and is returned unchanged (idempotent by input_hash).
    """
    # 1. Resolve render manifest
    manifest = get_render_manifest(conn, render_manifest_id)
    if manifest is None:
        raise PublishingPlanNotFoundError(
            f"Render manifest {render_manifest_id} not found."
        )
    if manifest.status != "approved":
        raise RenderManifestNotApprovedError(
            f"Render manifest {render_manifest_id} status is {manifest.status!r}, "
            "must be 'approved'."
        )

    # 2. Resolve approved render
    approved_render = get_approved_render(conn, manifest.scene_manifest_id)
    if approved_render is None:
        raise NoApprovedRenderForManifestError(
            f"No approved render found for manifest {render_manifest_id}."
        )

    # 3. Validate
    validate_approved_render_for_publishing(approved_render)
    validate_publishing_metadata(metadata)
    validate_schedule(schedule)

    # 4. Compute idempotency hash
    hash_input = PublishingHashInput(
        render_manifest_id=render_manifest_id,
        output_sha256=approved_render.output_sha256,
        provider=provider.provider_name,
        provider_version=provider.provider_version,
        title=metadata.title,
        description=metadata.description,
        tags=tuple(metadata.tags),
        language=metadata.language,
        visibility=metadata.visibility,
        category=metadata.category,
        made_for_kids=metadata.made_for_kids,
        captions_path=metadata.captions_path,
        playlist_id=metadata.playlist_id,
        copyright_notice=metadata.copyright_notice,
        licensing_notes=metadata.licensing_notes,
        schedule_type=schedule.schedule_type,
        scheduled_at=schedule.scheduled_at,
        timezone=schedule.timezone,
        experiment_id=approved_render.experiment_id,
    )
    input_hash = compute_publishing_input_hash(hash_input)

    # 5. Idempotency check
    existing = get_publishing_plan_by_hash(conn, input_hash)
    if existing is not None:
        return existing, False

    # 6. Create plan
    plan = create_publishing_plan(
        conn,
        render_manifest_id=render_manifest_id,
        render_job_id=approved_render.render_job_id,
        topic_id=approved_render.topic_id,
        production_plan_id=approved_render.plan_id,
        script_id=approved_render.script_id,
        scene_manifest_id=approved_render.scene_manifest_id,
        narration_run_id=approved_render.narration_run_id,
        caption_run_id=approved_render.caption_run_id,
        experiment_id=approved_render.experiment_id,
        input_hash=input_hash,
        provider=provider.provider_name,
        provider_version=provider.provider_version,
        metadata=metadata,
        schedule=schedule,
    )

    # 7. Supersede prior plan for same render if requested
    if supersede_existing:
        _supersede_prior_plans(conn, render_manifest_id, new_plan_id=plan.id)

    # 8. Record event
    record_publishing_review_event(
        conn,
        publishing_plan_id=plan.id,
        event_type=EVENT_PLAN_PREPARED,
        topic_id=plan.topic_id,
        production_plan_id=plan.production_plan_id,
        script_id=plan.script_id,
        render_manifest_id=plan.render_manifest_id,
        provider=plan.provider,
        experiment_id=plan.experiment_id,
    )

    return plan, True


def _supersede_prior_plans(
    conn: sqlite3.Connection,
    render_manifest_id: int,
    *,
    new_plan_id: int,
) -> None:
    from app.publishing.repository import list_publishing_plans
    priors = list_publishing_plans(
        conn, render_manifest_id=render_manifest_id, include_superseded=False
    )
    for prior in priors:
        if prior.id == new_plan_id:
            continue
        supersede_publishing_plan(conn, prior.id, new_plan_id)
        record_publishing_review_event(
            conn,
            publishing_plan_id=prior.id,
            event_type=EVENT_SUPERSEDED,
            topic_id=prior.topic_id,
            production_plan_id=prior.production_plan_id,
            script_id=prior.script_id,
            render_manifest_id=prior.render_manifest_id,
            provider=prior.provider,
            experiment_id=prior.experiment_id,
            notes=f"Superseded by plan {new_plan_id}.",
        )


# ── Job execution ─────────────────────────────────────────────────────────────

def start_publishing_job(
    conn: sqlite3.Connection,
    plan_id: int,
    provider: PublishingProvider,
    *,
    output_path: str,
    output_sha256: str,
    dry_run: bool = False,
) -> tuple[PublishingJob, Publication | None]:
    """Create and execute a publishing job for the given plan.

    Returns (job, publication).  publication is None in dry_run mode.
    Raises ActiveJobExistsError if the plan has an active job.
    """
    plan = get_publishing_plan(conn, plan_id)
    if plan is None:
        raise PublishingPlanNotFoundError(f"Publishing plan {plan_id} not found.")

    # Guard: no concurrent queued or running jobs (retry_scheduled is transitional, not blocking)
    active = get_active_publishing_job(conn, plan_id)
    if active is not None and active.status in {JOB_STATUS_QUEUED, JOB_STATUS_RUNNING}:
        raise ActiveJobExistsError(
            f"Plan {plan_id} already has an active job id={active.id} "
            f"(status={active.status!r}). Cancel or wait for it to complete."
        )

    # Guard: max attempts
    all_jobs = list_publishing_jobs(conn, plan_id)
    if len(all_jobs) >= MAX_RETRY_ATTEMPTS:
        raise MaxRetriesExceededError(
            f"Plan {plan_id} has reached the maximum of {MAX_RETRY_ATTEMPTS} attempts."
        )

    attempt_number = len(all_jobs) + 1

    if dry_run:
        return _dry_run_job(conn, plan, provider, attempt_number), None

    # Create job record
    job = create_publishing_job(
        conn, plan_id, attempt_number, provider.provider_name, provider.provider_version
    )
    record_publishing_review_event(
        conn,
        publishing_plan_id=plan_id,
        publishing_job_id=job.id,
        event_type=EVENT_JOB_QUEUED,
        topic_id=plan.topic_id,
        production_plan_id=plan.production_plan_id,
        script_id=plan.script_id,
        render_manifest_id=plan.render_manifest_id,
        provider=plan.provider,
        experiment_id=plan.experiment_id,
    )

    # Move to running
    job = update_publishing_job_status(
        conn, job.id, JOB_STATUS_RUNNING, mark_started=True
    )
    record_publishing_review_event(
        conn,
        publishing_plan_id=plan_id,
        publishing_job_id=job.id,
        event_type=EVENT_JOB_STARTED,
        topic_id=plan.topic_id,
        production_plan_id=plan.production_plan_id,
        script_id=plan.script_id,
        render_manifest_id=plan.render_manifest_id,
        provider=plan.provider,
        experiment_id=plan.experiment_id,
    )

    # Publication is not created until upload succeeds and a real provider_video_id
    # is returned.  A Publication row must never be inserted as a placeholder.
    publication: Publication | None = None

    try:
        package = UploadPackage(
            plan_id=plan_id,
            file_path=output_path,
            file_sha256=output_sha256,
            title=plan.title,
            description=plan.description,
            tags=plan.tags,
            language=plan.language,
            category=plan.category,
            visibility=plan.visibility,
            made_for_kids=bool(plan.made_for_kids),
            scheduled_at=plan.scheduled_at,
            captions_path=plan.captions_path,
            playlist_id=plan.playlist_id,
        )
        package = provider.prepare_package(package)

        # Upload — Publication is created only once a real provider_video_id is known
        upload_result = provider.upload(package)
        publication = create_publication(
            conn,
            publishing_plan_id=plan_id,
            publishing_job_id=job.id,
            provider=provider.provider_name,
            provider_version=provider.provider_version,
            provider_video_id=upload_result.provider_video_id,
            provider_url=upload_result.provider_url,
            provider_status=upload_result.provider_response,
            visibility=plan.visibility,
            scheduled_at=plan.scheduled_at,
            input_hash=plan.input_hash,
            output_sha256=output_sha256,
            initial_status=PUB_STATUS_UPLOADED,
        )

        # Publish (set visibility / schedule)
        publish_result = provider.publish(
            upload_result,
            scheduled_at=plan.scheduled_at,
            visibility=plan.visibility,
        )

        pub_to_status = (
            PUB_STATUS_SCHEDULED
            if publish_result.status == "scheduled"
            else PUB_STATUS_PUBLISHED
        )
        publication = update_publication_status(
            conn,
            publication.id,
            pub_to_status,
            provider_status=publish_result.provider_response,
            published_at=publish_result.published_at,
        )

        # Complete job
        job = update_publishing_job_status(
            conn, job.id, JOB_STATUS_COMPLETED, mark_completed=True
        )
        record_publishing_review_event(
            conn,
            publishing_plan_id=plan_id,
            publishing_job_id=job.id,
            publication_id=publication.id,
            event_type=EVENT_JOB_COMPLETED,
            topic_id=plan.topic_id,
            production_plan_id=plan.production_plan_id,
            script_id=plan.script_id,
            render_manifest_id=plan.render_manifest_id,
            provider=plan.provider,
            experiment_id=plan.experiment_id,
        )

    except Exception as exc:
        _fail_job(conn, plan, job, publication, str(exc))
        raise

    return job, publication


def _dry_run_job(
    conn: sqlite3.Connection,
    plan: PublishingPlan,
    provider: PublishingProvider,
    attempt_number: int,
) -> PublishingJob:
    """Return a non-persisted job object for dry-run inspection."""
    from datetime import UTC
    from datetime import datetime as _dt
    now = _dt.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")

    row: dict = {
        "id": -1,
        "publishing_plan_id": plan.id,
        "attempt_number": attempt_number,
        "provider": provider.provider_name,
        "provider_version": provider.provider_version,
        "status": "queued",
        "error_message": None,
        "retry_count": 0,
        "retry_after": None,
        "started_at": None,
        "completed_at": None,
        "created_at": now,
        "updated_at": now,
    }
    return PublishingJob(**row)


def _fail_job(
    conn: sqlite3.Connection,
    plan: PublishingPlan,
    job: PublishingJob,
    publication: Publication | None,
    error_msg: str,
) -> None:
    if publication is not None:
        try:
            update_publication_status(
                conn, publication.id, PUB_STATUS_FAILED, error_message=error_msg
            )
        except Exception:
            pass
    try:
        update_publishing_job_status(
            conn, job.id, JOB_STATUS_FAILED, error_message=error_msg, mark_completed=True
        )
    except Exception:
        pass
    record_publishing_review_event(
        conn,
        publishing_plan_id=plan.id,
        publishing_job_id=job.id,
        publication_id=publication.id if publication is not None else None,
        event_type=EVENT_JOB_FAILED,
        topic_id=plan.topic_id,
        production_plan_id=plan.production_plan_id,
        script_id=plan.script_id,
        render_manifest_id=plan.render_manifest_id,
        provider=plan.provider,
        experiment_id=plan.experiment_id,
        notes=error_msg,
    )


# ── Retry ─────────────────────────────────────────────────────────────────────

def retry_publishing_job(
    conn: sqlite3.Connection,
    plan_id: int,
    provider: PublishingProvider,
    *,
    output_path: str,
    output_sha256: str,
) -> tuple[PublishingJob, Publication | None]:
    """Schedule and immediately run a retry attempt for a failed job."""
    latest = get_latest_publishing_job(conn, plan_id)
    if latest is None or latest.status != JOB_STATUS_FAILED:
        raise JobNotRetryableError(
            f"Plan {plan_id} has no failed job to retry "
            f"(latest status: {latest.status if latest else 'none'!r})."
        )

    plan = get_publishing_plan(conn, plan_id)
    assert plan is not None
    record_publishing_review_event(
        conn,
        publishing_plan_id=plan_id,
        publishing_job_id=latest.id,
        event_type=EVENT_RETRY_REQUESTED,
        topic_id=plan.topic_id,
        production_plan_id=plan.production_plan_id,
        script_id=plan.script_id,
        render_manifest_id=plan.render_manifest_id,
        provider=plan.provider,
        experiment_id=plan.experiment_id,
    )

    return start_publishing_job(
        conn, plan_id, provider, output_path=output_path, output_sha256=output_sha256
    )


# ── Cancel ────────────────────────────────────────────────────────────────────

def cancel_publishing_job(
    conn: sqlite3.Connection,
    plan_id: int,
    *,
    actor: str | None = None,
    notes: str | None = None,
) -> PublishingJob:
    """Cancel the active queued or running job for the plan."""
    active = get_active_publishing_job(conn, plan_id)
    if active is None:
        raise JobNotCancellableError(
            f"Plan {plan_id} has no active job to cancel."
        )
    if active.status not in {JOB_STATUS_QUEUED, JOB_STATUS_RUNNING}:
        raise JobNotCancellableError(
            f"Job {active.id} is in status {active.status!r} and cannot be cancelled."
        )

    job = update_publishing_job_status(
        conn, active.id, JOB_STATUS_CANCELLED, mark_completed=True
    )

    plan = get_publishing_plan(conn, plan_id)
    assert plan is not None
    record_publishing_review_event(
        conn,
        publishing_plan_id=plan_id,
        publishing_job_id=job.id,
        event_type=EVENT_CANCELLATION_REQUESTED,
        topic_id=plan.topic_id,
        production_plan_id=plan.production_plan_id,
        script_id=plan.script_id,
        render_manifest_id=plan.render_manifest_id,
        provider=plan.provider,
        experiment_id=plan.experiment_id,
        actor=actor,
        notes=notes,
    )
    return job


# ── Schedule update ───────────────────────────────────────────────────────────

def update_plan_schedule(
    conn: sqlite3.Connection,
    plan_id: int,
    new_schedule: PublishingScheduleDraft,
    *,
    actor: str | None = None,
) -> PublishingPlan:
    """Update a draft plan's scheduling intent (no active job must exist)."""
    plan = get_publishing_plan(conn, plan_id)
    if plan is None:
        raise PublishingPlanNotFoundError(f"Publishing plan {plan_id} not found.")
    if plan.status != "draft":
        from app.publishing.errors import IllegalPublishingPlanTransitionError
        raise IllegalPublishingPlanTransitionError(
            f"Cannot update schedule: plan {plan_id} is in status {plan.status!r}."
        )

    validate_schedule(new_schedule)

    now = _now()
    conn.execute(
        "UPDATE publishing_plans"
        " SET schedule_type = ?, scheduled_at = ?, timezone = ?, updated_at = ?"
        " WHERE id = ?",
        (
            new_schedule.schedule_type,
            new_schedule.scheduled_at,
            new_schedule.timezone,
            now,
            plan_id,
        ),
    )

    updated = get_publishing_plan(conn, plan_id)
    assert updated is not None

    record_publishing_review_event(
        conn,
        publishing_plan_id=plan_id,
        event_type=EVENT_SCHEDULE_CHANGED,
        topic_id=plan.topic_id,
        production_plan_id=plan.production_plan_id,
        script_id=plan.script_id,
        render_manifest_id=plan.render_manifest_id,
        provider=plan.provider,
        experiment_id=plan.experiment_id,
        actor=actor,
        notes=(
            f"schedule_type={new_schedule.schedule_type!r}"
            f" scheduled_at={new_schedule.scheduled_at!r}"
        ),
    )

    return updated


def _now() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
