"""M3.3 scoring engine orchestrator."""

from __future__ import annotations

import json
import sqlite3

from app.intelligence.models import (
    ChannelProfileVersion,
    FactorContext,
    FactorResult,
    FactorStatus,
    MissingDataPolicy,
    OpportunityScore,
    ScoringPolicy,
)
from app.intelligence.repository import (
    create_opportunity_score,
    get_latest_score,
    get_max_similarity_to_existing,
    get_opportunity,
    list_evidence,
    list_observations,
)
from app.intelligence.scoring.confidence import compute_confidence
from app.intelligence.scoring.factors import (
    compute_audience_demand,
    compute_audience_fit,
    compute_competition,
    compute_content_novelty,
    compute_evergreen_value,
    compute_trend_strength,
)
from app.intelligence.scoring.snapshot import build_input_snapshot, compute_hash
from app.intelligence.scoring.weights import compute_composite, compute_effective_weights

SCORER_VERSION = "1.0"

_FACTOR_NAMES = [
    "trend_strength",
    "audience_demand",
    "competition",
    "evergreen_value",
    "audience_fit",
    "content_novelty",
]


def score_opportunity(
    conn: sqlite3.Connection,
    opportunity_id: int,
    policy: ScoringPolicy,
    profile_version: ChannelProfileVersion,
    *,
    observation_ids: list[int] | None = None,
    force: bool = False,
) -> OpportunityScore:
    """
    Score an opportunity under the given policy.

    All observations are loaded (stale included). Factors mark stale evidence
    as degraded. A single logical transaction: caller must commit.

    Skip logic: if force=False and a score already exists with matching
    (scoring_policy_id, scorer_version, channel_profile_version_id, input_hash),
    the existing score is returned without re-scoring.
    """
    assert policy.id is not None, "policy must be persisted before scoring"
    assert profile_version.id is not None, "profile_version must be persisted before scoring"

    opp = get_opportunity(conn, opportunity_id)
    if opp is None:
        raise ValueError(f"Opportunity {opportunity_id} not found")

    # Load all observations (no staleness filter — factors handle staleness themselves)
    if observation_ids is not None:
        all_obs = [
            obs for obs in list_observations(conn, opportunity_id) if obs.id in observation_ids
        ]
    else:
        all_obs = list_observations(conn, opportunity_id)

    # Load evidence for each observation
    evidence: dict[int, list] = {}
    for obs in all_obs:
        if obs.id is not None:
            evidence[obs.id] = list_evidence(conn, obs.id)

    # Fetch novelty context (dedicated function, not find_existing_opportunity)
    novelty = get_max_similarity_to_existing(conn, opportunity_id, opp.channel_id)
    best_similarity = novelty[0] if novelty else None
    matched_opp_id = novelty[1] if novelty else None
    matched_topic = novelty[2] if novelty else None

    ctx = FactorContext(
        opportunity=opp,
        profile=profile_version,
        observations=all_obs,
        evidence=evidence,
        best_similarity=best_similarity,
        matched_opportunity_id=matched_opp_id,
        matched_normalized_topic=matched_topic,
    )

    snapshot = build_input_snapshot(ctx)
    input_hash = compute_hash(snapshot)

    # Skip check
    if not force:
        existing = get_latest_score(conn, opportunity_id, policy.id)
        if (
            existing is not None
            and existing.scoring_policy_id == policy.id
            and existing.scorer_version == SCORER_VERSION
            and existing.channel_profile_version_id == profile_version.id
            and existing.input_hash == input_hash
        ):
            return existing

    # Run all six factors
    factor_results: list[FactorResult] = [
        compute_trend_strength(ctx, policy),
        compute_audience_demand(ctx, policy),
        compute_competition(ctx, policy),
        compute_evergreen_value(ctx, policy),
        compute_audience_fit(ctx, policy),
        compute_content_novelty(ctx, policy),
    ]

    # Compute effective weights and composite
    eff_weights = compute_effective_weights(policy, factor_results)
    composite = compute_composite(factor_results, eff_weights, policy)
    confidence = compute_confidence(factor_results, all_obs, policy)

    # Determine require_research flag
    mp_map = {
        "trend_strength": policy.missing_trend_strength,
        "audience_demand": policy.missing_audience_demand,
        "competition": policy.missing_competition,
        "evergreen_value": policy.missing_evergreen_value,
        "audience_fit": policy.missing_audience_fit,
        "content_novelty": policy.missing_content_novelty,
    }
    requires_research = any(
        fr.raw_score is None and mp_map[fr.name] == MissingDataPolicy.require_research
        for fr in factor_results
    )

    # Build factor score map for easy access
    fr_map = {fr.name: fr for fr in factor_results}

    def _score(name: str) -> float | None:
        return fr_map[name].raw_score

    def _status(name: str) -> FactorStatus:
        return fr_map[name].status

    score = OpportunityScore(
        opportunity_id=opportunity_id,
        scoring_policy_id=policy.id,
        channel_profile_version_id=profile_version.id,
        composite_score=composite,
        confidence=confidence,
        score_trend_strength=_score("trend_strength"),
        score_audience_demand=_score("audience_demand"),
        score_competition=_score("competition"),
        score_evergreen_value=_score("evergreen_value"),
        score_audience_fit=_score("audience_fit"),
        score_content_novelty=_score("content_novelty"),
        status_trend_strength=_status("trend_strength"),
        status_audience_demand=_status("audience_demand"),
        status_competition=_status("competition"),
        status_evergreen_value=_status("evergreen_value"),
        status_audience_fit=_status("audience_fit"),
        status_content_novelty=_status("content_novelty"),
        eff_weight_trend_strength=eff_weights["trend_strength"],
        eff_weight_audience_demand=eff_weights["audience_demand"],
        eff_weight_competition=eff_weights["competition"],
        eff_weight_evergreen_value=eff_weights["evergreen_value"],
        eff_weight_audience_fit=eff_weights["audience_fit"],
        eff_weight_content_novelty=eff_weights["content_novelty"],
        observation_ids_json=json.dumps([obs.id for obs in all_obs if obs.id]),
        input_snapshot_json=json.dumps(snapshot, sort_keys=True),
        input_hash=input_hash,
        below_confidence_threshold=confidence < policy.min_confidence_threshold,
        requires_research=requires_research,
        scorer_version=SCORER_VERSION,
    )

    return create_opportunity_score(conn, score)
