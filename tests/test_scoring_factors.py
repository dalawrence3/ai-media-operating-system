"""Tests for the six scoring factor functions."""

from __future__ import annotations

from app.intelligence.models import (
    ChannelProfileVersion,
    FactorContext,
    FactorStatus,
    LifecycleState,
    MissingDataPolicy,
    Opportunity,
    OpportunityObservation,
    OpportunitySourceEvidence,
    ScoringPolicy,
    SourceQualityTier,
)
from app.intelligence.scoring.factors import (
    compute_audience_demand,
    compute_audience_fit,
    compute_competition,
    compute_content_novelty,
    compute_evergreen_value,
    compute_trend_strength,
)


def _policy(**overrides) -> ScoringPolicy:
    defaults = dict(
        channel_id=1,
        version=1,
        label="test",
        freshness_decay_days=90.0,
        missing_trend_strength=MissingDataPolicy.reweight_available,
        missing_audience_demand=MissingDataPolicy.reweight_available,
        missing_competition=MissingDataPolicy.reweight_available,
        missing_evergreen_value=MissingDataPolicy.reweight_available,
        missing_audience_fit=MissingDataPolicy.reweight_available,
        missing_content_novelty=MissingDataPolicy.reweight_available,
    )
    defaults.update(overrides)
    return ScoringPolicy(**defaults)


def _opp(topic: str = "index fund basics", normalized: str | None = None) -> Opportunity:
    return Opportunity(
        id=1,
        channel_id=1,
        raw_topic=topic,
        normalized_topic=normalized or topic,
        current_lifecycle_state=LifecycleState.new,
        discovery_run_id=1,
    )


def _profile(
    primary: str = "personal finance", secondary: list[str] | None = None
) -> ChannelProfileVersion:
    return ChannelProfileVersion(
        id=1,
        channel_id=1,
        version=1,
        primary_niche=primary,
        secondary_niches=secondary or [],
        audience_description="people learning about money",
    )


def _obs(
    obs_id: int = 1, age: float | None = 20.0, adapter: str = "youtube_data_api", run_id: int = 1
) -> OpportunityObservation:
    return OpportunityObservation(
        id=obs_id,
        opportunity_id=1,
        discovery_run_id=run_id,
        adapter_name=adapter,
        raw_topic="test",
        normalized_topic="test",
        source_quality_tier=SourceQualityTier.medium,
        signal_age_days=age,
    )


def _ev(
    ev_type: str, value: float | None = None, text: str | None = None, ev_id: int = 10
) -> OpportunitySourceEvidence:
    return OpportunitySourceEvidence(
        id=ev_id,
        observation_id=1,
        evidence_type=ev_type,
        evidence_value=value,
        evidence_text=text,
        source_label="test",
        opportunity_id=1,
    )


def _ctx(
    topic: str = "index fund basics",
    normalized_topic: str | None = None,
    primary: str = "personal finance",
    secondary: list[str] | None = None,
    observations: list | None = None,
    evidence: dict | None = None,
    best_similarity: float | None = None,
) -> FactorContext:
    return FactorContext(
        opportunity=_opp(topic, normalized_topic),
        profile=_profile(primary, secondary),
        observations=observations or [],
        evidence=evidence or {},
        best_similarity=best_similarity,
        matched_opportunity_id=None,
        matched_normalized_topic=None,
    )


# ---------------------------------------------------------------------------
# compute_trend_strength
# ---------------------------------------------------------------------------


def test_trend_strength_no_evidence_absent() -> None:
    ctx = _ctx(observations=[_obs()])
    result = compute_trend_strength(ctx, _policy())
    assert result.raw_score is None
    assert result.status == FactorStatus.absent


def test_trend_strength_fresh_age() -> None:
    obs = _obs(1, age=10.0)
    ctx = _ctx(
        observations=[obs],
        evidence={1: [_ev("top_video_age_days", 10.0)]},
    )
    result = compute_trend_strength(ctx, _policy(freshness_decay_days=90.0))
    assert result.raw_score is not None
    assert result.raw_score > 0.8
    assert result.status == FactorStatus.present


