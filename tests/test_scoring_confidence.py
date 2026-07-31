"""Tests for scoring confidence calculation."""

from __future__ import annotations

from app.intelligence.models import (
    FactorResult,
    FactorStatus,
    MissingDataPolicy,
    OpportunityObservation,
    ScoringPolicy,
    SourceQualityTier,
)
from app.intelligence.scoring.confidence import SOURCE_QUALITY_NUMERIC, compute_confidence


def _policy(**overrides) -> ScoringPolicy:
    defaults = dict(
        channel_id=1,
        version=1,
        label="test",
        min_confidence_threshold=0.50,
        freshness_decay_days=90.0,
        max_corroboration_bonus=0.10,
        corroboration_bonus_per_source=0.05,
        missing_trend_strength=MissingDataPolicy.reweight_available,
        missing_audience_demand=MissingDataPolicy.reweight_available,
        missing_competition=MissingDataPolicy.reweight_available,
        missing_evergreen_value=MissingDataPolicy.reweight_available,
        missing_audience_fit=MissingDataPolicy.reweight_available,
        missing_content_novelty=MissingDataPolicy.reweight_available,
    )
    defaults.update(overrides)
    return ScoringPolicy(**defaults)


def _obs(
    signal_age_days: float | None = None,
    quality: str = "medium",
    adapter: str = "youtube_data_api",
) -> OpportunityObservation:
    return OpportunityObservation(
        opportunity_id=1,
        discovery_run_id=1,
        adapter_name=adapter,
        raw_topic="test topic",
        normalized_topic="test topic",
        source_quality_tier=SourceQualityTier(quality),
        signal_age_days=signal_age_days,
    )


def _fr(
    name: str,
    score: float | None = 0.5,
    missing: MissingDataPolicy = MissingDataPolicy.reweight_available,
) -> FactorResult:
    return FactorResult(
        name=name,
        raw_score=score,
        status=FactorStatus.present if score is not None else FactorStatus.absent,
    )


_ALL_NAMES = [
    "trend_strength", "audience_demand", "competition",
    "evergreen_value", "audience_fit", "content_novelty",
]


def test_source_quality_numeric_keys() -> None:
    assert SOURCE_QUALITY_NUMERIC["high"] == 1.00
    assert SOURCE_QUALITY_NUMERIC["medium_high"] == 0.75
    assert SOURCE_QUALITY_NUMERIC["medium"] == 0.50
    assert SOURCE_QUALITY_NUMERIC["variable"] == 0.25


def test_confidence_range_0_to_1() -> None:
    policy = _policy()
    frs = [_fr(n) for n in _ALL_NAMES]
    obs = [_obs(30.0)]
    conf = compute_confidence(frs, obs, policy)
    assert 0.0 <= conf <= 1.0


def test_confidence_all_present_high_quality_fresh() -> None:
    policy = _policy()
    frs = [_fr(n, 0.8) for n in _ALL_NAMES]
    obs = [_obs(10.0, "high")]
    conf = compute_confidence(frs, obs, policy)
    # completeness=1.0, quality=1.0, freshness≈0.89 → base≈0.878; no bonus
    assert conf > 0.80


def test_confidence_no_observations_reduces_score() -> None:
    policy = _policy()
    frs = [_fr(n) for n in _ALL_NAMES]
    conf = compute_confidence(frs, [], policy)
    # quality=0, freshness=0, completeness=1 → base=0.40
    assert abs(conf - 0.40) < 1e-6


def test_confidence_stale_obs_freshness_zero() -> None:
    policy = _policy(freshness_decay_days=90.0)
    frs = [_fr(n) for n in _ALL_NAMES]
    obs = [_obs(200.0)]  # stale
    conf = compute_confidence(frs, obs, policy)
    # freshness = max(0, 1 - 200/90) = 0.0
    # base = 0.40*1.0 + 0.30*0.5 + 0.20*0.0 = 0.55
    assert abs(conf - 0.55) < 1e-6


def test_confidence_none_age_uses_neutral_0_5() -> None:
    policy = _policy(freshness_decay_days=90.0)
    frs = [_fr(n) for n in _ALL_NAMES]
    obs = [_obs(None, "medium")]  # unknown age
    conf = compute_confidence(frs, obs, policy)
    # freshness = 0.5, quality=0.5, completeness=1.0
    # base = 0.40 + 0.15 + 0.10 = 0.65
    assert abs(conf - 0.65) < 1e-6


