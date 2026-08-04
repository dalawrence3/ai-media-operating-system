"""Tests for promote_opportunity() in the intelligence repository."""

from __future__ import annotations

import sqlite3

import pytest

from app.core.models import Topic
from app.core.repository import get_topic_by_promoted_opportunity, list_topics
from app.intelligence.models import (
    AdapterName,
    DiscoveryRun,
    LifecycleState,
    Opportunity,
    RunStatus,
)
from app.intelligence.repository import (
    create_channel_full,
    create_discovery_run,
    create_opportunity,
    get_opportunity,
    list_state_events,
    promote_opportunity,
    transition_opportunity_state,
)


def _make_channel(db: sqlite3.Connection, *, name: str = "Test Channel") -> int:
    channel, *_ = create_channel_full(db, channel_name=name, primary_niche="finance")
    assert channel.id is not None
    return channel.id


def _make_opportunity(
    db: sqlite3.Connection,
    channel_id: int,
    *,
    raw_topic: str = "index fund basics",
    title: str = "",
    topic_summary: str = "",
) -> Opportunity:
    run = create_discovery_run(
        db,
        DiscoveryRun(
            channel_id=channel_id,
            profile_version_id=1,
            adapter_name=AdapterName.manual,
            query_parameters_json="{}",
            status=RunStatus.completed,
        ),
    )
    db.commit()
    opp = create_opportunity(
        db,
        Opportunity(
            channel_id=channel_id,
            discovery_run_id=run.id,
            normalized_topic=raw_topic.lower(),
            raw_topic=raw_topic,
            title=title,
            topic_summary=topic_summary,
            current_lifecycle_state=LifecycleState.new,
        ),
    )
    db.commit()
    return opp


def _add_score(
    db: sqlite3.Connection, opportunity_id: int, policy_id: int, profile_id: int
) -> None:
    from app.intelligence.models import FactorStatus, OpportunityScore
    from app.intelligence.repository import create_opportunity_score

    score = OpportunityScore(
        opportunity_id=opportunity_id,
        scoring_policy_id=policy_id,
        channel_profile_version_id=profile_id,
        composite_score=0.72,
        confidence=0.61,
        status_trend_strength=FactorStatus.present,
        status_audience_demand=FactorStatus.present,
        status_competition=FactorStatus.present,
        status_evergreen_value=FactorStatus.present,
        status_audience_fit=FactorStatus.present,
        status_content_novelty=FactorStatus.present,
        eff_weight_trend_strength=0.05,
        eff_weight_audience_demand=0.20,
        eff_weight_competition=0.15,
        eff_weight_evergreen_value=0.20,
        eff_weight_audience_fit=0.30,
        eff_weight_content_novelty=0.10,
        input_hash="abc",
        scorer_version="1.0",
    )
    create_opportunity_score(db, score)
    db.commit()


def _make_policy(db: sqlite3.Connection, channel_id: int) -> int:
    from app.intelligence.models import PolicyStatus, ScoringPolicy
    from app.intelligence.repository import activate_scoring_policy, create_scoring_policy

    policy = create_scoring_policy(
        db,
        ScoringPolicy(
            channel_id=channel_id,
            version=1,
            label="default",
            status=PolicyStatus.draft,
        ),
    )
    activate_scoring_policy(db, policy.id)
    db.commit()
    assert policy.id is not None
    return policy.id


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_promote_returns_topic_and_event(db: sqlite3.Connection) -> None:
    channel_id = _make_channel(db)
    opp = _make_opportunity(db, channel_id)
    policy_id = _make_policy(db, channel_id)
    _add_score(db, opp.id, policy_id, 1)

    topic, event = promote_opportunity(db, opp.id)

    assert isinstance(topic, Topic)
    assert topic.id is not None
    assert topic.promoted_opportunity_id == opp.id
    assert event is not None


def test_promote_topic_title_from_raw_topic(db: sqlite3.Connection) -> None:
    channel_id = _make_channel(db)
    opp = _make_opportunity(db, channel_id, raw_topic="ETF investing guide", title="")
    policy_id = _make_policy(db, channel_id)
    _add_score(db, opp.id, policy_id, 1)

    topic, _ = promote_opportunity(db, opp.id)

    assert topic.title == "ETF investing guide"


def test_promote_topic_title_from_opportunity_title(db: sqlite3.Connection) -> None:
    channel_id = _make_channel(db)
    opp = _make_opportunity(
        db, channel_id, raw_topic="raw topic", title="Curated Title"
    )
    policy_id = _make_policy(db, channel_id)
    _add_score(db, opp.id, policy_id, 1)

    topic, _ = promote_opportunity(db, opp.id)

    assert topic.title == "Curated Title"


