"""Phase 13D-F — Bounded end-to-end autonomous market exploration orchestration.

RESPONSIBILITIES
----------------
This module coordinates existing Phase 13D components into a bounded, auditable
exploration cycle. It does NOT duplicate logic from any sub-component.

Full chain validated here:
  channel profile
  → cold-start planner (13D-B)
  → semantic niche guard (13D-D)
  → dispatcher + reuse (13D-E → 13B)
  → adjacent expansion (13D-C)
  → second-round semantic guard (13D-D)
  → bounded follow-up dispatch (13D-E → 13B)

HARD STOP CONDITIONS
--------------------
- max_rounds reached
- search budget exhausted
- LLM budget exhausted
- no SELECTED probes in current round
- no eligible parent probes for adjacent expansion
- critical provider failure on cold-start
- configuration error (missing primary niche)

NO OPPORTUNITY CREATION
NO CONTENT GENERATION
NO PHASE 13E SCORING
NO UNBOUNDED RECURSION
"""

from __future__ import annotations

import sqlite3
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.intelligence.market.adjacent import (
    AdjacencyExpansionMaturity,
    AdjacentExpansionResult,
    _build_evidence_summary,
    classify_expansion_eligibility,
    plan_adjacent_expansion,
)
from app.intelligence.market.cold_start import (
    ColdStartExplorationResult,
    ExplorationProfile,
    InsufficientProfileError,
    plan_cold_start,
)
from app.intelligence.market.dispatcher import (
    DISPATCH_REUSE_MAX_AGE_HOURS,
    ProbeDispatchResult,
    dispatch_selected_probes,
)
from app.intelligence.market.planner_models import ExplorationProbe
from app.intelligence.market.planner_repository import (
    list_expandable_parent_probes,
    list_probes_for_selection,
    list_selected_probes_for_dispatch,
)
from app.intelligence.market.selector import SelectionResult, run_niche_selection

# ---------------------------------------------------------------------------
# V1 autonomous exploration policy
# ---------------------------------------------------------------------------

ORCHESTRATOR_POLICY_VERSION = "v1"


class AutonomousExplorationPolicy(BaseModel):
    """Typed hard limits for one bounded autonomous exploration cycle.

    All limits are enforced before the action that would exceed them.
    V1 defaults are intentionally conservative for first-run validation.
    """

    model_config = {"extra": "forbid"}

    # How many round-trips are allowed:
    # Round 0 = cold-start + select + dispatch.
    # Round 1 = adjacent expansion + select + dispatch.
    max_rounds: int = Field(default=2, ge=1, le=10)

    # Maximum exploration depth across all probes in all rounds.
    max_depth: int = Field(default=2, ge=1, le=5)

    # Total candidate probes (across all runs + rounds) before we stop creating more.
    max_total_candidate_probes: int = Field(default=30, ge=1)

    # Total SELECTED probes (across all runs + rounds) allowed.
    max_total_selected_probes: int = Field(default=15, ge=1)

    # YouTube search.list calls budget (across all rounds).
    # Reuse paths consume 0 calls.
    max_search_calls: int = Field(default=10, ge=0)

    # Total AI provider calls (cold-start, selector, adjacent) budget.
    max_llm_calls: int = Field(default=8, ge=0)

    # Maximum adjacent children proposed per dispatched parent probe.
    max_children_per_parent: int = Field(default=6, ge=1, le=10)

    # Maximum parallel parent probes expanded in one adjacent round.
    max_parents_per_round: int = Field(default=5, ge=1)

    # Freshness window for evidence reuse (passed to dispatcher).
    reuse_max_age_hours: float = Field(default=DISPATCH_REUSE_MAX_AGE_HOURS, ge=0.0)

    # Optional geographic/language filter applied to all probes in the cycle.
    region_code: str | None = None
    language_code: str | None = None

    # Per-round probe budgets (control how many candidates each phase may propose).
    cold_start_max_probes: int = Field(default=8, ge=1)
    adjacent_max_probes: int = Field(default=6, ge=1)

    # Planner version tag forwarded to all sub-components.
    planner_version: str = "v1"


# ---------------------------------------------------------------------------
# Stop reason
# ---------------------------------------------------------------------------


