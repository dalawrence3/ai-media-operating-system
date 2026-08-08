"""Tests for the scoring engine orchestrator."""

from __future__ import annotations

import sqlite3

import pytest

from app.intelligence.models import (
    AdapterName,
    DiscoveryRun,
    MissingDataPolicy,
    Opportunity,
    OpportunityObservation,
    RunStatus,
    ScoringPolicy,
    SourceQualityTier,
)
from app.intelligence.repository import (
    activate_profile_version,
    activate_scoring_policy,
    create_channel_full,
    create_discovery_run,
    create_new_profile_version,
    create_observation,
    create_opportunity,
    create_scoring_policy,
    get_active_profile_version,
    get_latest_score,
    get_scoring_policy,
    list_scores_for_opportunity,
    list_scoring_policies,
)
from app.intelligence.scoring import SCORER_VERSION, score_opportunity


def _make_channel(db: sqlite3.Connection, name: str = "Test Channel"):
    return create_channel_full(db, channel_name=name, primary_niche="personal finance")


def _make_run(db: sqlite3.Connection, channel_id: int, profile_version_id: int) -> DiscoveryRun:
    return create_discovery_run(
        db,
        DiscoveryRun(
            channel_id=channel_id,
            profile_version_id=profile_version_id,
            adapter_name=AdapterName.manual,
            status=RunStatus.running,
        ),
    )


def _make_policy(db: sqlite3.Connection, channel_id: int) -> ScoringPolicy:
    existing = list_scoring_policies(db, channel_id)
    next_version = max((p.version for p in existing), default=0) + 1
    policy = create_scoring_policy(
        db,
        ScoringPolicy(
            channel_id=channel_id,
            version=next_version,
            label="test policy",
            missing_competition=MissingDataPolicy.reweight_available,
            missing_trend_strength=MissingDataPolicy.reweight_available,
            missing_audience_demand=MissingDataPolicy.reweight_available,
        ),
    )
    activate_scoring_policy(db, policy.id)
    return get_scoring_policy(db, policy.id)


def _make_opp(
    db: sqlite3.Connection,
    channel_id: int,
    profile_version_id: int,
    topic: str = "index fund basics",
) -> Opportunity:
    run = _make_run(db, channel_id, profile_version_id)
    return create_opportunity(
        db,
        Opportunity(
            channel_id=channel_id,
            raw_topic=topic,
            normalized_topic=topic,
            discovery_run_id=run.id,
        ),
    )


def _make_obs(
    db: sqlite3.Connection,
    opp_id: int,
    run_id: int,
    adapter: AdapterName = AdapterName.youtube_data_api,
) -> OpportunityObservation:
    return create_observation(
        db,
        OpportunityObservation(
            opportunity_id=opp_id,
            discovery_run_id=run_id,
            adapter_name=adapter,
            source_quality_tier=SourceQualityTier.medium,
            signal_age_days=30.0,
        ),
    )


# ---------------------------------------------------------------------------
# Basic scoring
# ---------------------------------------------------------------------------


def test_score_opportunity_returns_score(db: sqlite3.Connection) -> None:
    channel, profile, _, _ = _make_channel(db)
    policy = _make_policy(db, channel.id)
    opp = _make_opp(db, channel.id, profile.id)
    run = _make_run(db, channel.id, profile.id)
    _make_obs(db, opp.id, run.id)

    score = score_opportunity(db, opp.id, policy, profile)
    db.commit()

    assert score.id is not None
    assert 0.0 <= score.composite_score <= 1.0
    assert 0.0 <= score.confidence <= 1.0


def test_score_opportunity_scorer_version(db: sqlite3.Connection) -> None:
    channel, profile, _, _ = _make_channel(db)
    policy = _make_policy(db, channel.id)
    opp = _make_opp(db, channel.id, profile.id)

    score = score_opportunity(db, opp.id, policy, profile)
    db.commit()
    assert score.scorer_version == SCORER_VERSION


def test_score_opportunity_persisted_in_db(db: sqlite3.Connection) -> None:
    channel, profile, _, _ = _make_channel(db)
    policy = _make_policy(db, channel.id)
    opp = _make_opp(db, channel.id, profile.id)

    score = score_opportunity(db, opp.id, policy, profile)
    db.commit()

    latest = get_latest_score(db, opp.id, policy.id)
    assert latest is not None
    assert latest.id == score.id