def test_confidence_corroboration_bonus_for_two_adapters() -> None:
    policy = _policy(max_corroboration_bonus=0.10, corroboration_bonus_per_source=0.05)
    frs = [_fr(n) for n in _ALL_NAMES]
    obs_single = [_obs(30.0, "high", "youtube_data_api")]
    obs_dual = [_obs(30.0, "high", "youtube_data_api"), _obs(30.0, "high", "manual")]
    conf_single = compute_confidence(frs, obs_single, policy)
    conf_dual = compute_confidence(frs, obs_dual, policy)
    # dual adds 1 extra adapter → +0.05 bonus
    assert abs(conf_dual - conf_single - 0.05) < 1e-6


def test_confidence_corroboration_does_not_exceed_cap() -> None:
    # cap=0.05 → capped at first extra adapter; adding more should not increase
    policy = _policy(max_corroboration_bonus=0.05, corroboration_bonus_per_source=0.05)
    frs = [_fr(n) for n in _ALL_NAMES]
    obs_one = [_obs(0.0, "high", "youtube_data_api")]
    obs_two = [_obs(0.0, "high", "youtube_data_api"), _obs(0.0, "high", "manual")]
    conf_one = compute_confidence(frs, obs_one, policy)
    conf_two = compute_confidence(frs, obs_two, policy)
    # Extra adapter gives +0.05 (the cap); adding more doesn't go past it
    assert abs(conf_two - conf_one - 0.05) < 1e-6


def test_confidence_require_research_penalty() -> None:
    policy = _policy(
        missing_competition=MissingDataPolicy.require_research,
        missing_audience_demand=MissingDataPolicy.require_research,
    )
    frs = [
        _fr("trend_strength", 0.5),
        _fr("audience_demand", None),
        _fr("competition", None),
        _fr("evergreen_value", 0.5),
        _fr("audience_fit", 0.5),
        _fr("content_novelty", 0.5),
    ]
    obs = [_obs(None, "medium")]
    conf = compute_confidence(frs, obs, policy)
    # 2 absent require_research factors → penalty = min(2*0.20, 0.40) = 0.40
    conf_no_penalty_estimate = 0.40 * (4 / 6) + 0.30 * 0.5 + 0.20 * 0.5  # rough base
    assert conf < conf_no_penalty_estimate


def test_confidence_penalty_capped_at_0_40() -> None:
    # 3 absent require_research factors: penalty = min(3*0.20, 0.40) = 0.40
    # Same as 2 absent in same completeness setup → penalty is capped
    policy_2 = _policy(
        missing_trend_strength=MissingDataPolicy.require_research,
        missing_audience_demand=MissingDataPolicy.require_research,
    )
    policy_3 = _policy(
        missing_trend_strength=MissingDataPolicy.require_research,
        missing_audience_demand=MissingDataPolicy.require_research,
        missing_competition=MissingDataPolicy.require_research,
    )
    # Both have 3 absent factors in total (competition reweighted vs research)
    # to keep completeness equal, make competition absent in both
    frs_2 = [
        _fr("trend_strength", None),
        _fr("audience_demand", None),
        _fr("competition", None),  # reweight_available (default)
        _fr("evergreen_value", 0.5),
        _fr("audience_fit", 0.5),
        _fr("content_novelty", 0.5),
    ]
    frs_3 = [
        _fr("trend_strength", None),
        _fr("audience_demand", None),
        _fr("competition", None),  # require_research
        _fr("evergreen_value", 0.5),
        _fr("audience_fit", 0.5),
        _fr("content_novelty", 0.5),
    ]
    obs = [_obs(None, "high")]
    conf_2rr = compute_confidence(frs_2, obs, policy_2)  # 2 require_research absent
    conf_3rr = compute_confidence(frs_3, obs, policy_3)  # 3 require_research absent

    # Adding a 3rd require_research factor should not make confidence worse once capped
    assert conf_3rr == conf_2rr


def test_confidence_never_negative() -> None:
    policy = _policy(
        missing_trend_strength=MissingDataPolicy.require_research,
        missing_audience_demand=MissingDataPolicy.require_research,
    )
    frs = [_fr(n, None, MissingDataPolicy.reweight_available) for n in _ALL_NAMES]
    frs = [
        FactorResult(name=n, raw_score=None, status=FactorStatus.absent) for n in _ALL_NAMES
    ]
    conf = compute_confidence(frs, [], policy)
    assert conf >= 0.0
