"""Market Intelligence CLI commands — inspection, job management, and collection."""

from __future__ import annotations

import typer

market_app = typer.Typer(name="market", help="Market intelligence — observation & job management.")

explore_app = typer.Typer(name="explore", help="Autonomous market exploration planner.")
market_app.add_typer(explore_app)

interpret_app = typer.Typer(
    name="interpret", help="Market interpretation — topic clustering and signal scoring."
)
market_app.add_typer(interpret_app)

opportunities_app = typer.Typer(
    name="opportunities", help="Market-to-opportunity bridge (Phase 13F)."
)
market_app.add_typer(opportunities_app)


def _get_db():
    import os
    from pathlib import Path

    from app.core.database import open_db

    db_path = os.environ.get("ACE_DB_PATH")
    if db_path:
        return open_db(Path(db_path))
    from app.core.config import get_config

    return open_db(get_config().db_path)


@market_app.command("jobs")
def list_jobs(
    channel: int | None = typer.Option(None, "--channel", "-c", help="Filter by channel ID."),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Filter by workspace ID."),
    status: str | None = typer.Option(None, "--status", "-s", help="Filter by status."),
    limit: int = typer.Option(25, "--limit", "-n", help="Max rows to return."),
) -> None:
    """List market collection jobs."""
    from app.intelligence.market.repository import list_market_collection_jobs

    conn = _get_db()
    jobs = list_market_collection_jobs(
        conn,
        channel_id=channel,
        workspace_id=workspace,
        status=status,
        limit=limit,
    )
    if not jobs:
        typer.echo("No jobs found.")
        return
    for j in jobs:
        typer.echo(
            f"[{j.id:>5}] {j.status:<10} {j.job_type:<18} {j.origin_type:<20} "
            f"obs={j.observation_count:<5} quota={j.quota_consumed_total:<6} {j.created_at}"
        )


@market_app.command("job-show")
def show_job(job_id: int = typer.Argument(..., help="Job ID to inspect.")) -> None:
    """Show a market collection job in detail."""
    from app.intelligence.market.repository import (
        get_job_observations,
        get_job_quota_usage,
        get_market_collection_job,
    )

    conn = _get_db()
    job = get_market_collection_job(conn, job_id)
    if job is None:
        typer.echo(f"Job {job_id} not found.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Job #{job.id}")
    typer.echo(f"  type         : {job.job_type}")
    typer.echo(f"  origin       : {job.origin_type}")
    typer.echo(f"  status       : {job.status}")
    typer.echo(f"  provider     : {job.provider} / {job.platform}")
    typer.echo(f"  workspace    : {job.workspace_id or '—'}")
    typer.echo(f"  channel_id   : {job.channel_id or '—'}")
    typer.echo(f"  parent_job   : {job.parent_job_id or '—'}")
    typer.echo(f"  depth        : {job.exploration_depth}")
    typer.echo(f"  seeds        : {job.seeds()}")
    typer.echo(f"  observations : {job.observation_count}")
    typer.echo(f"  quota_total  : {job.quota_consumed_total}")
    typer.echo(f"  started_at   : {job.started_at or '—'}")
    typer.echo(f"  completed_at : {job.completed_at or '—'}")
    typer.echo(f"  created_at   : {job.created_at}")
    if job.error_message:
        typer.echo(f"  error        : {job.error_message}")
    if job.failure_stage:
        typer.echo(f"  stage        : {job.failure_stage}")

    quota_rows = get_job_quota_usage(conn, job_id)
    if quota_rows:
        typer.echo("\nQuota usage:")
        for q in quota_rows:
            limit_str = f" / {q.limit_snapshot}" if q.limit_snapshot else ""
            typer.echo(
                f"  {q.provider} {q.operation:<22} bucket={q.quota_bucket:<10} "
                f"units={q.units_consumed}{limit_str}  calls={q.call_count}"
            )

    obs = get_job_observations(conn, job_id)
    typer.echo(f"\nObservations linked: {len(obs)}")
    for o in obs[:10]:
        typer.echo(
            f"  [{o.id:>6}] {o.signal_type:<35} "
            f"vid={o.external_video_id or '—':<15} val={o.signal_value_numeric}"
        )
    if len(obs) > 10:
        typer.echo(f"  ... and {len(obs) - 10} more")