def test_score_opportunity_input_hash_set(db: sqlite3.Connection) -> None:
    channel, profile, _, _ = _make_channel(db)
    policy = _make_policy(db, channel.id)
    opp = _make_opp(db, channel.id, profile.id)

    score = score_opportunity(db, opp.id, policy, profile)
    db.commit()
    assert len(score.input_hash) == 64


# ---------------------------------------------------------------------------
# Skip-on-rescore
# ---------------------------------------------------------------------------


def test_score_opportunity_skip_on_identical_inputs(db: sqlite3.Connection) -> None:
    channel, profile, _, _ = _make_channel(db)
    policy = _make_policy(db, channel.id)
    opp = _make_opp(db, channel.id, profile.id)

    score1 = score_opportunity(db, opp.id, policy, profile)
    db.commit()
    score2 = score_opportunity(db, opp.id, policy, profile, force=False)
    db.commit()

    assert score1.id == score2.id
    all_scores = list_scores_for_opportunity(db, opp.id)
    assert len(all_scores) == 1


def test_score_opportunity_force_creates_new_row(db: sqlite3.Connection) -> None:
    channel, profile, _, _ = _make_channel(db)
    policy = _make_policy(db, channel.id)
    opp = _make_opp(db, channel.id, profile.id)

    score1 = score_opportunity(db, opp.id, policy, profile)
    db.commit()
    score2 = score_opportunity(db, opp.id, policy, profile, force=True)
    db.commit()

    assert score1.id != score2.id
    all_scores = list_scores_for_opportunity(db, opp.id)
    assert len(all_scores) == 2


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


def test_score_opportunity_requires_research_flag(db: sqlite3.Connection) -> None:
    channel, profile, _, _ = _make_channel(db)
    existing = list_scoring_policies(db, channel.id)
    next_version = max((p.version for p in existing), default=0) + 1
    # audience_fit=require_research; without proper niche data it'll be absent
    policy = create_scoring_policy(
        db,
        ScoringPolicy(
            channel_id=channel.id,
            version=next_version,
            label="research policy",
            missing_audience_fit=MissingDataPolicy.require_research,
            missing_competition=MissingDataPolicy.reweight_available,
            missing_trend_strength=MissingDataPolicy.reweight_available,
            missing_audience_demand=MissingDataPolicy.reweight_available,
        ),
    )
    activate_scoring_policy(db, policy.id)
    policy = get_scoring_policy(db, policy.id)

    # Use a profile where primary_niche is a single stopword ("a") so that
    # normalize_topic() strips it to an empty token set, making audience_fit absent.
    # "a" satisfies the min_length=1 Pydantic constraint.
    draft = create_new_profile_version(db, channel.id, primary_niche="a")
    activate_profile_version(db, draft.id)
    empty_profile = get_active_profile_version(db, channel.id)

    opp = _make_opp(db, channel.id, profile.id)
    score = score_opportunity(db, opp.id, policy, empty_profile)
    db.commit()
    assert score.requires_research is True


def test_score_opportunity_below_confidence_threshold(db: sqlite3.Connection) -> None:
    channel, profile, _, _ = _make_channel(db)
    existing = list_scoring_policies(db, channel.id)
    next_version = max((p.version for p in existing), default=0) + 1
    # Very high threshold so most scores fail it
    policy = create_scoring_policy(
        db,
        ScoringPolicy(
            channel_id=channel.id,
            version=next_version,
            label="high threshold",
            min_confidence_threshold=0.99,
            missing_competition=MissingDataPolicy.reweight_available,
            missing_trend_strength=MissingDataPolicy.reweight_available,
            missing_audience_demand=MissingDataPolicy.reweight_available,
        ),
    )
    activate_scoring_policy(db, policy.id)
    policy = get_scoring_policy(db, policy.id)
    opp = _make_opp(db, channel.id, profile.id)

    score = score_opportunity(db, opp.id, policy, profile)
    db.commit()
    assert score.below_confidence_threshold is True


# ---------------------------------------------------------------------------
# Missing opportunity raises
# ---------------------------------------------------------------------------


def test_score_opportunity_raises_for_missing_opportunity(db: sqlite3.Connection) -> None:
    channel, profile, _, _ = _make_channel(db)
    policy = _make_policy(db, channel.id)
    with pytest.raises(ValueError, match="not found"):
        score_opportunity(db, 99999, policy, profile)