def test_promote_angle_from_topic_summary(db: sqlite3.Connection) -> None:
    channel_id = _make_channel(db)
    opp = _make_opportunity(
        db, channel_id, topic_summary="How low-cost ETFs beat active funds"
    )
    policy_id = _make_policy(db, channel_id)
    _add_score(db, opp.id, policy_id, 1)

    topic, _ = promote_opportunity(db, opp.id)

    assert topic.angle == "How low-cost ETFs beat active funds"


def test_promote_angle_override(db: sqlite3.Connection) -> None:
    channel_id = _make_channel(db)
    opp = _make_opportunity(db, channel_id, topic_summary="original summary")
    policy_id = _make_policy(db, channel_id)
    _add_score(db, opp.id, policy_id, 1)

    topic, _ = promote_opportunity(db, opp.id, angle_override="custom angle")

    assert topic.angle == "custom angle"


def test_promote_transitions_to_approved(db: sqlite3.Connection) -> None:
    channel_id = _make_channel(db)
    opp = _make_opportunity(db, channel_id)
    policy_id = _make_policy(db, channel_id)
    _add_score(db, opp.id, policy_id, 1)

    promote_opportunity(db, opp.id)

    updated = get_opportunity(db, opp.id)
    assert updated is not None
    assert updated.current_lifecycle_state == LifecycleState.approved


def test_promote_operator_recorded_in_state_event(db: sqlite3.Connection) -> None:
    channel_id = _make_channel(db)
    opp = _make_opportunity(db, channel_id)
    policy_id = _make_policy(db, channel_id)
    _add_score(db, opp.id, policy_id, 1)

    promote_opportunity(db, opp.id, operator="alice")

    events = list_state_events(db, opp.id)
    promotion_event = next(e for e in events if e.to_state == LifecycleState.approved)
    assert promotion_event.actor == "alice"


def test_promote_topic_appears_in_list_topics(db: sqlite3.Connection) -> None:
    channel_id = _make_channel(db)
    opp = _make_opportunity(db, channel_id, raw_topic="dividend investing")
    policy_id = _make_policy(db, channel_id)
    _add_score(db, opp.id, policy_id, 1)

    promote_opportunity(db, opp.id)

    topics = list_topics(db)
    titles = [t.title for t in topics]
    assert "dividend investing" in titles


def test_promote_topic_status_is_active(db: sqlite3.Connection) -> None:
    from app.core.models import TopicStatus

    channel_id = _make_channel(db)
    opp = _make_opportunity(db, channel_id)
    policy_id = _make_policy(db, channel_id)
    _add_score(db, opp.id, policy_id, 1)

    topic, _ = promote_opportunity(db, opp.id)

    assert topic.status == TopicStatus.active


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_promote_idempotent_returns_same_topic(db: sqlite3.Connection) -> None:
    channel_id = _make_channel(db)
    opp = _make_opportunity(db, channel_id)
    policy_id = _make_policy(db, channel_id)
    _add_score(db, opp.id, policy_id, 1)

    topic1, _ = promote_opportunity(db, opp.id)
    topic2, _ = promote_opportunity(db, opp.id)

    assert topic1.id == topic2.id


def test_promote_idempotent_no_duplicate_topic_row(db: sqlite3.Connection) -> None:
    channel_id = _make_channel(db)
    opp = _make_opportunity(db, channel_id)
    policy_id = _make_policy(db, channel_id)
    _add_score(db, opp.id, policy_id, 1)

    promote_opportunity(db, opp.id)
    promote_opportunity(db, opp.id)

    count = db.execute(
        "SELECT COUNT(*) FROM topics WHERE promoted_opportunity_id = ?", (opp.id,)
    ).fetchone()[0]
    assert count == 1


def test_get_topic_by_promoted_opportunity_round_trip(db: sqlite3.Connection) -> None:
    channel_id = _make_channel(db)
    opp = _make_opportunity(db, channel_id)
    policy_id = _make_policy(db, channel_id)
    _add_score(db, opp.id, policy_id, 1)

    topic, _ = promote_opportunity(db, opp.id)
    found = get_topic_by_promoted_opportunity(db, opp.id)

    assert found is not None
    assert found.id == topic.id
    assert found.promoted_opportunity_id == opp.id


def test_get_topic_by_promoted_opportunity_returns_none_for_manual_topic(
    db: sqlite3.Connection,
) -> None:
    from app.core.models import Topic
    from app.core.repository import create_topic

    create_topic(db, Topic(title="Manual Topic"))
    result = get_topic_by_promoted_opportunity(db, 9999)
    assert result is None


# ---------------------------------------------------------------------------
# Guard: lifecycle state
# ---------------------------------------------------------------------------


def test_promote_new_state_eligible(db: sqlite3.Connection) -> None:
    channel_id = _make_channel(db)
    opp = _make_opportunity(db, channel_id)
    policy_id = _make_policy(db, channel_id)
    _add_score(db, opp.id, policy_id, 1)

    topic, _ = promote_opportunity(db, opp.id)
    assert topic.id is not None