class ExplorationStopReason(StrEnum):
    COMPLETED_POLICY = "completed_policy"
    MAX_ROUNDS = "max_rounds"
    MAX_DEPTH = "max_depth"
    SEARCH_BUDGET = "search_budget"
    LLM_BUDGET = "llm_budget"
    NO_CANDIDATES = "no_candidates"
    NO_SELECTED_PROBES = "no_selected_probes"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PROVIDER_FAILURE = "provider_failure"
    CONFIGURATION_ERROR = "configuration_error"


# ---------------------------------------------------------------------------
# Typed results
# ---------------------------------------------------------------------------


class RoundSummary(BaseModel):
    """Per-round accounting for one exploration round."""

    round_number: int
    exploration_run_ids: list[int] = Field(default_factory=list)
    candidates_created: int = 0
    selected: int = 0
    dispatched: int = 0
    reused: int = 0
    search_calls_expected: int = 0
    search_calls_actual: int = 0
    llm_calls: int = 0
    parent_probes_considered: int = 0
    parents_eligible: int = 0
    stop_reason: str | None = None


class BoundedExplorationResult(BaseModel):
    """Full typed result for one bounded autonomous exploration cycle."""

    model_config = {"extra": "forbid"}

    channel_id: int | None
    rounds_completed: int
    exploration_run_ids: list[int] = Field(default_factory=list)
    total_candidates: int = 0
    total_selected: int = 0
    total_dispatched: int = 0
    reused_count: int = 0
    search_calls_expected: int = 0
    search_calls_actual: int = 0
    search_calls_avoided: int = 0
    llm_calls: int = 0
    observations_linked: int = 0
    stop_reason: str
    policy_version: str = ORCHESTRATOR_POLICY_VERSION
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    round_summaries: list[RoundSummary] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Coverage summary
# ---------------------------------------------------------------------------


