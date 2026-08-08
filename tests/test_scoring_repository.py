"""Tests for scoring policy and opportunity score repository functions."""

from __future__ import annotations

import sqlite3

import pytest

from app.intelligence.models import (
    AdapterName,
    DiscoveryRun,
    Opportunity,
    PolicyStatus,
    RunStatus,
    ScoringPolicy,
)
from app.intelligence.repository import (
    activate_scoring_policy,
    archive_scoring_policy,
    clone_scoring_policy,
    create_channel_full,
    create_discovery_run,
    create_opportunity,
    create_opportunity_score,
    create_scoring_policy,
    get_default_scoring_policy,
    get_latest_score,
    get_scoring_policy,
    list_scored_opportunities,
    list_scores_for_opportunity,
    list_scoring_policies,
    update_scoring_policy,
)


def _make_channel(db: sqlite3.Connection, name: str = "Test Channel") -> tuple:
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


def _make_opp(
    db: sqlite3.Connection, channel_id: int, run_id: int, topic: str = "test topic"
) -> Opportunity:
    return create_opportunity(
        db,
        Opportunity(
            channel_id=channel_id,
            discovery_run_id=run_id,
            raw_topic=topic,
            normalized_topic=topic,
        ),
    )


def _make_policy(
    db: sqlite3.Connection, channel_id: int, label: str = "default policy"
) -> ScoringPolicy:
    existing = list_scoring_policies(db, channel_id)
    next_version = max((p.version for p in existing), default=0) + 1
    policy = ScoringPolicy(
        channel_id=channel_id,
        version=next_version,
        label=label,
    )
    return create_scoring_policy(db, policy)


def _make_score(
    db: sqlite3.Connection,
    opp_id: int,
    policy_id: int,
    profile_version_id: int,
    composite: float = 0.7,
    input_hash: str = "abc123",
) -> object:
    from app.intelligence.models import FactorStatus, OpportunityScore

    score = OpportunityScore(
        opportunity_id=opp_id,
        scoring_policy_id=policy_id,
        channel_profile_version_id=profile_version_id,
        composite_score=composite,
        confidence=0.65,
        status_trend_strength=FactorStatus.absent,
        status_audience_demand=FactorStatus.absent,
        status_competition=FactorStatus.absent,
        status_evergreen_value=FactorStatus.present,
        status_audience_fit=FactorStatus.present,
        status_content_novelty=FactorStatus.present,
        eff_weight_trend_strength=0.0,
        eff_weight_audience_demand=0.0,
        eff_weight_competition=0.0,
        eff_weight_evergreen_value=0.333,
        eff_weight_audience_fit=0.333,
        eff_weight_content_novelty=0.334,
        observation_ids_json="[]",
        input_snapshot_json="{}",
        input_hash=input_hash,
        below_confidence_threshold=False,
        requires_research=False,
        scorer_version="1.0",
    )
    return create_opportunity_score(db, score)


# ---------------------------------------------------------------------------
# create_scoring_policy
# ---------------------------------------------------------------------------


def test_create_scoring_policy_defaults_to_draft(db: sqlite3.Connection) -> None:
    channel, _, _, _ = _make_channel(db)
    policy = _make_policy(db, channel.id)
    assert policy.id is not None
    assert policy.status == PolicyStatus.draft
    assert policy.is_default is False


def test_create_scoring_policy_weight_sum_must_be_1(db: sqlite3.Connection) -> None:
    channel, _, _, _ = _make_channel(db)
    with pytest.raises(ValueError):
        ScoringPolicy(
            channel_id=channel.id,
            version=1,
            label="bad weights",
            weight_trend_strength=0.50,
            weight_audience_demand=0.50,
            weight_competition=0.50,
        )


# ---------------------------------------------------------------------------
# get / list
# ---------------------------------------------------------------------------


def test_get_scoring_policy_returns_none_for_missing(db: sqlite3.Connection) -> None:
    assert get_scoring_policy(db, 9999) is None


