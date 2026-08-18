"""Phase 9 — ace publish CLI.

Commands:
    ace publish prepare  <manifest_id>  — validate + create publishing plan
    ace publish start    <plan_id>      — upload and publish
    ace publish schedule <plan_id>      — update scheduled publication time
    ace publish list                    — list plans
    ace publish show     <plan_id>      — show plan + jobs + publication
    ace publish approve  <plan_id>      — approve a plan/publication
    ace publish reject   <plan_id>      — reject a plan/publication
    ace publish retry    <plan_id>      — retry the last failed job
    ace publish cancel   <plan_id>      — cancel the active queued/running job
    ace publish events   <plan_id>      — list review events
    ace publish doctor                  — check provider health
"""

from __future__ import annotations

from typing import Annotated

import typer

publish_app = typer.Typer(
    name="publish",
    help="Publish approved renders to video platforms.",
    no_args_is_help=True,
)


def _get_db():
    from app.core.config import get_config
    from app.core.database import open_db
    from app.core.logging import configure_logging

    cfg = get_config()
    configure_logging(cfg.log_level)
    return open_db(cfg.db_path)


def _default_provider():
    from app.publishing.providers.fake import FakePublishingProvider

    return FakePublishingProvider()


# ── prepare ───────────────────────────────────────────────────────────────────


@publish_app.command("prepare")
def publish_prepare(
    render_manifest_id: Annotated[int, typer.Argument(help="Render manifest ID.")],
    title: Annotated[str, typer.Option("--title", "-t", help="Video title.")],
    description: Annotated[str, typer.Option("--description", "-d", help="Description.")] = "",
    tags: Annotated[str, typer.Option("--tags", help="Comma-separated tags.")] = "",
    visibility: Annotated[
        str, typer.Option("--visibility", "-v", help="private | unlisted | public.")
    ] = "private",
    language: Annotated[str, typer.Option("--language", help="BCP-47 language tag.")] = "en",
    category: Annotated[
        str | None, typer.Option("--category", help="Platform category ID.")
    ] = None,
    made_for_kids: Annotated[
        bool, typer.Option("--made-for-kids/--not-for-kids", help="Made-for-kids flag.")
    ] = False,
    schedule_at: Annotated[
        str | None,
        typer.Option("--schedule-at", help="ISO 8601 UTC datetime for scheduled publishing."),
    ] = None,
    timezone: Annotated[
        str | None,
        typer.Option("--timezone", help="IANA timezone name for schedule reference."),
    ] = None,
    supersede: Annotated[
        bool,
        typer.Option("--supersede", help="Mark prior plans for this manifest as superseded."),
    ] = False,
    provider_name: Annotated[
        str, typer.Option("--provider", help="Provider name (default: fake).")
    ] = "fake",
) -> None:
    """Validate an approved render and create a publishing plan."""
    from app.publishing.errors import (
        NoApprovedRenderForManifestError,
        PublishingPlanNotFoundError,
        PublishingValidationError,
        RenderManifestNotApprovedError,
    )
    from app.publishing.metadata import build_metadata_draft
    from app.publishing.models import PublishingScheduleDraft
    from app.publishing.orchestrator import prepare_publishing_plan
    from app.publishing.providers.fake import FakePublishingProvider
    from app.publishing.providers.youtube import YouTubePublishingProvider

    conn = _get_db()

    if provider_name == "youtube":
        provider = YouTubePublishingProvider()
    else:
        provider = FakePublishingProvider()

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    schedule_type = "scheduled" if schedule_at else "immediate"

    from app.media.repository import get_approved_render, get_render_manifest

    manifest = get_render_manifest(conn, render_manifest_id)
    if manifest is None:
        typer.echo(f"Error: Render manifest {render_manifest_id} not found.", err=True)
        raise typer.Exit(1)

    approved_render = get_approved_render(conn, manifest.scene_manifest_id)
    if approved_render is None:
        typer.echo(f"Error: No approved render for manifest {render_manifest_id}.", err=True)
        raise typer.Exit(1)

    metadata = build_metadata_draft(
        approved_render,
        title=title,
        description=description,
        tags=tag_list,
        language=language,
        category=category,
        visibility=visibility,
        made_for_kids=made_for_kids,
    )
    schedule = PublishingScheduleDraft(
        schedule_type=schedule_type,
        scheduled_at=schedule_at,
        timezone=timezone,
    )

    try:
        plan, created = prepare_publishing_plan(
            conn,
            render_manifest_id,
            metadata,
            schedule,
            provider=provider,
            supersede_existing=supersede,
        )
        conn.commit()
    except (
        PublishingPlanNotFoundError,
        RenderManifestNotApprovedError,
        NoApprovedRenderForManifestError,
        PublishingValidationError,
    ) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    if created:
        typer.echo(f"Created publishing plan id={plan.id} for manifest {render_manifest_id}.")
    else:
        typer.echo(f"Idempotent: existing publishing plan id={plan.id} returned (same input_hash).")
    typer.echo(
        f"  provider={plan.provider}  visibility={plan.visibility}"
        f"  schedule={plan.schedule_type}  status={plan.status}"
    )


