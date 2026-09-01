"""Phase 13D-B — Cold-start bootstrap + bounded semantic seed expansion.

No YouTube calls. No Opportunity creation. No scoring mutations.
Single LLM call maximum. Deterministic fallback on LLM failure.

V1 PORTFOLIO ALLOCATION POLICY
-------------------------------
Cold-start must produce a portfolio of:
  - CHANNEL_BOOTSTRAP (direct profile anchors)
  - MARKET_REGION     (semantically diverse LLM-proposed regions)

CHANNEL_BOOTSTRAP probes are bounded by COLD_START_V1_ANCHOR_CAP to prevent
secondary niches from consuming all available capacity before the LLM can
propose diverse market regions.  Secondary niches that lose the anchor
competition are persisted as DEFERRED (portfolio_allocation) for auditability.

Allocation by max_probes (anchor_cap=2):
  max_probes=1 → 1 anchor, 0 semantic
  max_probes=2 → 1 anchor, 1 semantic
  max_probes=3 → 2 anchors, 1 semantic
  max_probes=N → 2 anchors, N-2 semantic  (N ≥ 4)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.ai.provider import AIProvider, AIRequest
from app.ai.registry import PromptRegistry
from app.intelligence.dedup import jaccard_similarity, normalize_topic
from app.intelligence.market.planner_models import (
    CollectionPolicy,
    ExplorationProbeType,
)
from app.intelligence.market.planner_prompts import (
    COLD_START_DISPATCHED_PROBE_THRESHOLD,
    COLD_START_LLM_CANDIDATE_HARD_CAP,
    COLD_START_MAX_LLM_CANDIDATES,
    COLD_START_PRIORITY_WEIGHTS,
    COLD_START_PROMPT_NAME,
    COLD_START_PROMPT_VERSION,
    COLD_START_V1_ANCHOR_CAP,
    DEFAULT_JACCARD_DEDUP_THRESHOLD,
    EXCLUDED_TOPIC_JACCARD_THRESHOLD,
)
from app.intelligence.market.planner_repository import (
    count_dispatched_probes,
    create_exploration_probe,
    create_exploration_run,
    list_prior_market_region_labels,
    list_prior_probe_normalized_queries,
    update_exploration_run_provenance,
    update_exploration_run_status,
    update_probe_status,
)

# ---------------------------------------------------------------------------
# Typed input / output contracts
# ---------------------------------------------------------------------------


class ExplorationProfile(BaseModel):
    """Typed input derived from ChannelProfileVersion for cold-start planning."""

    primary_niche: str
    secondary_niches: list[str] = Field(default_factory=list)
    excluded_topics: list[str] = Field(default_factory=list)
    audience_description: str = ""
    duplicate_similarity_threshold: float = DEFAULT_JACCARD_DEDUP_THRESHOLD
    region_code: str | None = None
    language_code: str | None = None

    model_config = {"extra": "forbid"}


class SeedExpansionCandidate(BaseModel):
    """One market region proposed by the LLM."""

    query: str
    market_region_label: str
    rationale: str
    semantic_fit_score_estimate: float
    distinctiveness_rationale: str

    model_config = {"extra": "forbid"}


class SeedExpansionOutput(BaseModel):
    """Structured LLM response — list of market region candidates."""

    probes: list[SeedExpansionCandidate] = Field(max_length=COLD_START_LLM_CANDIDATE_HARD_CAP)

    model_config = {"extra": "forbid"}


class ColdStartExplorationResult(BaseModel):
    """Typed return contract from plan_cold_start."""

    exploration_run_id: int
    candidate_count: int
    selected_count: int
    deferred_count: int
    rejected_count: int
    selected_probe_ids: list[int]
    llm_used: bool
    provider_name: str | None
    model: str | None
    prompt_version: str | None
    diagnostics: dict[str, Any]

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InsufficientProfileError(Exception):
    """Raised when the channel profile lacks the minimum data for cold-start planning."""


# ---------------------------------------------------------------------------
# Cold-start predicate
# ---------------------------------------------------------------------------


def is_channel_cold_start(conn: sqlite3.Connection, channel_id: int | None) -> bool:
    """Return True if the channel has fewer than the threshold dispatched probes."""
    return count_dispatched_probes(conn, channel_id) < COLD_START_DISPATCHED_PROBE_THRESHOLD


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _compute_anchor_slots(max_probes: int, anchor_cap: int) -> int:
    """Return the number of CHANNEL_BOOTSTRAP slots for this run (V1 policy).

    Always 1 for primary niche. Bounded by anchor_cap so secondary niches
    cannot crowd out MARKET_REGION exploration capacity.

    max_probes=0 → 0 (degenerate, validated upstream)
    max_probes=1 → 1 (only primary niche anchor)
    max_probes=2 → 1 (1 anchor + 1 semantic)
    max_probes=3 → 2 (2 anchors + 1 semantic)
    max_probes≥4 → 2 (2 anchors + N-2 semantic, capped by anchor_cap=2)
    """
    if max_probes <= 0:
        return 0
    return min(anchor_cap, max(1, max_probes - 1))


def _compute_priority_score(
    *,
    niche_fit: float,
    novelty: float,
) -> tuple[float, dict[str, float]]:
    """Return (weighted_score, components_dict). depth_factor is always 1.0 at depth 0."""
    components: dict[str, float] = {
        "niche_fit": max(0.0, min(1.0, niche_fit)),
        "novelty": max(0.0, min(1.0, novelty)),
        "evidence_strength": 0.0,
        "velocity_trigger": 0.0,
        "corroboration": 0.0,
        "depth_factor": 1.0,
    }
    score = sum(COLD_START_PRIORITY_WEIGHTS[k] * v for k, v in components.items())
    return score, components


def _novelty_score(normalized_query: str, prior_queries: set[str]) -> float:
    """Cross-run coverage: same query is allowed but deprioritised."""
    return 0.2 if normalized_query in prior_queries else 1.0


def _is_excluded(
    normalized_query: str,
    excluded_normalized: list[str],
    threshold: float,
) -> bool:
    return any(jaccard_similarity(normalized_query, ex) >= threshold for ex in excluded_normalized)


def _is_near_duplicate(
    normalized_query: str,
    selected_normalized: list[str],
    threshold: float,
) -> bool:
    return any(
        jaccard_similarity(normalized_query, sel) >= threshold for sel in selected_normalized
    )


def _default_policy(profile: ExplorationProfile) -> CollectionPolicy:
    return CollectionPolicy(
        max_pages=1,
        max_results=25,
        order="relevance",
        region_code=profile.region_code,
        language_code=profile.language_code,
    )


# ---------------------------------------------------------------------------
# Main planner entry point
# ---------------------------------------------------------------------------


def plan_cold_start(
    conn: sqlite3.Connection,
    profile: ExplorationProfile,
    *,
    provider: AIProvider,
    channel_id: int | None = None,
    workspace_id: str | None = None,
    max_probes: int = 10,
    max_depth: int = 1,
    search_budget: int = 15,
    planner_version: str = "v1",
) -> ColdStartExplorationResult:
    """Plan a cold-start exploration run.

    V1 portfolio allocation:
    1. Validate profile
    2. Create + open exploration run
    3. Anchor pass: select up to anchor_slots CHANNEL_BOOTSTRAP probes; defer extras
    4. LLM call for MARKET_REGION seed expansion (single call, if budget remains)
    5. Market-region pass: dedup, score, select within remaining budget; defer/reject rest
    6. Record LLM provenance and close run
    """
    # 1. Validate
    if not profile.primary_niche or not profile.primary_niche.strip():
        raise InsufficientProfileError("primary_niche is required for cold-start planning")

    dedup_threshold = profile.duplicate_similarity_threshold
    excluded_normalized = [normalize_topic(t) for t in profile.excluded_topics if t.strip()]

    # 2. Create run
    run = create_exploration_run(
        conn,
        channel_id=channel_id,
        workspace_id=workspace_id,
        planner_version=planner_version,
        max_depth=max_depth,
        max_probes=max_probes,
        search_budget=search_budget,
    )
    run = update_exploration_run_status(conn, run.id, status="running", started_at=_now())

    selected_probe_ids: list[int] = []
    selected_normalized: list[str] = []
    search_calls_used = 0

    # ---------------------------------------------------------------------------
    # 3. Anchor pass — V1 portfolio allocation
    # ---------------------------------------------------------------------------
    anchor_slots = _compute_anchor_slots(max_probes, COLD_START_V1_ANCHOR_CAP)
    anchor_selected = 0
    bootstrap_count = 0  # selected anchors
    bootstrap_deferred_count = 0

    bootstrap_sources = [profile.primary_niche] + list(profile.secondary_niches)
    bootstrap_components: dict[str, float] = {
        "niche_fit": 1.0,
        "novelty": 1.0,
        "evidence_strength": 0.0,
        "velocity_trigger": 0.0,
        "corroboration": 0.0,
        "depth_factor": 1.0,
    }

    for niche_text in bootstrap_sources:
        if not niche_text or not niche_text.strip():
            continue
        nq = normalize_topic(niche_text)
        if not nq:
            continue
        # Excluded topics and semantic duplicates are silently skipped (not created).
        if _is_excluded(nq, excluded_normalized, EXCLUDED_TOPIC_JACCARD_THRESHOLD):
            continue
        if _is_near_duplicate(nq, selected_normalized, dedup_threshold):
            continue

        policy = _default_policy(profile)
        probe = create_exploration_probe(
            conn,
            run_id=run.id,
            query_text=niche_text.strip(),
            normalized_query=nq,
            probe_type=ExplorationProbeType.CHANNEL_BOOTSTRAP.value,
            channel_id=channel_id,
            workspace_id=workspace_id,
            exploration_depth=0,
            region_code=profile.region_code,
            language_code=profile.language_code,
            collection_policy=policy,
            planner_version=planner_version,
        )

        # Decide: select or defer based on portfolio cap and search budget.
        anchor_budget_ok = search_calls_used + policy.expected_max_search_calls <= search_budget
        anchor_cap_ok = anchor_selected < anchor_slots

        if anchor_cap_ok and anchor_budget_ok:
            update_probe_status(
                conn,
                probe.id,
                status="selected",
                decided_at=_now(),
                decision_reason="channel_bootstrap: direct profile seed",
                priority_score=1.0,
                priority_components_json=json.dumps(bootstrap_components),
                niche_fit_score=1.0,
            )
            selected_probe_ids.append(probe.id)
            selected_normalized.append(nq)
            search_calls_used += policy.expected_max_search_calls
            anchor_selected += 1
            bootstrap_count += 1
        else:
            reason = (
                "portfolio_allocation: search budget reached"
                if not anchor_budget_ok
                else "portfolio_allocation: anchor capacity exhausted"
            )
            update_probe_status(
                conn,
                probe.id,
                status="deferred",
                decided_at=_now(),
                decision_reason=reason,
                priority_score=1.0,
                priority_components_json=json.dumps(bootstrap_components),
                niche_fit_score=1.0,
            )
            bootstrap_deferred_count += 1

    # ---------------------------------------------------------------------------
    # 4. LLM call — only if market-region capacity remains
    # ---------------------------------------------------------------------------
    llm_used = False
    provider_name: str | None = None
    model_used: str | None = None
    prompt_version_used: str | None = None
    llm_candidates: list[SeedExpansionCandidate] = []
    llm_error: str | None = None

    prior_queries = list_prior_probe_normalized_queries(conn, channel_id)
    prior_regions = list_prior_market_region_labels(conn, channel_id)

    remaining_probe_budget = max_probes - len(selected_probe_ids)
    remaining_search_budget = search_budget - search_calls_used

    if remaining_probe_budget > 0 and remaining_search_budget > 0:
        try:
            registry = PromptRegistry()
            prompt = registry.get(COLD_START_PROMPT_NAME, COLD_START_PROMPT_VERSION)
            user_msg = prompt.format_user(
                primary_niche=profile.primary_niche,
                secondary_niches=(
                    ", ".join(profile.secondary_niches) if profile.secondary_niches else "none"
                ),
                excluded_topics=(
                    ", ".join(profile.excluded_topics) if profile.excluded_topics else "none"
                ),
                audience_description=profile.audience_description or "not specified",
                prior_regions=(", ".join(prior_regions) if prior_regions else "none"),
                max_candidates=str(COLD_START_MAX_LLM_CANDIDATES),
            )
            model_id = getattr(provider, "_model", provider.name)
            request = AIRequest(
                system=prompt.system,
                user=user_msg,
                model=model_id,
                response_schema=SeedExpansionOutput,
                prompt_name=COLD_START_PROMPT_NAME,
                prompt_version=COLD_START_PROMPT_VERSION,
            )
            response = provider.complete(request)
            provider_name = provider.name
            model_used = getattr(provider, "_model", provider.name)
            prompt_version_used = COLD_START_PROMPT_VERSION

            if response.parsed is not None:
                seed_output: SeedExpansionOutput = response.parsed  # type: ignore[assignment]
                llm_candidates = seed_output.probes[:COLD_START_LLM_CANDIDATE_HARD_CAP]
                llm_used = True
            else:
                llm_error = "LLM returned non-structured response"

        except Exception as exc:
            llm_error = str(exc)

    # ---------------------------------------------------------------------------
    # 5. Market-region pass — greedy selection within remaining budget
    # ---------------------------------------------------------------------------
    candidate_count = len(llm_candidates)
    market_region_deferred = 0
    rejected_count = 0

    scored: list[tuple[float, SeedExpansionCandidate, str, dict[str, float], bool]] = []
    for cand in llm_candidates:
        nq = normalize_topic(cand.query)
        if not nq:
            rejected_count += 1
            continue
        is_excluded = _is_excluded(nq, excluded_normalized, EXCLUDED_TOPIC_JACCARD_THRESHOLD)
        score, comps = _compute_priority_score(
            niche_fit=max(0.0, min(1.0, cand.semantic_fit_score_estimate)),
            novelty=_novelty_score(nq, prior_queries),
        )
        scored.append((score, cand, nq, comps, is_excluded))

    # Non-excluded first, then sort by score descending (stable)
    scored.sort(key=lambda x: (not x[4], x[0]), reverse=True)

    for score, cand, nq, comps, is_excluded_topic in scored:
        policy = _default_policy(profile)
        probe = create_exploration_probe(
            conn,
            run_id=run.id,
            query_text=cand.query.strip(),
            normalized_query=nq,
            probe_type=ExplorationProbeType.MARKET_REGION.value,
            channel_id=channel_id,
            workspace_id=workspace_id,
            exploration_depth=0,
            region_code=profile.region_code,
            language_code=profile.language_code,
            collection_policy=policy,
            planner_version=planner_version,
            market_region_label=cand.market_region_label,
        )

        if is_excluded_topic:
            update_probe_status(
                conn,
                probe.id,
                status="rejected",
                decided_at=_now(),
                decision_reason="excluded_topic: overlaps channel excluded topics",
                priority_score=0.0,
                priority_components_json=json.dumps(comps),
                niche_fit_score=comps["niche_fit"],
            )
            rejected_count += 1

        elif _is_near_duplicate(nq, selected_normalized, dedup_threshold):
            update_probe_status(
                conn,
                probe.id,
                status="deferred",
                decided_at=_now(),
                decision_reason=f"near_duplicate: Jaccard >= {dedup_threshold}",
                priority_score=score,
                priority_components_json=json.dumps(comps),
                niche_fit_score=comps["niche_fit"],
            )
            market_region_deferred += 1

        elif (
            len(selected_probe_ids) >= max_probes
            or search_calls_used + policy.expected_max_search_calls > search_budget
        ):
            update_probe_status(
                conn,
                probe.id,
                status="deferred",
                decided_at=_now(),
                decision_reason="budget_exhausted: probe or search budget reached",
                priority_score=score,
                priority_components_json=json.dumps(comps),
                niche_fit_score=comps["niche_fit"],
            )
            market_region_deferred += 1

        else:
            update_probe_status(
                conn,
                probe.id,
                status="selected",
                decided_at=_now(),
                decision_reason=f"market_region: {cand.market_region_label}",
                priority_score=score,
                priority_components_json=json.dumps(comps),
                niche_fit_score=comps["niche_fit"],
            )
            selected_probe_ids.append(probe.id)
            selected_normalized.append(nq)
            search_calls_used += policy.expected_max_search_calls

    # ---------------------------------------------------------------------------
    # 6. Provenance + run finalisation
    # ---------------------------------------------------------------------------
    if llm_used and provider_name and model_used:
        update_exploration_run_provenance(
            conn,
            run.id,
            provider=provider_name,
            model=model_used,
            prompt_version=prompt_version_used,
        )

    selected_count = len(selected_probe_ids)
    total_deferred = bootstrap_deferred_count + market_region_deferred
    final_status = (
        "partial"
        if llm_error and remaining_probe_budget > 0 and remaining_search_budget > 0
        else "completed"
    )
    update_exploration_run_status(
        conn,
        run.id,
        status=final_status,
        candidate_count=candidate_count,
        selected_count=selected_count,
        deferred_count=total_deferred,
        rejected_count=rejected_count,
        completed_at=_now(),
        error_message=llm_error,
    )

    diagnostics: dict[str, Any] = {
        "anchor_slots_allocated": anchor_slots,
        "bootstrap_count": bootstrap_count,
        "bootstrap_deferred_count": bootstrap_deferred_count,
        "search_calls_used": search_calls_used,
        "final_status": final_status,
    }
    if llm_used:
        diagnostics["llm_candidate_count"] = candidate_count
    if llm_error:
        diagnostics["llm_error"] = llm_error
        if remaining_probe_budget > 0 and remaining_search_budget > 0:
            diagnostics["llm_fallback"] = "deterministic seeds only"

    return ColdStartExplorationResult(
        exploration_run_id=run.id,
        candidate_count=candidate_count,
        selected_count=selected_count,
        deferred_count=total_deferred,
        rejected_count=rejected_count,
        selected_probe_ids=selected_probe_ids,
        llm_used=llm_used,
        provider_name=provider_name,
        model=model_used,
        prompt_version=prompt_version_used,
        diagnostics=diagnostics,
    )
