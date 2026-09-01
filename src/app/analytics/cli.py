"""Phase 10 — ace analytics CLI.

Commands:
    ace analytics ingest   <publication_id>  — fetch and store provider metrics
    ace analytics snapshot <snapshot_id>     — show a snapshot + its metrics
    ace analytics metrics  <publication_id>  — list normalized metrics
    ace analytics aggregate <publication_id> — run or refresh aggregation rollups
    ace analytics show     <publication_id>  — show aggregates summary
    ace analytics list                       — list snapshots
    ace analytics events   [snapshot_id]     — list review events
    ace analytics doctor                     — check provider health
"""

from __future__ import annotations

from typing import Annotated

import typer

analytics_app = typer.Typer(
    name="analytics",
    help="Platform analytics — ingest, normalize, and aggregate publication metrics.",
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
    from app.analytics.providers.fake import FakeAnalyticsProvider

    return FakeAnalyticsProvider()


# ── ingest ────────────────────────────────────────────────────────────────────


@analytics_app.command("ingest")
def analytics_ingest(
    publication_id: Annotated[int, typer.Argument(help="Publication ID.")],
    provider_video_id: Annotated[
        str | None,
        typer.Argument(
            help="Provider-side video ID. Derived from the publication when --provider youtube."
        ),
    ] = None,
    publishing_plan_id: Annotated[
        int | None, typer.Option("--plan", help="Publishing plan ID (auto-derived for youtube).")
    ] = None,
    publishing_job_id: Annotated[
        int | None, typer.Option("--job", help="Publishing job ID (auto-derived for youtube).")
    ] = None,
    render_manifest_id: Annotated[
        int | None,
        typer.Option("--render", help="Render manifest ID (auto-derived for youtube)."),
    ] = None,
    scene_manifest_id: Annotated[
        int | None,
        typer.Option("--scene", help="Scene manifest ID (auto-derived for youtube)."),
    ] = None,
    production_plan_id: Annotated[
        int | None,
        typer.Option("--production", help="Production plan ID (auto-derived for youtube)."),
    ] = None,
    script_id: Annotated[
        int | None, typer.Option("--script", help="Script ID (auto-derived for youtube).")
    ] = None,
    topic_id: Annotated[
        int | None, typer.Option("--topic", help="Topic ID (auto-derived for youtube).")
    ] = None,
    narration_run_id: Annotated[
        int | None,
        typer.Option("--narration", help="Narration run ID (auto-derived for youtube)."),
    ] = None,
    caption_run_id: Annotated[
        int | None,
        typer.Option("--caption", help="Caption run ID (auto-derived for youtube)."),
    ] = None,
    period_start: Annotated[
        str | None, typer.Option("--period-start", help="ISO 8601 period start.")
    ] = None,
    period_end: Annotated[
        str | None, typer.Option("--period-end", help="ISO 8601 period end.")
    ] = None,
    experiment_id: Annotated[
        str | None, typer.Option("--experiment", help="Experiment ID.")
    ] = None,
    provider_name: Annotated[
        str, typer.Option("--provider", help="Provider name: fake (default) or youtube.")
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
    """Fetch metrics from provider and store a normalized analytics snapshot.

    For --provider youtube, supply only the publication_id — all upstream lineage
    IDs are derived automatically from the publication and publishing plan rows.
    You must also supply --account-id, --workspace-id, and --channel-id so the
    gate can load and refresh the stored OAuth token.

    For --provider fake, supply all IDs explicitly.
    """
    conn = _get_db()

    if provider_name == "youtube":
        (
            provider,
            (
                provider_video_id,
                publishing_plan_id,
                publishing_job_id,
                render_manifest_id,
                scene_manifest_id,
                production_plan_id,
                script_id,
                topic_id,
                narration_run_id,
                caption_run_id,
            ),
            _pub_published_at,
            _derived_experiment_id,
        ) = _build_youtube_provider_and_lineage(
            conn,
            publication_id=publication_id,
            account_id=account_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
        )
        if experiment_id is not None and _derived_experiment_id is not None:
            if experiment_id != _derived_experiment_id:
                typer.echo(
                    f"Error: --experiment {experiment_id!r} does not match the experiment "
                    f"derived from publication {publication_id} ({_derived_experiment_id!r}). "
                    "Omit --experiment to use the derived value.",
                    err=True,
                )
                raise typer.Exit(1)
        if experiment_id is None:
            experiment_id = _derived_experiment_id
        if period_start is None:
            if _pub_published_at is None:
                typer.echo(
                    "Error: Publication has no published_at date and --period-start was not "
                    "supplied. Provide --period-start to ingest metrics for this publication.",
                    err=True,
                )
                raise typer.Exit(1)
            period_start = _pub_published_at
    else:
        provider = _default_provider()
        # For fake provider, all IDs must be supplied explicitly
        _require_explicit_lineage(
            provider_video_id=provider_video_id,
            publishing_plan_id=publishing_plan_id,
            publishing_job_id=publishing_job_id,
            render_manifest_id=render_manifest_id,
            scene_manifest_id=scene_manifest_id,
            production_plan_id=production_plan_id,
            script_id=script_id,
            topic_id=topic_id,
            narration_run_id=narration_run_id,
            caption_run_id=caption_run_id,
        )

    from app.analytics.orchestrator import AnalyticsOrchestrator

    orch = AnalyticsOrchestrator(conn, provider)
    snapshot, metrics = orch.ingest(
        provider_video_id=provider_video_id,  # type: ignore[arg-type]
        publication_id=publication_id,
        publishing_plan_id=publishing_plan_id,  # type: ignore[arg-type]
        publishing_job_id=publishing_job_id,  # type: ignore[arg-type]
        render_manifest_id=render_manifest_id,  # type: ignore[arg-type]
        scene_manifest_id=scene_manifest_id,  # type: ignore[arg-type]
        production_plan_id=production_plan_id,  # type: ignore[arg-type]
        script_id=script_id,  # type: ignore[arg-type]
        topic_id=topic_id,  # type: ignore[arg-type]
        narration_run_id=narration_run_id,  # type: ignore[arg-type]
        caption_run_id=caption_run_id,  # type: ignore[arg-type]
        experiment_id=experiment_id,
        period_start=period_start,
        period_end=period_end,
    )
    typer.echo(f"Snapshot #{snapshot.id}  hash={snapshot.input_hash[:12]}...")
    typer.echo(f"  provider={snapshot.provider}  metrics={len(metrics)}")
    for m in metrics:
        typer.echo(f"  {m.metric_name:<40} {m.metric_value}")


def _build_youtube_provider_and_lineage(
    conn: object,
    *,
    publication_id: int,
    account_id: str | None,
    workspace_id: str | None,
    channel_id: str | None,
) -> tuple[object, tuple, str | None, str | None]:
    """Build authenticated YouTube Analytics provider and derive lineage from publication.

    Returns (provider, lineage_tuple, published_at_date, derived_experiment_id) where:
    - published_at_date is the publication's published_at truncated to YYYY-MM-DD, or None
    - derived_experiment_id is publishing_plan.experiment_id, or None if not an experiment
    """
    import typer

    if not account_id or not workspace_id or not channel_id:
        typer.echo(
            "Error: --account-id, --workspace-id, and --channel-id are required "
            "for --provider youtube.",
            err=True,
        )
        raise typer.Exit(1)

    from app.analytics.gate import build_authenticated_analytics_provider
    from app.publishing.repository import get_publication, get_publishing_plan

    pub = get_publication(conn, publication_id)  # type: ignore[arg-type]
    if pub is None:
        typer.echo(f"Error: Publication {publication_id} not found.", err=True)
        raise typer.Exit(1)

    plan = get_publishing_plan(conn, pub.publishing_plan_id)  # type: ignore[arg-type]
    if plan is None:
        typer.echo(
            f"Error: Publishing plan {pub.publishing_plan_id} not found for "
            f"publication {publication_id}.",
            err=True,
        )
        raise typer.Exit(1)

    from app.core.config import get_config
    from app.oauth.client_google import RealGoogleOAuthClient

    cfg = get_config()
    try:
        oauth_client = RealGoogleOAuthClient(
            client_secrets_path=cfg.youtube_client_secrets_path,
            redirect_uri=cfg.youtube_redirect_uri,
        )
    except Exception as exc:
        typer.echo(f"Error: OAuth client construction failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    try:
        provider = build_authenticated_analytics_provider(
            conn,
            account_id=account_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            oauth_client=oauth_client,
        )
    except Exception as exc:
        typer.echo(f"Error: Analytics provider gate failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    lineage = (
        pub.provider_video_id,
        pub.publishing_plan_id,
        pub.publishing_job_id,
        plan.render_manifest_id,
        plan.scene_manifest_id,
        plan.production_plan_id,
        plan.script_id,
        plan.topic_id,
        plan.narration_run_id,
        plan.caption_run_id,
    )
    published_at_date = pub.published_at[:10] if pub.published_at else None
    derived_experiment_id: str | None = (
        plan.experiment_id if hasattr(plan, "experiment_id") else None
    )
    return provider, lineage, published_at_date, derived_experiment_id


def _require_explicit_lineage(
    *,
    provider_video_id: str | None,
    publishing_plan_id: int | None,
    publishing_job_id: int | None,
    render_manifest_id: int | None,
    scene_manifest_id: int | None,
    production_plan_id: int | None,
    script_id: int | None,
    topic_id: int | None,
    narration_run_id: int | None,
    caption_run_id: int | None,
) -> None:
    """Verify all lineage IDs were supplied explicitly (required for fake provider)."""
    import typer

    missing = [
        name
        for name, val in [
            ("provider_video_id (positional)", provider_video_id),
            ("--plan", publishing_plan_id),
            ("--job", publishing_job_id),
            ("--render", render_manifest_id),
            ("--scene", scene_manifest_id),
            ("--production", production_plan_id),
            ("--script", script_id),
            ("--topic", topic_id),
            ("--narration", narration_run_id),
            ("--caption", caption_run_id),
        ]
        if val is None
    ]
    if missing:
        typer.echo(
            f"Error: the following required arguments are missing: {', '.join(missing)}",
            err=True,
        )
        raise typer.Exit(1)


# ── snapshot ──────────────────────────────────────────────────────────────────


@analytics_app.command("snapshot")
def analytics_snapshot(
    snapshot_id: Annotated[int, typer.Argument(help="Snapshot ID.")],
) -> None:
    """Show an analytics snapshot and its normalized metrics."""
    conn = _get_db()
    from app.analytics.errors import SnapshotNotFoundError
    from app.analytics.repository import get_snapshot, list_metrics_for_snapshot

    try:
        snap = get_snapshot(conn, snapshot_id)
    except SnapshotNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Snapshot #{snap.id}")
    typer.echo(f"  publication_id   : {snap.publication_id}")
    typer.echo(f"  provider         : {snap.provider} v{snap.provider_version}")
    typer.echo(f"  period           : {snap.period_start} → {snap.period_end}")
    typer.echo(f"  ingested_at      : {snap.ingested_at}")
    typer.echo(f"  input_hash       : {snap.input_hash}")
    typer.echo("")

    metrics = list_metrics_for_snapshot(conn, snapshot_id)
    if not metrics:
        typer.echo("  No metrics.")
        return
    typer.echo("Metrics:")
    for m in metrics:
        typer.echo(f"  {m.metric_name:<40} {m.metric_value}")


# ── metrics ───────────────────────────────────────────────────────────────────


@analytics_app.command("metrics")
def analytics_metrics(
    publication_id: Annotated[int, typer.Argument(help="Publication ID.")],
    metric_name: Annotated[
        str | None, typer.Option("--metric", "-m", help="Filter by metric name.")
    ] = None,
) -> None:
    """List normalized metrics for a publication."""
    conn = _get_db()
    from app.analytics.repository import list_metrics_for_publication

    metrics = list_metrics_for_publication(conn, publication_id, metric_name=metric_name)
    if not metrics:
        typer.echo("No metrics found.")
        return
    for m in metrics:
        typer.echo(f"#{m.id:4d}  {m.metric_name:<40} {m.metric_value:>14.4f}  snap={m.snapshot_id}")


# ── aggregate ─────────────────────────────────────────────────────────────────


@analytics_app.command("aggregate")
def analytics_aggregate(
    publication_id: Annotated[int, typer.Argument(help="Publication ID.")],
    topic_id: Annotated[int, typer.Option("--topic", help="Topic ID.")],
    provider_name: Annotated[
        str, typer.Option("--provider", help="Provider name (default: fake).")
    ] = "fake",
) -> None:
    """Recompute all period rollups for a publication."""
    conn = _get_db()
    from app.analytics.aggregation import aggregate_all_periods

    results = aggregate_all_periods(
        conn,
        publication_id=publication_id,
        topic_id=topic_id,
        provider=provider_name,
    )
    total = sum(len(r.aggregates) for r in results)
    typer.echo(f"Aggregated {total} rollup rows across {len(results)} period types.")
    for r in results:
        typer.echo(f"  {r.period_type:<10} {len(r.aggregates)} metric rows")


# ── show ──────────────────────────────────────────────────────────────────────


@analytics_app.command("show")
def analytics_show(
    publication_id: Annotated[int, typer.Argument(help="Publication ID.")],
    period_type: Annotated[
        str, typer.Option("--period", "-p", help="daily | weekly | monthly | lifetime.")
    ] = "lifetime",
    provider_name: Annotated[
        str, typer.Option("--provider", help="Provider name (default: fake).")
    ] = "fake",
) -> None:
    """Show aggregate rollups for a publication."""
    conn = _get_db()
    from app.analytics.repository import list_aggregates

    aggs = list_aggregates(
        conn,
        publication_id=publication_id,
        provider=provider_name,
        period_type=period_type,
    )
    if not aggs:
        typer.echo("No aggregates found. Run `ace analytics aggregate` first.")
        return
    typer.echo(f"Publication #{publication_id}  period={period_type}  provider={provider_name}")
    for a in aggs:
        typer.echo(f"  {a.period_key:<12}  {a.metric_name:<40} {a.metric_value:>14.4f}")


# ── list ──────────────────────────────────────────────────────────────────────


@analytics_app.command("list")
def analytics_list(
    publication_id: Annotated[
        int | None, typer.Option("--publication", "-p", help="Filter by publication ID.")
    ] = None,
    topic_id: Annotated[
        int | None, typer.Option("--topic", "-t", help="Filter by topic ID.")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max rows.")] = 20,
    provider_name: Annotated[
        str, typer.Option("--provider", help="Provider name (default: fake).")
    ] = "fake",
) -> None:
    """List analytics snapshots."""
    conn = _get_db()
    from app.analytics.repository import list_snapshots

    snaps = list_snapshots(
        conn,
        publication_id=publication_id,
        topic_id=topic_id,
        provider=provider_name,
        limit=limit,
    )
    if not snaps:
        typer.echo("No snapshots found.")
        return
    for s in snaps:
        typer.echo(
            f"#{s.id:4d}  pub={s.publication_id}  {s.provider:<10}"
            f"  {s.ingested_at}  hash={s.input_hash[:12]}..."
        )


# ── events ────────────────────────────────────────────────────────────────────


@analytics_app.command("events")
def analytics_events(
    snapshot_id: Annotated[int | None, typer.Argument(help="Snapshot ID (omit for all).")] = None,
    severity: Annotated[
        str | None, typer.Option("--severity", "-s", help="Filter by severity.")
    ] = None,
) -> None:
    """List analytics review events."""
    conn = _get_db()
    from app.analytics.repository import list_review_events

    events = list_review_events(conn, snapshot_id=snapshot_id, severity=severity)
    if not events:
        typer.echo("No events found.")
        return
    for e in events:
        typer.echo(
            f"#{e.id:4d}  snap={e.snapshot_id}  {e.severity:<10}  {e.created_at}  {e.notes[:60]}"
        )


# ── retention-ingest ──────────────────────────────────────────────────────────


@analytics_app.command("retention-ingest")
def analytics_retention_ingest(
    publication_id: Annotated[int, typer.Argument(help="Publication ID.")],
    snapshot_id: Annotated[
        int | None, typer.Option("--snapshot-id", help="Snapshot to link to (default: latest).")
    ] = None,
    period_start: Annotated[
        str | None, typer.Option("--period-start", help="ISO 8601 period start.")
    ] = None,
    period_end: Annotated[
        str | None, typer.Option("--period-end", help="ISO 8601 period end.")
    ] = None,
    account_id: Annotated[
        str | None, typer.Option("--account-id", help="Platform account ID.")
    ] = None,
    workspace_id: Annotated[
        str | None, typer.Option("--workspace-id", help="Workspace ID.")
    ] = None,
    channel_id: Annotated[str | None, typer.Option("--channel-id", help="Channel ID.")] = None,
) -> None:
    """Fetch audience retention curve from YouTube Analytics and persist with scene attribution.

    The retention query uses dimensions=elapsedVideoTimeRatio, which is separate
    from the scalar ingest.  Link to an existing scalar snapshot via --snapshot-id,
    or omit to use the most recent snapshot for this publication.
    """
    from datetime import date

    from app.analytics.errors import ProviderAdapterError, SnapshotNotFoundError
    from app.analytics.repository import get_snapshot, list_snapshots
    from app.analytics.retention import (
        RetentionCurve,
        attribute_retention_curve,
        load_scene_catalog,
        parse_retention_rows,
        persist_retention_curve,
    )

    conn = _get_db()

    # Resolve snapshot
    if snapshot_id is not None:
        try:
            snap = get_snapshot(conn, snapshot_id)
        except SnapshotNotFoundError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        if snap.publication_id != publication_id:
            typer.echo(
                f"Error: snapshot {snapshot_id} belongs to publication "
                f"{snap.publication_id}, not {publication_id}.",
                err=True,
            )
            raise typer.Exit(1)
    else:
        snaps = list_snapshots(
            conn, publication_id=publication_id, observation_state="data", limit=1
        )
        if not snaps:
            typer.echo(
                f"Error: no snapshots found for publication {publication_id}. "
                "Run `ace analytics ingest` first.",
                err=True,
            )
            raise typer.Exit(1)
        snap = snaps[0]
        typer.echo(f"Using latest snapshot #{snap.id}")
        if (
            period_start is not None
            and snap.period_start is not None
            and period_start != snap.period_start
        ):
            typer.echo(
                f"Error: --period-start {period_start!r} does not match snapshot #{snap.id} "
                f"period_start {snap.period_start!r}. "
                "Use --snapshot-id to target a specific snapshot.",
                err=True,
            )
            raise typer.Exit(1)

    # Build provider
    (
        provider,
        _lineage,
        _pub_date,
        _,
    ) = _build_youtube_provider_and_lineage(
        conn,
        publication_id=publication_id,
        account_id=account_id,
        workspace_id=workspace_id,
        channel_id=channel_id,
    )

    # Resolve period window
    _start = period_start or snap.period_start
    _end = period_end or snap.period_end or date.today().isoformat()
    if _start is None:
        typer.echo("Error: cannot determine period_start. Supply --period-start.", err=True)
        raise typer.Exit(1)

    # Fetch
    typer.echo(f"Fetching retention curve for video {_lineage[0]}  period={_start}→{_end}")
    try:
        raw_rows = provider.fetch_retention(  # type: ignore[attr-defined]
            _lineage[0],  # provider_video_id
            period_start=_start,
            period_end=_end,
        )
    except ProviderAdapterError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    # Parse, attribute, persist
    points = parse_retention_rows(raw_rows)
    scenes, duration_ms = load_scene_catalog(conn, snap.scene_manifest_id)
    curve = RetentionCurve(
        provider_video_id=_lineage[0],
        scene_manifest_id=snap.scene_manifest_id,
        video_duration_ms=duration_ms,
        period_start=_start,
        period_end=_end,
        points=points,
    )
    attribute_retention_curve(curve, scenes)
    n = persist_retention_curve(conn, curve, snapshot_id=snap.id, publication_id=publication_id)
    typer.echo(f"Stored {n} retention points linked to snapshot #{snap.id}")


# ── retention-show ────────────────────────────────────────────────────────────


@analytics_app.command("retention-show")
def analytics_retention_show(
    publication_id: Annotated[int, typer.Argument(help="Publication ID.")],
    snapshot_id: Annotated[
        int | None, typer.Option("--snapshot-id", help="Snapshot ID (default: latest).")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max points to display.")] = 50,
) -> None:
    """Show attributed audience retention curve for a publication.

    Each line: elapsed=Xs  scene N  section_type  awr=0.xxxx  [rel=0.xxxx]
    """
    from app.analytics.repository import list_snapshots
    from app.analytics.retention import list_retention_for_publication

    conn = _get_db()

    if snapshot_id is None:
        snaps = list_snapshots(conn, publication_id=publication_id, limit=1)
        if not snaps:
            typer.echo("No snapshots found for this publication.", err=True)
            raise typer.Exit(1)
        snapshot_id = snaps[0].id

    points = list_retention_for_publication(conn, publication_id, snapshot_id=snapshot_id)
    if not points:
        typer.echo("No retention data found. Run `ace analytics retention-ingest` first.")
        return

    typer.echo(
        f"Retention curve — publication #{publication_id}  snapshot #{snapshot_id}"
        f"  ({len(points)} points)"
    )
    for p in points[:limit]:
        typer.echo("  " + p.format_line())
    if len(points) > limit:
        typer.echo(f"  … {len(points) - limit} more points (use --limit to show more)")


# ── doctor ────────────────────────────────────────────────────────────────────


@analytics_app.command("doctor")
def analytics_doctor(
    provider_name: Annotated[
        str, typer.Option("--provider", help="Provider name (default: fake).")
    ] = "fake",
) -> None:
    """Check analytics provider health and credentials."""
    provider = _default_provider()
    report = provider.health()
    status = "OK" if report.ok else "FAIL"
    typer.echo(f"Provider : {report.provider} v{report.provider_version}")
    typer.echo(f"Status   : {status}")
    typer.echo(f"Message  : {report.message}")
    if not report.ok:
        raise typer.Exit(1)


# ── observe-daemon ────────────────────────────────────────────────────────────


@analytics_app.command("observe-daemon")
def analytics_observe_daemon(
    poll_interval: Annotated[
        int,
        typer.Option(
            "--poll-interval",
            "-i",
            help="Scheduler poll interval in seconds (default: 60).",
        ),
    ] = 60,
) -> None:
    """Run the analytics observation scheduler daemon.

    On startup, reconciles all public publications that have no active
    observation schedule, then loops every POLL_INTERVAL seconds to dispatch
    due analytics_observation ticks inline (no Redis required).

    This is the recommended way to run ACE's autonomous analytics lifecycle.
    Exit with Ctrl-C.
    """
    from app.workers.scheduler import run_scheduler_daemon

    run_scheduler_daemon(poll_interval_seconds=poll_interval)