def test_trend_strength_stale_age_degraded() -> None:
    obs = _obs(1, age=200.0)
    ctx = _ctx(
        observations=[obs],
        evidence={1: [_ev("top_video_age_days", 200.0)]},
    )
    result = compute_trend_strength(ctx, _policy(freshness_decay_days=90.0))
    assert result.status == FactorStatus.degraded


def test_trend_strength_views_only() -> None:
    obs = _obs(1)
    ctx = _ctx(
        observations=[obs],
        evidence={1: [_ev("view_count", 500_000)]},
    )
    result = compute_trend_strength(ctx, _policy())
    assert result.raw_score is not None
    assert 0.0 <= result.raw_score <= 1.0


# ---------------------------------------------------------------------------
# compute_audience_demand
# ---------------------------------------------------------------------------


def test_audience_demand_no_evidence_absent() -> None:
    ctx = _ctx(observations=[_obs()])
    result = compute_audience_demand(ctx, _policy())
    assert result.raw_score is None
    assert result.status == FactorStatus.absent


def test_audience_demand_manual_note_alone_absent() -> None:
    obs = _obs(1)
    ctx = _ctx(
        observations=[obs],
        evidence={1: [_ev("manual_demand_note", None, "people are searching this")]},
    )
    result = compute_audience_demand(ctx, _policy())
    assert result.raw_score is None
    assert result.status == FactorStatus.absent
    assert result.notes and "manual demand note" in result.notes


def test_audience_demand_view_count_present() -> None:
    obs = _obs(1)
    ctx = _ctx(
        observations=[obs],
        evidence={1: [_ev("view_count", 1_000_000), _ev("like_count", 50_000, ev_id=11)]},
    )
    result = compute_audience_demand(ctx, _policy())
    assert result.raw_score is not None
    assert result.status == FactorStatus.present


def test_audience_demand_likes_only_insufficient() -> None:
    obs = _obs(1)
    ctx = _ctx(
        observations=[obs],
        evidence={1: [_ev("like_count", 10_000)]},
    )
    result = compute_audience_demand(ctx, _policy())
    assert result.raw_score is not None
    assert result.status == FactorStatus.insufficient


# ---------------------------------------------------------------------------
# compute_competition
# ---------------------------------------------------------------------------


def test_competition_no_data_absent() -> None:
    ctx = _ctx(observations=[_obs()])
    result = compute_competition(ctx, _policy())
    assert result.raw_score is None
    assert result.status == FactorStatus.absent


def test_competition_with_video_count() -> None:
    obs = _obs(1)
    ctx = _ctx(
        observations=[obs],
        evidence={1: [_ev("video_count_in_niche", 1000)]},
    )
    result = compute_competition(ctx, _policy())
    assert result.raw_score is not None
    assert 0.0 <= result.raw_score <= 1.0
    assert result.status == FactorStatus.present


def test_competition_high_count_low_score() -> None:
    obs = _obs(1)
    low_ctx = _ctx(observations=[obs], evidence={1: [_ev("video_count_in_niche", 100)]})
    high_ctx = _ctx(observations=[obs], evidence={1: [_ev("video_count_in_niche", 900_000)]})
    low_result = compute_competition(low_ctx, _policy())
    high_result = compute_competition(high_ctx, _policy())
    assert low_result.raw_score > high_result.raw_score  # type: ignore[operator]


# ---------------------------------------------------------------------------
# compute_evergreen_value
# ---------------------------------------------------------------------------


def test_evergreen_value_always_present() -> None:
    ctx = _ctx(topic="best budgeting apps 2025")
    result = compute_evergreen_value(ctx, _policy())
    assert result.raw_score is not None
    assert result.status == FactorStatus.present