def test_promote_under_review_eligible(db: sqlite3.Connection) -> None:
    channel_id = _make_channel(db)
    opp = _make_opportunity(db, channel_id)
    policy_id = _make_policy(db, channel_id)
    _add_score(db, opp.id, policy_id, 1)
    transition_opportunity_state(db, opp.id, LifecycleState.under_review)
    db.commit()

    topic, _ = promote_opportunity(db, opp.id)
    assert topic.id is not None


def test_promote_rejected_state_raises(db: sqlite3.Connection) -> None:
    channel_id = _make_channel(db)
    opp = _make_opportunity(db, channel_id)
    policy_id = _make_policy(db, channel_id)
    _add_score(db, opp.id, policy_id, 1)
    transition_opportunity_state(db, opp.id, LifecycleState.rejected)
    db.commit()

    with pytest.raises(ValueError, match="cannot be promoted"):
        promote_opportunity(db, opp.id)


def test_promote_produced_state_raises(db: sqlite3.Connection) -> None:
    channel_id = _make_channel(db)
    opp = _make_opportunity(db, channel_id)
    policy_id = _make_policy(db, channel_id)
    _add_score(db, opp.id, policy_id, 1)
    transition_opportunity_state(db, opp.id, LifecycleState.produced)
    db.commit()

    with pytest.raises(ValueError, match="cannot be promoted"):
        promote_opportunity(db, opp.id)


def test_promote_archived_state_raises(db: sqlite3.Connection) -> None:
    channel_id = _make_channel(db)
    opp = _make_opportunity(db, channel_id)
    policy_id = _make_policy(db, channel_id)
    _add_score(db, opp.id, policy_id, 1)
    transition_opportunity_state(db, opp.id, LifecycleState.archived)
    db.commit()

    with pytest.raises(ValueError, match="cannot be promoted"):
        promote_opportunity(db, opp.id)


def test_promote_not_found_raises(db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="not found"):
        promote_opportunity(db, 9999)


# ---------------------------------------------------------------------------
# Guard: score requirement
# ---------------------------------------------------------------------------


def test_promote_without_score_raises(db: sqlite3.Connection) -> None:
    channel_id = _make_channel(db)
    opp = _make_opportunity(db, channel_id)

    with pytest.raises(ValueError, match="no score"):
        promote_opportunity(db, opp.id)


def test_promote_allow_unscored_bypasses_check(db: sqlite3.Connection) -> None:
    channel_id = _make_channel(db)
    opp = _make_opportunity(db, channel_id)

    topic, _ = promote_opportunity(db, opp.id, allow_unscored=True)
    assert topic.id is not None


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


def test_promote_atomicity_on_transition_failure(
    db: sqlite3.Connection, monkeypatch
) -> None:
    """If transition_opportunity_state raises, no topics row must be committed."""
    from app.intelligence import repository as repo_module

    channel_id = _make_channel(db)
    opp = _make_opportunity(db, channel_id)
    policy_id = _make_policy(db, channel_id)
    _add_score(db, opp.id, policy_id, 1)

    def _failing(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(repo_module, "transition_opportunity_state", _failing)

    with pytest.raises(RuntimeError, match="simulated failure"):
        promote_opportunity(db, opp.id)

    count = db.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
    assert count == 0

    updated = get_opportunity(db, opp.id)
    assert updated is not None
    assert updated.current_lifecycle_state == LifecycleState.new


# ---------------------------------------------------------------------------
# Unique index
# ---------------------------------------------------------------------------


def test_unique_index_prevents_direct_double_insert(db: sqlite3.Connection) -> None:
    channel_id = _make_channel(db)
    opp = _make_opportunity(db, channel_id)

    db.execute(
        "INSERT INTO topics (title, angle, status, promoted_opportunity_id, created_at, updated_at)"
        " VALUES ('a', '', 'active', ?, '2024-01-01T00:00:00', '2024-01-01T00:00:00')",
        (opp.id,),
    )
    db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO topics"
            " (title, angle, status, promoted_opportunity_id, created_at, updated_at)"
            " VALUES ('b', '', 'active', ?, '2024-01-01T00:00:00', '2024-01-01T00:00:00')",
            (opp.id,),
        )
        db.commit()


def test_null_promoted_opportunity_id_does_not_violate_unique_index(
    db: sqlite3.Connection,
) -> None:
    """Multiple manually-created topics (NULL promoted_opportunity_id) must coexist."""
    for i in range(3):
        db.execute(
            "INSERT INTO topics (title, angle, status, created_at, updated_at)"
            " VALUES (?, '', 'active', '2024-01-01T00:00:00', '2024-01-01T00:00:00')",
            (f"Manual Topic {i}",),
        )
    db.commit()
    count = db.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
    assert count == 3