def test_list_scoring_policies_empty(db: sqlite3.Connection) -> None:
    channel, _, _, _ = _make_channel(db)
    assert list_scoring_policies(db, channel.id) == []


def test_list_scoring_policies_returns_all(db: sqlite3.Connection) -> None:
    channel, _, _, _ = _make_channel(db)
    p1 = _make_policy(db, channel.id, "p1")
    clone_scoring_policy(db, p1.id, "p2")
    policies = list_scoring_policies(db, channel.id)
    assert len(policies) == 2


# ---------------------------------------------------------------------------
# update_scoring_policy
# ---------------------------------------------------------------------------


def test_update_scoring_policy_label(db: sqlite3.Connection) -> None:
    channel, _, _, _ = _make_channel(db)
    policy = _make_policy(db, channel.id, "old label")
    update_scoring_policy(db, policy.id, label="new label")
    updated = get_scoring_policy(db, policy.id)
    assert updated.label == "new label"


def test_update_scoring_policy_rejects_active(db: sqlite3.Connection) -> None:
    channel, _, _, _ = _make_channel(db)
    policy = _make_policy(db, channel.id)
    activate_scoring_policy(db, policy.id)
    with pytest.raises(ValueError, match="draft"):
        update_scoring_policy(db, policy.id, label="new label")


# ---------------------------------------------------------------------------
# activate_scoring_policy
# ---------------------------------------------------------------------------


def test_activate_policy_from_draft(db: sqlite3.Connection) -> None:
    channel, _, _, _ = _make_channel(db)
    policy = _make_policy(db, channel.id)
    activate_scoring_policy(db, policy.id)
    activated = get_scoring_policy(db, policy.id)
    assert activated.status == PolicyStatus.active
    assert activated.is_default is True


def test_activate_policy_sets_channel_default(db: sqlite3.Connection) -> None:
    channel, _, _, _ = _make_channel(db)
    p1 = _make_policy(db, channel.id, "p1")
    p2 = _make_policy(db, channel.id, "p2")
    activate_scoring_policy(db, p1.id)
    activate_scoring_policy(db, p2.id)
    assert get_scoring_policy(db, p2.id).is_default is True
    assert get_scoring_policy(db, p1.id).is_default is False


def test_activate_policy_idempotent_if_already_default(db: sqlite3.Connection) -> None:
    channel, _, _, _ = _make_channel(db)
    policy = _make_policy(db, channel.id)
    activate_scoring_policy(db, policy.id)
    activate_scoring_policy(db, policy.id)  # second call is a no-op
    assert get_scoring_policy(db, policy.id).is_default is True


def test_activate_archived_policy_raises(db: sqlite3.Connection) -> None:
    channel, _, _, _ = _make_channel(db)
    p1 = _make_policy(db, channel.id, "default")
    p2 = _make_policy(db, channel.id, "to archive")
    activate_scoring_policy(db, p1.id)
    activate_scoring_policy(db, p2.id)
    activate_scoring_policy(db, p1.id)  # restore p1 as default
    archive_scoring_policy(db, p2.id)
    with pytest.raises(ValueError, match="archived"):
        activate_scoring_policy(db, p2.id)


def test_get_default_scoring_policy_none_when_none(db: sqlite3.Connection) -> None:
    channel, _, _, _ = _make_channel(db)
    assert get_default_scoring_policy(db, channel.id) is None


def test_get_default_scoring_policy_returns_default(db: sqlite3.Connection) -> None:
    channel, _, _, _ = _make_channel(db)
    policy = _make_policy(db, channel.id)
    activate_scoring_policy(db, policy.id)
    default = get_default_scoring_policy(db, channel.id)
    assert default is not None
    assert default.id == policy.id


# ---------------------------------------------------------------------------
# clone_scoring_policy
# ---------------------------------------------------------------------------


def test_clone_scoring_policy_creates_draft(db: sqlite3.Connection) -> None:
    channel, _, _, _ = _make_channel(db)
    policy = _make_policy(db, channel.id)
    activate_scoring_policy(db, policy.id)
    cloned = clone_scoring_policy(db, policy.id, "cloned")
    assert cloned.status == PolicyStatus.draft
    assert cloned.is_default is False
    assert cloned.label == "cloned"
    assert cloned.version == policy.version + 1


