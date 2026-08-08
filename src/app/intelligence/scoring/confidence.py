"""Confidence calculation for M3.3 scoring."""

from __future__ import annotations

from app.intelligence.models import (
    FactorResult,
    MissingDataPolicy,
    OpportunityObservation,
    ScoringPolicy,
)

SOURCE_QUALITY_NUMERIC: dict[str, float] = {
    "high": 1.00,
    "medium_high": 0.75,
    "medium": 0.50,
    "variable": 0.25,
}


def compute_confidence(
    factor_results: list[FactorResult],
    observations: list[OpportunityObservation],
    policy: ScoringPolicy,
) -> float:
    """
    Confidence is orthogonal to composite score.  All observations are included
    regardless of staleness — stale observations reduce the freshness component
    rather than being silently excluded.

    Components (weighted sum + additive corroboration - research penalty):
      data_completeness (0.40): fraction of the 6 factors with a real score
      source_quality    (0.30): mean SOURCE_QUALITY_NUMERIC across observations
      freshness         (0.20): mean per-observation freshness
        - signal_age_days=None → 0.5 (neutral; age is unknown)
        - stale observations   → 0.0
      corroboration bonus (additive, capped at max_corroboration_bonus)
      require_research penalty (-0.20 per absent require_research factor, capped at -0.40)
    """
    _TOTAL_FACTORS = 6

    # Component 1: data completeness
    present_count = sum(1 for fr in factor_results if fr.raw_score is not None)
    data_completeness = present_count / _TOTAL_FACTORS

    # Component 2: source quality
    if observations:
        quality_scores = [
            SOURCE_QUALITY_NUMERIC.get(obs.source_quality_tier.value, 0.25)
            for obs in observations
        ]
        source_quality = sum(quality_scores) / len(quality_scores)
    else:
        source_quality = 0.0

    # Component 3: freshness (all observations, stale included)
    if observations:
        freshness_vals = []
        for obs in observations:
            if obs.signal_age_days is None:
                freshness_vals.append(0.5)
            else:
                freshness_vals.append(
                    max(0.0, 1.0 - obs.signal_age_days / policy.freshness_decay_days)
                )
        freshness = sum(freshness_vals) / len(freshness_vals)
    else:
        freshness = 0.0

    base = 0.40 * data_completeness + 0.30 * source_quality + 0.20 * freshness

    # Component 4: corroboration bonus (additive)
    unique_adapters = len({obs.adapter_name for obs in observations})
    additional = max(0, unique_adapters - 1)
    corroboration = min(
        additional * policy.corroboration_bonus_per_source,
        policy.max_corroboration_bonus,
    )

    # Component 5: require_research penalty
    mp_map = {
        "trend_strength": policy.missing_trend_strength,
        "audience_demand": policy.missing_audience_demand,
        "competition": policy.missing_competition,
        "evergreen_value": policy.missing_evergreen_value,
        "audience_fit": policy.missing_audience_fit,
        "content_novelty": policy.missing_content_novelty,
    }
    research_penalty = 0.0
    for fr in factor_results:
        if fr.raw_score is None and mp_map.get(fr.name) == MissingDataPolicy.require_research:
            research_penalty += 0.20
    research_penalty = min(research_penalty, 0.40)

    return max(0.0, min(1.0, base + corroboration - research_penalty))
