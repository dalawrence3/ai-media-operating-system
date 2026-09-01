"""Phase 13D-E — Selected probe dispatch and evidence reuse orchestration.

NETWORK BOUNDARY: this is the ONLY Phase 13D module that may call YouTube.
  cold_start.py, adjacent.py, and selector.py must never gain collector calls.

No LLM calls. No Opportunity creation. No market interpretation.
Interpretation is Phase 13E. Scoring is Phase 13F.

V1 DISPATCH POLICY
------------------
1. Load SELECTED probes for the run, ordered priority_score DESC then probe_id ASC.
2. Merge dispatch policy snapshot into run.policy_json["dispatch"] (selector policy preserved).
3. For each probe (deterministic order):
   a. Idempotency: probe.status == 'dispatched' AND job succeeded/partial → skip.
   b. Reuse check: find a compatible fresh completed/partial search_scan job.
      - Compatibility: normalized_query, region_code, language_code, order, published_after
        must match exactly; prior.max_pages >= required; prior.max_results >= required.
      - Freshness: job.completed_at >= now - DISPATCH_REUSE_MAX_AGE_HOURS.
      - Minimum evidence: ≥ 1 SEARCH_RESULT_RANK observation linked to the prior job.
   c. If reusable: link probe to reused job (probe.dispatched_job_id), 0 search calls consumed.
   d. If not reusable and budget remains: create search_scan job, run Phase 13B collector.
      - On completed/partial: probe → DISPATCHED.
      - On failed: probe stays SELECTED (dispatched_job_id set for audit); retry is safe.
   e. If budget exhausted: probe → DEFERRED(dispatch_budget_exhausted).
   f. If no API key and no reuse: probe stays SELECTED; can retry when key is available.

Reuse does NOT duplicate observations. Original global observations are shared as-is.
Reuse does NOT corrupt origin provenance of the original job.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from app.intelligence.market.models import (
    SEARCH_RESULT_RANK,
    MarketCollectionJob,
    MarketJobOriginType,
)
from app.intelligence.market.planner_models import ExplorationProbe, ExplorationProbeType
from app.intelligence.market.planner_repository import (
    get_exploration_run,
    link_probe_evidence,
    list_selected_probes_for_dispatch,
    record_probe_attempted_dispatch,
    update_exploration_run_policy,
    update_probe_dispatch,
    update_probe_status,
)
from app.intelligence.market.repository import (
    create_market_collection_job,
    get_market_collection_job,
)

# ---------------------------------------------------------------------------
# V1 dispatch policy constants
# ---------------------------------------------------------------------------

DISPATCH_POLICY_VERSION = "v1"

# Conservative freshness window: YouTube search landscapes change, but within
# 12 hours the result set is substantially stable for exploration purposes.
# New-video tracking is handled by Phase 13C velocity rescans, not re-search.
DISPATCH_REUSE_MAX_AGE_HOURS = 12.0

# Key used in quota_policy_snapshot_json to store execution parameters.
# Dispatcher-created jobs always write this; CLI "scan" jobs may not.
DISPATCH_EXECUTION_PARAMS_KEY = "execution"

# Minimum required signal type for reuse approval.
# A partial job is reusable if search discovery succeeded (SEARCH_RESULT_RANK
# observations exist), even if video enrichment partially failed.
_REUSE_REQUIRED_SIGNAL = SEARCH_RESULT_RANK
_REUSE_MIN_SIGNAL_COUNT = 1


# ---------------------------------------------------------------------------
# Action labels
# ---------------------------------------------------------------------------


class DispatchAction:
    NEW_EXECUTION = "new_execution"
    EVIDENCE_REUSE = "evidence_reuse"
    DEFERRED_BUDGET = "deferred_budget"
    SKIPPED_DISPATCHED = "skipped_already_dispatched"
    SKIPPED_INELIGIBLE = "skipped_ineligible"
    FAILED_EXECUTION = "failed_execution"


# ---------------------------------------------------------------------------
# Typed result contracts
# ---------------------------------------------------------------------------


class ProbeDispatchDiagnostic(BaseModel):
    probe_id: int
    query_text: str
    probe_type: str
    action: str
    reused_job_id: int | None = None
    reuse_age_hours: float | None = None
    new_job_id: int | None = None
    job_status: str | None = None
    expected_search_calls: int = 1
    actual_search_calls: int = 0
    error: str | None = None


class ProbeDispatchResult(BaseModel):
    run_id: int
    selected_considered: int = 0
    newly_dispatched: int = 0
    reused_count: int = 0
    failed_count: int = 0
    partial_count: int = 0
    deferred_for_budget: int = 0
    expected_search_calls: int = 0
    actual_search_calls: int = 0
    search_calls_avoided: int = 0
    dispatched_probe_ids: list[int] = Field(default_factory=list)
    reused_probe_ids: list[int] = Field(default_factory=list)
    failed_probe_ids: list[int] = Field(default_factory=list)
    deferred_probe_ids: list[int] = Field(default_factory=list)
    policy_version: str = DISPATCH_POLICY_VERSION
    dry_run: bool = False
    diagnostics: list[ProbeDispatchDiagnostic] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Policy snapshot
# ---------------------------------------------------------------------------


def build_dispatch_policy_snapshot() -> dict:
    """V1 dispatch policy as a serialisable dict.

    Merged into run.policy_json["dispatch"] at the start of every dispatch run.
    Preserves the selector policy snapshot already stored in policy_json.
    """
    return {
        "dispatch_policy_version": DISPATCH_POLICY_VERSION,
        "reuse_max_age_hours": DISPATCH_REUSE_MAX_AGE_HOURS,
        "reuse_compatible_statuses": ["completed", "partial"],
        "reuse_min_required_signal": _REUSE_REQUIRED_SIGNAL,
        "reuse_min_signal_count": _REUSE_MIN_SIGNAL_COUNT,
        "reuse_compatibility_dimensions": [
            "normalized_query",
            "region_code",
            "language_code",
            "order",
            "max_pages_gte",
            "max_results_gte",
            "published_after",
        ],
        "partial_job_reuse_policy": ("allowed_when_search_result_rank_observations_present"),
        "failed_job_reuse_policy": "never",
        "budget_exhaustion_action": "deferred:dispatch_budget_exhausted",
        "missing_api_key_action": "skip_execution:probe_remains_selected",
        "ordering_policy": "priority_score_desc_then_probe_id_asc",
        "network_boundary": "dispatcher_only:cold_start_adjacent_selector_no_network",
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _origin_type_for_probe(probe_type: str) -> str:
    if probe_type == ExplorationProbeType.ADJACENT_TOPIC:
        return MarketJobOriginType.ADJACENT_TOPIC
    return MarketJobOriginType.EXPLORATION_PLANNER


def _has_required_reuse_observations(conn: sqlite3.Connection, job_id: int) -> bool:
    """Return True if the job has at least the minimum required SEARCH_RESULT_RANK observations."""
    row = conn.execute(
        """
        SELECT COUNT(*)
          FROM market_intelligence_observations o
          JOIN market_job_observations jo ON jo.observation_id = o.id
         WHERE jo.job_id = ? AND o.signal_type = ?
        """,
        (job_id, _REUSE_REQUIRED_SIGNAL),
    ).fetchone()
    return (row[0] if row else 0) >= _REUSE_MIN_SIGNAL_COUNT


def _row_to_job(row) -> MarketCollectionJob:
    return MarketCollectionJob(**dict(row))


# ---------------------------------------------------------------------------
# Reuse matching
# ---------------------------------------------------------------------------


def find_reusable_job(
    conn: sqlite3.Connection,
    probe: ExplorationProbe,
    *,
    max_age_hours: float = DISPATCH_REUSE_MAX_AGE_HOURS,
) -> tuple[MarketCollectionJob | None, float | None]:
    """Find the most-recent compatible fresh search_scan job that can satisfy this probe.

    Compatibility (all must match):
      - normalized_query exact match
      - region_code exact match (including None == None)
      - language_code exact match
      - order exact match
      - published_after exact match
      - prior.max_pages >= probe.collection_policy.max_pages
      - prior.max_results >= probe.collection_policy.max_results

    Freshness: job.completed_at within max_age_hours of now.

    Minimum evidence: at least 1 SEARCH_RESULT_RANK observation.
    FAILED jobs are never reused.

    Returns (job, age_hours) on match, (None, None) otherwise.
    """
    policy = probe.collection_policy()
    normalized_query = probe.normalized_query

    cutoff = (datetime.now(UTC) - timedelta(hours=max_age_hours)).strftime("%Y-%m-%dT%H:%M:%S")

    rows = conn.execute(
        """
        SELECT * FROM market_collection_jobs
         WHERE job_type = 'search_scan'
           AND status IN ('completed', 'partial')
           AND completed_at >= ?
           AND quota_policy_snapshot_json IS NOT NULL
         ORDER BY completed_at DESC
         LIMIT 100
        """,
        (cutoff,),
    ).fetchall()

    now_dt = datetime.now(UTC)

    for row in rows:
        job = _row_to_job(row)
        try:
            snapshot = json.loads(job.quota_policy_snapshot_json or "{}")
        except (ValueError, TypeError):
            continue
        exec_params = snapshot.get(DISPATCH_EXECUTION_PARAMS_KEY)
        if not exec_params:
            continue

        if exec_params.get("normalized_query") != normalized_query:
            continue
        if exec_params.get("region_code") != probe.region_code:
            continue
        if exec_params.get("language_code") != probe.language_code:
            continue
        if exec_params.get("order") != policy.order:
            continue
        if exec_params.get("published_after") != policy.published_after:
            continue
        if exec_params.get("max_pages", 0) < policy.max_pages:
            continue
        if exec_params.get("max_results", 0) < policy.max_results:
            continue

        if not _has_required_reuse_observations(conn, job.id):
            continue

        completed_at = job.completed_at or ""
        if completed_at:
            try:
                completed_dt = datetime.fromisoformat(completed_at).replace(tzinfo=UTC)
                age_hours = (now_dt - completed_dt).total_seconds() / 3600.0
            except ValueError:
                age_hours = max_age_hours
        else:
            age_hours = max_age_hours

        return job, age_hours

    return None, None


def _build_execution_params(probe: ExplorationProbe) -> dict:
    policy = probe.collection_policy()
    return {
        "normalized_query": probe.normalized_query,
        "region_code": probe.region_code,
        "language_code": probe.language_code,
        "order": policy.order,
        "max_pages": policy.max_pages,
        "max_results": policy.max_results,
        "published_after": policy.published_after,
    }


# ---------------------------------------------------------------------------
# Core dispatch logic
# ---------------------------------------------------------------------------


def _dispatch_one_probe(
    conn: sqlite3.Connection,
    probe: ExplorationProbe,
    *,
    now: str,
    api_key: str,
    collector,
    remaining_search_calls: list[int | None],  # mutable [budget] — None = unlimited
    reuse_max_age_hours: float,
    dry_run: bool,
) -> ProbeDispatchDiagnostic:
    """Dispatch a single SELECTED probe. Mutates remaining_search_calls[0].

    Returns a diagnostic record describing what happened.
    """
    policy = probe.collection_policy()
    diag = ProbeDispatchDiagnostic(
        probe_id=probe.id,
        query_text=probe.query_text,
        probe_type=probe.probe_type,
        action=DispatchAction.SKIPPED_INELIGIBLE,
        expected_search_calls=policy.expected_max_search_calls,
    )

    # --- Idempotency: already successfully dispatched ---
    if probe.status == "dispatched":
        prior_job = (
            get_market_collection_job(conn, probe.dispatched_job_id)
            if probe.dispatched_job_id
            else None
        )
        if prior_job and prior_job.status in ("completed", "partial"):
            diag.action = DispatchAction.SKIPPED_DISPATCHED
            diag.new_job_id = probe.dispatched_job_id
            diag.job_status = prior_job.status
            return diag

    # --- Reuse check ---
    reuse_job, reuse_age = find_reusable_job(conn, probe, max_age_hours=reuse_max_age_hours)

    if reuse_job is not None:
        diag.action = DispatchAction.EVIDENCE_REUSE
        diag.reused_job_id = reuse_job.id
        diag.reuse_age_hours = reuse_age
        diag.job_status = reuse_job.status
        diag.actual_search_calls = 0

        if not dry_run:
            update_probe_dispatch(
                conn,
                probe.id,
                dispatched_job_id=reuse_job.id,
                dispatched_at=now,
            )
            link_probe_evidence(
                conn,
                probe_id=probe.id,
                evidence_type="job",
                job_id=reuse_job.id,
                notes=f"dispatch:reuse:age_hours={reuse_age:.2f}",
            )
        return diag

    # --- Budget check ---
    budget = remaining_search_calls[0]
    if budget is not None and budget <= 0:
        diag.action = DispatchAction.DEFERRED_BUDGET
        if not dry_run:
            update_probe_status(
                conn,
                probe.id,
                status="deferred",
                decided_at=now,
                decision_reason="dispatch_budget_exhausted",
            )
        return diag

    # --- New execution ---
    if not api_key and collector is None:
        diag.action = DispatchAction.SKIPPED_INELIGIBLE
        diag.error = "no_api_key"
        return diag

    if dry_run:
        diag.action = DispatchAction.NEW_EXECUTION
        diag.actual_search_calls = policy.expected_max_search_calls
        return diag

    # Create job with execution params in quota snapshot for future reuse matching.
    exec_params = _build_execution_params(probe)
    quota_snapshot = {
        DISPATCH_EXECUTION_PARAMS_KEY: exec_params,
        "dispatch_policy_version": DISPATCH_POLICY_VERSION,
    }

    job = create_market_collection_job(
        conn,
        job_type="search_scan",
        origin_type=_origin_type_for_probe(probe.probe_type),
        channel_id=probe.channel_id,
        workspace_id=probe.workspace_id,
        parent_job_id=probe.parent_job_id,
        exploration_depth=probe.exploration_depth,
        seeds=[probe.query_text],
        quota_policy_snapshot=quota_snapshot,
    )
    diag.new_job_id = job.id

    # Record attempt immediately so failed jobs are auditable.
    record_probe_attempted_dispatch(conn, probe.id, job_id=job.id)

    from app.intelligence.market.collector import YouTubeMarketCollector

    coll = collector or YouTubeMarketCollector(api_key=api_key)
    try:
        col_result = coll.collect_search_scan(
            conn,
            job,
            query=probe.query_text,
            region_code=probe.region_code,
            language_code=probe.language_code,
            published_after=policy.published_after,
            order=policy.order,
            max_results=policy.max_results,
            max_pages=policy.max_pages,
            max_search_calls=policy.expected_max_search_calls,
        )
    finally:
        if collector is None:
            coll.close()

    diag.actual_search_calls = col_result.search_calls
    diag.job_status = col_result.status

    # Deduct from budget.
    if remaining_search_calls[0] is not None:
        remaining_search_calls[0] -= col_result.search_calls

    if col_result.status in ("completed", "partial"):
        # Transition to DISPATCHED only on success/partial.
        update_probe_dispatch(
            conn,
            probe.id,
            dispatched_job_id=job.id,
            dispatched_at=now,
        )
        link_probe_evidence(
            conn,
            probe_id=probe.id,
            evidence_type="job",
            job_id=job.id,
            notes="dispatch:new_execution",
        )
        diag.action = DispatchAction.NEW_EXECUTION
    else:
        # Failed: probe stays SELECTED (dispatched_job_id set via record_probe_attempted_dispatch).
        diag.action = DispatchAction.FAILED_EXECUTION

    return diag


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------


def dispatch_selected_probes(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    api_key: str = "",
    collector=None,
    max_search_calls: int | None = None,
    reuse_max_age_hours: float = DISPATCH_REUSE_MAX_AGE_HOURS,
    dry_run: bool = False,
) -> ProbeDispatchResult:
    """Dispatch all SELECTED probes for a run.

    Ordered: priority_score DESC, then probe_id ASC.
    Reusable probes consume 0 search calls.
    Budget-exhausted probes become DEFERRED(dispatch_budget_exhausted).
    Probes that fail execution stay SELECTED for safe retry.

    This is the ONLY Phase 13D function that calls YouTube. Do not call
    collect_search_scan from cold_start, adjacent, or selector.

    dry_run=True evaluates reuse and costs but makes zero network calls,
    creates zero jobs, and changes zero statuses or probe states.
    """
    now = _now()

    probes = list_selected_probes_for_dispatch(conn, run_id)
    result = ProbeDispatchResult(
        run_id=run_id,
        selected_considered=len(probes),
        dry_run=dry_run,
        policy_version=DISPATCH_POLICY_VERSION,
    )

    if not probes:
        return result

    result.expected_search_calls = sum(
        p.collection_policy().expected_max_search_calls for p in probes
    )

    # Merge dispatch policy into run.policy_json without overwriting selector snapshot.
    if not dry_run:
        run = get_exploration_run(conn, run_id)
        if run:
            try:
                existing_policy = json.loads(run.policy_json) if run.policy_json else {}
            except (ValueError, TypeError):
                existing_policy = {}
            existing_policy["dispatch"] = build_dispatch_policy_snapshot()
            update_exploration_run_policy(conn, run_id, json.dumps(existing_policy))

    remaining = [max_search_calls]  # mutable cell

    for probe in probes:
        diag = _dispatch_one_probe(
            conn,
            probe,
            now=now,
            api_key=api_key,
            collector=collector,
            remaining_search_calls=remaining,
            reuse_max_age_hours=reuse_max_age_hours,
            dry_run=dry_run,
        )
        result.diagnostics.append(diag)

        action = diag.action
        if action == DispatchAction.EVIDENCE_REUSE:
            result.reused_probe_ids.append(probe.id)
            result.dispatched_probe_ids.append(probe.id)
            result.reused_count += 1
            result.search_calls_avoided += probe.collection_policy().expected_max_search_calls

        elif action == DispatchAction.NEW_EXECUTION:
            result.dispatched_probe_ids.append(probe.id)
            result.actual_search_calls += diag.actual_search_calls
            if diag.job_status == "partial":
                result.partial_count += 1
                result.newly_dispatched += 1
            else:
                result.newly_dispatched += 1

        elif action == DispatchAction.FAILED_EXECUTION:
            result.failed_probe_ids.append(probe.id)
            result.failed_count += 1
            result.actual_search_calls += diag.actual_search_calls

        elif action == DispatchAction.DEFERRED_BUDGET:
            result.deferred_probe_ids.append(probe.id)
            result.deferred_for_budget += 1

    return result


def dispatch_probe(
    conn: sqlite3.Connection,
    probe_id: int,
    *,
    api_key: str = "",
    collector=None,
    reuse_max_age_hours: float = DISPATCH_REUSE_MAX_AGE_HOURS,
    dry_run: bool = False,
) -> ProbeDispatchResult:
    """Dispatch a single probe by ID.

    Uses the same orchestration as dispatch_selected_probes — not a separate
    code path. Useful for operator debugging or targeted retry.
    Only dispatches if the probe is in SELECTED status (or DISPATCHED+failed).
    """
    from app.intelligence.market.planner_repository import get_exploration_probe

    probe = get_exploration_probe(conn, probe_id)
    if probe is None:
        return ProbeDispatchResult(run_id=0)

    eligible_statuses = {"selected", "dispatched"}
    if probe.status not in eligible_statuses:
        return ProbeDispatchResult(run_id=probe.exploration_run_id)

    now = _now()
    policy = probe.collection_policy()
    result = ProbeDispatchResult(
        run_id=probe.exploration_run_id,
        selected_considered=1,
        expected_search_calls=policy.expected_max_search_calls,
        dry_run=dry_run,
        policy_version=DISPATCH_POLICY_VERSION,
    )

    remaining = [None]  # no budget limit for single-probe dispatch
    diag = _dispatch_one_probe(
        conn,
        probe,
        now=now,
        api_key=api_key,
        collector=collector,
        remaining_search_calls=remaining,
        reuse_max_age_hours=reuse_max_age_hours,
        dry_run=dry_run,
    )
    result.diagnostics.append(diag)

    action = diag.action
    if action == DispatchAction.EVIDENCE_REUSE:
        result.reused_probe_ids.append(probe.id)
        result.dispatched_probe_ids.append(probe.id)
        result.reused_count += 1
        result.search_calls_avoided += policy.expected_max_search_calls

    elif action == DispatchAction.NEW_EXECUTION:
        result.dispatched_probe_ids.append(probe.id)
        result.actual_search_calls += diag.actual_search_calls
        if diag.job_status == "partial":
            result.partial_count += 1
            result.newly_dispatched += 1
        else:
            result.newly_dispatched += 1

    elif action == DispatchAction.FAILED_EXECUTION:
        result.failed_probe_ids.append(probe.id)
        result.failed_count += 1
        result.actual_search_calls += diag.actual_search_calls

    return result
