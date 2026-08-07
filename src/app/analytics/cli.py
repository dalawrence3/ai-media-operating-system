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
        str, typer.Argument(help="Provider-side video identifier.")
    ],
    publishing_plan_id: Annotated[
        int, typer.Option("--plan", help="Publishing plan ID.")
    ],
    publishing_job_id: Annotated[
        int, typer.Option("--job", help="Publishing job ID.")
    ],
    render_manifest_id: Annotated[
        int, typer.Option("--render", help="Render manifest ID.")
    ],
    scene_manifest_id: Annotated[
        int, typer.Option("--scene", help="Scene manifest ID.")
    ],
    production_plan_id: Annotated[
        int, typer.Option("--production", help="Production plan ID.")
    ],
    script_id: Annotated[int, typer.Option("--script", help="Script ID.")],
    topic_id: Annotated[int, typer.Option("--topic", help="Topic ID.")],
    narration_run_id: Annotated[
        int, typer.Option("--narration", help="Narration run ID.")
    ],
    caption_run_id: Annotated[
        int, typer.Option("--caption", help="Caption run ID.")
    ],
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
        str, typer.Option("--provider", help="Provider name (default: fake).")
    ] = "fake",
) -> None:
    """Fetch metrics from provider and store a normalized analytics snapshot."""
    conn = _get_db()
    provider = _default_provider() if provider_name == "fake" else _default_provider()

    from app.analytics.orchestrator import AnalyticsOrchestrator

    orch = AnalyticsOrchestrator(conn, provider)
    snapshot, metrics = orch.ingest(
        provider_video_id=provider_video_id,
        publication_id=publication_id,
        publishing_plan_id=publishing_plan_id,
        publishing_job_id=publishing_job_id,
        render_manifest_id=render_manifest_id,
        scene_manifest_id=scene_manifest_id,
        production_plan_id=production_plan_id,
        script_id=script_id,
        topic_id=topic_id,
        narration_run_id=narration_run_id,
        caption_run_id=caption_run_id,
        experiment_id=experiment_id,
        period_start=period_start,
        period_end=period_end,
    )
    typer.echo(f"Snapshot #{snapshot.id}  hash={snapshot.input_hash[:12]}...")
    typer.echo(f"  provider={snapshot.provider}  metrics={len(metrics)}")
    for m in metrics:
        typer.echo(f"  {m.metric_name:<40} {m.metric_value}")


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
    snapshot_id: Annotated[
        int | None, typer.Argument(help="Snapshot ID (omit for all).")
    ] = None,
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
            f"#{e.id:4d}  snap={e.snapshot_id}  {e.severity:<10}"
            f"  {e.created_at}  {e.notes[:60]}"
        )


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
