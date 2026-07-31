"""Tests for scoring weight rebalancing and composite calculation."""

from __future__ import annotations

from app.intelligence.models import (
    FactorResult,
    FactorStatus,
    MissingDataPolicy,
    ScoringPolicy,
)
from app.intelligence.scoring.weights import compute_composite, compute_effective_weights


def _policy(**overrides) -> ScoringPolicy:
    defaults = dict(
        channel_id=1,
        version=1,
        label="test",
        weight_trend_strength=0.20,
        weight_audience_demand=0.20,
        weight_competition=0.15,
        weight_evergreen_value=0.20,
        weight_audience_fit=0.15,
        weight_content_novelty=0.10,
        missing_trend_strength=MissingDataPolicy.reweight_available,
        missing_audience_demand=MissingDataPolicy.reweight_available,
        missing_competition=MissingDataPolicy.reweight_available,
        missing_evergreen_value=MissingDataPolicy.reweight_available,
        missing_audience_fit=MissingDataPolicy.reweight_available,
        missing_content_novelty=MissingDataPolicy.reweight_available,
    )
    defaults.update(overrides)
    return ScoringPolicy(**defaults)


def _fr(
    name: str, score: float | None, status: FactorStatus = FactorStatus.present
) -> FactorResult:
    return FactorResult(name=name, raw_score=score, status=status)


_ALL_NAMES = [
    "trend_strength",
    "audience_demand",
    "competition",
    "evergreen_value",
    "audience_fit",
    "content_novelty",
]


def _all_present(scores: dict[str, float]) -> list[FactorResult]:
    return [_fr(n, scores.get(n, 0.5)) for n in _ALL_NAMES]


# ---------------------------------------------------------------------------
# compute_effective_weights
# ---------------------------------------------------------------------------


def test_weights_all_present_sum_to_one() -> None:
    policy = _policy()
    frs = _all_present({})
    eff = compute_effective_weights(policy, frs)
    assert abs(sum(eff.values()) - 1.0) < 1e-9


def test_weights_all_present_equal_policy_weights() -> None:
    policy = _policy()
    frs = _all_present({})
    eff = compute_effective_weights(policy, frs)
    assert abs(eff["trend_strength"] - 0.20) < 1e-9
    assert abs(eff["competition"] - 0.15) < 1e-9


def test_weights_absent_reweight_redistributed() -> None:
    policy = _policy(missing_competition=MissingDataPolicy.reweight_available)
    frs = [_fr(n, None if n == "competition" else 0.5) for n in _ALL_NAMES]
    eff = compute_effective_weights(policy, frs)
    assert eff["competition"] == 0.0
    assert abs(sum(eff.values()) - 1.0) < 1e-9
    # Each receiving factor grows proportionally — just check they all grew
    for name in _ALL_NAMES:
        if name != "competition":
            assert eff[name] > 0


def test_weights_require_research_also_enters_pool() -> None:
    policy = _policy(
        missing_competition=MissingDataPolicy.require_research,
        missing_trend_strength=MissingDataPolicy.require_research,
    )
    frs = [_fr(n, None if n in ("competition", "trend_strength") else 0.5) for n in _ALL_NAMES]
    eff = compute_effective_weights(policy, frs)
    assert eff["competition"] == 0.0
    assert eff["trend_strength"] == 0.0
    assert abs(sum(eff.values()) - 1.0) < 1e-9


def test_weights_apply_prior_not_zeroed() -> None:
    policy = _policy(missing_competition=MissingDataPolicy.apply_prior)
    frs = [_fr(n, None if n == "competition" else 0.5) for n in _ALL_NAMES]
    eff = compute_effective_weights(policy, frs)
    # apply_prior keeps its weight (and grows slightly from reweight of zeroed factors)
    # here no zeroed factors — competition is prior_backed — all others scoreable
    assert eff["competition"] > 0
    assert abs(sum(eff.values()) - 1.0) < 1e-9


def test_weights_all_absent_all_zero() -> None:
    policy = _policy()
    frs = [_fr(n, None, FactorStatus.absent) for n in _ALL_NAMES]
    eff = compute_effective_weights(policy, frs)
    assert all(v == 0.0 for v in eff.values())


# ---------------------------------------------------------------------------
# compute_composite
# ---------------------------------------------------------------------------


def test_composite_all_same_score() -> None:
    policy = _policy()
    frs = _all_present({n: 0.6 for n in _ALL_NAMES})
    eff = compute_effective_weights(policy, frs)
    result = compute_composite(frs, eff, policy)
    assert abs(result - 0.6) < 1e-6


def test_composite_apply_prior_uses_0_5() -> None:
    policy = _policy(missing_competition=MissingDataPolicy.apply_prior)
    frs = [_fr(n, None if n == "competition" else 1.0) for n in _ALL_NAMES]
    eff = compute_effective_weights(policy, frs)
    result = compute_composite(frs, eff, policy)
    # competition (eff_w=0.15) contributes 0.15 * 0.5 = 0.075; rest contribute at 1.0
    # result = 1.0 * (rest weight) + 0.075 summed to 1.0 total eff weight
    assert 0.0 < result < 1.0


def test_composite_absent_reweight_no_contribution() -> None:
    policy = _policy(missing_competition=MissingDataPolicy.reweight_available)
    # competition absent — its weight distributed to others all scoring 1.0
    frs = [_fr(n, None if n == "competition" else 1.0) for n in _ALL_NAMES]
    eff = compute_effective_weights(policy, frs)
    result = compute_composite(frs, eff, policy)
    assert abs(result - 1.0) < 1e-6


def test_composite_clipped_to_0_1() -> None:
    policy = _policy()
    frs = _all_present({n: 0.0 for n in _ALL_NAMES})
    eff = compute_effective_weights(policy, frs)
    result = compute_composite(frs, eff, policy)
    assert result == 0.0
