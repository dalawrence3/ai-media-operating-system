"""Phase 13D-D — Semantic niche guard + authoritative priority / branch selection.
Phase 13D-D.1 — Semantic inter-probe duplicate suppression + policy provenance.

No YouTube calls. No Opportunity creation. No trending/viral/underserved labels.
Single LLM batch call maximum per selection run.

V1 SELECTION POLICY (see build_selector_policy_snapshot for machine-readable form)
-------------------
Selection pipeline order:
  1. Deterministic excluded-topic rejection (Jaccard ≥ EXCLUDED_TOPIC_JACCARD_THRESHOLD)
  2. LLM batch: niche eligibility + inter-probe semantic duplicate detection
  3. LLM-ineligible → REJECTED
  4. Priority components computed for all eligible probes
  5. Semantic duplicate resolution: non-canonical probes → DEFERRED(semantic_duplicate_of:<id>)
  6. Diversity-aware portfolio selection on canonical probes only
       - Cluster cap: ≤ SELECTOR_MAX_REGION_PER_CLUSTER per Jaccard cluster
       - Portfolio ratio: 50 % exploration / 50 % evidence slots, with overflow
  7. Policy snapshot persisted to run.policy_json

Velocity normalization ownership: UPSTREAM (adjacent.py._normalize_velocity_trigger).
  Formula: min(1.0, peak_vpd / 10_000.0) with negative → 0.0.
  The selector inherits priority_components_json from prior planners verbatim.
  SELECTOR_VELOCITY_REF_VIEWS_PER_DAY is documented but not applied here.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.ai.provider import AIProvider, AIRequest
from app.ai.registry import PromptRegistry
from app.intelligence.dedup import jaccard_similarity, normalize_topic
from app.intelligence.market.planner_models import (
    ExplorationProbe,
    ExplorationProbeType,
    PriorityComponents,
)
from app.intelligence.market.planner_prompts import (
    DEFAULT_JACCARD_DEDUP_THRESHOLD,
    EXCLUDED_TOPIC_JACCARD_THRESHOLD,
    SELECTOR_DIVERSITY_POLICY_VERSION,
    SELECTOR_EXPLORATION_SLOT_RATIO,
    SELECTOR_MAX_BATCH_SIZE,
    SELECTOR_MAX_REGION_PER_CLUSTER,
    SELECTOR_POLICY_VERSION,
    SELECTOR_PRIORITY_WEIGHTS,
    SELECTOR_PROMPT_NAME,
    SELECTOR_PROMPT_VERSION,
    SELECTOR_REGION_CLUSTER_JACCARD,
    SELECTOR_VELOCITY_NORM_VERSION,
    SELECTOR_VELOCITY_REF_VIEWS_PER_DAY,
)
from app.intelligence.market.planner_repository import (
    list_probes_for_selection,
    update_exploration_run_policy,
    update_exploration_run_provenance,
    update_probe_status,
)

# ---------------------------------------------------------------------------
# Depth-factor lookup (V1)
# ---------------------------------------------------------------------------

_DEPTH_FACTORS: dict[int, float] = {0: 1.0, 1: 0.7, 2: 0.4}
_DEPTH_FACTOR_DEFAULT: float = 0.2

# Types that consume "exploration" slots (no prior evidence required).
_EXPLORATION_TYPES: frozenset[str] = frozenset(
    {
        ExplorationProbeType.CHANNEL_BOOTSTRAP,
        ExplorationProbeType.MARKET_REGION,
    }
)


# ---------------------------------------------------------------------------
# Typed contracts
# ---------------------------------------------------------------------------


class NicheEvaluation(BaseModel):
    probe_id: int
    eligible: bool
    fit_score: float = Field(ge=0.0, le=1.0)
    rationale: str
    semantic_duplicate_of: int | None = None  # Phase 13D-D.1: inter-probe duplicate flag


class NicheGuardOutput(BaseModel):
    evaluations: list[NicheEvaluation]


class SelectionResult(BaseModel):
    run_id: int
    selected: list[int] = Field(default_factory=list)
    deferred: list[int] = Field(default_factory=list)
    rejected: list[int] = Field(default_factory=list)
    llm_provider: str | None = None
    llm_model: str | None = None
    policy_version: str = SELECTOR_POLICY_VERSION
    llm_error: str | None = None


# ---------------------------------------------------------------------------
# Policy snapshot (persisted to run.policy_json at selection start)
# ---------------------------------------------------------------------------


def build_selector_policy_snapshot() -> dict[str, Any]:
    """Return the complete V1 selector policy as a serialisable dict.

    Persisted at the start of every selection run so historical decisions
    remain reproducible even after constants change.
    """
    return {
        "policy_version": SELECTOR_POLICY_VERSION,
        "priority_weights": dict(SELECTOR_PRIORITY_WEIGHTS),
        "applicable_component_policy": "skip_none_renormalize_v1",
        "semantic_evaluator": {
            "prompt_name": SELECTOR_PROMPT_NAME,
            "prompt_version": SELECTOR_PROMPT_VERSION,
        },
        "semantic_duplicate_policy": {
            "version": "v1",
            "output_field": "semantic_duplicate_of",
            "representative_selection": "highest_score_then_lowest_probe_id",
            "deferred_reason_prefix": "semantic_duplicate_of:",
            "validation": "referenced_id_must_be_in_batch_and_eligible_not_self",
        },
        "diversity_policy": {
            "version": SELECTOR_DIVERSITY_POLICY_VERSION,
            "cluster_jaccard_threshold": SELECTOR_REGION_CLUSTER_JACCARD,
            "max_probes_per_cluster": SELECTOR_MAX_REGION_PER_CLUSTER,
        },
        "portfolio_policy": {
            "exploration_slot_ratio": SELECTOR_EXPLORATION_SLOT_RATIO,
            "overflow": True,
        },
        "velocity_normalization": {
            "version": SELECTOR_VELOCITY_NORM_VERSION,
            "ownership": "upstream_adjacent_planner",
            "formula": "min(1.0, peak_vpd / 10000.0) with negative_clamp_to_zero",
            "ref_views_per_day_documented_only": SELECTOR_VELOCITY_REF_VIEWS_PER_DAY,
            "note": (
                "velocity_trigger normalized by adjacent.py._normalize_velocity_trigger; "
                "selector inherits priority_components_json verbatim without re-normalizing"
            ),
        },
        "excluded_topic_jaccard_threshold": EXCLUDED_TOPIC_JACCARD_THRESHOLD,
        "cluster_jaccard_threshold": SELECTOR_REGION_CLUSTER_JACCARD,
        "dedup_jaccard_threshold": DEFAULT_JACCARD_DEDUP_THRESHOLD,
        "max_batch_size": SELECTOR_MAX_BATCH_SIZE,
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _check_excluded(normalized_query: str, excluded_topics: list[str]) -> str | None:
    """Return the first excluded topic that is Jaccard-similar enough, or None."""
    for topic in excluded_topics:
        norm = normalize_topic(topic)
        if jaccard_similarity(normalized_query, norm) >= EXCLUDED_TOPIC_JACCARD_THRESHOLD:
            return topic
    return None


def _run_llm_batch(
    provider: AIProvider,
    batch: list[ExplorationProbe],
    primary_niche: str,
    excluded_topics: list[str],
) -> tuple[dict[int, NicheEvaluation], str, str]:
    """Call the niche-guard LLM once and return a map of probe_id → NicheEvaluation.

    The response includes both niche eligibility (eligible, fit_score) and
    inter-probe semantic duplicate flags (semantic_duplicate_of).
    """
    registry = PromptRegistry()
    prompt = registry.get(SELECTOR_PROMPT_NAME, SELECTOR_PROMPT_VERSION)

    candidates = [{"probe_id": p.id, "query": p.query_text} for p in batch]
    user_text = prompt.format_user(
        primary_niche=primary_niche,
        excluded_topics=", ".join(excluded_topics) if excluded_topics else "none",
        candidate_count=len(candidates),
        candidates_json=json.dumps(candidates, indent=2),
    )

    model_id = getattr(provider, "_model", provider.name)
    request = AIRequest(
        system=prompt.system,
        user=user_text,
        model=model_id,
        response_schema=NicheGuardOutput,
        prompt_name=SELECTOR_PROMPT_NAME,
        prompt_version=SELECTOR_PROMPT_VERSION,
    )
    response = provider.complete(request)
    output: NicheGuardOutput = response.parsed  # type: ignore[assignment]
    result = {ev.probe_id: ev for ev in output.evaluations}
    return result, response.provider_name, getattr(provider, "_model", provider.name)


def _validate_duplicate_refs(
    niche_fits: dict[int, NicheEvaluation],
    batch_ids: set[int],
    eligible_ids: set[int],
) -> None:
    """Sanitize semantic_duplicate_of fields in-place.

    Invalid references — self-references, unknown probe IDs, or references to
    ineligible probes — are cleared to None.
    """
    for ev in niche_fits.values():
        dup = ev.semantic_duplicate_of
        if dup is None:
            continue
        if dup == ev.probe_id or dup not in batch_ids or dup not in eligible_ids:
            ev.semantic_duplicate_of = None


def _find_root(probe_id: int, parent: dict[int, int]) -> int:
    """Iterative root-finder with cycle guard (bounded hops)."""
    seen: set[int] = set()
    x = probe_id
    while x in parent:
        if x in seen:
            break  # cycle detected — stop here
        seen.add(x)
        x = parent[x]
    return x


# Type alias for a scored probe tuple.
_Scored = tuple[ExplorationProbe, PriorityComponents, float, "NicheEvaluation | None"]


def _resolve_duplicate_groups(
    scored: list[_Scored],
) -> tuple[list[_Scored], dict[int, int]]:
    """Identify semantic duplicate groups and return canonical representatives.

    Uses union-find on the semantic_duplicate_of graph to handle chains.
    Canonical representative = highest priority score, tiebreak = lowest probe_id.

    Returns:
        canonicals:           items that should proceed to portfolio selection.
        non_canonical_to_canonical: {probe_id: canonical_probe_id} for deferral.
    """
    eligible_ids = {item[0].id for item in scored}

    # Build parent map from semantic_duplicate_of references.
    parent: dict[int, int] = {}
    for probe, _, _, eval_ in scored:
        if eval_ is not None and eval_.semantic_duplicate_of is not None:
            dup_of = eval_.semantic_duplicate_of
            if dup_of in eligible_ids and dup_of != probe.id:
                parent[probe.id] = dup_of

    # Group probes by their root.
    groups: dict[int, list[_Scored]] = defaultdict(list)
    for item in scored:
        root = _find_root(item[0].id, parent)
        groups[root].append(item)

    canonicals: list[_Scored] = []
    non_canonical_to_canonical: dict[int, int] = {}

    for members in groups.values():
        if len(members) == 1:
            canonicals.append(members[0])
        else:
            # Deterministic: highest score first, then lowest probe_id.
            sorted_members = sorted(members, key=lambda x: (-x[2], x[0].id))
            canonical = sorted_members[0]
            canonicals.append(canonical)
            for non_canon in sorted_members[1:]:
                non_canonical_to_canonical[non_canon[0].id] = canonical[0].id

    # Preserve original score-descending ordering for canonicals.
    canonical_ids = {item[0].id for item in canonicals}
    canonicals = [item for item in scored if item[0].id in canonical_ids]

    return canonicals, non_canonical_to_canonical


def _compute_components(
    probe: ExplorationProbe,
    eval_: NicheEvaluation | None,
    prior_queries: set[str],
) -> tuple[PriorityComponents, float]:
    """Compute authoritative priority components and weighted score for a probe."""
    existing = probe.priority_components()

    niche_fit = eval_.fit_score if eval_ is not None else None
    novelty = 0.2 if probe.normalized_query in prior_queries else 1.0
    depth_factor = _DEPTH_FACTORS.get(probe.exploration_depth, _DEPTH_FACTOR_DEFAULT)

    # Evidence components: inherit from previous planner (None for cold-start probes).
    evidence_strength = existing.evidence_strength if existing else None
    velocity_trigger = existing.velocity_trigger if existing else None
    corroboration = existing.corroboration if existing else None

    components = PriorityComponents(
        niche_fit=niche_fit,
        novelty=novelty,
        evidence_strength=evidence_strength,
        velocity_trigger=velocity_trigger,
        corroboration=corroboration,
        depth_factor=depth_factor,
    )
    score = _weighted_score(components)
    return components, score


def _weighted_score(components: PriorityComponents) -> float:
    """Applicable-component weighted average over SELECTOR_PRIORITY_WEIGHTS.

    Components that are None are excluded from both numerator and denominator.
    Zero (observed) is included and lowers the score; None (unavailable) is not.
    """
    total_weight = 0.0
    weighted_sum = 0.0
    for name, weight in SELECTOR_PRIORITY_WEIGHTS.items():
        value = getattr(components, name)
        if value is not None:
            total_weight += weight
            weighted_sum += value * weight
    if total_weight == 0.0:
        return 0.0
    return weighted_sum / total_weight


def _build_cluster_map(probes: list[ExplorationProbe]) -> dict[int, int]:
    """Greedy Jaccard clustering — returns probe.id → cluster_id."""
    cluster_reps: list[str] = []
    probe_to_cluster: dict[int, int] = {}

    for probe in probes:
        placed = False
        for c_id, rep in enumerate(cluster_reps):
            if jaccard_similarity(probe.normalized_query, rep) >= SELECTOR_REGION_CLUSTER_JACCARD:
                probe_to_cluster[probe.id] = c_id
                placed = True
                break
        if not placed:
            probe_to_cluster[probe.id] = len(cluster_reps)
            cluster_reps.append(probe.normalized_query)

    return probe_to_cluster


def _sem_status(eval_: NicheEvaluation | None) -> str:
    if eval_ is None:
        return "pending"
    return "eligible" if eval_.eligible else "ineligible"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_niche_selection(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    primary_niche: str,
    excluded_topics: list[str],
    prior_queries: set[str],
    max_probes: int,
    channel_id: int | None = None,
    ai_provider: AIProvider | None = None,
) -> SelectionResult:
    """Evaluate candidates for a run and persist SELECTED / DEFERRED / REJECTED decisions.

    No YouTube calls. No Opportunity creation. No scoring mutations outside this run.
    Policy snapshot is written to run.policy_json before any decisions are made.

    Pipeline:
      excluded-topic guard → LLM niche+duplicate batch → ineligible reject →
      priority scoring → semantic dedup → portfolio/diversity → persist decisions
    """
    now = _now()

    # -----------------------------------------------------------------
    # 0. Persist V1 policy snapshot (written early so partial failures
    #    still leave the run auditable).
    # -----------------------------------------------------------------
    update_exploration_run_policy(conn, run_id, json.dumps(build_selector_policy_snapshot()))

    # -----------------------------------------------------------------
    # 1. Load CANDIDATE + DEFERRED probes for this run
    # -----------------------------------------------------------------
    probes = list_probes_for_selection(conn, run_id)
    if not probes:
        return SelectionResult(run_id=run_id, policy_version=SELECTOR_POLICY_VERSION)

    # -----------------------------------------------------------------
    # 2. Deterministic excluded-topic rejection (Layer 1)
    # -----------------------------------------------------------------
    remaining: list[ExplorationProbe] = []
    rejected_ids: list[int] = []

    for probe in probes:
        match = _check_excluded(probe.normalized_query, excluded_topics)
        if match:
            update_probe_status(
                conn,
                probe.id,
                status="rejected",
                decided_at=now,
                decision_reason=f"rejected:excluded_topic_match={match}",
                semantic_fit_status="ineligible",
            )
            rejected_ids.append(probe.id)
        else:
            remaining.append(probe)

    # -----------------------------------------------------------------
    # 3. LLM batch niche guard + duplicate detection (Layer 2)
    # -----------------------------------------------------------------
    niche_fits: dict[int, NicheEvaluation] = {}
    batch_ids: set[int] = set()
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_error: str | None = None

    if ai_provider and remaining:
        batch = remaining[:SELECTOR_MAX_BATCH_SIZE]
        batch_ids = {p.id for p in batch}
        try:
            niche_fits, llm_provider, llm_model = _run_llm_batch(
                ai_provider, batch, primary_niche, excluded_topics
            )
        except Exception as exc:
            llm_error = str(exc)

    # -----------------------------------------------------------------
    # 4. LLM-based rejection for ineligible probes
    # -----------------------------------------------------------------
    eligible: list[ExplorationProbe] = []

    for probe in remaining:
        eval_ = niche_fits.get(probe.id)
        if eval_ is not None and not eval_.eligible:
            update_probe_status(
                conn,
                probe.id,
                status="rejected",
                decided_at=now,
                decision_reason=(f"rejected:niche_guard_ineligible={eval_.rationale[:200]}"),
                semantic_fit_status="ineligible",
                niche_fit_score=eval_.fit_score,
            )
            rejected_ids.append(probe.id)
        else:
            eligible.append(probe)

    # Validate semantic_duplicate_of references now that eligible set is known.
    eligible_ids = {p.id for p in eligible}
    _validate_duplicate_refs(niche_fits, batch_ids, eligible_ids)

    # -----------------------------------------------------------------
    # 5. Compute priority components + score for all eligible probes
    # -----------------------------------------------------------------
    scored: list[_Scored] = []

    for probe in eligible:
        eval_ = niche_fits.get(probe.id)
        components, score = _compute_components(probe, eval_, prior_queries)
        scored.append((probe, components, score, eval_))

    # Sort descending by priority score.
    scored.sort(key=lambda x: x[2], reverse=True)

    # -----------------------------------------------------------------
    # 6. Semantic duplicate resolution
    #    Non-canonical probes are deferred here, BEFORE portfolio selection,
    #    so they cannot consume exploration/evidence slots.
    # -----------------------------------------------------------------
    deferred_ids: list[int] = []
    canonicals, non_canonical_map = _resolve_duplicate_groups(scored)

    for probe, components, score, eval_ in scored:
        canonical_id = non_canonical_map.get(probe.id)
        if canonical_id is not None:
            update_probe_status(
                conn,
                probe.id,
                status="deferred",
                decided_at=now,
                decision_reason=f"semantic_duplicate_of:{canonical_id}",
                priority_score=score,
                priority_components_json=components.model_dump_json(),
                niche_fit_score=components.niche_fit,
                semantic_fit_status=_sem_status(eval_),
            )
            deferred_ids.append(probe.id)

    # -----------------------------------------------------------------
    # 7. Diversity-aware selection on canonical probes
    # -----------------------------------------------------------------
    cluster_map = _build_cluster_map([p for p, _, _, _ in canonicals])
    cluster_selected: dict[int, int] = {}

    exploration_slots = round(max_probes * SELECTOR_EXPLORATION_SLOT_RATIO)
    evidence_slots = max_probes - exploration_slots
    remaining_exp = exploration_slots
    remaining_evi = evidence_slots

    selected_ids: list[int] = []

    for probe, components, score, eval_ in canonicals:
        c_id = cluster_map.get(probe.id, -1)
        is_exploration = probe.probe_type in _EXPLORATION_TYPES
        sem = _sem_status(eval_)

        def _persist_selected(p=probe, c=components, s=score, sm=sem) -> None:
            update_probe_status(
                conn,
                p.id,
                status="selected",
                decided_at=now,
                decision_reason=f"selected:priority_score={s:.4f}",
                priority_score=s,
                priority_components_json=c.model_dump_json(),
                niche_fit_score=c.niche_fit,
                semantic_fit_status=sm,
            )
            selected_ids.append(p.id)

        def _persist_deferred(reason: str, p=probe, c=components, s=score, sm=sem) -> None:
            update_probe_status(
                conn,
                p.id,
                status="deferred",
                decided_at=now,
                decision_reason=reason,
                priority_score=s,
                priority_components_json=c.model_dump_json(),
                niche_fit_score=c.niche_fit,
                semantic_fit_status=sm,
            )
            deferred_ids.append(p.id)

        # Cluster cap.
        if cluster_selected.get(c_id, 0) >= SELECTOR_MAX_REGION_PER_CLUSTER:
            _persist_deferred("deferred:cluster_diversity_cap")
            continue

        # Total budget.
        if len(selected_ids) >= max_probes:
            _persist_deferred("deferred:capacity_exceeded")
            continue

        # Portfolio allocation with overflow.
        if is_exploration:
            if remaining_exp > 0:
                _persist_selected()
                remaining_exp -= 1
            elif remaining_evi > 0:
                _persist_selected()
                remaining_evi -= 1
            else:
                _persist_deferred("deferred:capacity_exceeded")
                continue
        else:
            if remaining_evi > 0:
                _persist_selected()
                remaining_evi -= 1
            elif remaining_exp > 0:
                _persist_selected()
                remaining_exp -= 1
            else:
                _persist_deferred("deferred:capacity_exceeded")
                continue

        cluster_selected[c_id] = cluster_selected.get(c_id, 0) + 1

    # -----------------------------------------------------------------
    # 8. Write LLM provenance to run
    # -----------------------------------------------------------------
    if llm_provider:
        update_exploration_run_provenance(
            conn,
            run_id,
            provider=llm_provider,
            model=llm_model,
            prompt_version=SELECTOR_PROMPT_VERSION,
        )

    return SelectionResult(
        run_id=run_id,
        selected=selected_ids,
        deferred=deferred_ids,
        rejected=rejected_ids,
        llm_provider=llm_provider,
        llm_model=llm_model,
        policy_version=SELECTOR_POLICY_VERSION,
        llm_error=llm_error,
    )
