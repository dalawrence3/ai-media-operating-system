"""Phase 11 — ace learn CLI.

Commands:
    ace learn analyze  <publication_id>        — run optimizer for a publication
    ace learn list     [--topic] [--domain]    — list recommendations
    ace learn show     <recommendation_id>     — show recommendation with evidence
    ace learn accept   <recommendation_id>     — accept a recommendation
    ace learn reject   <recommendation_id>     — reject a recommendation (notes required)
    ace learn events   [recommendation_id]     — list review events
    ace learn runs     [--topic]               — list learning runs
"""

from __future__ import annotations

from typing import Annotated

import typer

learn_app = typer.Typer(
    name="learn",
    help="Learning & Optimization Engine — deterministic recommendations from analytics history.",
    no_args_is_help=True,
)


def _get_db():
    from app.core.config import get_config
    from app.core.database import open_db
    from app.core.logging import configure_logging

    cfg = get_config()
    configure_logging(cfg.log_level)
    return open_db(cfg.db_path)


# ── analyze ───────────────────────────────────────────────────────────────────


@learn_app.command("analyze")
def learn_analyze(
    publication_id: Annotated[int, typer.Argument(help="Publication ID to analyze.")],
    topic_id: Annotated[int, typer.Option("--topic", help="Topic ID for the publication.")],
) -> None:
    """Run the optimization engine for a publication and store recommendations."""
    from app.learning.orchestrator import analyze_publication

    conn = _get_db()
    try:
        run_id = analyze_publication(conn, publication_id=publication_id, topic_id=topic_id)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    from app.learning.repository import get_learning_run

    run = get_learning_run(conn, run_id)
    typer.echo(f"Learning run {run.id} completed.")
    typer.echo(f"  Status:          {run.status}")
    typer.echo(f"  Recommendations: {run.recommendation_count}")
    typer.echo(f"  Engine version:  {run.engine_version}")
    typer.echo(f"  Input hash:      {run.input_hash[:16]}…")


# ── list ──────────────────────────────────────────────────────────────────────


@learn_app.command("list")
def learn_list(
    topic_id: Annotated[int | None, typer.Option("--topic", help="Filter by topic ID.")] = None,
    publication_id: Annotated[
        int | None, typer.Option("--publication", help="Filter by publication ID.")
    ] = None,
    domain: Annotated[str | None, typer.Option("--domain", help="Filter by domain.")] = None,
    status: Annotated[
        str | None, typer.Option("--status", help="Filter by status (pending/accepted/rejected).")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Maximum results.")] = 50,
) -> None:
    """List optimization recommendations."""
    from app.learning.repository import list_recommendations

    conn = _get_db()
    recs = list_recommendations(
        conn,
        topic_id=topic_id,
        publication_id=publication_id,
        domain=domain,
        status=status,
        limit=limit,
    )

    if not recs:
        typer.echo("No recommendations found.")
        return

    typer.echo(f"{'ID':>5}  {'Domain':<12}  {'Subsystem':<22}  {'Conf':<6}  {'Status':<10}  Title")
    typer.echo("-" * 100)
    for r in recs:
        title_short = r.title[:50] + "…" if len(r.title) > 50 else r.title
        typer.echo(
            f"{r.id:>5}  {r.domain:<12}  {r.subsystem:<24}  "
            f"{r.confidence:<8}  {r.status:<10}  {title_short}"
        )


# ── show ──────────────────────────────────────────────────────────────────────


@learn_app.command("show")
def learn_show(
    recommendation_id: Annotated[int, typer.Argument(help="Recommendation ID.")],
) -> None:
    """Show a recommendation with full evidence and explanation."""
    from app.learning.repository import get_recommendation

    conn = _get_db()
    try:
        rec = get_recommendation(conn, recommendation_id)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"\nRecommendation #{rec.id}")
    typer.echo(f"  Status:           {rec.status}")
    typer.echo(f"  Domain:           {rec.domain}")
    typer.echo(f"  Subsystem:        {rec.subsystem}")
    typer.echo(f"  Measure:          {rec.measure}")
    typer.echo(f"  Confidence:       {rec.confidence} ({rec.confidence_score:.3f})")
    typer.echo(f"  Affected:         {rec.affected_subsystem}")
    if rec.subsystem_entity_type:
        typer.echo(f"  Entity:           {rec.subsystem_entity_type} #{rec.subsystem_entity_id}")
    typer.echo(f"\n  Title\n  {'─' * 60}")
    typer.echo(f"  {rec.title}")
    typer.echo(f"\n  Why this recommendation exists\n  {'─' * 60}")
    for line in rec.explanation.split(". "):
        if line.strip():
            typer.echo(f"  {line.strip()}.")
    typer.echo(f"\n  Expected improvement\n  {'─' * 60}")
    for line in rec.expected_improvement.split(". "):
        if line.strip():
            typer.echo(f"  {line.strip()}.")
    typer.echo(f"\n  Evidence ({len(rec.evidence)} item(s))\n  {'─' * 60}")
    for i, ev in enumerate(rec.evidence, 1):
        typer.echo(f"  [{i}] {ev.metric_name} | {ev.period_type} ({ev.period_key})")
        typer.echo(f"       Observed: {ev.observed_value:.4f}")
        if ev.comparison_value is not None:
            typer.echo(f"       Threshold: {ev.comparison_value:.4f}")
        typer.echo(f"       Snapshots: {ev.snapshot_ids}")
        typer.echo(f"       {ev.interpretation}")
    typer.echo(f"\n  Provenance\n  {'─' * 60}")
    typer.echo(f"  Engine: {rec.engine_version}  Schema: {rec.schema_version}")
    typer.echo(f"  Hash:   {rec.input_hash[:32]}…")
    typer.echo(f"  Run:    #{rec.learning_run_id}")
    if rec.experiment_id:
        typer.echo(f"  Experiment: {rec.experiment_id}")
    typer.echo()