# ---------------------------------------------------------------------------
# archive_scoring_policy
# ---------------------------------------------------------------------------


def test_archive_policy(db: sqlite3.Connection) -> None:
    channel, _, _, _ = _make_channel(db)
    p1 = _make_policy(db, channel.id, "p1")
    p2 = _make_policy(db, channel.id, "p2")
    activate_scoring_policy(db, p1.id)
    activate_scoring_policy(db, p2.id)
    activate_scoring_policy(db, p1.id)  # p1 is default now
    archive_scoring_policy(db, p2.id)
    assert get_scoring_policy(db, p2.id).status == PolicyStatus.archived


def test_archive_default_policy_raises(db: sqlite3.Connection) -> None:
    channel, _, _, _ = _make_channel(db)
    policy = _make_policy(db, channel.id)
    activate_scoring_policy(db, policy.id)
    with pytest.raises(ValueError, match="default"):
        archive_scoring_policy(db, policy.id)


# ---------------------------------------------------------------------------
# opportunity scores
# ---------------------------------------------------------------------------


def test_create_opportunity_score_appended(db: sqlite3.Connection) -> None:
    channel, profile, _, _ = _make_channel(db)
    run = _make_run(db, channel.id, profile.id)
    opp = _make_opp(db, channel.id, run.id)
    policy = _make_policy(db, channel.id)
    activate_scoring_policy(db, policy.id)
    score = _make_score(db, opp.id, policy.id, profile.id, composite=0.75)
    assert score.id is not None
    assert score.composite_score == 0.75


def test_get_latest_score_uses_deterministic_order(db: sqlite3.Connection) -> None:
    channel, profile, _, _ = _make_channel(db)
    run = _make_run(db, channel.id, profile.id)
    opp = _make_opp(db, channel.id, run.id)
    policy = _make_policy(db, channel.id)
    activate_scoring_policy(db, policy.id)

    _make_score(db, opp.id, policy.id, profile.id, composite=0.40, input_hash="hash1")
    _make_score(db, opp.id, policy.id, profile.id, composite=0.80, input_hash="hash2")

    latest = get_latest_score(db, opp.id, policy.id)
    assert latest is not None
    assert latest.composite_score == 0.80


def test_list_scores_for_opportunity_all_returned(db: sqlite3.Connection) -> None:
    channel, profile, _, _ = _make_channel(db)
    run = _make_run(db, channel.id, profile.id)
    opp = _make_opp(db, channel.id, run.id)
    policy = _make_policy(db, channel.id)
    activate_scoring_policy(db, policy.id)

    _make_score(db, opp.id, policy.id, profile.id, composite=0.3, input_hash="h1")
    _make_score(db, opp.id, policy.id, profile.id, composite=0.7, input_hash="h2")

    scores = list_scores_for_opportunity(db, opp.id)
    assert len(scores) == 2
    # Most recent first
    assert scores[0].composite_score == 0.7


def test_list_scored_opportunities_deduplicates_to_latest(db: sqlite3.Connection) -> None:
    channel, profile, _, _ = _make_channel(db)
    run = _make_run(db, channel.id, profile.id)
    opp1 = _make_opp(db, channel.id, run.id, topic="topic one")
    opp2 = _make_opp(db, channel.id, run.id, topic="topic two")
    policy = _make_policy(db, channel.id)
    activate_scoring_policy(db, policy.id)

    _make_score(db, opp1.id, policy.id, profile.id, composite=0.5, input_hash="h1")
    _make_score(db, opp1.id, policy.id, profile.id, composite=0.9, input_hash="h2")
    _make_score(db, opp2.id, policy.id, profile.id, composite=0.6, input_hash="h3")

    rows = list_scored_opportunities(db, channel.id, policy.id)
    opp1_rows = [r for r in rows if r[0].id == opp1.id]
    assert len(opp1_rows) == 1
    assert opp1_rows[0][1].composite_score == 0.9
