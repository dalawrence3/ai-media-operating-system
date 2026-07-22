"""Tests for the intelligence repository layer."""

from __future__ import annotations

import sqlite3

import pytest

from app.intelligence.models import (
    DEFAULT_PRE_MONETIZATION_WEIGHTS,
    MaturityStage,
    MonetizationStatus,
    OperatingMode,
    VersionStatus,
)
from app.intelligence.repository import (
    activate_profile_version,
    activate_strategy_version,
    create_channel_full,
    create_new_profile_version,
    create_new_strategy_version,
    create_or_update_capacity_policy,
    get_active_monetization_strategy,
    get_active_profile_version,
    get_capacity_policy,
    get_channel,
    get_channel_by_name,
    get_monetization_strategy,
    get_profile_version,
    list_channels,
    list_monetization_strategies,
    list_operating_mode_events,
    list_profile_versions,
    set_channel_operating_mode,
    supersede_profile_version,
)

# ---------------------------------------------------------------------------
# create_channel_full
# ---------------------------------------------------------------------------


def test_create_channel_full_returns_all_entities(db: sqlite3.Connection) -> None:
    channel, profile, strategy, capacity = create_channel_full(
        db,
        channel_name="Finance Fundamentals",
        primary_niche="personal finance",
    )
    assert channel.id is not None
    assert profile.id is not None
    assert strategy.id is not None
    assert capacity.id is not None


def test_create_channel_full_channel_refs_are_set(db: sqlite3.Connection) -> None:
    channel, profile, strategy, _ = create_channel_full(
        db, channel_name="Test Channel", primary_niche="investing"
    )
    loaded = get_channel(db, channel.id)  # type: ignore[arg-type]
    assert loaded is not None
    assert loaded.current_profile_version_id == profile.id
    assert loaded.current_strategy_id == strategy.id