# ── accept ────────────────────────────────────────────────────────────────────


@learn_app.command("accept")
def learn_accept(
    recommendation_id: Annotated[int, typer.Argument(help="Recommendation ID to accept.")],
    reviewer: Annotated[str, typer.Option("--reviewer", help="Name of the reviewer.")],
    notes: Annotated[str, typer.Option("--notes", help="Optional acceptance notes.")] = "",
    expected_outcome: Annotated[
        str, typer.Option("--expected-outcome", help="Expected result if acted upon.")
    ] = "",
) -> None:
    """Accept a recommendation (records human approval)."""
    from app.learning.orchestrator import accept_recommendation

    conn = _get_db()
    try:
        event = accept_recommendation(
            conn,
            recommendation_id,
            reviewer=reviewer,
            notes=notes,
            expected_outcome=expected_outcome,
        )
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Recommendation #{recommendation_id} accepted.")
    typer.echo(f"  Event ID: {event.id}  Reviewer: {event.reviewer}")


# ── reject ────────────────────────────────────────────────────────────────────


@learn_app.command("reject")
def learn_reject(
    recommendation_id: Annotated[int, typer.Argument(help="Recommendation ID to reject.")],
    reviewer: Annotated[str, typer.Option("--reviewer", help="Name of the reviewer.")],
    notes: Annotated[str, typer.Option("--notes", help="Reason for rejection (required).")],
    expected_outcome: Annotated[
        str, typer.Option("--expected-outcome", help="What you expect instead.")
    ] = "",
) -> None:
    """Reject a recommendation (notes are required — they are a training signal)."""
    from app.learning.orchestrator import reject_recommendation

    conn = _get_db()
    try:
        event = reject_recommendation(
            conn,
            recommendation_id,
            reviewer=reviewer,
            notes=notes,
            expected_outcome=expected_outcome,
        )
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Recommendation #{recommendation_id} rejected.")
    typer.echo(f"  Event ID: {event.id}  Reviewer: {event.reviewer}")
    typer.echo(f"  Notes:    {event.notes}")


# ── events ────────────────────────────────────────────────────────────────────


@learn_app.command("events")
def learn_events(
    recommendation_id: Annotated[
        int | None, typer.Argument(help="Filter by recommendation ID.")
    ] = None,
    topic_id: Annotated[int | None, typer.Option("--topic", help="Filter by topic ID.")] = None,
    limit: Annotated[int, typer.Option("--limit", help="Maximum results.")] = 50,
) -> None:
    """List review events for recommendations."""
    from app.learning.repository import list_review_events

    conn = _get_db()
    events = list_review_events(
        conn,
        recommendation_id=recommendation_id,
        topic_id=topic_id,
        limit=limit,
    )

    if not events:
        typer.echo("No review events found.")
        return

    typer.echo(f"{'ID':>5}  {'Rec':>5}  {'Type':<10}  {'Reviewer':<16}  Notes")
    typer.echo("-" * 80)
    for ev in events:
        notes_short = ev.notes[:40] + "…" if len(ev.notes) > 40 else ev.notes
        typer.echo(
            f"{ev.id:>5}  {ev.recommendation_id:>5}  {ev.event_type:<10}  "
            f"{ev.reviewer:<16}  {notes_short}"
        )


# ── runs ──────────────────────────────────────────────────────────────────────


@learn_app.command("runs")
def learn_runs(
    topic_id: Annotated[int | None, typer.Option("--topic", help="Filter by topic ID.")] = None,
    limit: Annotated[int, typer.Option("--limit", help="Maximum results.")] = 50,
) -> None:
    """List learning runs."""
    from app.learning.repository import list_learning_runs

    conn = _get_db()
    runs = list_learning_runs(conn, topic_id=topic_id, limit=limit)

    if not runs:
        typer.echo("No learning runs found.")
        return

    typer.echo(f"{'ID':>5}  {'Topic':>6}  {'Pub':>5}  {'Status':<10}  {'Recs':>4}  Created")
    typer.echo("-" * 72)
    for run in runs:
        pub = str(run.publication_id) if run.publication_id is not None else "—"
        typer.echo(
            f"{run.id:>5}  {run.topic_id:>6}  {pub:>5}  {run.status:<10}  "
            f"{run.recommendation_count:>4}  {run.created_at}"
        )
