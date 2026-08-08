"""Weight rebalancing and composite calculation for M3.3 scoring."""

from __future__ import annotations

from app.intelligence.models import FactorResult, MissingDataPolicy, ScoringPolicy

_FACTOR_NAMES = [
    "trend_strength",
    "audience_demand",
    "competition",
    "evergreen_value",
    "audience_fit",
    "content_novelty",
]


def _policy_weights(policy: ScoringPolicy) -> dict[str, float]:
    return {
        "trend_strength": policy.weight_trend_strength,
        "audience_demand": policy.weight_audience_demand,
        "competition": policy.weight_competition,
        "evergreen_value": policy.weight_evergreen_value,
        "audience_fit": policy.weight_audience_fit,
        "content_novelty": policy.weight_content_novelty,
    }


def _missing_policies(policy: ScoringPolicy) -> dict[str, MissingDataPolicy]:
    return {
        "trend_strength": policy.missing_trend_strength,
        "audience_demand": policy.missing_audience_demand,
        "competition": policy.missing_competition,
        "evergreen_value": policy.missing_evergreen_value,
        "audience_fit": policy.missing_audience_fit,
        "content_novelty": policy.missing_content_novelty,
    }


def compute_effective_weights(
    policy: ScoringPolicy,
    factor_results: list[FactorResult],
) -> dict[str, float]:
    """
    Classify factors and compute effective weights.

    Dispositions:
      scoreable    – raw_score is not None
      prior_backed – raw_score is None and missing_policy == apply_prior  (uses 0.5)
      zeroed       – raw_score is None and missing_policy in
                     {reweight_available, require_research}

    Both reweight_available and require_research absent factors enter the
    redistribution pool. Their weight is redistributed proportionally across
    scoreable + prior_backed factors. Effective weights always sum to 1.0
    when any receiving factor exists.
    """
    pw = _policy_weights(policy)
    mp = _missing_policies(policy)
    score_map = {fr.name: fr.raw_score for fr in factor_results}

    scoreable: list[str] = []
    prior_backed: list[str] = []
    zeroed: list[str] = []

    for name in _FACTOR_NAMES:
        raw = score_map.get(name)
        if raw is not None:
            scoreable.append(name)
        elif mp[name] == MissingDataPolicy.apply_prior:
            prior_backed.append(name)
        else:
            zeroed.append(name)

    redistribution_pool = sum(pw[n] for n in zeroed)
    receiving_names = scoreable + prior_backed
    receiving_base = sum(pw[n] for n in receiving_names)

    eff: dict[str, float] = {}

    if receiving_base == 0.0:
        for name in _FACTOR_NAMES:
            eff[name] = 0.0
    else:
        scale = 1.0 + redistribution_pool / receiving_base
        for name in receiving_names:
            eff[name] = pw[name] * scale
        for name in zeroed:
            eff[name] = 0.0

    # Invariant: weights sum to 1.0 when any factor can receive weight
    if receiving_base > 0.0:
        total = sum(eff.values())
        assert abs(total - 1.0) < 1e-9, f"effective weight sum={total}"

    return eff


def compute_composite(
    factor_results: list[FactorResult],
    effective_weights: dict[str, float],
    policy: ScoringPolicy,
) -> float:
    mp = _missing_policies(policy)
    total = 0.0
    for fr in factor_results:
        ew = effective_weights.get(fr.name, 0.0)
        if ew == 0.0:
            continue
        if fr.raw_score is not None:
            total += ew * fr.raw_score
        elif mp[fr.name] == MissingDataPolicy.apply_prior:
            total += ew * 0.5
    return max(0.0, min(1.0, total))