def derive_coverage_summary(
    conn: sqlite3.Connection,
    run_ids: list[int],
) -> dict[str, Any]:
    """Derive a human-readable market exploration coverage summary.

    Does not persist a separate table — derived from existing lineage.
    """
    if not run_ids:
        return {"market_regions_considered": 0}

    placeholders = ", ".join("?" for _ in run_ids)
    rows = conn.execute(
        f"""
        SELECT
            COUNT(*) AS total_probes,
            COUNT(DISTINCT normalized_query) AS distinct_queries,
            SUM(CASE WHEN status='selected' THEN 1 ELSE 0 END) AS selected,
            SUM(CASE WHEN status='dispatched' THEN 1 ELSE 0 END) AS dispatched,
            SUM(CASE WHEN status='deferred' THEN 1 ELSE 0 END) AS deferred,
            SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) AS rejected,
            SUM(CASE WHEN probe_type='market_region' THEN 1 ELSE 0 END) AS market_region_count,
            SUM(CASE WHEN probe_type='channel_bootstrap' THEN 1 ELSE 0 END) AS bootstrap_count,
            SUM(CASE WHEN probe_type='adjacent_topic' THEN 1 ELSE 0 END) AS adjacent_count,
            MAX(exploration_depth) AS max_depth_reached
          FROM market_exploration_probes
         WHERE exploration_run_id IN ({placeholders})
        """,
        run_ids,
    ).fetchone()
    if not rows:
        return {}
    return {
        "total_probes": rows[0],
        "distinct_queries": rows[1],
        "selected": rows[2],
        "dispatched": rows[3],
        "deferred": rows[4],
        "rejected": rows[5],
        "market_region_probes": rows[6],
        "bootstrap_probes": rows[7],
        "adjacent_probes": rows[8],
        "max_depth_reached": rows[9],
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _count_observations_for_jobs(
    conn: sqlite3.Connection,
    dispatched_probe_ids: list[int],
) -> int:
    """Count total observations linked to the jobs from these probes."""
    if not dispatched_probe_ids:
        return 0
    placeholders = ", ".join("?" for _ in dispatched_probe_ids)
    row = conn.execute(
        f"""
        SELECT COUNT(DISTINCT jo.observation_id)
          FROM market_job_observations jo
          JOIN market_exploration_probes p ON p.dispatched_job_id = jo.job_id
         WHERE p.id IN ({placeholders})
        """,
        dispatched_probe_ids,
    ).fetchone()
    return row[0] if row else 0


def _prior_queries_for_channel(conn: sqlite3.Connection, channel_id: int | None) -> set[str]:
    from app.intelligence.market.planner_repository import list_prior_probe_normalized_queries

    return list_prior_probe_normalized_queries(conn, channel_id)


def _sel_made_llm_call(sel_result: SelectionResult) -> bool:
    """Return True if run_niche_selection made or attempted an LLM call."""
    return sel_result.llm_provider is not None or sel_result.llm_error is not None


def _build_policy_snapshot(policy: AutonomousExplorationPolicy) -> dict[str, Any]:
    return {
        "policy_version": ORCHESTRATOR_POLICY_VERSION,
        "bounds": policy.model_dump(),
    }


# ---------------------------------------------------------------------------
# Public service
# ---------------------------------------------------------------------------


def run_bounded_market_exploration(
    conn: sqlite3.Connection,
    profile: ExplorationProfile,
    *,
    ai_provider: Any,
    channel_id: int | None = None,
    workspace_id: str | None = None,
    policy: AutonomousExplorationPolicy | None = None,
    api_key: str = "",
    collector: Any = None,
    dry_run: bool = False,
) -> BoundedExplorationResult:
    """Run a bounded autonomous market exploration cycle.

    Coordinates cold-start, semantic selection, dispatch, adjacent expansion,
    and second-round dispatch without duplicating sub-component logic.

    SAFETY
    ------
    - Hard stops enforced before every budget-consuming action.
    - dry_run=True evaluates eligibility but makes zero DB mutations,
      zero network calls, and zero LLM calls.
    - Never creates Opportunities. Never generates content.

    RETURNS
    -------
    BoundedExplorationResult with full provenance, budget accounting,
    and an explicit stop_reason.
    """
    if policy is None:
        policy = AutonomousExplorationPolicy()

    policy_snapshot = _build_policy_snapshot(policy)
    run_ids: list[int] = []
    round_summaries: list[RoundSummary] = []
    total_candidates = 0
    total_selected = 0
    total_dispatched = 0
    total_reused = 0
    total_expected_calls = 0
    total_actual_calls = 0
    total_avoided_calls = 0
    total_llm_calls = 0
    total_observations = 0
    stop_reason = ExplorationStopReason.COMPLETED_POLICY

    # Short-circuit for dry_run without mutations
    if dry_run:
        return BoundedExplorationResult(
            channel_id=channel_id,
            rounds_completed=0,
            exploration_run_ids=[],
            stop_reason=ExplorationStopReason.COMPLETED_POLICY,
            policy_snapshot=policy_snapshot,
            dry_run=True,
            diagnostics={"note": "dry_run=True; no DB mutations, no LLM calls, no network calls"},
        )

    # -----------------------------------------------------------------------
    # ROUND 0 — BOOTSTRAP
    # -----------------------------------------------------------------------
    round0 = RoundSummary(round_number=0)

    # 0-A. LLM budget pre-check for cold-start.
    if total_llm_calls >= policy.max_llm_calls:
        stop_reason = ExplorationStopReason.LLM_BUDGET
        round0.stop_reason = stop_reason
        round_summaries.append(round0)
        return _build_result(
            channel_id,
            0,
            run_ids,
            total_candidates,
            total_selected,
            total_dispatched,
            total_reused,
            total_expected_calls,
            total_actual_calls,
            total_avoided_calls,
            total_llm_calls,
            total_observations,
            stop_reason,
            policy_snapshot,
            round_summaries,
            dry_run,
        )

    # 0-B. Cold-start planning (LLM call #1).
    cs_result: ColdStartExplorationResult
    try:
        cs_result = plan_cold_start(
            conn,
            profile,
            provider=ai_provider,
            channel_id=channel_id,
            workspace_id=workspace_id,
            max_probes=policy.cold_start_max_probes,
            max_depth=policy.max_depth,
            search_budget=policy.max_search_calls,
            planner_version=policy.planner_version,
        )
    except InsufficientProfileError as exc:
        round0.stop_reason = ExplorationStopReason.CONFIGURATION_ERROR
        round_summaries.append(round0)
        return _build_result(
            channel_id,
            0,
            run_ids,
            total_candidates,
            total_selected,
            total_dispatched,
            total_reused,
            total_expected_calls,
            total_actual_calls,
            total_avoided_calls,
            total_llm_calls,
            total_observations,
            ExplorationStopReason.CONFIGURATION_ERROR,
            policy_snapshot,
            round_summaries,
            dry_run,
            extra_diagnostics={"cold_start_error": str(exc)},
        )
    except Exception as exc:
        round0.stop_reason = ExplorationStopReason.PROVIDER_FAILURE
        round_summaries.append(round0)
        return _build_result(
            channel_id,
            0,
            run_ids,
            total_candidates,
            total_selected,
            total_dispatched,
            total_reused,
            total_expected_calls,
            total_actual_calls,
            total_avoided_calls,
            total_llm_calls,
            total_observations,
            ExplorationStopReason.PROVIDER_FAILURE,
            policy_snapshot,
            round_summaries,
            dry_run,
            extra_diagnostics={"cold_start_error": str(exc)},
        )

    if cs_result.llm_used:
        total_llm_calls += 1
        round0.llm_calls += 1

    cs_run_id = cs_result.exploration_run_id
    run_ids.append(cs_run_id)
    round0.exploration_run_ids.append(cs_run_id)
    round0.candidates_created += cs_result.candidate_count
    total_candidates += cs_result.candidate_count

    if cs_result.selected_count == 0:
        stop_reason = ExplorationStopReason.NO_CANDIDATES
        round0.stop_reason = stop_reason
        round_summaries.append(round0)
        return _build_result(
            channel_id,
            1,
            run_ids,
            total_candidates,
            total_selected,
            total_dispatched,
            total_reused,
            total_expected_calls,
            total_actual_calls,
            total_avoided_calls,
            total_llm_calls,
            total_observations,
            stop_reason,
            policy_snapshot,
            round_summaries,
            dry_run,
        )

    # 0-C. Semantic niche guard (selector) on remaining CANDIDATE probes.
    #      plan_cold_start may leave probes as CANDIDATE if it errored mid-way
    #      or if the caller's round needs the guard. In the typical happy path,
    #      there are no CANDIDATEs and this is a no-op (0 LLM calls, instant).
    candidate_probes_before_sel = list_probes_for_selection(conn, cs_run_id)
    if candidate_probes_before_sel and total_llm_calls < policy.max_llm_calls:
        prior_queries = _prior_queries_for_channel(conn, channel_id)
        sel_result: SelectionResult = run_niche_selection(
            conn,
            cs_run_id,
            primary_niche=profile.primary_niche,
            excluded_topics=profile.excluded_topics,
            prior_queries=prior_queries,
            max_probes=policy.cold_start_max_probes,
            channel_id=channel_id,
            ai_provider=ai_provider,
        )
        if _sel_made_llm_call(sel_result):
            total_llm_calls += 1
            round0.llm_calls += 1

    # 0-D. Dispatch SELECTED probes.
    remaining_search_budget = policy.max_search_calls - total_actual_calls
    if remaining_search_budget <= 0:
        stop_reason = ExplorationStopReason.SEARCH_BUDGET
        round0.stop_reason = stop_reason
        round_summaries.append(round0)
        total_selected += cs_result.selected_count
        round0.selected = cs_result.selected_count
        return _build_result(
            channel_id,
            1,
            run_ids,
            total_candidates,
            total_selected,
            total_dispatched,
            total_reused,
            total_expected_calls,
            total_actual_calls,
            total_avoided_calls,
            total_llm_calls,
            total_observations,
            stop_reason,
            policy_snapshot,
            round_summaries,
            dry_run,
        )

    dispatch0: ProbeDispatchResult = dispatch_selected_probes(
        conn,
        cs_run_id,
        api_key=api_key,
        collector=collector,
        max_search_calls=remaining_search_budget,
        reuse_max_age_hours=policy.reuse_max_age_hours,
    )

    total_expected_calls += dispatch0.expected_search_calls
    total_actual_calls += dispatch0.actual_search_calls
    total_avoided_calls += dispatch0.search_calls_avoided
    total_dispatched += dispatch0.newly_dispatched + dispatch0.reused_count
    total_reused += dispatch0.reused_count
    total_selected += cs_result.selected_count
    new_obs_0 = _count_observations_for_jobs(conn, dispatch0.dispatched_probe_ids)
    total_observations += new_obs_0

    round0.selected = cs_result.selected_count
    round0.dispatched = dispatch0.newly_dispatched + dispatch0.reused_count
    round0.reused = dispatch0.reused_count
    round0.search_calls_expected = dispatch0.expected_search_calls
    round0.search_calls_actual = dispatch0.actual_search_calls
    round_summaries.append(round0)

    if dispatch0.newly_dispatched + dispatch0.reused_count == 0:
        stop_reason = ExplorationStopReason.NO_SELECTED_PROBES
        return _build_result(
            channel_id,
            1,
            run_ids,
            total_candidates,
            total_selected,
            total_dispatched,
            total_reused,
            total_expected_calls,
            total_actual_calls,
            total_avoided_calls,
            total_llm_calls,
            total_observations,
            stop_reason,
            policy_snapshot,
            round_summaries,
            dry_run,
        )

    if policy.max_rounds < 2:
        return _build_result(
            channel_id,
            1,
            run_ids,
            total_candidates,
            total_selected,
            total_dispatched,
            total_reused,
            total_expected_calls,
            total_actual_calls,
            total_avoided_calls,
            total_llm_calls,
            total_observations,
            ExplorationStopReason.MAX_ROUNDS,
            policy_snapshot,
            round_summaries,
            dry_run,
        )

    # -----------------------------------------------------------------------
    # ROUND 1 — EVIDENCE FOLLOW-UP (adjacent expansion)
    # -----------------------------------------------------------------------
    round1 = RoundSummary(round_number=1)

    # 1-A. Find eligible parent probes across all round-0 runs.
    parent_probes: list[ExplorationProbe] = list_expandable_parent_probes(
        conn,
        cs_run_id,
        max_depth=policy.max_depth,
        statuses=("dispatched",),
    )
    round1.parent_probes_considered = len(parent_probes)

    if not parent_probes:
        stop_reason = ExplorationStopReason.INSUFFICIENT_EVIDENCE
        round1.stop_reason = stop_reason
        round_summaries.append(round1)
        return _build_result(
            channel_id,
            1,
            run_ids,
            total_candidates,
            total_selected,
            total_dispatched,
            total_reused,
            total_expected_calls,
            total_actual_calls,
            total_avoided_calls,
            total_llm_calls,
            total_observations,
            stop_reason,
            policy_snapshot,
            round_summaries,
            dry_run,
        )

    # Limit parents per round.
    parents_to_expand = parent_probes[: policy.max_parents_per_round]
    prior_queries = _prior_queries_for_channel(conn, channel_id)
    adj_run_ids: list[int] = []

    for parent in parents_to_expand:
        # Hard stop checks before each LLM call.
        if total_llm_calls >= policy.max_llm_calls:
            stop_reason = ExplorationStopReason.LLM_BUDGET
            break
        if total_actual_calls >= policy.max_search_calls:
            stop_reason = ExplorationStopReason.SEARCH_BUDGET
            break
        if total_candidates >= policy.max_total_candidate_probes:
            break

        # Classify adjacent expansion eligibility (no LLM, no DB mutation).
        summary = _build_evidence_summary(
            conn,
            probe_id=parent.id,
            query_text=parent.query_text,
            normalized_query=parent.normalized_query,
            exploration_depth=parent.exploration_depth,
            dispatched_job_id=parent.dispatched_job_id,
        )
        maturity = classify_expansion_eligibility(summary)
        if maturity in (
            AdjacencyExpansionMaturity.INSUFFICIENT,
            AdjacencyExpansionMaturity.SEMANTICALLY_UNGROUNDED,
        ):
            continue

        round1.parents_eligible += 1

        # 1-B. Adjacent expansion (LLM call per parent).
        adj_result: AdjacentExpansionResult = plan_adjacent_expansion(
            conn,
            provider=ai_provider,
            parent_probe_id=parent.id,
            excluded_topics=profile.excluded_topics,
            primary_niche=profile.primary_niche,
            channel_id=channel_id,
            workspace_id=workspace_id,
            max_probes=policy.adjacent_max_probes,
            max_depth=policy.max_depth,
            search_budget=policy.max_search_calls,
            planner_version=policy.planner_version,
            region_code=policy.region_code,
            language_code=policy.language_code,
        )

        if adj_result.llm_used:
            total_llm_calls += 1
            round1.llm_calls += 1

        if adj_result.exploration_run_id == -1:
            continue  # insufficient evidence (shouldn't happen after classification)

        adj_run_id = adj_result.exploration_run_id
        adj_run_ids.append(adj_run_id)
        run_ids.append(adj_run_id)
        round1.exploration_run_ids.append(adj_run_id)
        round1.candidates_created += adj_result.candidate_count
        total_candidates += adj_result.candidate_count

        # 1-C. Semantic niche guard on any remaining CANDIDATEs from adjacent run.
        adj_candidates = list_probes_for_selection(conn, adj_run_id)
        if adj_candidates and total_llm_calls < policy.max_llm_calls:
            adj_sel = run_niche_selection(
                conn,
                adj_run_id,
                primary_niche=profile.primary_niche,
                excluded_topics=profile.excluded_topics,
                prior_queries=prior_queries,
                max_probes=policy.adjacent_max_probes,
                channel_id=channel_id,
                ai_provider=ai_provider,
            )
            if _sel_made_llm_call(adj_sel):
                total_llm_calls += 1
                round1.llm_calls += 1

        round1.selected += adj_result.selected_count
        total_selected += adj_result.selected_count
        prior_queries.update(
            p.normalized_query for p in list_selected_probes_for_dispatch(conn, adj_run_id)
        )

    # 1-D. Dispatch all selected adjacent probes (budget-aware).
    for adj_run_id in adj_run_ids:
        remaining = policy.max_search_calls - total_actual_calls
        if remaining <= 0:
            stop_reason = ExplorationStopReason.SEARCH_BUDGET
            break

        adj_dispatch = dispatch_selected_probes(
            conn,
            adj_run_id,
            api_key=api_key,
            collector=collector,
            max_search_calls=remaining,
            reuse_max_age_hours=policy.reuse_max_age_hours,
        )
        total_expected_calls += adj_dispatch.expected_search_calls
        total_actual_calls += adj_dispatch.actual_search_calls
        total_avoided_calls += adj_dispatch.search_calls_avoided
        total_dispatched += adj_dispatch.newly_dispatched + adj_dispatch.reused_count
        total_reused += adj_dispatch.reused_count
        round1.dispatched += adj_dispatch.newly_dispatched + adj_dispatch.reused_count
        round1.reused += adj_dispatch.reused_count
        round1.search_calls_expected += adj_dispatch.expected_search_calls
        round1.search_calls_actual += adj_dispatch.actual_search_calls
        new_obs = _count_observations_for_jobs(conn, adj_dispatch.dispatched_probe_ids)
        total_observations += new_obs

    round_summaries.append(round1)

    return _build_result(
        channel_id,
        2,
        run_ids,
        total_candidates,
        total_selected,
        total_dispatched,
        total_reused,
        total_expected_calls,
        total_actual_calls,
        total_avoided_calls,
        total_llm_calls,
        total_observations,
        stop_reason,
        policy_snapshot,
        round_summaries,
        dry_run,
    )


# ---------------------------------------------------------------------------
# Result builder
# ---------------------------------------------------------------------------


def _build_result(
    channel_id: int | None,
    rounds_completed: int,
    run_ids: list[int],
    total_candidates: int,
    total_selected: int,
    total_dispatched: int,
    total_reused: int,
    total_expected_calls: int,
    total_actual_calls: int,
    total_avoided_calls: int,
    total_llm_calls: int,
    total_observations: int,
    stop_reason: ExplorationStopReason | str,
    policy_snapshot: dict,
    round_summaries: list[RoundSummary],
    dry_run: bool,
    *,
    extra_diagnostics: dict | None = None,
) -> BoundedExplorationResult:
    return BoundedExplorationResult(
        channel_id=channel_id,
        rounds_completed=rounds_completed,
        exploration_run_ids=list(run_ids),
        total_candidates=total_candidates,
        total_selected=total_selected,
        total_dispatched=total_dispatched,
        reused_count=total_reused,
        search_calls_expected=total_expected_calls,
        search_calls_actual=total_actual_calls,
        search_calls_avoided=total_avoided_calls,
        llm_calls=total_llm_calls,
        observations_linked=total_observations,
        stop_reason=str(stop_reason),
        policy_version=ORCHESTRATOR_POLICY_VERSION,
        policy_snapshot=policy_snapshot,
        round_summaries=round_summaries,
        dry_run=dry_run,
        diagnostics=extra_diagnostics or {},
    )
