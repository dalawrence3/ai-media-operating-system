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


# ── cross-pub ─────────────────────────────────────────────────────────────────


@learn_app.command("cross-pub")
def learn_cross_pub(
    channel_id: Annotated[str, typer.Option("--channel", help="Channel ID to analyze.")],
    workspace_id: Annotated[
        str | None, typer.Option("--workspace", help="Workspace ID (optional).")
    ] = None,
) -> None:
    """Run cross-publication learning for a channel.

    Computes channel performance baselines and feature association observations
    from all publications in this channel that have feature snapshots and analytics.
    Idempotent: safe to re-run; updated when evidence changes.
    """
    from app.learning.cross_publication import run_cross_publication_learning

    conn = _get_db()
    try:
        result = run_cross_publication_learning(
            conn, channel_id=channel_id, workspace_id=workspace_id
        )
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        conn.close()

    typer.echo(f"Cross-publication analysis complete  channel={channel_id}")
    typer.echo(f"  Publications analyzed:  {result.publication_count}")
    typer.echo(f"  Baselines computed:     {result.baselines_computed}")
    typer.echo(f"  Observations computed:  {result.observations_computed}")
    if result.metrics_with_data:
        typer.echo(f"  Metrics with data:      {', '.join(result.metrics_with_data)}")
    else:
        typer.echo("  No analytics data found for this channel yet.")
    typer.echo(f"  Schema:   {result.schema_version}  Observer: {result.observer_version}")
    typer.echo(f"  Computed: {result.computed_at}")


# ── baseline ──────────────────────────────────────────────────────────────────


@learn_app.command("baseline")
def learn_baseline(
    channel_id: Annotated[str, typer.Option("--channel", help="Channel ID.")],
    metric: Annotated[str | None, typer.Option("--metric", help="Filter by metric name.")] = None,
) -> None:
    """Show channel performance baselines.

    Baselines are populated by 'ace learn cross-pub'.
    Results are associations derived from existing publications — not causal claims.
    """
    from app.learning.cross_publication import get_channel_baselines

    conn = _get_db()
    try:
        baselines = get_channel_baselines(conn, channel_id=channel_id, metric_name=metric)
    finally:
        conn.close()

    if not baselines:
        typer.echo(
            f"No baselines found for channel={channel_id}.  "
            "Run 'ace learn cross-pub --channel ...' first."
        )
        return

    typer.echo(f"\nChannel performance baselines  channel={channel_id}")
    typer.echo("─" * 72)
    typer.echo(f"{'Metric':<26}  {'n':>4}  {'Mean':>10}  {'Median':>10}  {'StdDev':>9}  Maturity")
    typer.echo("─" * 72)
    for b in baselines:
        mean_s = f"{b.mean:.4f}" if b.mean is not None else "—"
        med_s = f"{b.median:.4f}" if b.median is not None else "—"
        std_s = f"{b.std_dev:.4f}" if b.std_dev is not None else "—"
        typer.echo(
            f"{b.metric_name:<26}  {b.publication_count:>4}  "
            f"{mean_s:>10}  {med_s:>10}  {std_s:>9}  {b.sample_maturity}"
        )
    typer.echo()


# ── compare ───────────────────────────────────────────────────────────────────


@learn_app.command("compare")
def learn_compare(
    channel_id: Annotated[str, typer.Option("--channel", help="Channel ID.")],
    feature: Annotated[
        str | None, typer.Option("--feature", help="Filter by feature name.")
    ] = None,
    metric: Annotated[
        str | None, typer.Option("--metric", help="Filter by outcome metric.")
    ] = None,
    min_n: Annotated[
        int, typer.Option("--min-n", help="Minimum publication count to display.")
    ] = 1,
) -> None:
    """Show feature performance associations for a channel.

    Observations are computed by 'ace learn cross-pub'.
    These are ASSOCIATIONS only — not causal conclusions.
    Column 'rel%' is relative difference from channel baseline (blank if n/a).
    """
    from app.learning.cross_publication import get_feature_observations

    conn = _get_db()
    try:
        obs = get_feature_observations(
            conn, channel_id=channel_id, feature_name=feature, metric_name=metric
        )
    finally:
        conn.close()

    obs = [o for o in obs if o.publication_count >= min_n]

    if not obs:
        typer.echo(
            f"No observations found for channel={channel_id}.  "
            "Run 'ace learn cross-pub --channel ...' first."
        )
        return

    typer.echo(f"\nFeature performance associations  channel={channel_id}")
    typer.echo("Note: these are associations, not causal effects.")
    typer.echo("─" * 90)
    typer.echo(
        f"{'Feature':<26}  {'Bucket':<14}  {'Metric':<22}  {'n':>3}  "
        f"{'Mean':>9}  {'rel%':>7}  Maturity"
    )
    typer.echo("─" * 90)
    for o in obs:
        mean_s = f"{o.mean:.4f}" if o.mean is not None else "—"
        rel_s = (
            f"{o.rel_diff_from_baseline * 100:+.1f}%"
            if o.rel_diff_from_baseline is not None
            else ""
        )
        typer.echo(
            f"{o.feature_name:<26}  {o.feature_bucket:<14}  {o.metric_name:<22}  "
            f"{o.publication_count:>3}  {mean_s:>9}  {rel_s:>7}  {o.sample_maturity}"
        )
    typer.echo()


# ── coverage ──────────────────────────────────────────────────────────────────


@learn_app.command("coverage")
def learn_coverage(
    channel_id: Annotated[str, typer.Option("--channel", help="Channel ID.")],
    feature: Annotated[
        str | None, typer.Option("--feature", help="Filter by feature name.")
    ] = None,
) -> None:
    """Show exploration coverage — which feature values have been tested.

    Coverage is populated by 'ace learn cross-pub'.
    Untested feature values (absent here) are candidates for Phase 14 exploration.
    """
    from app.learning.cross_publication import get_exploration_coverage

    conn = _get_db()
    try:
        coverage = get_exploration_coverage(conn, channel_id=channel_id, feature_name=feature)
    finally:
        conn.close()

    if not coverage:
        typer.echo(
            f"No coverage data found for channel={channel_id}.  "
            "Run 'ace learn cross-pub --channel ...' first."
        )
        return

    typer.echo(f"\nExploration coverage  channel={channel_id}")
    typer.echo("─" * 60)
    for feat_name in sorted(coverage):
        buckets = coverage[feat_name]
        typer.echo(f"\n  {feat_name}")
        for bkt in sorted(buckets):
            info = buckets[bkt]
            pub_ids = info["source_publication_ids"]
            ids_str = ",".join(str(i) for i in sorted(pub_ids[:5]))
            if len(pub_ids) > 5:
                ids_str += "…"
            typer.echo(
                f"    {bkt:<20}  n={info['publication_count']:>3}  "
                f"({info['sample_maturity']})  pubs=[{ids_str}]"
            )
    typer.echo()