def test_evergreen_value_temporal_token_reduces() -> None:
    timeless_ctx = _ctx(topic="how to invest money", normalized_topic="how to invest money")
    temporal_ctx = _ctx(
        topic="trending stocks today 2025", normalized_topic="trending stocks today 2025"
    )
    timeless_result = compute_evergreen_value(timeless_ctx, _policy())
    temporal_result = compute_evergreen_value(temporal_ctx, _policy())
    assert timeless_result.raw_score > temporal_result.raw_score  # type: ignore[operator]


def test_evergreen_value_multi_run_bonus() -> None:
    obs1 = _obs(1, run_id=1)
    obs2 = _obs(2, run_id=2)
    single_ctx = _ctx(observations=[obs1])
    multi_ctx = _ctx(observations=[obs1, obs2])
    single_result = compute_evergreen_value(single_ctx, _policy())
    multi_result = compute_evergreen_value(multi_ctx, _policy())
    assert multi_result.raw_score >= single_result.raw_score  # type: ignore[operator]


# ---------------------------------------------------------------------------
# compute_audience_fit
# ---------------------------------------------------------------------------


def test_audience_fit_no_niche_tokens_absent() -> None:
    # A single stopword normalizes to empty tokens → absent
    ctx = _ctx(primary="the", secondary=[])
    result = compute_audience_fit(ctx, _policy())
    assert result.raw_score is None
    assert result.status == FactorStatus.absent


def test_audience_fit_matching_topic_higher_than_unrelated() -> None:
    related = _ctx(
        topic="personal finance basics",
        normalized_topic="personal finance basics",
        primary="personal finance",
    )
    unrelated = _ctx(
        topic="gaming hardware reviews",
        normalized_topic="gaming hardware review",
        primary="personal finance",
    )
    r_related = compute_audience_fit(related, _policy())
    r_unrelated = compute_audience_fit(unrelated, _policy())
    assert r_related.raw_score is not None
    assert r_unrelated.raw_score is not None
    assert r_related.raw_score > r_unrelated.raw_score


def test_audience_fit_no_overlap_low_score() -> None:
    ctx = _ctx(
        topic="gaming hardware reviews",
        normalized_topic="gaming hardware review",
        primary="personal finance",
        secondary=["investing"],
    )
    result = compute_audience_fit(ctx, _policy())
    assert result.raw_score is not None
    assert result.raw_score < 0.3


def test_audience_fit_secondary_niche_contributes() -> None:
    no_secondary = _ctx(
        topic="index fund guide",
        normalized_topic="index fund guide",
        primary="personal finance",
        secondary=[],
    )
    with_secondary = _ctx(
        topic="index fund guide",
        normalized_topic="index fund guide",
        primary="personal finance",
        secondary=["index fund investing"],
    )
    no_sec_result = compute_audience_fit(no_secondary, _policy())
    sec_result = compute_audience_fit(with_secondary, _policy())
    assert sec_result.raw_score >= no_sec_result.raw_score  # type: ignore[operator]


# ---------------------------------------------------------------------------
# compute_content_novelty
# ---------------------------------------------------------------------------


def test_content_novelty_no_similarity_fully_novel() -> None:
    ctx = _ctx(best_similarity=None)
    result = compute_content_novelty(ctx, _policy())
    assert result.raw_score == 1.0
    assert result.status == FactorStatus.present


def test_content_novelty_high_similarity_low_score() -> None:
    ctx = _ctx(best_similarity=0.92)
    result = compute_content_novelty(ctx, _policy())
    assert result.raw_score is not None
    assert abs(result.raw_score - 0.08) < 1e-6


def test_content_novelty_zero_similarity_perfect() -> None:
    ctx = _ctx(best_similarity=0.0)
    result = compute_content_novelty(ctx, _policy())
    assert result.raw_score == 1.0


def test_content_novelty_score_clipped_0_1() -> None:
    # Edge case: similarity=1.0 → novelty=0.0
    ctx = _ctx(best_similarity=1.0)
    result = compute_content_novelty(ctx, _policy())
    assert result.raw_score == 0.0