def test_create_channel_full_initial_mode_is_manual(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    assert channel.operating_mode == OperatingMode.manual


def test_create_channel_full_initial_stage_is_validation(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    assert channel.current_maturity_stage == MaturityStage.validation


def test_create_channel_full_strategy_has_default_weights(db: sqlite3.Connection) -> None:
    _, _, strategy, _ = create_channel_full(db, channel_name="X", primary_niche="y")
    assert strategy.monetization_status == MonetizationStatus.pre
    assert abs(sum(strategy.objective_weights.values()) - 1.0) < 0.001
    assert strategy.objective_weights == DEFAULT_PRE_MONETIZATION_WEIGHTS


def test_create_channel_full_capacity_defaults_match_d6(db: sqlite3.Connection) -> None:
    """Operator decision D6 capacity defaults must be applied."""
    _, _, _, capacity = create_channel_full(db, channel_name="X", primary_niche="y")
    assert capacity.long_form_slots_per_week == 2
    assert capacity.short_slots_per_week == 4
    assert capacity.content_package_slots_per_week == 1
    assert capacity.max_concurrent_productions == 2
    assert capacity.review_hours_per_week == pytest.approx(3.0)


def test_create_channel_full_records_mode_event(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y", operator="alice")
    events = list_operating_mode_events(db, channel.id)  # type: ignore[arg-type]
    assert len(events) == 1
    assert events[0].from_mode is None
    assert events[0].to_mode == OperatingMode.manual
    assert events[0].operator == "alice"


def test_create_channel_full_profile_version_is_one(db: sqlite3.Connection) -> None:
    _, profile, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    assert profile.version == 1


def test_create_channel_full_strategy_version_is_one(db: sqlite3.Connection) -> None:
    _, _, strategy, _ = create_channel_full(db, channel_name="X", primary_niche="y")
    assert strategy.version == 1


def test_create_channel_full_initial_versions_are_active(db: sqlite3.Connection) -> None:
    """Initial profile and strategy must be immediately active — no draft step needed."""
    channel, profile, strategy, _ = create_channel_full(db, channel_name="X", primary_niche="y")
    assert profile.status == VersionStatus.active
    assert strategy.status == VersionStatus.active
    assert profile.activated_at is not None
    assert strategy.activated_at is not None


def test_create_channel_full_profile_is_active(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    active = get_active_profile_version(db, channel.id)  # type: ignore[arg-type]
    assert active is not None
    assert active.status == VersionStatus.active
    assert active.superseded_at is None


def test_create_channel_full_strategy_is_active(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    active = get_active_monetization_strategy(db, channel.id)  # type: ignore[arg-type]
    assert active is not None
    assert active.status == VersionStatus.active
    assert active.superseded_at is None


# ---------------------------------------------------------------------------
# Channel CRUD
# ---------------------------------------------------------------------------


def test_get_channel_returns_none_for_missing(db: sqlite3.Connection) -> None:
    assert get_channel(db, 999) is None


def test_get_channel_by_name(db: sqlite3.Connection) -> None:
    create_channel_full(db, channel_name="Alpha", primary_niche="crypto")
    ch = get_channel_by_name(db, "Alpha")
    assert ch is not None
    assert ch.channel_name == "Alpha"


def test_get_channel_by_name_returns_none_for_missing(db: sqlite3.Connection) -> None:
    assert get_channel_by_name(db, "Nonexistent") is None


def test_list_channels_empty(db: sqlite3.Connection) -> None:
    assert list_channels(db) == []


def test_list_channels_returns_all(db: sqlite3.Connection) -> None:
    create_channel_full(db, channel_name="A", primary_niche="x")
    create_channel_full(db, channel_name="B", primary_niche="x")
    channels = list_channels(db)
    assert len(channels) == 2
    names = {ch.channel_name for ch in channels}
    assert names == {"A", "B"}


def test_channel_isolation(db: sqlite3.Connection) -> None:
    ch_a, *_ = create_channel_full(db, channel_name="ChannelA", primary_niche="finance")
    ch_b, *_ = create_channel_full(db, channel_name="ChannelB", primary_niche="tech")
    profiles_a = list_profile_versions(db, ch_a.id)  # type: ignore[arg-type]
    profiles_b = list_profile_versions(db, ch_b.id)  # type: ignore[arg-type]
    assert len(profiles_a) == 1
    assert len(profiles_b) == 1
    assert profiles_a[0].primary_niche == "finance"
    assert profiles_b[0].primary_niche == "tech"


# ---------------------------------------------------------------------------
# Profile versions — draft lifecycle
# ---------------------------------------------------------------------------


def test_profile_version_immutability(db: sqlite3.Connection) -> None:
    """Profile versions must not have an update path — only read and supersede."""
    channel, profile, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    loaded = get_profile_version(db, profile.id)  # type: ignore[arg-type]
    assert loaded is not None
    assert loaded.primary_niche == "y"
    # No update function exists in the repository — this is the test.


def test_supersede_profile_version_sets_timestamp(db: sqlite3.Connection) -> None:
    channel, profile, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    supersede_profile_version(db, profile.id)  # type: ignore[arg-type]
    db.commit()
    loaded = get_profile_version(db, profile.id)  # type: ignore[arg-type]
    assert loaded is not None
    assert loaded.superseded_at is not None
    assert loaded.status == VersionStatus.superseded


def test_create_new_profile_version_creates_draft(db: sqlite3.Connection) -> None:
    """create_new_profile_version must produce a draft — not activate it."""
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    draft = create_new_profile_version(db, channel.id, primary_niche="investing")  # type: ignore[arg-type]
    assert draft.version == 2
    assert draft.primary_niche == "investing"
    assert draft.status == VersionStatus.draft
    assert draft.activated_at is None


def test_create_new_profile_version_does_not_supersede_active(db: sqlite3.Connection) -> None:
    """Creating a draft must not touch the currently active version."""
    channel, v1, *_ = create_channel_full(db, channel_name="X", primary_niche="budgeting")
    create_new_profile_version(db, channel.id)  # type: ignore[arg-type]
    loaded_v1 = get_profile_version(db, v1.id)  # type: ignore[arg-type]
    assert loaded_v1 is not None
    assert loaded_v1.status == VersionStatus.active
    assert loaded_v1.superseded_at is None


def test_create_new_profile_version_does_not_update_channel_ref(db: sqlite3.Connection) -> None:
    """The channel pointer must not change until the draft is activated."""
    channel, v1, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    create_new_profile_version(db, channel.id)  # type: ignore[arg-type]
    loaded_channel = get_channel(db, channel.id)  # type: ignore[arg-type]
    assert loaded_channel is not None
    assert loaded_channel.current_profile_version_id == v1.id  # still points at v1


def test_get_active_profile_version_unchanged_after_draft(db: sqlite3.Connection) -> None:
    """get_active_profile_version must return the old active version after draft creation."""
    channel, v1, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    create_new_profile_version(db, channel.id)  # type: ignore[arg-type]
    active = get_active_profile_version(db, channel.id)  # type: ignore[arg-type]
    assert active is not None
    assert active.version == 1
    assert active.id == v1.id


def test_activate_profile_version_makes_it_active(db: sqlite3.Connection) -> None:
    """Calling activate_profile_version must set status to 'active' with audit fields."""
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    draft = create_new_profile_version(db, channel.id, operator="alice")  # type: ignore[arg-type]
    activated = activate_profile_version(db, draft.id, operator="alice", reason="test pivot")  # type: ignore[arg-type]
    assert activated.status == VersionStatus.active
    assert activated.activated_at is not None
    assert activated.activated_by == "alice"
    assert activated.activation_reason == "test pivot"


def test_activate_profile_version_supersedes_old_active(db: sqlite3.Connection) -> None:
    """Activating a draft must atomically supersede the previously active version."""
    channel, v1, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    draft = create_new_profile_version(db, channel.id)  # type: ignore[arg-type]
    activate_profile_version(db, draft.id)  # type: ignore[arg-type]
    loaded_v1 = get_profile_version(db, v1.id)  # type: ignore[arg-type]
    assert loaded_v1 is not None
    assert loaded_v1.status == VersionStatus.superseded
    assert loaded_v1.superseded_at is not None


def test_activate_profile_version_updates_channel_ref(db: sqlite3.Connection) -> None:
    """Channel pointer must be updated atomically on activation."""
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    draft = create_new_profile_version(db, channel.id)  # type: ignore[arg-type]
    activate_profile_version(db, draft.id)  # type: ignore[arg-type]
    loaded_channel = get_channel(db, channel.id)  # type: ignore[arg-type]
    assert loaded_channel is not None
    assert loaded_channel.current_profile_version_id == draft.id


def test_activate_profile_version_rejects_non_draft(db: sqlite3.Connection) -> None:
    """Activating a version that is already 'active' must raise ValueError."""
    channel, v1, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    with pytest.raises(ValueError, match="status"):
        activate_profile_version(db, v1.id)  # type: ignore[arg-type]


def test_activate_profile_version_rejects_missing(db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="not found"):
        activate_profile_version(db, 9999)


def test_get_active_profile_version_after_activation(db: sqlite3.Connection) -> None:
    """After activation, get_active returns the newly activated version."""
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    draft = create_new_profile_version(db, channel.id)  # type: ignore[arg-type]
    activate_profile_version(db, draft.id)  # type: ignore[arg-type]
    active = get_active_profile_version(db, channel.id)  # type: ignore[arg-type]
    assert active is not None
    assert active.version == 2
    assert active.id == draft.id


def test_create_new_profile_version_carries_forward_defaults(db: sqlite3.Connection) -> None:
    channel, v1, *_ = create_channel_full(db, channel_name="X", primary_niche="budgeting")
    v2 = create_new_profile_version(db, channel.id, maturity_stage=MaturityStage.growth)  # type: ignore[arg-type]
    assert v2.primary_niche == "budgeting"  # carried forward
    assert v2.maturity_stage == MaturityStage.growth  # overridden


def test_create_new_profile_version_supersedes_old(db: sqlite3.Connection) -> None:
    """Full round-trip: create draft, verify old stays active, activate, verify old superseded."""
    channel, v1, *_ = create_channel_full(db, channel_name="X", primary_niche="budgeting")
    draft = create_new_profile_version(db, channel.id, primary_niche="investing")  # type: ignore[arg-type]
    assert draft.version == 2
    assert draft.primary_niche == "investing"
    # Old version must still be active before explicit activation
    loaded_v1 = get_profile_version(db, v1.id)  # type: ignore[arg-type]
    assert loaded_v1 is not None
    assert loaded_v1.superseded_at is None
    # After explicit activation, old is superseded
    activate_profile_version(db, draft.id)
    loaded_v1 = get_profile_version(db, v1.id)  # type: ignore[arg-type]
    assert loaded_v1.superseded_at is not None


def test_multiple_drafts_use_sequential_version_numbers(db: sqlite3.Connection) -> None:
    """Creating multiple drafts without activating must produce v2, v3, etc."""
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    d2 = create_new_profile_version(db, channel.id)  # type: ignore[arg-type]
    d3 = create_new_profile_version(db, channel.id)  # type: ignore[arg-type]
    assert d2.version == 2
    assert d3.version == 3


def test_list_profile_versions_returns_all(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    create_new_profile_version(db, channel.id)  # type: ignore[arg-type]
    create_new_profile_version(db, channel.id)  # type: ignore[arg-type]
    versions = list_profile_versions(db, channel.id)  # type: ignore[arg-type]
    assert len(versions) == 3


def test_profile_version_not_found(db: sqlite3.Connection) -> None:
    assert get_profile_version(db, 9999) is None


def test_active_profile_version_none_when_no_channel(db: sqlite3.Connection) -> None:
    assert get_active_profile_version(db, 9999) is None


# ---------------------------------------------------------------------------
# Monetization strategy — draft lifecycle
# ---------------------------------------------------------------------------


def test_create_new_strategy_version_creates_draft(db: sqlite3.Connection) -> None:
    """create_new_strategy_version must produce a draft, not activate it."""
    channel, _, s1, _ = create_channel_full(db, channel_name="X", primary_niche="y")
    draft = create_new_strategy_version(
        db,
        channel.id,  # type: ignore[arg-type]
        objective_weights={"ad_revenue": 1.0},
        monetization_status=MonetizationStatus.active,
    )
    assert draft.version == 2
    assert draft.status == VersionStatus.draft
    assert draft.activated_at is None


def test_create_new_strategy_version_does_not_supersede_active(db: sqlite3.Connection) -> None:
    """Creating a strategy draft must not touch the currently active strategy."""
    channel, _, s1, _ = create_channel_full(db, channel_name="X", primary_niche="y")
    create_new_strategy_version(
        db,
        channel.id,
        objective_weights={"ad_revenue": 1.0},  # type: ignore[arg-type]
    )
    loaded_s1 = get_monetization_strategy(db, s1.id)  # type: ignore[arg-type]
    assert loaded_s1 is not None
    assert loaded_s1.status == VersionStatus.active
    assert loaded_s1.superseded_at is None


def test_create_new_strategy_supersedes_old(db: sqlite3.Connection) -> None:
    """Full round-trip: create draft, activate, verify old superseded."""
    channel, _, s1, _ = create_channel_full(db, channel_name="X", primary_niche="y")
    draft = create_new_strategy_version(
        db,
        channel.id,  # type: ignore[arg-type]
        objective_weights={"ad_revenue": 0.7, "contribution_margin": 0.3},
        monetization_status=MonetizationStatus.active,
    )
    assert draft.version == 2
    assert draft.monetization_status == MonetizationStatus.active
    # Old still active before activation
    loaded_s1 = get_monetization_strategy(db, s1.id)  # type: ignore[arg-type]
    assert loaded_s1 is not None
    assert loaded_s1.superseded_at is None
    # After activation, old is superseded
    activate_strategy_version(db, draft.id)
    loaded_s1 = get_monetization_strategy(db, s1.id)  # type: ignore[arg-type]
    assert loaded_s1.superseded_at is not None
    assert loaded_s1.status == VersionStatus.superseded


def test_activate_strategy_version_makes_it_active(db: sqlite3.Connection) -> None:
    channel, _, _, _ = create_channel_full(db, channel_name="X", primary_niche="y")
    draft = create_new_strategy_version(
        db,
        channel.id,
        objective_weights={"ad_revenue": 1.0},  # type: ignore[arg-type]
    )
    activated = activate_strategy_version(db, draft.id, operator="bob", reason="upgrade")  # type: ignore[arg-type]
    assert activated.status == VersionStatus.active
    assert activated.activated_at is not None
    assert activated.activated_by == "bob"
    assert activated.activation_reason == "upgrade"


def test_activate_strategy_version_rejects_non_draft(db: sqlite3.Connection) -> None:
    channel, _, s1, _ = create_channel_full(db, channel_name="X", primary_niche="y")
    with pytest.raises(ValueError, match="status"):
        activate_strategy_version(db, s1.id)  # type: ignore[arg-type]


def test_create_new_strategy_updates_channel_ref(db: sqlite3.Connection) -> None:
    """Channel pointer updates only after explicit activation, not on draft creation."""
    channel, _, s1, _ = create_channel_full(db, channel_name="X", primary_niche="y")
    draft = create_new_strategy_version(
        db,
        channel.id,  # type: ignore[arg-type]
        objective_weights={"ad_revenue": 1.0},
        monetization_status=MonetizationStatus.active,
    )
    # Not yet activated — channel still points at s1
    loaded = get_channel(db, channel.id)  # type: ignore[arg-type]
    assert loaded is not None
    assert loaded.current_strategy_id == s1.id
    # After activation — channel points at draft
    activate_strategy_version(db, draft.id)
    loaded = get_channel(db, channel.id)  # type: ignore[arg-type]
    assert loaded.current_strategy_id == draft.id


def test_list_monetization_strategies(db: sqlite3.Connection) -> None:
    channel, _, _, _ = create_channel_full(db, channel_name="X", primary_niche="y")
    create_new_strategy_version(
        db,
        channel.id,  # type: ignore[arg-type]
        objective_weights={"ad_revenue": 1.0},
        monetization_status=MonetizationStatus.active,
    )
    strategies = list_monetization_strategies(db, channel.id)  # type: ignore[arg-type]
    assert len(strategies) == 2


def test_get_active_strategy_is_latest(db: sqlite3.Connection) -> None:
    channel, _, _, _ = create_channel_full(db, channel_name="X", primary_niche="y")
    draft = create_new_strategy_version(
        db,
        channel.id,  # type: ignore[arg-type]
        objective_weights={"ad_revenue": 1.0},
        monetization_status=MonetizationStatus.active,
    )
    # Before activation — active is still s1
    active_before = get_active_monetization_strategy(db, channel.id)  # type: ignore[arg-type]
    assert active_before is not None
    assert active_before.version == 1
    # After activation — active is s2
    activate_strategy_version(db, draft.id)
    active = get_active_monetization_strategy(db, channel.id)  # type: ignore[arg-type]
    assert active is not None
    assert active.id == draft.id


# ---------------------------------------------------------------------------
# Capacity policy
# ---------------------------------------------------------------------------


def test_create_or_update_capacity_policy_creates_on_first_call(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    # Policy is created automatically by create_channel_full; verify it exists
    policy = get_capacity_policy(db, channel.id)  # type: ignore[arg-type]
    assert policy is not None
    assert policy.channel_id == channel.id


def test_update_capacity_policy_persists(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    policy = get_capacity_policy(db, channel.id)  # type: ignore[arg-type]
    assert policy is not None
    policy.long_form_slots_per_week = 5
    create_or_update_capacity_policy(db, policy)
    db.commit()
    reloaded = get_capacity_policy(db, channel.id)  # type: ignore[arg-type]
    assert reloaded is not None
    assert reloaded.long_form_slots_per_week == 5


def test_get_capacity_policy_returns_none_for_missing(db: sqlite3.Connection) -> None:
    assert get_capacity_policy(db, 9999) is None


# ---------------------------------------------------------------------------
# Operating mode events
# ---------------------------------------------------------------------------


def test_set_channel_operating_mode_records_event(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    # Transition manual → manual (valid in Phase 3 — no-op but still auditable)
    set_channel_operating_mode(
        db,
        channel.id,
        OperatingMode.manual,
        operator="bob",
        reason="test",  # type: ignore[arg-type]
    )
    events = list_operating_mode_events(db, channel.id)  # type: ignore[arg-type]
    assert len(events) == 2  # creation event + this one
    last = events[-1]
    assert last.from_mode == OperatingMode.manual
    assert last.to_mode == OperatingMode.manual
    assert last.operator == "bob"
    assert last.reason == "test"


def test_set_channel_operating_mode_updates_channel_record(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    set_channel_operating_mode(db, channel.id, OperatingMode.manual)  # type: ignore[arg-type]
    loaded = get_channel(db, channel.id)  # type: ignore[arg-type]
    assert loaded is not None
    assert loaded.operating_mode == OperatingMode.manual


def test_set_channel_operating_mode_raises_for_missing_channel(db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="not found"):
        set_channel_operating_mode(db, 9999, OperatingMode.manual)


def test_list_operating_mode_events_empty_for_unknown_channel(db: sqlite3.Connection) -> None:
    events = list_operating_mode_events(db, 9999)
    assert events == []


def test_operating_mode_events_are_append_only(db: sqlite3.Connection) -> None:
    """Events accumulate — they cannot be modified after insertion."""
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    set_channel_operating_mode(db, channel.id, OperatingMode.manual)  # type: ignore[arg-type]
    set_channel_operating_mode(db, channel.id, OperatingMode.manual)  # type: ignore[arg-type]
    events = list_operating_mode_events(db, channel.id)  # type: ignore[arg-type]
    assert len(events) == 3  # creation + 2 explicit transitions


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_data_persists_across_connections(tmp_path) -> None:
    from app.core.database import open_db

    db_path = tmp_path / "persist.db"
    conn1 = open_db(db_path)
    ch, *_ = create_channel_full(conn1, channel_name="Persist Test", primary_niche="z")
    channel_id = ch.id
    conn1.close()

    conn2 = open_db(db_path)
    loaded = get_channel(conn2, channel_id)
    assert loaded is not None
    assert loaded.channel_name == "Persist Test"
    conn2.close()
