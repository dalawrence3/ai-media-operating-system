"""Integration tests for M3.2 repository functions."""

from __future__ import annotations

import sqlite3

import pytest

from app.intelligence.models import (
    AdapterName,
    DiscoveryRun,
    LifecycleState,
    Opportunity,
    OpportunityObservation,
    OpportunitySourceEvidence,
    RunStatus,
    SourceQualityTier,
)
from app.intelligence.repository import (
    create_channel_full,
    create_discovery_run,
    create_observation,
    create_opportunity,
    create_source_evidence,
    find_existing_opportunity,
    get_discovery_run,
    get_opportunity,
    list_discovery_runs,
    list_evidence,
    list_observations,
    list_opportunities,
    list_state_events,
    transition_opportunity_state,
    update_discovery_run,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _channel_and_run(db: sqlite3.Connection) -> tuple:
    channel, profile, *_ = create_channel_full(db, channel_name="Test", primary_niche="finance")
    run = create_discovery_run(
        db,
        DiscoveryRun(
            channel_id=channel.id,
            profile_version_id=profile.id,
            adapter_name=AdapterName.manual,
            status=RunStatus.running,
        ),
    )
    db.commit()
    return channel, profile, run


def _opportunity(db, channel_id, run_id, topic="personal finance") -> Opportunity:
    from app.intelligence.dedup import normalize_topic

    opp = create_opportunity(
        db,
        Opportunity(
            channel_id=channel_id,
            discovery_run_id=run_id,
            normalized_topic=normalize_topic(topic),
            raw_topic=topic,
        ),
    )
    db.commit()
    return opp


def _observation(db, opp_id, run_id) -> OpportunityObservation:
    obs = create_observation(
        db,
        OpportunityObservation(
            opportunity_id=opp_id,
            discovery_run_id=run_id,
            adapter_name=AdapterName.manual,
            source_quality_tier=SourceQualityTier.variable,
        ),
    )
    db.commit()
    return obs


# ---------------------------------------------------------------------------
# discovery_runs
# ---------------------------------------------------------------------------


def test_create_discovery_run_persists(db: sqlite3.Connection) -> None:
    channel, profile, run = _channel_and_run(db)
    loaded = get_discovery_run(db, run.id)
    assert loaded is not None
    assert loaded.channel_id == channel.id
    assert loaded.adapter_name == AdapterName.manual
    assert loaded.status == RunStatus.running


def test_get_discovery_run_returns_none_for_missing(db: sqlite3.Connection) -> None:
    assert get_discovery_run(db, 9999) is None


def test_update_discovery_run_status(db: sqlite3.Connection) -> None:
    _, _, run = _channel_and_run(db)
    update_discovery_run(db, run.id, status=RunStatus.completed, completed_at="2025-01-01T00:00:00")
    db.commit()
    loaded = get_discovery_run(db, run.id)
    assert loaded is not None
    assert loaded.status == RunStatus.completed
    assert loaded.completed_at is not None


def test_update_discovery_run_counts(db: sqlite3.Connection) -> None:
    _, _, run = _channel_and_run(db)
    update_discovery_run(db, run.id, new_opportunity_count=3, dedup_count=1, failed_count=0)
    db.commit()
    loaded = get_discovery_run(db, run.id)
    assert loaded is not None
    assert loaded.new_opportunity_count == 3
    assert loaded.dedup_count == 1
    assert loaded.failed_count == 0


def test_list_discovery_runs_empty(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    assert list_discovery_runs(db, channel.id) == []


def test_list_discovery_runs_returns_all(db: sqlite3.Connection) -> None:
    channel, profile, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    for _ in range(3):
        create_discovery_run(
            db,
            DiscoveryRun(
                channel_id=channel.id,
                profile_version_id=profile.id,
                adapter_name=AdapterName.manual,
                status=RunStatus.running,
            ),
        )
    db.commit()
    assert len(list_discovery_runs(db, channel.id)) == 3


# ---------------------------------------------------------------------------
# opportunities
# ---------------------------------------------------------------------------


def test_create_opportunity_persists(db: sqlite3.Connection) -> None:
    channel, _, run = _channel_and_run(db)
    opp = _opportunity(db, channel.id, run.id)
    loaded = get_opportunity(db, opp.id)
    assert loaded is not None
    assert loaded.raw_topic == "personal finance"
    assert loaded.current_lifecycle_state == LifecycleState.new


def test_create_opportunity_inserts_initial_state_event(db: sqlite3.Connection) -> None:
    channel, _, run = _channel_and_run(db)
    opp = _opportunity(db, channel.id, run.id)
    events = list_state_events(db, opp.id)
    assert len(events) == 1
    assert events[0].from_state is None
    assert events[0].to_state == LifecycleState.new
    assert events[0].actor == "system"
    assert events[0].reason == "discovered"


def test_get_opportunity_returns_none_for_missing(db: sqlite3.Connection) -> None:
    assert get_opportunity(db, 9999) is None


def test_list_opportunities_empty(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    assert list_opportunities(db, channel.id) == []


def test_list_opportunities_returns_all(db: sqlite3.Connection) -> None:
    channel, _, run = _channel_and_run(db)
    _opportunity(db, channel.id, run.id, "topic one")
    _opportunity(db, channel.id, run.id, "topic two")
    opps = list_opportunities(db, channel.id)
    assert len(opps) == 2


def test_list_opportunities_filters_by_state(db: sqlite3.Connection) -> None:
    channel, _, run = _channel_and_run(db)
    opp1 = _opportunity(db, channel.id, run.id, "topic one")
    _opportunity(db, channel.id, run.id, "topic two")
    transition_opportunity_state(db, opp1.id, LifecycleState.approved)
    db.commit()
    approved = list_opportunities(db, channel.id, state=LifecycleState.approved)
    new = list_opportunities(db, channel.id, state=LifecycleState.new)
    assert len(approved) == 1
    assert len(new) == 1


def test_opportunity_unique_constraint_per_channel(db: sqlite3.Connection) -> None:
    channel, _, run = _channel_and_run(db)
    _opportunity(db, channel.id, run.id, "finance tips")
    with pytest.raises(sqlite3.IntegrityError):
        _opportunity(db, channel.id, run.id, "finance tips")


# ---------------------------------------------------------------------------
# find_existing_opportunity
# ---------------------------------------------------------------------------


def test_find_existing_opportunity_no_match_when_empty(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    result = find_existing_opportunity(db, channel.id, "personal finance", 0.70)
    assert result is None


def test_find_existing_opportunity_exact_match(db: sqlite3.Connection) -> None:
    channel, _, run = _channel_and_run(db)
    _opportunity(db, channel.id, run.id, "personal finance tips")
    result = find_existing_opportunity(db, channel.id, "personal finance tips", 0.70)
    assert result is not None
    opp, score = result
    assert score == pytest.approx(1.0)


def test_find_existing_opportunity_above_threshold(db: sqlite3.Connection) -> None:
    # "personal finance tips" norm → "personal finance tips"
    # "finance tips guide" norm → "finance tips guide"
    # Jaccard: inter={finance, tips} / union={personal, finance, tips, guide} = 2/4 = 0.5
    # Below 0.70 → no match
    channel, _, run = _channel_and_run(db)
    _opportunity(db, channel.id, run.id, "personal finance tips")
    result = find_existing_opportunity(db, channel.id, "finance tips guide", 0.70)
    assert result is None


def test_find_existing_opportunity_at_threshold(db: sqlite3.Connection) -> None:
    # "finance tips" norm → "finance tips"
    # "finance tips" → identical → Jaccard = 1.0 ≥ 0.70
    channel, _, run = _channel_and_run(db)
    _opportunity(db, channel.id, run.id, "finance tips")
    result = find_existing_opportunity(db, channel.id, "finance tips", 0.70)
    assert result is not None


def test_find_existing_opportunity_excludes_rejected(db: sqlite3.Connection) -> None:
    channel, _, run = _channel_and_run(db)
    opp = _opportunity(db, channel.id, run.id, "finance tips")
    transition_opportunity_state(db, opp.id, LifecycleState.rejected)
    db.commit()
    result = find_existing_opportunity(db, channel.id, "finance tips", 0.70)
    assert result is None


def test_find_existing_opportunity_excludes_archived(db: sqlite3.Connection) -> None:
    channel, _, run = _channel_and_run(db)
    opp = _opportunity(db, channel.id, run.id, "finance tips")
    transition_opportunity_state(db, opp.id, LifecycleState.archived)
    db.commit()
    result = find_existing_opportunity(db, channel.id, "finance tips", 0.70)
    assert result is None


def test_find_existing_opportunity_returns_best_match(db: sqlite3.Connection) -> None:
    from app.intelligence.dedup import normalize_topic

    channel, _, run = _channel_and_run(db)
    # Create two opportunities; second is closer to the query
    create_opportunity(
        db,
        Opportunity(
            channel_id=channel.id,
            discovery_run_id=run.id,
            normalized_topic=normalize_topic("personal finance tips"),
            raw_topic="personal finance tips",
        ),
    )
    create_opportunity(
        db,
        Opportunity(
            channel_id=channel.id,
            discovery_run_id=run.id,
            normalized_topic=normalize_topic("budgeting basics savings"),
            raw_topic="budgeting basics savings",
        ),
    )
    db.commit()
    result = find_existing_opportunity(db, channel.id, "personal finance tips", 0.70)
    assert result is not None
    opp, score = result
    assert opp.raw_topic == "personal finance tips"
    assert score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# transition_opportunity_state
# ---------------------------------------------------------------------------


def test_transition_state_updates_current_state(db: sqlite3.Connection) -> None:
    channel, _, run = _channel_and_run(db)
    opp = _opportunity(db, channel.id, run.id)
    transition_opportunity_state(db, opp.id, LifecycleState.under_review)
    db.commit()
    loaded = get_opportunity(db, opp.id)
    assert loaded is not None
    assert loaded.current_lifecycle_state == LifecycleState.under_review


def test_transition_state_appends_event(db: sqlite3.Connection) -> None:
    channel, _, run = _channel_and_run(db)
    opp = _opportunity(db, channel.id, run.id)
    transition_opportunity_state(
        db, opp.id, LifecycleState.approved, actor="alice", reason="looks good"
    )
    db.commit()
    events = list_state_events(db, opp.id)
    # creation event + this transition
    assert len(events) == 2
    last = events[-1]
    assert last.from_state == LifecycleState.new
    assert last.to_state == LifecycleState.approved
    assert last.actor == "alice"
    assert last.reason == "looks good"


def test_transition_state_raises_for_missing_opportunity(db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="not found"):
        transition_opportunity_state(db, 9999, LifecycleState.approved)


def test_transition_state_is_atomic_with_state_update(db: sqlite3.Connection) -> None:
    """Both the event row and the denormalized column must update together."""
    channel, _, run = _channel_and_run(db)
    opp = _opportunity(db, channel.id, run.id)
    transition_opportunity_state(db, opp.id, LifecycleState.rejected)
    db.commit()
    loaded = get_opportunity(db, opp.id)
    events = list_state_events(db, opp.id)
    assert loaded.current_lifecycle_state == LifecycleState.rejected
    assert events[-1].to_state == LifecycleState.rejected


# ---------------------------------------------------------------------------
# observations
# ---------------------------------------------------------------------------


def test_create_observation_persists(db: sqlite3.Connection) -> None:
    channel, _, run = _channel_and_run(db)
    opp = _opportunity(db, channel.id, run.id)
    obs = _observation(db, opp.id, run.id)
    loaded = list_observations(db, opp.id)
    assert len(loaded) == 1
    assert loaded[0].id == obs.id
    assert loaded[0].adapter_name == AdapterName.manual


def test_observation_is_stale_computed_correctly(db: sqlite3.Connection) -> None:
    channel, profile, run = _channel_and_run(db)
    opp = _opportunity(db, channel.id, run.id)
    obs_fresh = create_observation(
        db,
        OpportunityObservation(
            opportunity_id=opp.id,
            discovery_run_id=run.id,
            adapter_name=AdapterName.manual,
            signal_age_days=3.0,
        ),
    )
    obs_stale = create_observation(
        db,
        OpportunityObservation(
            opportunity_id=opp.id,
            discovery_run_id=run.id,
            adapter_name=AdapterName.manual,
            signal_age_days=30.0,
        ),
    )
    db.commit()
    # signal_staleness_days default = 7
    staleness_days = profile.signal_staleness_days
    assert not obs_fresh.is_stale(staleness_days)
    assert obs_stale.is_stale(staleness_days)


def test_observation_is_stale_none_age_not_stale(db: sqlite3.Connection) -> None:
    obs = OpportunityObservation(
        opportunity_id=1,
        discovery_run_id=1,
        adapter_name=AdapterName.manual,
        signal_age_days=None,
    )
    assert not obs.is_stale(7)


def test_observation_dedup_fields_stored(db: sqlite3.Connection) -> None:
    channel, _, run = _channel_and_run(db)
    opp = _opportunity(db, channel.id, run.id)
    create_observation(
        db,
        OpportunityObservation(
            opportunity_id=opp.id,
            discovery_run_id=run.id,
            adapter_name=AdapterName.manual,
            was_deduplicated=True,
            candidate_topic="budgeting basics",
            dedup_similarity_score=0.85,
        ),
    )
    db.commit()
    loaded = list_observations(db, opp.id)[0]
    assert loaded.was_deduplicated is True
    assert loaded.candidate_topic == "budgeting basics"
    assert loaded.dedup_similarity_score == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# source evidence
# ---------------------------------------------------------------------------


def test_create_source_evidence_persists(db: sqlite3.Connection) -> None:
    channel, _, run = _channel_and_run(db)
    opp = _opportunity(db, channel.id, run.id)
    obs = _observation(db, opp.id, run.id)
    create_source_evidence(
        db,
        OpportunitySourceEvidence(
            observation_id=obs.id,
            opportunity_id=opp.id,
            evidence_type="view_count",
            evidence_value=100_000.0,
            evidence_unit="views",
            source_label="youtube_data_api:videos.list",
        ),
    )
    db.commit()
    loaded = list_evidence(db, obs.id)
    assert len(loaded) == 1
    assert loaded[0].evidence_type == "view_count"
    assert loaded[0].evidence_value == pytest.approx(100_000.0)


def test_list_evidence_empty_for_unknown_observation(db: sqlite3.Connection) -> None:
    assert list_evidence(db, 9999) == []