@market_app.command("observations")
def list_observations(
    video: str | None = typer.Option(None, "--video", "-v", help="External video ID."),
    query: str | None = typer.Option(None, "--query", "-q", help="Normalized query."),
    signal_type: str | None = typer.Option(None, "--signal-type", "-s", help="Signal type filter."),
    limit: int = typer.Option(25, "--limit", "-n", help="Max rows."),
) -> None:
    """List market intelligence observations."""
    from app.intelligence.market.repository import (
        list_observations_for_query,
        list_observations_for_video,
    )

    conn = _get_db()
    if video:
        obs = list_observations_for_video(conn, video, signal_type=signal_type, limit=limit)
    elif query:
        obs = list_observations_for_query(conn, query, signal_type=signal_type, limit=limit)
    else:
        rows = conn.execute(
            "SELECT * FROM market_intelligence_observations ORDER BY observed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        from app.intelligence.market.repository import _row_to_observation

        obs = [_row_to_observation(r) for r in rows]

    if not obs:
        typer.echo("No observations found.")
        return
    for o in obs:
        typer.echo(
            f"[{o.id:>6}] {o.signal_type:<35} "
            f"vid={o.external_video_id or '—':<15} "
            f"val={o.signal_value_numeric!s:<12} {o.observed_at}"
        )


@market_app.command("observation-show")
def show_observation(obs_id: int = typer.Argument(..., help="Observation ID.")) -> None:
    """Show a single market intelligence observation in detail."""
    from app.intelligence.market.repository import get_observation

    conn = _get_db()
    obs = get_observation(conn, obs_id)
    if obs is None:
        typer.echo(f"Observation {obs_id} not found.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Observation #{obs.id}")
    typer.echo(f"  platform         : {obs.platform}")
    typer.echo(f"  provider         : {obs.provider}")
    typer.echo(f"  collector        : {obs.collector_name}")
    typer.echo(f"  signal_type      : {obs.signal_type}")
    typer.echo(f"  value_numeric    : {obs.signal_value_numeric}")
    typer.echo(f"  value_text       : {obs.signal_value_text or '—'}")
    typer.echo(f"  external_video   : {obs.external_video_id or '—'}")
    typer.echo(f"  external_channel : {obs.external_channel_id or '—'}")
    typer.echo(f"  query            : {obs.query_text or '—'}")
    typer.echo(f"  normalized_query : {obs.normalized_query or '—'}")
    typer.echo(f"  region           : {obs.region_code or '—'}")
    typer.echo(f"  language         : {obs.language_code or '—'}")
    typer.echo(f"  content_pub_at   : {obs.content_published_at or '—'}")
    typer.echo(f"  content_age_days : {obs.content_age_days}")
    typer.echo(f"  observed_at      : {obs.observed_at}")
    typer.echo(f"  retain_until     : {obs.retain_until or '—'}")
    typer.echo(f"  input_hash       : {obs.input_hash}")
    typer.echo(f"  fingerprint      : {obs.provider_payload_fingerprint or '—'}")
    typer.echo(f"  created_at       : {obs.created_at}")


@market_app.command("job-create")
def create_job(
    job_type: str = typer.Option("search_scan", "--job-type", "-t", help="Job type."),
    channel: int | None = typer.Option(None, "--channel", "-c", help="Channel ID."),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace ID."),
    seeds: str | None = typer.Option(None, "--seeds", help="Comma-separated seed topics."),
    origin: str = typer.Option("manual", "--origin", help="Origin type."),
) -> None:
    """Create a market collection job (no live API calls)."""
    from app.intelligence.market.repository import create_market_collection_job

    seed_list = [s.strip() for s in seeds.split(",") if s.strip()] if seeds else []
    conn = _get_db()
    job = create_market_collection_job(
        conn,
        job_type=job_type,
        origin_type=origin,
        channel_id=channel,
        workspace_id=workspace,
        seeds=seed_list,
    )
    typer.echo(f"Created job #{job.id} ({job.job_type}, status={job.status})")


def execute_search_scan(
    conn,
    query: str,
    *,
    channel_id: int | None = None,
    workspace_id: str | None = None,
    region: str | None = None,
    language: str | None = None,
    published_after: str | None = None,
    order: str = "relevance",
    max_results: int = 25,
    max_pages: int = 1,
    api_key: str = "",
    collector=None,
) -> None:
    """Create a search_scan job and run it.  Accepts an injectable collector for tests."""
    from app.intelligence.market.collector import YouTubeMarketCollector
    from app.intelligence.market.repository import create_market_collection_job

    job = create_market_collection_job(
        conn,
        job_type="search_scan",
        origin_type="manual",
        channel_id=channel_id,
        workspace_id=workspace_id,
        seeds=[query],
    )
    typer.echo(f"Job #{job.id} created (pending)")

    coll = collector or YouTubeMarketCollector(api_key=api_key)
    try:
        result = coll.collect_search_scan(
            conn,
            job,
            query=query,
            region_code=region,
            language_code=language,
            published_after=published_after,
            order=order,
            max_results=max_results,
            max_pages=max_pages,
        )
    finally:
        if collector is None:
            coll.close()

    typer.echo(f"Job #{result.job_id} → {result.status}")
    typer.echo(f"  search calls     : {result.search_calls}")
    typer.echo(f"  enrichment calls : {result.enrichment_calls}")
    typer.echo(f"  observations new : {result.observations_new}")
    typer.echo(f"  observations used: {result.observations_reused}")
    if result.partial_failures:
        typer.echo(f"  partial failures : {len(result.partial_failures)}")
        for pf in result.partial_failures[:5]:
            typer.echo(f"    {pf}")


@market_app.command("rescan")
def rescan(
    max_videos: int = typer.Option(50, "--max-videos", help="Max videos to refresh per run."),
    min_age_hours: float = typer.Option(
        6.0, "--min-age-hours", help="Min hours since last observation."
    ),
    max_tracking_age_days: int = typer.Option(
        365, "--max-tracking-age-days", help="Skip videos older than this."
    ),
    max_batch_calls: int | None = typer.Option(
        None, "--max-batch-calls", help="Limit provider batch calls."
    ),
    channel: int | None = typer.Option(None, "--channel", help="Associate job with channel ID."),
    workspace: str | None = typer.Option(None, "--workspace", help="Associate job with workspace."),
    api_key: str = typer.Option(
        "", "--api-key", envvar="ACE_YOUTUBE_API_KEY", help="YouTube Data API key."
    ),
) -> None:
    """Refresh statistics for known videos and compute view velocity intervals.

    No search.list calls. Only previously-observed video IDs are refreshed.
    No Opportunities created. No scoring changes.
    """
    from app.core.config import get_config
    from app.intelligence.market.collector import YouTubeMarketCollector
    from app.intelligence.market.repository import create_market_collection_job

    if not api_key:
        api_key = get_config().youtube_data_api_key
    if not api_key:
        typer.echo(
            "Error: YouTube Data API key required. Set ACE_YOUTUBE_API_KEY or pass --api-key.",
            err=True,
        )
        raise typer.Exit(1)

    conn = _get_db()
    job = create_market_collection_job(
        conn,
        job_type="velocity_rescan",
        origin_type="velocity_rescan",
        channel_id=channel,
        workspace_id=workspace,
    )
    typer.echo(f"Job #{job.id} created (velocity_rescan, pending)")

    coll = YouTubeMarketCollector(api_key=api_key)
    try:
        result = coll.collect_velocity_rescan(
            conn,
            job,
            max_videos=max_videos,
            min_age_seconds=int(min_age_hours * 3600),
            max_tracking_age_days=max_tracking_age_days,
            max_batch_calls=max_batch_calls,
        )
    finally:
        coll.close()

    typer.echo(f"Job #{result.job_id} → {result.status}")
    typer.echo(f"  enrichment calls : {result.enrichment_calls}")
    typer.echo(f"  observations new : {result.observations_new}")
    if result.partial_failures:
        typer.echo(f"  partial failures : {len(result.partial_failures)}")
        for pf in result.partial_failures[:5]:
            typer.echo(f"    {pf}")


@market_app.command("velocity")
def show_velocity(
    video: str = typer.Option(..., "--video", "-v", help="External video ID."),
    signal_type: str = typer.Option("video_view_count", "--signal-type", "-s", help="Signal type."),
    limit: int = typer.Option(10, "--limit", "-n", help="Max intervals to show."),
) -> None:
    """Show view velocity history for a specific video.

    Displays observation timestamps, view counts, raw delta, interval length,
    views/day, maturity, and anomaly status.
    """
    from app.intelligence.market.repository import (
        get_velocity_estimates_for_video,
        get_video_observation_history,
    )

    conn = _get_db()
    history = get_video_observation_history(conn, video, signal_type=signal_type)
    typer.echo(f"Observation history for video '{video}' (signal: {signal_type}):")
    typer.echo(f"  {len(history)} observation(s) found")
    for obs in history[:5]:
        typer.echo(f"  [{obs.id:>6}] {obs.observed_at}  val={obs.signal_value_numeric}")
    if len(history) > 5:
        typer.echo(f"  ... and {len(history) - 5} earlier")

    estimates = get_velocity_estimates_for_video(conn, video, signal_type=signal_type, limit=limit)
    if not estimates:
        typer.echo("\nVelocity: UNAVAILABLE (need ≥ 2 observations with ≥ 6h gap)")
        return

    typer.echo(f"\nVelocity intervals ({len(estimates)} shown, most recent first):")
    for e in estimates:
        anomaly = " [ANOMALY: count decreased]" if e.is_negative_delta else ""
        typer.echo(f"  [{e.id:>5}] {e.start_time} → {e.end_time}")
        elapsed_h = e.elapsed_seconds / 3600
        views_day = f"{e.units_per_day:+.0f}" if e.units_per_day is not None else "—"
        typer.echo(
            f"         Δ={e.raw_delta:+.0f} views  elapsed={elapsed_h:.1f}h"
            f"  views/day={views_day}  maturity={e.velocity_maturity}{anomaly}"
        )


@market_app.command("scan")
def scan(
    query: str = typer.Option(..., "--query", "-q", help="Search query."),
    region: str | None = typer.Option(None, "--region", help="Region code (e.g. US)."),
    language: str | None = typer.Option(None, "--language", help="Relevance language (e.g. en)."),
    published_after: str | None = typer.Option(
        None, "--published-after", help="ISO 8601 datetime."
    ),
    order: str = typer.Option(
        "relevance", "--order", help="Sort order (relevance|date|viewCount|rating)."
    ),
    max_results: int = typer.Option(25, "--max-results", help="Results per page (max 50)."),
    max_pages: int = typer.Option(1, "--max-pages", help="Maximum search pages to fetch."),
    channel: int | None = typer.Option(None, "--channel", help="Channel ID to associate with job."),
    workspace: str | None = typer.Option(None, "--workspace", help="Workspace ID."),
    api_key: str = typer.Option(
        "", "--api-key", envvar="ACE_YOUTUBE_API_KEY", help="YouTube Data API key."
    ),
) -> None:
    """Discover YouTube videos for a search query and persist market observations.

    No Opportunities are created. No scoring changes. Public data only.
    """
    if not api_key:
        from app.core.config import get_config

        api_key = get_config().youtube_data_api_key
    if not api_key:
        typer.echo(
            "Error: YouTube Data API key required. Set ACE_YOUTUBE_API_KEY or pass --api-key.",
            err=True,
        )
        raise typer.Exit(1)

    conn = _get_db()
    execute_search_scan(
        conn,
        query,
        channel_id=channel,
        workspace_id=workspace,
        region=region,
        language=language,
        published_after=published_after,
        order=order,
        max_results=max_results,
        max_pages=max_pages,
        api_key=api_key,
    )


# ---------------------------------------------------------------------------
# Exploration planner — read-only inspection commands (Phase 13D-A)
# ---------------------------------------------------------------------------


@explore_app.command("runs")
def list_runs(
    channel: int | None = typer.Option(None, "--channel", "-c", help="Filter by channel ID."),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Filter by workspace ID."),
    status: str | None = typer.Option(None, "--status", "-s", help="Filter by status."),
    limit: int = typer.Option(25, "--limit", "-n", help="Max rows to return."),
) -> None:
    """List market exploration runs."""
    from app.intelligence.market.planner_repository import list_exploration_runs

    conn = _get_db()
    runs = list_exploration_runs(
        conn,
        channel_id=channel,
        workspace_id=workspace,
        status=status,
        limit=limit,
    )
    if not runs:
        typer.echo("No exploration runs found.")
        return
    for r in runs:
        typer.echo(
            f"[{r.id:>5}] {r.status:<10} planner={r.planner_version}  "
            f"depth={r.max_depth}  probes={r.max_probes}  "
            f"selected={r.selected_count}  dispatched={r.dispatched_count}  "
            f"{r.created_at}"
        )


@explore_app.command("run-show")
def show_run(run_id: int = typer.Argument(..., help="Exploration run ID.")) -> None:
    """Show a market exploration run in detail."""
    from app.intelligence.market.planner_repository import (
        get_exploration_run,
        list_exploration_probes,
    )

    conn = _get_db()
    run = get_exploration_run(conn, run_id)
    if run is None:
        typer.echo(f"Exploration run {run_id} not found.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Exploration Run #{run.id}")
    typer.echo(f"  status          : {run.status}")
    typer.echo(f"  planner_version : {run.planner_version}")
    typer.echo(f"  channel_id      : {run.channel_id or '—'}")
    typer.echo(f"  workspace_id    : {run.workspace_id or '—'}")
    typer.echo(f"  max_depth       : {run.max_depth}")
    typer.echo(f"  max_probes      : {run.max_probes}")
    typer.echo(f"  search_budget   : {run.search_budget}")
    typer.echo(f"  candidate_count : {run.candidate_count}")
    typer.echo(f"  selected_count  : {run.selected_count}")
    typer.echo(f"  deferred_count  : {run.deferred_count}")
    typer.echo(f"  rejected_count  : {run.rejected_count}")
    typer.echo(f"  dispatched_count: {run.dispatched_count}")
    typer.echo(f"  started_at      : {run.started_at or '—'}")
    typer.echo(f"  completed_at    : {run.completed_at or '—'}")
    typer.echo(f"  created_at      : {run.created_at}")
    if run.error_message:
        typer.echo(f"  error           : {run.error_message}")

    probes = list_exploration_probes(conn, run_id=run_id, limit=20)
    probe_total = (
        run.candidate_count
        + run.selected_count
        + run.deferred_count
        + run.rejected_count
        + run.dispatched_count
    )
    typer.echo(f"\nProbes (up to 20 of {probe_total} total):")
    for p in probes:
        typer.echo(
            f"  [{p.id:>5}] {p.status:<10} {p.probe_type:<20} depth={p.exploration_depth}  "
            f"q={p.query_text[:50]!r}"
        )


@explore_app.command("probes")
def list_probes(
    run_id: int = typer.Option(..., "--run", "-r", help="Exploration run ID."),
    status: str | None = typer.Option(None, "--status", "-s", help="Filter by status."),
    limit: int = typer.Option(50, "--limit", "-n", help="Max rows to return."),
) -> None:
    """List probes for an exploration run."""
    from app.intelligence.market.planner_repository import list_exploration_probes

    conn = _get_db()
    probes = list_exploration_probes(conn, run_id=run_id, status=status, limit=limit)
    if not probes:
        typer.echo("No probes found.")
        return
    for p in probes:
        score_str = f"{p.priority_score:.3f}" if p.priority_score is not None else "—"
        typer.echo(
            f"[{p.id:>5}] {p.status:<10} {p.probe_type:<20} "
            f"depth={p.exploration_depth}  score={score_str}  "
            f"corr={p.corroboration_count}  q={p.query_text[:40]!r}"
        )


@explore_app.command("probe-show")
def show_probe(probe_id: int = typer.Argument(..., help="Probe ID.")) -> None:
    """Show a single exploration probe in detail."""
    from app.intelligence.market.planner_repository import (
        get_exploration_probe,
        get_probe_evidence,
    )

    conn = _get_db()
    probe = get_exploration_probe(conn, probe_id)
    if probe is None:
        typer.echo(f"Probe {probe_id} not found.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Exploration Probe #{probe.id}")
    typer.echo(f"  run_id          : {probe.exploration_run_id}")
    typer.echo(f"  status          : {probe.status}")
    typer.echo(f"  probe_type      : {probe.probe_type}")
    typer.echo(f"  query_text      : {probe.query_text}")
    typer.echo(f"  normalized_query: {probe.normalized_query}")
    typer.echo(f"  depth           : {probe.exploration_depth}")
    typer.echo(f"  parent_probe    : {probe.parent_probe_id or '—'}")
    typer.echo(f"  parent_job      : {probe.parent_job_id or '—'}")
    typer.echo(f"  region          : {probe.region_code or '—'}")
    typer.echo(f"  language        : {probe.language_code or '—'}")
    typer.echo(f"  priority_score  : {probe.priority_score}")
    typer.echo(f"  niche_fit_score : {probe.niche_fit_score}")
    typer.echo(f"  semantic_status : {probe.semantic_fit_status or '—'}")
    typer.echo(f"  corroboration   : {probe.corroboration_count}")
    typer.echo(f"  dispatched_job  : {probe.dispatched_job_id or '—'}")
    decision_display = probe.decision_reason or "—"
    typer.echo(f"  decision_reason : {decision_display}")
    if probe.decision_reason and probe.decision_reason.startswith("semantic_duplicate_of:"):
        canonical_id = probe.decision_reason.split(":", 1)[1]
        typer.echo(f"  canonical_probe : #{canonical_id}")
    typer.echo(f"  decided_at      : {probe.decided_at or '—'}")
    typer.echo(f"  dispatched_at   : {probe.dispatched_at or '—'}")
    typer.echo(f"  created_at      : {probe.created_at}")
    typer.echo(f"  input_hash      : {probe.input_hash}")

    policy = probe.collection_policy()
    typer.echo("\nCollection Policy:")
    typer.echo(f"  max_pages       : {policy.max_pages}")
    typer.echo(f"  max_results     : {policy.max_results}")
    typer.echo(f"  order           : {policy.order}")
    typer.echo(f"  expected_calls  : {policy.expected_max_search_calls}")

    evidence = get_probe_evidence(conn, probe_id)
    typer.echo(f"\nEvidence links: {len(evidence)}")
    for e in evidence:
        typer.echo(
            f"  [{e.id:>5}] {e.evidence_type:<12} "
            f"obs={e.observation_id or '—'}  vel={e.velocity_id or '—'}  "
            f"job={e.job_id or '—'}  {e.created_at}"
        )


# ---------------------------------------------------------------------------
# Exploration planner — cold-start bootstrap (Phase 13D-B)
# ---------------------------------------------------------------------------


@explore_app.command("plan")
def plan(
    channel: int = typer.Option(..., "--channel", "-c", help="Channel ID to plan for."),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace ID."),
    max_probes: int = typer.Option(10, "--max-probes", help="Maximum probes to select."),
    max_depth: int = typer.Option(1, "--max-depth", help="Maximum exploration depth."),
    search_budget: int = typer.Option(
        15, "--search-budget", help="Maximum search calls across all probes."
    ),
    planner_version: str = typer.Option("v1", "--planner-version", help="Planner version tag."),
    model: str = typer.Option(
        "claude-haiku-4-5-20251001", "--model", help="Claude model ID for seed expansion."
    ),
    force: bool = typer.Option(
        False, "--force", help="Run even if channel is not in cold-start state."
    ),
) -> None:
    """Plan market exploration probes for a channel using cold-start bootstrap.

    Direct profile seeds (channel_bootstrap) are created from the channel's
    primary and secondary niches. A single LLM call proposes additional broad
    market_region probes. All candidates are persisted with their disposition.

    No YouTube calls. No Opportunity creation. No scoring changes.
    """
    from app.ai.claude import ClaudeProvider
    from app.intelligence.market.cold_start import (
        ExplorationProfile,
        InsufficientProfileError,
        is_channel_cold_start,
        plan_cold_start,
    )
    from app.intelligence.repository import get_active_profile_version

    conn = _get_db()

    if not force and not is_channel_cold_start(conn, channel):
        typer.echo(
            f"Channel {channel} is not in cold-start state "
            f"(already has enough dispatched probes). "
            f"Use --force to override.",
            err=True,
        )
        raise typer.Exit(1)

    profile_version = get_active_profile_version(conn, channel)
    if profile_version is None:
        typer.echo(
            f"No active channel profile found for channel {channel}. Create a profile first.",
            err=True,
        )
        raise typer.Exit(1)

    profile = ExplorationProfile(
        primary_niche=profile_version.primary_niche or "",
        secondary_niches=list(profile_version.secondary_niches or []),
        excluded_topics=list(profile_version.excluded_topics or []),
        audience_description=profile_version.audience_description or "",
        duplicate_similarity_threshold=profile_version.duplicate_similarity_threshold or 0.70,
    )

    from app.core.config import get_config

    config = get_config()
    api_key = config.anthropic_api_key if hasattr(config, "anthropic_api_key") else ""

    provider = ClaudeProvider(model=model, api_key=api_key)

    try:
        result = plan_cold_start(
            conn,
            profile,
            provider=provider,
            channel_id=channel,
            workspace_id=workspace,
            max_probes=max_probes,
            max_depth=max_depth,
            search_budget=search_budget,
            planner_version=planner_version,
        )
    except InsufficientProfileError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    typer.echo(f"Cold-start plan complete — run #{result.exploration_run_id}")
    typer.echo(f"  status          : {result.diagnostics.get('final_status', '—')}")
    typer.echo(f"  candidates      : {result.candidate_count}")
    typer.echo(f"  selected        : {result.selected_count}")
    typer.echo(f"  deferred        : {result.deferred_count}")
    typer.echo(f"  rejected        : {result.rejected_count}")
    typer.echo(f"  search calls    : {result.diagnostics.get('search_calls_used', '—')}")
    typer.echo(f"  llm_used        : {result.llm_used}")
    if result.provider_name:
        typer.echo(f"  provider        : {result.provider_name}")
    if result.model:
        typer.echo(f"  model           : {result.model}")
    if result.diagnostics.get("llm_error"):
        typer.echo(f"  llm_error       : {result.diagnostics['llm_error']}", err=True)
    typer.echo(f"  selected probes : {result.selected_probe_ids}")


# ---------------------------------------------------------------------------
# Exploration planner — niche guard + authoritative selection (Phase 13D-D)
# ---------------------------------------------------------------------------


@explore_app.command("select")
def select_probes(
    run: int = typer.Option(..., "--run", "-r", help="Exploration run ID to select probes from."),
    primary_niche: str = typer.Option("", "--primary-niche", help="Channel primary niche."),
    excluded_topics: str = typer.Option(
        "", "--excluded-topics", help="Comma-separated excluded topics."
    ),
    prior_queries: str = typer.Option(
        "", "--prior-queries", help="Comma-separated prior normalized queries."
    ),
    max_probes: int = typer.Option(
        10, "--max-probes", help="Selection budget (max probes to select)."
    ),
    channel: int | None = typer.Option(
        None, "--channel", "-c", help="Channel ID (for provenance)."
    ),
    model: str = typer.Option(
        "claude-haiku-4-5-20251001", "--model", help="Claude model for niche guard."
    ),
    no_llm: bool = typer.Option(
        False, "--no-llm", help="Skip LLM; all eligible probes get status=pending."
    ),
) -> None:
    """Evaluate CANDIDATE probes in a run through the semantic niche guard and select.

    Layer 1 (deterministic): probes Jaccard-similar to excluded_topics → REJECTED.
    Layer 2 (LLM batch):     remaining probes evaluated by niche-guard prompt →
                             REJECTED or eligible.
    Priority scoring:        applicable-component weighted average using selector V1 weights.
    Diversity guards:        cluster cap + exploration/evidence portfolio balance.

    No YouTube calls. No Opportunity creation. No scoring mutations outside this run.
    """
    from app.intelligence.market.selector import run_niche_selection

    excl = [t.strip() for t in excluded_topics.split(",") if t.strip()] if excluded_topics else []
    prior = {q.strip() for q in prior_queries.split(",") if q.strip()} if prior_queries else set()

    ai_provider = None
    if not no_llm:
        from app.ai.claude import ClaudeProvider
        from app.core.config import get_config

        config = get_config()
        api_key = config.anthropic_api_key if hasattr(config, "anthropic_api_key") else ""
        ai_provider = ClaudeProvider(model=model, api_key=api_key)

    conn = _get_db()
    result = run_niche_selection(
        conn,
        run,
        primary_niche=primary_niche,
        excluded_topics=excl,
        prior_queries=prior,
        max_probes=max_probes,
        channel_id=channel,
        ai_provider=ai_provider,
    )

    typer.echo(f"Niche selection complete — run #{result.run_id}")
    typer.echo(f"  policy_version  : {result.policy_version}")
    typer.echo(f"  selected        : {len(result.selected)}  {result.selected}")
    typer.echo(f"  deferred        : {len(result.deferred)}  {result.deferred}")
    typer.echo(f"  rejected        : {len(result.rejected)}  {result.rejected}")
    if result.llm_provider:
        typer.echo(f"  llm_provider    : {result.llm_provider}")
    if result.llm_model:
        typer.echo(f"  llm_model       : {result.llm_model}")
    if result.llm_error:
        typer.echo(f"  llm_error       : {result.llm_error}", err=True)


# ---------------------------------------------------------------------------
# Exploration planner — adjacent concept expansion (Phase 13D-C)
# ---------------------------------------------------------------------------


@explore_app.command("expand")
def expand(
    probe: int = typer.Option(..., "--probe", "-p", help="Parent probe ID to expand from."),
    channel: int | None = typer.Option(None, "--channel", "-c", help="Channel ID."),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace ID."),
    max_probes: int = typer.Option(8, "--max-probes", help="Maximum adjacent probes to select."),
    max_depth: int = typer.Option(3, "--max-depth", help="Maximum exploration depth allowed."),
    search_budget: int = typer.Option(
        15, "--search-budget", help="Maximum search calls across all probes."
    ),
    primary_niche: str = typer.Option(
        "", "--primary-niche", help="Channel primary niche for context."
    ),
    excluded_topics: str = typer.Option(
        "", "--excluded-topics", help="Comma-separated excluded topics."
    ),
    planner_version: str = typer.Option("v1", "--planner-version", help="Planner version tag."),
    model: str = typer.Option("claude-haiku-4-5-20251001", "--model", help="Claude model ID."),
) -> None:
    """Expand a dispatched probe into adjacent concept probes.

    Reads collected evidence from the parent probe's job and makes a single
    LLM call to propose adjacent market regions. All candidates are persisted.

    No YouTube calls. No Opportunity creation. No scoring changes.
    """
    from app.ai.claude import ClaudeProvider
    from app.intelligence.market.adjacent import plan_adjacent_expansion

    conn = _get_db()
    from app.core.config import get_config

    config = get_config()
    api_key = config.anthropic_api_key if hasattr(config, "anthropic_api_key") else ""

    provider = ClaudeProvider(model=model, api_key=api_key)
    excl = [t.strip() for t in excluded_topics.split(",") if t.strip()] if excluded_topics else []

    result = plan_adjacent_expansion(
        conn,
        provider=provider,
        parent_probe_id=probe,
        excluded_topics=excl,
        primary_niche=primary_niche,
        channel_id=channel,
        workspace_id=workspace,
        max_probes=max_probes,
        max_depth=max_depth,
        search_budget=search_budget,
        planner_version=planner_version,
    )

    if result.exploration_run_id == -1:
        from app.intelligence.market.planner_prompts import (
            ADJACENT_MIN_VIDEOS_FOR_EXPANSION,
        )

        typer.echo(
            f"Probe #{probe} has insufficient evidence for adjacent expansion "
            f"(video_count={result.diagnostics.get('video_count', 0)}, "
            f"min={ADJACENT_MIN_VIDEOS_FOR_EXPANSION})."
        )
        return

    typer.echo(f"Adjacent expansion complete — run #{result.exploration_run_id}")
    typer.echo(f"  parent probe    : #{probe}")
    typer.echo(f"  maturity        : {result.maturity}")
    typer.echo(f"  status          : {result.diagnostics.get('final_status', '—')}")
    typer.echo(f"  candidates      : {result.candidate_count}")
    typer.echo(f"  selected        : {result.selected_count}")
    typer.echo(f"  deferred        : {result.deferred_count}")
    typer.echo(f"  rejected        : {result.rejected_count}")
    typer.echo(f"  search calls    : {result.diagnostics.get('search_calls_used', '—')}")
    typer.echo(f"  llm_used        : {result.llm_used}")
    if result.provider_name:
        typer.echo(f"  provider        : {result.provider_name}")
    if result.model:
        typer.echo(f"  model           : {result.model}")
    if result.diagnostics.get("llm_error"):
        typer.echo(f"  llm_error       : {result.diagnostics['llm_error']}", err=True)
    typer.echo(f"  selected probes : {result.selected_probe_ids}")


# ---------------------------------------------------------------------------
# Phase 13D-E: Selected Probe Dispatch / Reuse Orchestration
# ---------------------------------------------------------------------------


@explore_app.command("dispatch")
def dispatch(
    run: int | None = typer.Option(
        None, "--run", "-r", help="Run ID whose SELECTED probes to dispatch."
    ),
    probe: int | None = typer.Option(
        None, "--probe", "-p", help="Single probe ID to dispatch (bypasses run selection)."
    ),
    max_search_calls: int | None = typer.Option(
        None,
        "--max-search-calls",
        help="Global search call budget across all probes. None = unlimited.",
    ),
    reuse_max_age_hours: float = typer.Option(
        12.0, "--reuse-max-age-hours", help="Max age (hours) for reusable search jobs."
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Evaluate reuse and costs without creating jobs or calling YouTube.",
    ),
    api_key: str | None = typer.Option(
        None, "--api-key", help="YouTube Data API key (overrides ACE_YOUTUBE_API_KEY env var)."
    ),
) -> None:
    """Dispatch SELECTED exploration probes to Phase 13B collection.

    Reuses fresh compatible search jobs within reuse_max_age_hours.
    Probes that hit the budget become DEFERRED. Failed probes stay
    SELECTED for safe retry. Never creates Opportunities or calls LLMs.

    REQUIRES EXPLICIT APPROVAL before running against live YouTube API.
    Use --dry-run to inspect costs and reuse without any network calls.
    """
    from app.core.config import get_config
    from app.intelligence.market.dispatcher import (
        dispatch_probe,
        dispatch_selected_probes,
    )

    if run is None and probe is None:
        typer.echo("Error: one of --run or --probe is required.", err=True)
        raise typer.Exit(1)

    conn = _get_db()
    config = get_config()
    effective_key = api_key or (
        config.youtube_data_api_key if hasattr(config, "youtube_data_api_key") else ""
    )

    if dry_run:
        typer.echo("[dry-run] No jobs will be created. No YouTube calls will be made.")

    if probe is not None:
        result = dispatch_probe(
            conn,
            probe,
            api_key=effective_key,
            reuse_max_age_hours=reuse_max_age_hours,
            dry_run=dry_run,
        )
    else:
        result = dispatch_selected_probes(
            conn,
            run,
            api_key=effective_key,
            max_search_calls=max_search_calls,
            reuse_max_age_hours=reuse_max_age_hours,
            dry_run=dry_run,
        )

    mode = "[dry-run] " if dry_run else ""
    typer.echo(f"{mode}Dispatch complete \u2014 run #{result.run_id}")
    typer.echo(f"  selected_considered : {result.selected_considered}")
    typer.echo(f"  newly_dispatched    : {result.newly_dispatched}")
    typer.echo(f"  reused              : {result.reused_count}")
    typer.echo(f"  failed              : {result.failed_count}")
    typer.echo(f"  deferred_for_budget : {result.deferred_for_budget}")
    typer.echo(f"  expected_search_calls : {result.expected_search_calls}")
    typer.echo(f"  actual_search_calls   : {result.actual_search_calls}")
    typer.echo(f"  search_calls_avoided  : {result.search_calls_avoided}")
    if result.dispatched_probe_ids:
        typer.echo(f"  dispatched_probes   : {result.dispatched_probe_ids}")
    if result.reused_probe_ids:
        typer.echo(f"  reused_probes       : {result.reused_probe_ids}")
    if result.failed_probe_ids:
        typer.echo(f"  failed_probes       : {result.failed_probe_ids}", err=True)
    if result.deferred_probe_ids:
        typer.echo(f"  deferred_probes     : {result.deferred_probe_ids}")
    if result.diagnostics:
        typer.echo("  diagnostics:")
        for d in result.diagnostics:
            status_suffix = f" [{d.job_status}]" if d.job_status else ""
            reuse_suffix = (
                f" (age={d.reuse_age_hours:.1f}h)" if d.reuse_age_hours is not None else ""
            )
            calls_str = f"search_calls={d.actual_search_calls}"
            typer.echo(
                f"    probe #{d.probe_id} ({d.probe_type}): "
                f"{d.action}{status_suffix}{reuse_suffix} {calls_str}"
            )
            if d.error:
                typer.echo(f"      error: {d.error}", err=True)


# ---------------------------------------------------------------------------
# Phase 13D-F: Bounded End-to-End Autonomous Market Exploration
# ---------------------------------------------------------------------------


@explore_app.command("run")
def run_exploration(
    channel: int = typer.Option(..., "--channel", "-c", help="Channel ID to explore."),
    max_rounds: int = typer.Option(
        2,
        "--max-rounds",
        help="Maximum exploration rounds (0=bootstrap only, 1=+adjacent, 2=full).",
    ),
    max_search_calls: int = typer.Option(
        10, "--max-search-calls", help="Maximum YouTube search.list calls across all rounds."
    ),
    max_llm_calls: int = typer.Option(
        8, "--max-llm-calls", help="Maximum AI provider calls across all rounds."
    ),
    max_probes: int = typer.Option(
        8, "--max-probes", help="Maximum candidate probes per cold-start round."
    ),
    max_depth: int = typer.Option(
        2, "--max-depth", help="Maximum exploration depth across all rounds."
    ),
    region: str | None = typer.Option(None, "--region", help="Optional region code (e.g. US)."),
    language: str | None = typer.Option(
        None, "--language", help="Optional language code (e.g. en)."
    ),
    model: str = typer.Option(
        "claude-haiku-4-5-20251001", "--model", help="Claude model ID for LLM calls."
    ),
    api_key: str | None = typer.Option(
        None, "--api-key", help="YouTube Data API key (overrides ACE_YOUTUBE_API_KEY)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Evaluate but make no mutations, LLM calls, or network calls."
    ),
) -> None:
    """Run a bounded autonomous market exploration cycle.

    Chains: cold-start planning → semantic selection → dispatch → adjacent
    expansion → second-round dispatch. Stops at hard limits.

    REQUIRES EXPLICIT APPROVAL before running live (calls Claude + YouTube).
    Use --dry-run to inspect the planned policy without execution.

    Expected quota consumption (at defaults):
      LLM: up to --max-llm-calls calls
      Search: up to --max-search-calls calls
    """
    from app.ai.claude import ClaudeProvider
    from app.core.config import get_config
    from app.intelligence.market.orchestrator import (
        AutonomousExplorationPolicy,
        BoundedExplorationResult,
        derive_coverage_summary,
        run_bounded_market_exploration,
    )
    from app.intelligence.repository import get_active_profile_version

    conn = _get_db()
    config = get_config()
    effective_api_key = api_key or (
        config.youtube_data_api_key if hasattr(config, "youtube_data_api_key") else ""
    )
    anthropic_key = config.anthropic_api_key if hasattr(config, "anthropic_api_key") else ""

    # Load channel profile.
    profile_record = get_active_profile_version(conn, channel)
    if profile_record is None:
        typer.echo(f"No active channel profile found for channel #{channel}.", err=True)
        typer.echo("Run 'ace learn profile update' to create one.", err=True)
        raise typer.Exit(1)

    from app.intelligence.market.cold_start import ExplorationProfile

    profile = ExplorationProfile(
        primary_niche=profile_record.primary_niche or "",
        secondary_niches=list(profile_record.secondary_niches or []),
        excluded_topics=list(profile_record.excluded_topics or []),
        audience_description=profile_record.audience_description or "",
        duplicate_similarity_threshold=profile_record.duplicate_similarity_threshold or 0.70,
    )

    policy = AutonomousExplorationPolicy(
        max_rounds=max_rounds,
        max_depth=max_depth,
        max_search_calls=max_search_calls,
        max_llm_calls=max_llm_calls,
        cold_start_max_probes=max_probes,
        region_code=region,
        language_code=language,
    )

    if dry_run:
        typer.echo("[dry-run] No LLM calls. No YouTube calls. No DB mutations.")
        typer.echo(f"  policy.max_rounds        : {policy.max_rounds}")
        typer.echo(f"  policy.max_search_calls  : {policy.max_search_calls}")
        typer.echo(f"  policy.max_llm_calls     : {policy.max_llm_calls}")
        typer.echo(f"  policy.max_depth         : {policy.max_depth}")
        typer.echo(f"  policy.cold_start_probes : {policy.cold_start_max_probes}")
        typer.echo(f"  profile.primary_niche    : {profile.primary_niche}")
        typer.echo(f"  profile.secondary_niches : {profile.secondary_niches}")
        typer.echo(f"  YouTube API key set      : {bool(effective_api_key)}")
        typer.echo(f"  Anthropic API key set    : {bool(anthropic_key)}")
        return

    provider = ClaudeProvider(model=model, api_key=anthropic_key)

    typer.echo(f"Starting bounded market exploration for channel #{channel}.")
    typer.echo(
        f"  max_rounds={policy.max_rounds}  max_search_calls={policy.max_search_calls}  "
        f"max_llm_calls={policy.max_llm_calls}"
    )

    result: BoundedExplorationResult = run_bounded_market_exploration(
        conn,
        profile,
        ai_provider=provider,
        channel_id=channel,
        policy=policy,
        api_key=effective_api_key,
    )

    typer.echo("Exploration complete.")
    typer.echo(f"  stop_reason           : {result.stop_reason}")
    typer.echo(f"  rounds_completed      : {result.rounds_completed}")
    typer.echo(f"  exploration_runs      : {result.exploration_run_ids}")
    typer.echo(f"  total_candidates      : {result.total_candidates}")
    typer.echo(f"  total_selected        : {result.total_selected}")
    typer.echo(f"  total_dispatched      : {result.total_dispatched}")
    typer.echo(f"  reused_count          : {result.reused_count}")
    typer.echo(f"  search_calls_expected : {result.search_calls_expected}")
    typer.echo(f"  search_calls_actual   : {result.search_calls_actual}")
    typer.echo(f"  search_calls_avoided  : {result.search_calls_avoided}")
    typer.echo(f"  llm_calls             : {result.llm_calls}")
    typer.echo(f"  observations_linked   : {result.observations_linked}")

    if result.exploration_run_ids:
        coverage = derive_coverage_summary(conn, result.exploration_run_ids)
        typer.echo("  coverage:")
        for k, v in coverage.items():
            typer.echo(f"    {k}: {v}")

    for rs in result.round_summaries:
        typer.echo(
            f"  round {rs.round_number}: dispatched={rs.dispatched} reused={rs.reused} "
            f"llm_calls={rs.llm_calls} search={rs.search_calls_actual}"
        )


# ---------------------------------------------------------------------------
# ace market interpret run
# ---------------------------------------------------------------------------


@interpret_app.command("run")
def interpret_run(
    platform: str = typer.Option("youtube", "--platform", help="Platform."),
    provider: str = typer.Option("youtube_data_api", "--provider", help="Provider."),
    region: str | None = typer.Option(None, "--region", "-r", help="Region code."),
    language: str | None = typer.Option(None, "--language", "-l", help="Language code."),
    source_runs: str | None = typer.Option(
        None, "--source-runs", help="Comma-separated exploration run IDs to scope (optional)."
    ),
    threshold: float = typer.Option(
        0.35, "--threshold", "-t", help="Jaccard similarity threshold."
    ),
) -> None:
    """Run market interpretation: cluster probes and score demand/saturation/freshness signals."""
    from app.intelligence.market.interpreter import run_market_interpretation

    conn = _get_db()
    src_ids: list[int] = []
    if source_runs:
        src_ids = [int(x.strip()) for x in source_runs.split(",") if x.strip().isdigit()]

    result = run_market_interpretation(
        conn,
        platform=platform,
        provider=provider,
        region_code=region,
        language_code=language,
        source_run_ids=src_ids,
        jaccard_threshold=threshold,
    )

    typer.echo(f"Interpretation run id   : {result['run_id']}")
    typer.echo(f"Status                  : {result['status']}")
    typer.echo(f"Clusters produced       : {result['cluster_count']}")
    typer.echo(f"LLM used                : {result['llm_used']}")
    if result.get("idempotent_hit"):
        typer.echo("(idempotent hit — returned existing run)")


# ---------------------------------------------------------------------------
# ace market clusters
# ---------------------------------------------------------------------------


@market_app.command("clusters")
def list_clusters(
    run_id: int | None = typer.Option(None, "--run", "-r", help="Filter by interpretation run ID."),
    limit: int = typer.Option(25, "--limit", "-n", help="Max rows."),
) -> None:
    """List market topic clusters."""
    from app.intelligence.market.interpretation_repository import (
        list_clusters_for_run,
        list_interpretation_runs,
    )

    conn = _get_db()
    if run_id is not None:
        clusters = list_clusters_for_run(conn, run_id)
    else:
        runs = list_interpretation_runs(conn, limit=1, status="completed")
        if not runs:
            typer.echo("No completed interpretation runs found.")
            return
        clusters = list_clusters_for_run(conn, runs[0].id)

    if not clusters:
        typer.echo("No clusters found.")
        return

    for c in clusters[:limit]:
        canonical_tag = f" canonical={c.canonical_cluster_id}" if c.canonical_cluster_id else ""
        typer.echo(
            f"  [{c.id}] {c.cluster_label} | probes={c.member_probe_count} "
            f"videos={c.member_video_count} type={c.cluster_type}{canonical_tag}"
        )


# ---------------------------------------------------------------------------
# ace market cluster-show <id>
# ---------------------------------------------------------------------------


@market_app.command("cluster-show")
def cluster_show(
    cluster_id: int = typer.Argument(..., help="Cluster ID to inspect."),
) -> None:
    """Show full detail for a market topic cluster including latest signal scores."""
    from app.intelligence.market.interpreter import get_cluster_summary

    conn = _get_db()
    summary = get_cluster_summary(conn, cluster_id)
    if summary is None:
        typer.echo(f"Cluster {cluster_id} not found.")
        raise typer.Exit(1)

    for k, v in summary.items():
        typer.echo(f"  {k:<35}: {v}")


# ---------------------------------------------------------------------------
# ace market signals --cluster <id>
# ---------------------------------------------------------------------------


@market_app.command("signals")
def list_signals(
    cluster_id: int | None = typer.Option(None, "--cluster", "-c", help="Cluster ID."),
    run_id: int | None = typer.Option(None, "--run", "-r", help="Interpretation run ID."),
    limit: int = typer.Option(25, "--limit", "-n", help="Max rows."),
) -> None:
    """List market cluster signal snapshots."""
    from app.intelligence.market.interpretation_repository import list_signals_for_run
    from app.intelligence.market.interpreter import list_signals_for_cluster_display

    conn = _get_db()

    if cluster_id is not None:
        snapshots = list_signals_for_cluster_display(conn, cluster_id)
    elif run_id is not None:
        raw = list_signals_for_run(conn, run_id)
        snapshots = [
            {
                "signal_id": s.id,
                "cluster_id": s.cluster_id,
                "interpretation_run_id": s.interpretation_run_id,
                "demand_score": s.demand_score,
                "saturation_score": s.saturation_score,
                "freshness_score": s.freshness_score,
                "momentum_score": s.momentum_score,
                "confidence": s.confidence,
                "signal_maturity": s.signal_maturity,
                "state_label": s.state_label,
                "scored_at": s.scored_at,
            }
            for s in raw
        ]
    else:
        typer.echo("Specify --cluster <id> or --run <id>.")
        raise typer.Exit(1)

    if not snapshots:
        typer.echo("No signals found.")
        return

    for s in snapshots[:limit]:
        typer.echo(
            f"  signal_id={s.get('signal_id')} "
            f"demand={s.get('demand_score')} "
            f"sat={s.get('saturation_score')} "
            f"fresh={s.get('freshness_score')} "
            f"mom={s.get('momentum_score')} "
            f"conf={s.get('confidence')} "
            f"maturity={s.get('signal_maturity')} "
            f"state={s.get('state_label')}"
        )


# ---------------------------------------------------------------------------
# ace market cluster-history <canonical_id>   (Phase 13E.1)
# ---------------------------------------------------------------------------


@market_app.command("cluster-history")
def cluster_history(
    canonical_id: int = typer.Argument(..., help="Canonical cluster ID."),
    limit: int = typer.Option(25, "--limit", "-n", help="Max snapshots to return."),
) -> None:
    """Show signal history for a canonical market cluster across interpretation runs.

    Displays demand, saturation, freshness, momentum, and persistence
    chronologically without requiring manual label matching.
    """
    from app.intelligence.market.interpretation_repository import get_canonical_cluster
    from app.intelligence.market.interpreter import get_canonical_signal_history

    conn = _get_db()

    try:
        canonical = get_canonical_cluster(conn, canonical_id)
    except LookupError:
        typer.echo(f"Canonical cluster {canonical_id} not found.")
        raise typer.Exit(1) from None

    typer.echo(
        f"\nCanonical cluster #{canonical.id}: {canonical.canonical_label}"
        f"\n  scope: {canonical.platform}/{canonical.provider} "
        f"region={canonical.region_code} lang={canonical.language_code}"
        f"\n  identity_version: {canonical.identity_version}"
        f"\n  created: {canonical.created_at}\n"
    )

    history = get_canonical_signal_history(conn, canonical_id)
    if not history:
        typer.echo("No signal history found for this canonical cluster.")
        return

    typer.echo(f"Signal history ({len(history)} snapshot(s)):\n")
    for snap in history[:limit]:
        typer.echo(
            f"  [{snap.get('signal_id')}] run={snap.get('interpretation_run_id')} "
            f"label={snap.get('cluster_label', '?')!r}\n"
            f"    demand={snap.get('demand_score')} "
            f"sat={snap.get('saturation_score')} "
            f"fresh={snap.get('freshness_score')} "
            f"mom={snap.get('momentum_score')} "
            f"persist={snap.get('persistence_score')}\n"
            f"    conf={snap.get('confidence')} "
            f"maturity={snap.get('signal_maturity')} "
            f"state={snap.get('state_label')} "
            f"scored={snap.get('scored_at')}"
        )


# ---------------------------------------------------------------------------
# Phase 13F: Market → Opportunity bridge CLI
# ---------------------------------------------------------------------------


@opportunities_app.command("sync")
def sync_market_opportunities(
    channel: int = typer.Option(
        ..., "--channel", "-c", help="Channel ID to sync opportunities for."
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="Max clusters to process per run."),
    min_maturity: str = typer.Option(
        "directional",
        "--min-maturity",
        help="Minimum signal maturity level (insufficient/exploratory/directional/actionable).",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show mappings without writing to DB."),
) -> None:
    """Sync external market cluster signals into the channel's Opportunity system.

    Reads the latest scored clusters (via market_cluster_signals), maps signals
    into the existing six scoring factors, and creates or refreshes Opportunities
    without making any YouTube or external API calls.
    """
    from app.intelligence.market.bridge import sync_channel_market_opportunities
    from app.intelligence.market.bridge_models import ExternalMarketBridgePolicy
    from app.intelligence.market.interpretation_models import build_opportunity_evidence
    from app.intelligence.market.interpretation_repository import (
        get_latest_signal_for_cluster,
        list_clusters_for_run,
        list_interpretation_runs,
    )
    from app.intelligence.repository import (
        get_active_profile_version,
        get_default_scoring_policy,
    )

    conn = _get_db()
    bridge_policy = ExternalMarketBridgePolicy(min_maturity_level=min_maturity)

    # Load channel context
    scoring_policy = get_default_scoring_policy(conn, channel)
    if scoring_policy is None:
        typer.echo(f"No scoring policy found for channel {channel}. Create one first.")
        raise typer.Exit(1)

    profile_version = get_active_profile_version(conn, channel)
    if profile_version is None:
        typer.echo(f"No active channel profile found for channel {channel}.")
        raise typer.Exit(1)

    # Load clusters from the most recent completed interpretation run
    runs = list_interpretation_runs(conn, status="completed", limit=1)
    if not runs:
        typer.echo("No completed interpretation runs found. Run `ace market interpret run` first.")
        raise typer.Exit(0)

    latest_run = runs[0]
    clusters = list_clusters_for_run(conn, latest_run.id)[:limit]

    evidences = []
    for cluster in clusters:
        signal = get_latest_signal_for_cluster(conn, cluster.id)
        if signal is not None:
            evidences.append(build_opportunity_evidence(cluster, signal))

    prefix = "[DRY RUN] " if dry_run else ""
    typer.echo(f"{prefix}Syncing {len(evidences)} cluster(s) for channel {channel}...")

    result = sync_channel_market_opportunities(
        conn,
        channel_id=channel,
        evidences=evidences,
        profile_version=profile_version,
        scoring_policy=scoring_policy,
        bridge_policy=bridge_policy,
        dry_run=dry_run,
    )

    if not dry_run:
        conn.commit()

    typer.echo(
        f"\n{prefix}Results: created={result.created_count} "
        f"refreshed={result.refreshed_count} "
        f"skipped={result.skipped_count} "
        f"scored={result.scored_count}"
    )
    for item in result.items:
        if item.skipped:
            typer.echo(f"  SKIP  {item.cluster_label!r}: {item.skip_reason}")
        else:
            action = "NEW" if item.opportunity_created else "UPD"
            score_str = f"{item.composite_score:.3f}" if item.composite_score is not None else "n/a"
            typer.echo(
                f"  {action}   opp={item.opportunity_id} "
                f"canonical={item.canonical_cluster_id} "
                f"score={score_str} "
                f"conf={f'{item.confidence:.3f}' if item.confidence is not None else 'n/a'} "
                f"{item.cluster_label!r}"
            )


@opportunities_app.command("list")
def list_market_opportunities(
    channel: int = typer.Option(..., "--channel", "-c", help="Channel ID."),
    state: str | None = typer.Option(None, "--state", "-s", help="Filter by lifecycle state."),
    limit: int = typer.Option(25, "--limit", "-n", help="Max rows."),
) -> None:
    """List opportunities for a channel, showing market provenance when available."""
    from app.intelligence.models import LifecycleState
    from app.intelligence.repository import list_opportunities

    conn = _get_db()
    lc_state = LifecycleState(state) if state else None
    opps = list_opportunities(conn, channel, state=lc_state)[:limit]

    if not opps:
        typer.echo("No opportunities found.")
        return

    for opp in opps:
        canonical = f" canonical={opp.canonical_cluster_id}" if opp.canonical_cluster_id else ""
        typer.echo(
            f"[{opp.id:>5}] {opp.current_lifecycle_state.value:<12} "
            f"{canonical} {opp.normalized_topic!r}"
        )