# ── start ─────────────────────────────────────────────────────────────────────


@publish_app.command("start")
def publish_start(
    plan_id: Annotated[int, typer.Argument(help="Publishing plan ID.")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Validate without uploading.")] = False,
    execute: Annotated[
        bool,
        typer.Option(
            "--execute", help="Confirm live publishing intent (required for non-fake providers)."
        ),  # noqa: E501
    ] = False,
    provider_name: Annotated[
        str, typer.Option("--provider", help="Provider name (default: fake).")
    ] = "fake",
    account_id: Annotated[
        str | None,
        typer.Option("--account-id", help="Platform account ID (required for --provider youtube)."),
    ] = None,
    workspace_id: Annotated[
        str | None,
        typer.Option("--workspace-id", help="Workspace ID (required for --provider youtube)."),
    ] = None,
    channel_id: Annotated[
        str | None,
        typer.Option("--channel-id", help="Channel ID (required for --provider youtube)."),
    ] = None,
) -> None:
    """Upload and publish the video for a publishing plan."""
    from app.publishing.errors import (
        ActiveJobExistsError,
        MaxRetriesExceededError,
        PublicationOwnershipError,
        PublishingPlanNotFoundError,
        PublishingProviderError,
    )
    from app.publishing.orchestrator import start_publishing_job
    from app.publishing.providers.fake import FakePublishingProvider
    from app.publishing.repository import get_publishing_plan

    conn = _get_db()
    plan = get_publishing_plan(conn, plan_id)
    if plan is None:
        typer.echo(f"Error: Publishing plan {plan_id} not found.", err=True)
        raise typer.Exit(1)

    if dry_run:
        typer.echo(f"[dry-run] Would start job for plan {plan_id}.")
        typer.echo(
            f"  render_manifest_id={plan.render_manifest_id}"
            f"  provider={plan.provider}"
            f"  visibility={plan.visibility}"
        )
        return

    if provider_name != "fake":
        from app.core.config import get_config

        cfg = get_config()
        if not cfg.publishing_live_enabled:
            typer.echo(
                "Error: Live publishing requires ACE_PUBLISHING_LIVE_ENABLED=true"
                " in the environment.",
                err=True,
            )
            raise typer.Exit(1)
        if not execute:
            typer.echo(
                "Error: Live publishing requires --execute flag to confirm intent.",
                err=True,
            )
            raise typer.Exit(1)

    if provider_name == "youtube":
        if not account_id or not workspace_id or not channel_id:
            typer.echo(
                "Error: --account-id, --workspace-id, and --channel-id are required"
                " for --provider youtube.",
                err=True,
            )
            raise typer.Exit(1)
        from app.publishing.providers.youtube import build_youtube_provider_for_account

        cfg = get_config()
        from app.oauth.client_google import RealGoogleOAuthClient

        oauth_client = RealGoogleOAuthClient(
            client_secrets_path=cfg.youtube_client_secrets_path,
            redirect_uri=cfg.youtube_redirect_uri,
        )
        try:
            provider = build_youtube_provider_for_account(
                conn,
                account_id=account_id,
                workspace_id=workspace_id,
                channel_id=channel_id,
                oauth_client=oauth_client,
            )
        except Exception as exc:
            typer.echo(f"Error: Upload pre-flight gate failed: {exc}", err=True)
            raise typer.Exit(1) from None
    else:
        provider = FakePublishingProvider()

    from app.media.repository import get_approved_render, get_render_manifest

    manifest = get_render_manifest(conn, plan.render_manifest_id)
    approved_render = get_approved_render(conn, manifest.scene_manifest_id) if manifest else None
    if approved_render is None:
        typer.echo("Error: Approved render not found.", err=True)
        raise typer.Exit(1)

    # For the YouTube provider, thread explicit ownership IDs so create_publication
    # persists workspace/channel/account at the point of upload.
    ownership_workspace_id = workspace_id if provider_name == "youtube" else None
    ownership_channel_id = channel_id if provider_name == "youtube" else None
    ownership_account_id = account_id if provider_name == "youtube" else None

    try:
        job, publication = start_publishing_job(
            conn,
            plan_id,
            provider,
            output_path=approved_render.output_path,
            output_sha256=approved_render.output_sha256,
            workspace_id=ownership_workspace_id,
            channel_id=ownership_channel_id,
            platform_account_id=ownership_account_id,
        )
        conn.commit()
    except PublicationOwnershipError as exc:
        typer.echo(f"Error: Ownership validation failed: {exc}", err=True)
        raise typer.Exit(1) from None
    except (ActiveJobExistsError, MaxRetriesExceededError, PublishingPlanNotFoundError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None
    except PublishingProviderError as exc:
        typer.echo(f"Provider error: {exc}", err=True)
        conn.commit()
        raise typer.Exit(1) from None

    typer.echo(f"Job id={job.id} attempt={job.attempt_number}  status={job.status}")
    if publication:
        typer.echo(
            f"  publication id={publication.id}"
            f"  video_id={publication.provider_video_id}"
            f"  status={publication.status}"
            f"  url={publication.provider_url or '(none)'}"
        )


# ── schedule ──────────────────────────────────────────────────────────────────


@publish_app.command("schedule")
def publish_schedule(
    plan_id: Annotated[int, typer.Argument(help="Publishing plan ID.")],
    at: Annotated[str, typer.Option("--at", help="ISO 8601 UTC datetime.")],
    timezone: Annotated[str | None, typer.Option("--timezone", help="IANA timezone name.")] = None,
    actor: Annotated[str | None, typer.Option("--actor", help="Operator name.")] = None,
) -> None:
    """Set or update the scheduled publication time for a draft plan."""
    from app.publishing.errors import (
        IllegalPublishingPlanTransitionError,
        PublishingPlanNotFoundError,
        PublishingValidationError,
    )
    from app.publishing.models import PublishingScheduleDraft
    from app.publishing.orchestrator import update_plan_schedule

    conn = _get_db()
    try:
        update_plan_schedule(
            conn,
            plan_id,
            PublishingScheduleDraft(schedule_type="scheduled", scheduled_at=at, timezone=timezone),
            actor=actor,
        )
        conn.commit()
    except (
        PublishingPlanNotFoundError,
        IllegalPublishingPlanTransitionError,
        PublishingValidationError,
    ) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    typer.echo(f"Plan {plan_id} scheduled for {at}" + (f" ({timezone})" if timezone else ""))


# ── list ──────────────────────────────────────────────────────────────────────


@publish_app.command("list")
def publish_list(
    render_manifest_id: Annotated[
        int | None, typer.Option("--manifest-id", "-m", help="Filter by render manifest.")
    ] = None,
    topic_id: Annotated[int | None, typer.Option("--topic-id", help="Filter by topic.")] = None,
    status: Annotated[str | None, typer.Option("--status", "-s", help="Filter by status.")] = None,
    include_superseded: Annotated[
        bool, typer.Option("--include-superseded", help="Include superseded plans.")
    ] = False,
) -> None:
    """List publishing plans."""
    from app.publishing.repository import list_publishing_plans

    conn = _get_db()
    plans = list_publishing_plans(
        conn,
        render_manifest_id=render_manifest_id,
        topic_id=topic_id,
        status=status,
        include_superseded=include_superseded,
    )
    if not plans:
        typer.echo("No publishing plans found.")
        return
    for p in plans:
        sup = "  [superseded]" if p.is_superseded else ""
        typer.echo(
            f"[{p.id}] manifest={p.render_manifest_id}"
            f"  provider={p.provider}"
            f"  visibility={p.visibility}"
            f"  schedule={p.schedule_type}"
            f"  status={p.status}"
            f"{sup}"
        )
    typer.echo(f"\n{len(plans)} plan(s).")


# ── show ──────────────────────────────────────────────────────────────────────


@publish_app.command("show")
def publish_show(
    plan_id: Annotated[int, typer.Argument(help="Publishing plan ID.")],
) -> None:
    """Show detailed information about a publishing plan."""
    from app.publishing.repository import (
        get_latest_publication,
        get_publishing_plan,
        list_publishing_jobs,
    )

    conn = _get_db()
    plan = get_publishing_plan(conn, plan_id)
    if plan is None:
        typer.echo(f"Error: Publishing plan {plan_id} not found.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Publishing plan id={plan.id}")
    typer.echo(f"  render_manifest   : {plan.render_manifest_id}")
    typer.echo(f"  topic_id          : {plan.topic_id}")
    typer.echo(f"  provider          : {plan.provider} v{plan.provider_version}")
    typer.echo(f"  title             : {plan.title!r}")
    typer.echo(f"  description       : {plan.description[:80]!r}")
    typer.echo(f"  tags              : {plan.tags}")
    typer.echo(f"  language          : {plan.language}")
    typer.echo(f"  visibility        : {plan.visibility}")
    typer.echo(f"  made_for_kids     : {bool(plan.made_for_kids)}")
    typer.echo(f"  schedule_type     : {plan.schedule_type}")
    typer.echo(f"  scheduled_at      : {plan.scheduled_at or '(none)'}")
    typer.echo(f"  timezone          : {plan.timezone or '(none)'}")
    typer.echo(f"  status            : {plan.status}")
    if plan.approved_at:
        typer.echo(f"  approved_at       : {plan.approved_at}")
    if plan.rejected_at:
        typer.echo(f"  rejected_at       : {plan.rejected_at}")
    if plan.rejection_reason:
        typer.echo(f"  rejection_reason  : {plan.rejection_reason}")
    if plan.is_superseded:
        typer.echo(f"  superseded_at     : {plan.superseded_at}")
        typer.echo(f"  superseded_by     : {plan.superseded_by_id}")
    typer.echo(f"  input_hash        : {plan.input_hash[:16]}…")
    typer.echo(f"  created_at        : {plan.created_at}")

    jobs = list_publishing_jobs(conn, plan_id)
    if jobs:
        typer.echo(f"\n  Jobs ({len(jobs)}):")
        for j in jobs:
            typer.echo(
                f"    [{j.id}] attempt={j.attempt_number}"
                f"  status={j.status}"
                f"  retries={j.retry_count}"
                + (f"  error={j.error_message!r}" if j.error_message else "")
            )
    else:
        typer.echo("\n  No jobs yet.")

    pub = get_latest_publication(conn, plan_id)
    if pub:
        typer.echo(f"\n  Latest publication id={pub.id}")
        typer.echo(f"    provider_video_id : {pub.provider_video_id or '(pending)'}")
        typer.echo(f"    provider_url      : {pub.provider_url or '(none)'}")
        typer.echo(f"    status            : {pub.status}")
        typer.echo(f"    visibility        : {pub.visibility}")
        if pub.published_at:
            typer.echo(f"    published_at      : {pub.published_at}")
        if pub.scheduled_at:
            typer.echo(f"    scheduled_at      : {pub.scheduled_at}")
    else:
        typer.echo("\n  No publication yet.")


# ── approve ───────────────────────────────────────────────────────────────────


@publish_app.command("approve")
def publish_approve(
    plan_id: Annotated[int, typer.Argument(help="Publishing plan ID.")],
    actor: Annotated[str | None, typer.Option("--actor", help="Reviewer name.")] = None,
    notes: Annotated[str | None, typer.Option("--notes", help="Approval notes.")] = None,
) -> None:
    """Approve a publishing plan/publication."""
    from app.publishing.errors import (
        IllegalPublishingPlanTransitionError,
        PublishingPlanNotFoundError,
    )
    from app.publishing.repository import approve_publishing_plan

    conn = _get_db()
    try:
        approve_publishing_plan(conn, plan_id, actor=actor, notes=notes)
        conn.commit()
    except PublishingPlanNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None
    except IllegalPublishingPlanTransitionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    typer.echo(f"Publishing plan {plan_id} approved." + (f" (actor={actor})" if actor else ""))


# ── reject ────────────────────────────────────────────────────────────────────


@publish_app.command("reject")
def publish_reject(
    plan_id: Annotated[int, typer.Argument(help="Publishing plan ID.")],
    reason: Annotated[str | None, typer.Option("--reason", help="Reason code.")] = None,
    severity: Annotated[int | None, typer.Option("--severity", help="1-5 severity.")] = None,
    notes: Annotated[str | None, typer.Option("--notes", help="Additional notes.")] = None,
    actor: Annotated[str | None, typer.Option("--actor", help="Reviewer name.")] = None,
    correction: Annotated[
        str | None,
        typer.Option("--correction", help="Expected correction description."),
    ] = None,
) -> None:
    """Reject a publishing plan/publication."""
    from app.publishing.errors import (
        IllegalPublishingPlanTransitionError,
        PublishingPlanNotFoundError,
    )
    from app.publishing.repository import reject_publishing_plan

    conn = _get_db()
    try:
        reject_publishing_plan(
            conn,
            plan_id,
            reason_code=reason,
            severity=severity,
            notes=notes,
            actor=actor,
            expected_correction=correction,
        )
        conn.commit()
    except PublishingPlanNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None
    except IllegalPublishingPlanTransitionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    typer.echo(f"Publishing plan {plan_id} rejected.")


# ── retry ─────────────────────────────────────────────────────────────────────


@publish_app.command("retry")
def publish_retry(
    plan_id: Annotated[int, typer.Argument(help="Publishing plan ID.")],
    execute: Annotated[
        bool,
        typer.Option(
            "--execute", help="Confirm live publishing intent (required for non-fake providers)."
        ),  # noqa: E501
    ] = False,
    provider_name: Annotated[
        str, typer.Option("--provider", help="Provider name (default: fake).")
    ] = "fake",
) -> None:
    """Retry the last failed publishing job for a plan."""
    from app.publishing.errors import (
        JobNotRetryableError,
        MaxRetriesExceededError,
        PublishingPlanNotFoundError,
        PublishingProviderError,
    )
    from app.publishing.orchestrator import retry_publishing_job
    from app.publishing.providers.fake import FakePublishingProvider
    from app.publishing.providers.youtube import YouTubePublishingProvider

    conn = _get_db()

    from app.publishing.repository import get_publishing_plan

    plan = get_publishing_plan(conn, plan_id)
    if plan is None:
        typer.echo(f"Error: Publishing plan {plan_id} not found.", err=True)
        raise typer.Exit(1)

    if provider_name != "fake":
        from app.core.config import get_config

        cfg = get_config()
        if not cfg.publishing_live_enabled:
            typer.echo(
                "Error: Live publishing requires ACE_PUBLISHING_LIVE_ENABLED=true"
                " in the environment.",
                err=True,
            )
            raise typer.Exit(1)
        if not execute:
            typer.echo(
                "Error: Live publishing requires --execute flag to confirm intent.",
                err=True,
            )
            raise typer.Exit(1)

    if provider_name == "youtube":
        provider = YouTubePublishingProvider()
    else:
        provider = FakePublishingProvider()

    from app.media.repository import get_approved_render, get_render_manifest

    manifest = get_render_manifest(conn, plan.render_manifest_id)
    approved_render = get_approved_render(conn, manifest.scene_manifest_id) if manifest else None
    if approved_render is None:
        typer.echo("Error: Approved render not found.", err=True)
        raise typer.Exit(1)

    try:
        job, publication = retry_publishing_job(
            conn,
            plan_id,
            provider,
            output_path=approved_render.output_path,
            output_sha256=approved_render.output_sha256,
        )
        conn.commit()
    except (JobNotRetryableError, MaxRetriesExceededError, PublishingPlanNotFoundError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None
    except PublishingProviderError as exc:
        typer.echo(f"Provider error: {exc}", err=True)
        conn.commit()
        raise typer.Exit(1) from None

    typer.echo(f"Retry job id={job.id} attempt={job.attempt_number} status={job.status}")


# ── cancel ────────────────────────────────────────────────────────────────────


@publish_app.command("cancel")
def publish_cancel(
    plan_id: Annotated[int, typer.Argument(help="Publishing plan ID.")],
    actor: Annotated[str | None, typer.Option("--actor", help="Operator name.")] = None,
    notes: Annotated[str | None, typer.Option("--notes", help="Cancellation notes.")] = None,
) -> None:
    """Cancel the active queued or running publishing job for a plan."""
    from app.publishing.errors import (
        JobNotCancellableError,
        PublishingPlanNotFoundError,
    )
    from app.publishing.orchestrator import cancel_publishing_job

    conn = _get_db()
    try:
        job = cancel_publishing_job(conn, plan_id, actor=actor, notes=notes)
        conn.commit()
    except (PublishingPlanNotFoundError, JobNotCancellableError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    typer.echo(f"Job id={job.id} cancelled.")


# ── events ────────────────────────────────────────────────────────────────────


@publish_app.command("events")
def publish_events(
    plan_id: Annotated[int, typer.Argument(help="Publishing plan ID.")],
) -> None:
    """List review events for a publishing plan."""
    from app.publishing.repository import (
        get_publishing_plan,
        list_publishing_review_events,
    )

    conn = _get_db()
    plan = get_publishing_plan(conn, plan_id)
    if plan is None:
        typer.echo(f"Error: Publishing plan {plan_id} not found.", err=True)
        raise typer.Exit(1)

    events = list_publishing_review_events(conn, plan_id)
    if not events:
        typer.echo(f"No review events for plan id={plan_id}.")
        return
    for ev in events:
        actor_s = f"  actor={ev.actor}" if ev.actor else ""
        reason_s = f"  reason={ev.reason_code}" if ev.reason_code else ""
        notes_s = f"  notes={ev.notes!r}" if ev.notes else ""
        typer.echo(f"[{ev.id}] {ev.event_type}{actor_s}{reason_s}{notes_s}  {ev.created_at}")


# ── doctor ────────────────────────────────────────────────────────────────────


@publish_app.command("doctor")
def publish_doctor(
    provider_name: Annotated[
        str, typer.Option("--provider", help="Provider to check (default: fake).")
    ] = "fake",
) -> None:
    """Check publishing provider health and configuration."""
    from app.publishing.providers.fake import FakePublishingProvider
    from app.publishing.providers.youtube import YouTubePublishingProvider

    if provider_name == "youtube":
        provider = YouTubePublishingProvider()
    else:
        provider = FakePublishingProvider()

    report = provider.health()
    caps = provider.capabilities()

    status = "OK" if report.ok else "FAIL"
    typer.echo(f"Provider: {caps.name} v{caps.version}  [{status}]")
    if report.message:
        typer.echo(f"  Message: {report.message}")
    typer.echo(f"  scheduling      : {caps.supports_scheduling}")
    typer.echo(f"  playlists       : {caps.supports_playlists}")
    typer.echo(f"  captions        : {caps.supports_captions}")
    typer.echo(f"  thumbnails      : {caps.supports_thumbnails}")
    typer.echo(f"  status polling  : {caps.supports_status_polling}")

    if provider_name == "youtube":
        typer.echo("")
        typer.echo(
            "Note: YouTube live credentials require OAuth 2.0 authorization."
            " See DECISIONS.md for the pending credential design decision."
        )

    if not report.ok:
        raise typer.Exit(1)
