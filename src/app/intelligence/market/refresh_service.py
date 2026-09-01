"""Phase 17F — recurring market-intelligence maintenance cycle.

Chains three existing, independently-invoked CLI steps into one safe,
callable service function:

  velocity rescan (YouTube stats refresh for ALREADY-KNOWN videos only,
  no search.list call, gracefully skipped when no API key is configured)
    -> interpretation (pure clustering + demand/saturation/freshness/
       persistence scoring; max_llm_clusters=0 means zero LLM calls)
    -> opportunity sync (bridge fresh cluster evidence into Opportunity /
       OpportunitySourceEvidence rows and rescore; zero external calls)

Deliberately excludes `run_bounded_market_exploration` (new-probe
discovery). That function's own docstring says it "REQUIRES EXPLICIT
APPROVAL before running live" because it makes real Claude + YouTube
calls — this module does not override that gate. Discovering brand-new
market probes remains an explicit, human-triggered CLI action
(`ace market explore run`), never something the recurring scheduler does
unattended. This cycle only keeps EXISTING evidence, velocity, and scores
current — which is what makes an already-scored opportunity's confidence
correctly decay (via the pre-existing `ScoringPolicy.freshness_decay_days`
mechanism in scoring/confidence.py) when nothing new has been observed,
rather than freezing forever at its first score.

Callable from three places without any duplicated logic:
  - the recurring scheduler (app.workers.scheduler, operation_type
    'market_refresh')
  - `ace market refresh` (a thin CLI wrapper, for one-off operator runs)
  - directly, e.g. for a Phase 18 orchestration step (see module docstring
    end for the exact handoff signature)
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


@dataclass
class MarketRefreshResult:
    """Outcome of one run_market_refresh_cycle() call. Every count here is
    real — nothing is fabricated when a step is skipped or produces zero."""

    channel_id: int
    started_at: str
    completed_at: str = ""

    velocity_attempted: bool = False
    velocity_skip_reason: str | None = None
    velocity_enrichment_calls: int = 0
    velocity_observations_new: int = 0

    interpretation_run_id: int | None = None
    interpretation_status: str | None = None
    interpretation_skip_reason: str | None = None
    clusters_produced: int = 0

    sync_attempted: bool = False
    sync_skip_reason: str | None = None
    sync_created: int = 0
    sync_refreshed: int = 0
    sync_skipped: int = 0
    sync_scored: int = 0

    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _run_velocity_rescan(
    conn: sqlite3.Connection,
    *,
    channel_id: int,
    workspace_id: str | None,
    api_key: str,
    collector: Any,
    max_videos: int,
    min_age_hours: float,
    dry_run: bool,
    result: MarketRefreshResult,
) -> None:
    result.velocity_attempted = True
    if dry_run:
        # collect_velocity_rescan has no dry-run mode upstream either — it
        # always issues real batchGetStats calls when a key is present.
        result.velocity_skip_reason = "dry_run"
        return
    if collector is None and not api_key:
        result.velocity_skip_reason = "no_youtube_api_key"
        logger.info("Market refresh: velocity rescan skipped — no YouTube API key configured")
        return

    from app.intelligence.market.repository import create_market_collection_job

    job = create_market_collection_job(
        conn,
        job_type="velocity_rescan",
        origin_type="velocity_rescan",
        channel_id=channel_id,
        workspace_id=workspace_id,
    )

    owns_collector = collector is None
    if owns_collector:
        from app.intelligence.market.collector import YouTubeMarketCollector

        collector = YouTubeMarketCollector(api_key=api_key)
    try:
        rescan_result = collector.collect_velocity_rescan(
            conn,
            job,
            max_videos=max_videos,
            min_age_seconds=int(min_age_hours * 3600),
        )
        result.velocity_enrichment_calls = rescan_result.enrichment_calls
        result.velocity_observations_new = rescan_result.observations_new
    except Exception as exc:  # noqa: BLE001 — a market-data hiccup must never break the tick
        result.errors.append(f"velocity_rescan: {exc}")
        logger.warning("Market refresh: velocity rescan failed (non-fatal): %s", exc)
    finally:
        if owns_collector:
            collector.close()


def _run_interpretation(
    conn: sqlite3.Connection,
    *,
    jaccard_threshold: float,
    dry_run: bool,
    result: MarketRefreshResult,
) -> None:
    from app.intelligence.market.interpreter import run_market_interpretation

    if dry_run:
        # run_market_interpretation has no dry-run mode upstream (unlike
        # velocity rescan and opportunity sync) — it always persists a
        # market_interpretation_runs row and its clusters. A true dry run of
        # this cycle must skip the stage entirely rather than claim a
        # preview it cannot actually provide.
        result.interpretation_skip_reason = "dry_run"
        return

    try:
        interp = run_market_interpretation(
            conn,
            jaccard_threshold=jaccard_threshold,
            max_llm_clusters=0,  # deterministic clustering only — zero LLM calls
        )
        result.interpretation_run_id = interp.get("run_id")
        result.interpretation_status = interp.get("status")
        result.clusters_produced = interp.get("cluster_count", 0)
    except Exception as exc:  # noqa: BLE001
        result.interpretation_skip_reason = str(exc)
        result.errors.append(f"interpretation: {exc}")
        logger.warning("Market refresh: interpretation failed (non-fatal): %s", exc)


def _run_opportunity_sync(
    conn: sqlite3.Connection,
    *,
    channel_id: int,
    min_maturity: str,
    limit: int,
    dry_run: bool,
    result: MarketRefreshResult,
) -> None:
    from app.intelligence.market.bridge import sync_channel_market_opportunities
    from app.intelligence.market.bridge_models import ExternalMarketBridgePolicy
    from app.intelligence.market.interpretation_models import build_opportunity_evidence
    from app.intelligence.market.interpretation_repository import (
        get_latest_signal_for_cluster,
        list_clusters_for_run,
        list_interpretation_runs,
    )
    from app.intelligence.repository import get_active_profile_version, get_default_scoring_policy

    result.sync_attempted = True

    scoring_policy = get_default_scoring_policy(conn, channel_id)
    if scoring_policy is None:
        result.sync_skip_reason = "no_scoring_policy"
        return
    profile_version = get_active_profile_version(conn, channel_id)
    if profile_version is None:
        result.sync_skip_reason = "no_channel_profile"
        return

    runs = list_interpretation_runs(conn, status="completed", limit=1)
    if not runs:
        result.sync_skip_reason = "no_completed_interpretation_run"
        return

    clusters = list_clusters_for_run(conn, runs[0].id)[:limit]
    evidences = []
    for cluster in clusters:
        signal = get_latest_signal_for_cluster(conn, cluster.id)
        if signal is not None:
            evidences.append(build_opportunity_evidence(cluster, signal))

    try:
        sync_result = sync_channel_market_opportunities(
            conn,
            channel_id=channel_id,
            evidences=evidences,
            profile_version=profile_version,
            scoring_policy=scoring_policy,
            bridge_policy=ExternalMarketBridgePolicy(min_maturity_level=min_maturity),
            dry_run=dry_run,
        )
        if not dry_run:
            conn.commit()
        result.sync_created = sync_result.created_count
        result.sync_refreshed = sync_result.refreshed_count
        result.sync_skipped = sync_result.skipped_count
        result.sync_scored = sync_result.scored_count
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"opportunity_sync: {exc}")
        logger.warning("Market refresh: opportunity sync failed (non-fatal): %s", exc)


def run_market_refresh_cycle(
    conn: sqlite3.Connection,
    *,
    channel_id: int,
    workspace_id: str | None = None,
    api_key: str = "",
    collector: Any = None,
    max_velocity_videos: int = 50,
    min_velocity_age_hours: float = 6.0,
    interpret_jaccard_threshold: float = 0.35,
    sync_min_maturity: str = "directional",
    sync_limit: int = 50,
    dry_run: bool = False,
) -> MarketRefreshResult:
    """Run one market-intelligence maintenance cycle for a channel.

    channel_id is the intelligence-domain integer id (see
    app.intelligence.channel_bridge) — resolve it from a cp_channel_id
    before calling. Never raises: each stage catches its own exceptions
    into result.errors so one stage's failure cannot break the others or
    the caller's scheduler tick.
    """
    result = MarketRefreshResult(channel_id=channel_id, started_at=_now_iso())

    _run_velocity_rescan(
        conn,
        channel_id=channel_id,
        workspace_id=workspace_id,
        api_key=api_key,
        collector=collector,
        max_videos=max_velocity_videos,
        min_age_hours=min_velocity_age_hours,
        dry_run=dry_run,
        result=result,
    )
    _run_interpretation(
        conn, jaccard_threshold=interpret_jaccard_threshold, dry_run=dry_run, result=result
    )
    _run_opportunity_sync(
        conn,
        channel_id=channel_id,
        min_maturity=sync_min_maturity,
        limit=sync_limit,
        dry_run=dry_run,
        result=result,
    )

    result.completed_at = _now_iso()
    return result


# ---------------------------------------------------------------------------
# Phase 18 handoff
# ---------------------------------------------------------------------------
#
# The exact call Phase 18's orchestration loop should make to request
# "refresh market intelligence for channel X and use the latest valid
# opportunities":
#
#   from app.intelligence.channel_bridge import get_intelligence_channel_id
#   from app.intelligence.market.refresh_service import run_market_refresh_cycle
#   from app.intelligence.repository import get_default_scoring_policy, list_scored_opportunities
#
#   intel_channel_id = get_intelligence_channel_id(conn, cp_channel_id)
#   run_market_refresh_cycle(conn, channel_id=intel_channel_id, workspace_id=workspace_id,
#                             api_key=config.youtube_data_api_key)
#   policy = get_default_scoring_policy(conn, intel_channel_id)
#   opportunities = list_scored_opportunities(conn, intel_channel_id, policy.id, limit=50)
#
# `opportunities` is already latest-score-first (see
# intelligence.repository.list_scored_opportunities' ORDER BY composite_score
# DESC, confidence DESC).
#
# Phase 17G addition — resolve semantic-fit eligibility BEFORE planning, so
# the planner's eligible_count reflects reality instead of UNRESOLVED:
#
#   from app.ai.claude import ClaudeProvider
#   from app.intelligence.experiments.eligibility_service import (
#       resolve_unresolved_opportunities_for_channel,
#   )
#
#   provider = (
#       ClaudeProvider(api_key=config.anthropic_api_key) if config.anthropic_api_key else None
#   )
#   resolve_unresolved_opportunities_for_channel(
#       conn, channel_id=intel_channel_id, ai_provider=provider, max_evaluations=10,
#   )
#
# This is the only step in the whole chain that spends real Claude calls,
# and it is self-bounding (max_evaluations) and self-caching (a persisted
# opportunity_semantic_fit_results row is reused across every later call
# with the same input_hash — see eligibility_service.py's module docstring).
# Safe to call on every cycle; a channel with nothing new to resolve spends
# zero LLM calls.
#
# The resulting eligible opportunities are safe to hand directly to the
# Phase 17E experiment planner (app.intelligence.experiments.planning_service
# .build_portfolio_plan), which independently loads the channel's active
# Strategy Profile via app.intelligence.experiments.strategy_policy
# .load_policy_for_channel — Phase 18 does not need to touch strategy
# loading itself, only ensure market data and eligibility are fresh before
# planning runs.
#
# Full Phase 18 orchestration sequence, with each step's cost/mutation profile:
#
#   1. analytics/learning refresh   — read-only/analytical; no LLM, no YouTube quota
#   2. market refresh (this module) — YouTube quota (video_stats_batch only,
#                                      no search.list); zero LLM calls
#   3. semantic-fit resolution      — Claude quota, bounded by max_evaluations;
#                                      writes only to opportunity_semantic_fit_results
#   4. strategy-aware planner       — read-only/analytical; build_portfolio_plan(dry_run=True)
#                                      previews with zero mutation; dry_run=False persists
#                                      an experiment_planning_runs row (internal planning
#                                      state only — still not a production experiment)
#   5. selected experiment          — Phase 18's own decision of what to do with the
#                                      plan's selected candidates. NOT built by this phase.
#                                      Everything past this point (script/narration/render
#                                      generation, queueing, YouTube upload, visibility
#                                      change) requires the publishing gates
#                                      (ACE_PUBLISHING_LIVE_ENABLED, ACE_RELEASE_PUBLIC_ENABLED)
#                                      to be explicitly enabled by an operator — both remain
#                                      false through Phase 17G.
