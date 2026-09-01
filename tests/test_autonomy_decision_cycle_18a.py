"""Phase 18A — Autonomous decision & queue orchestrator.

Covers:
- Cadence math (daily / every_12h / every_n_days / weekly), timezone
  validation, and the CHECK constraint tying decision_automation_enabled
  to a non-null timezone.
- publishing_slots uniqueness (one slot per channel+slot_key, one active
  slot per brief_id).
- The full decision cycle: disabled channel, queue-satisfied short-circuit,
  no-eligible-candidate, happy-path selection through to a filled slot,
  cross-publication learning invocation, market-freshness reuse vs.
  refresh vs. degraded-refresh, bounded + cached semantic-fit spend,
  concurrency lock (same-hour duplicate tick), restart recovery (resuming
  an in-flight reserved slot), multi-channel isolation, and the hard
  prohibition on any production/publishing side effect.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.control_plane import repository as cp_repo
from app.control_plane.models import ChannelDraft, WorkspaceDraft
from app.core.database import open_db
from app.intelligence.autonomy.decision_cycle import run_decision_cycle
from app.intelligence.autonomy.models import AutonomyPolicy, DecisionOutcome
from app.intelligence.autonomy.repository import (
    InvalidTimezoneError,
    compute_next_slot,
    fill_slot,
    get_autonomy_policy,
    list_active_slots,
    reserve_slot,
    upsert_autonomy_policy,
)
from app.intelligence.channel_bridge import (
    bootstrap_intelligence_channel,
    get_intelligence_channel_id,
)


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def db(tmp_path: Path):
    conn = open_db(tmp_path / "autonomy_test.db")
    conn.execute("PRAGMA foreign_keys = OFF")
    yield conn
    conn.close()


@pytest.fixture()
def workspace(db):
    ws = cp_repo.create_workspace(
        db,
        WorkspaceDraft(id=_uid(), name="Test WS", slug=f"ws-{_uid()[:8]}", actor="cli"),
    )
    db.commit()
    return ws


@pytest.fixture()
def channel(db, workspace):
    ch = cp_repo.create_channel(
        db,
        ChannelDraft(
            id=_uid(),
            workspace_id=workspace.id,
            name="Test Channel",
            slug="test-channel",
            actor="cli",
        ),
    )
    db.commit()
    bootstrap_intelligence_channel(db, ch.id, channel_name="Test Channel")
    db.commit()
    return ch


def _enable_policy(db, channel, workspace, **overrides):
    kwargs = dict(
        channel_id=channel.id,
        workspace_id=workspace.id,
        actor="test",
        decision_automation_enabled=True,
        timezone="UTC",
        cadence_type="daily",
        preferred_local_hour=9,
        queue_target=1,
    )
    kwargs.update(overrides)
    return upsert_autonomy_policy(db, **kwargs)


def _seed_bare_opportunity(
    conn, intel_channel_id: int, opp_id: int = 1, topic: str = "unique test topic"
) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        """INSERT INTO opportunities
           (id, channel_id, discovery_run_id, normalized_topic, raw_topic, title,
            current_lifecycle_state, created_at, updated_at)
           VALUES (?, ?, 0, ?, ?, 'Test', 'new', datetime('now'), datetime('now'))""",
        (opp_id, intel_channel_id, topic, topic),
    )
    conn.commit()


def _seed_opportunity_with_market_signal(
    conn,
    intel_channel_id: int,
    opp_id: int = 1,
    topic: str = "unique test topic",
) -> None:
    """A bare opportunity alone hard-blocks on market freshness ('no
    market_signal_snapshot') before ever reaching semantic fit — this
    builds the minimal real market_interpretation_runs -> market_topic_clusters
    -> market_cluster_signals chain so the opportunity genuinely reaches
    the semantic-fit stage as UNRESOLVED (no deterministic niche match,
    since these tests use no channel profile), exercising the real
    Phase 17G cache/bound behavior rather than a mocked classification."""
    conn.execute("PRAGMA foreign_keys = OFF")
    now = "2026-08-29T00:00:00"
    canon_id = opp_id * 1000
    conn.execute(
        "INSERT INTO market_canonical_clusters "
        "(id, canonical_label, normalized_label, semantic_fingerprint, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (canon_id, topic, topic, f"fp-{opp_id}", now, now),
    )
    run_id = opp_id * 1000
    conn.execute(
        "INSERT INTO market_interpretation_runs "
        "(id, evidence_cutoff, status, completed_at, input_hash) "
        "VALUES (?, ?, 'completed', ?, ?)",
        (run_id, now, now, f"hash-{opp_id}"),
    )
    cluster_id = opp_id * 1000
    conn.execute(
        "INSERT INTO market_topic_clusters "
        "(id, interpretation_run_id, cluster_label, normalized_label, "
        "canonical_cluster_id, input_hash) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (cluster_id, run_id, topic, topic, canon_id, f"cluster-hash-{opp_id}"),
    )
    signal_id = opp_id * 1000
    conn.execute(
        "INSERT INTO market_cluster_signals "
        "(id, cluster_id, interpretation_run_id, confidence, signal_maturity, input_hash) "
        "VALUES (?, ?, ?, 0.65, 'directional', ?)",
        (signal_id, cluster_id, run_id, f"signal-hash-{opp_id}"),
    )
    conn.execute(
        """INSERT INTO opportunities
           (id, channel_id, discovery_run_id, normalized_topic, raw_topic, title,
            current_lifecycle_state, canonical_cluster_id, market_signal_snapshot_id,
            created_at, updated_at)
           VALUES (?, ?, 0, ?, ?, 'Test', 'new', ?, ?, datetime('now'), datetime('now'))""",
        (opp_id, intel_channel_id, topic, topic, canon_id, signal_id),
    )
    conn.commit()


def _patch_eligible(monkeypatch, opp_ids: set[int] | None = None):
    """Monkeypatch assess_experiment_eligibility to GENERAL_ELIGIBLE for the
    given opportunity ids (or all, if None) — the full market-signal chain
    needed for a *real* eligible assessment is exhaustively covered by
    test_experiment_eligibility_14c.py; this file tests decision-cycle
    orchestration, not eligibility computation itself."""
    from app.intelligence.experiments import eligibility_service as elig_svc
    from app.intelligence.experiments.eligibility import (
        ExperimentEligibilityAssessment,
        ExperimentEligibilityClassification,
    )

    def _fake(conn, opp_id, ch_id, ai_provider=None, policy=None):
        cls = (
            ExperimentEligibilityClassification.GENERAL_ELIGIBLE
            if opp_ids is None or opp_id in opp_ids
            else ExperimentEligibilityClassification.UNRESOLVED
        )
        return ExperimentEligibilityAssessment(
            opportunity_id=opp_id,
            channel_id=ch_id,
            classification=cls,
            findings=[],
            policy_snapshot_json="{}",
            assessed_at="2026-01-01T00:00:00",
            signal_maturity="directional",
            signal_confidence=0.7,
        )

    monkeypatch.setattr(elig_svc, "assess_experiment_eligibility", _fake)


def _stub_market_refresh(monkeypatch, *, raises: bool = False):
    import app.intelligence.market.refresh_service as refresh_mod

    if raises:

        def _boom(*a, **kw):
            raise RuntimeError("simulated market refresh outage")

        monkeypatch.setattr(refresh_mod, "run_market_refresh_cycle", _boom)
    else:
        result = MagicMock(ok=True, errors=[])
        monkeypatch.setattr(refresh_mod, "run_market_refresh_cycle", lambda *a, **kw: result)
    return refresh_mod


# ---------------------------------------------------------------------------
# Cadence math
# ---------------------------------------------------------------------------


def test_cadence_daily_rolls_to_next_day_when_hour_passed():
    p = AutonomyPolicy(
        channel_id="c",
        workspace_id="w",
        cadence_type="daily",
        timezone="UTC",
        preferred_local_hour=9,
    )
    after = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)  # past 9am UTC
    utc_dt, _, slot_key = compute_next_slot(p, after_utc=after)
    assert slot_key == "2026-08-30"
    assert utc_dt.hour == 9


def test_cadence_daily_same_day_when_hour_not_yet_passed():
    p = AutonomyPolicy(
        channel_id="c",
        workspace_id="w",
        cadence_type="daily",
        timezone="UTC",
        preferred_local_hour=20,
    )
    after = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)
    _, _, slot_key = compute_next_slot(p, after_utc=after)
    assert slot_key == "2026-08-29"


def test_cadence_every_12h_finds_next_of_two_daily_anchors():
    p = AutonomyPolicy(
        channel_id="c",
        workspace_id="w",
        cadence_type="every_12h",
        timezone="UTC",
        preferred_local_hour=9,
    )
    after = datetime(2026, 8, 29, 10, 0, 0, tzinfo=UTC)  # just past 9am anchor
    utc_dt, _, slot_key = compute_next_slot(p, after_utc=after)
    assert utc_dt.hour == 21
    assert slot_key == "2026-08-29T21"


def test_cadence_every_n_days_respects_interval():
    p = AutonomyPolicy(
        channel_id="c",
        workspace_id="w",
        cadence_type="every_n_days",
        cadence_interval_days=3,
        timezone="UTC",
        preferred_local_hour=9,
    )
    after = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)
    _, _, slot_key = compute_next_slot(p, after_utc=after)
    assert slot_key == "2026-09-01"  # +3 days from the 29th


def test_cadence_weekly_steps_seven_days():
    p = AutonomyPolicy(
        channel_id="c",
        workspace_id="w",
        cadence_type="weekly",
        timezone="UTC",
        preferred_local_hour=9,
    )
    after = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)
    _, _, slot_key = compute_next_slot(p, after_utc=after)
    assert slot_key == "2026-09-05"


def test_cadence_timezone_conversion_is_correct():
    p = AutonomyPolicy(
        channel_id="c",
        workspace_id="w",
        cadence_type="daily",
        timezone="America/New_York",
        preferred_local_hour=9,
    )
    after = datetime(2026, 8, 29, 6, 0, 0, tzinfo=UTC)  # 2am ET
    utc_dt, local_iso, _ = compute_next_slot(p, after_utc=after)
    assert "09:00:00" in local_iso
    assert utc_dt.hour == 13  # 9am ET == 13:00 UTC in August (EDT, UTC-4)


def test_cadence_custom_cron_not_implemented():
    p = AutonomyPolicy(channel_id="c", workspace_id="w", cadence_type="custom_cron", timezone="UTC")
    with pytest.raises(NotImplementedError):
        compute_next_slot(p, after_utc=datetime.now(UTC))


def test_cadence_requires_timezone():
    p = AutonomyPolicy(channel_id="c", workspace_id="w", cadence_type="daily", timezone=None)
    with pytest.raises(ValueError):
        compute_next_slot(p, after_utc=datetime.now(UTC))


# ---------------------------------------------------------------------------
# Policy CRUD, timezone validation, CHECK constraint
# ---------------------------------------------------------------------------


def test_invalid_timezone_rejected(db, channel, workspace):
    with pytest.raises(InvalidTimezoneError):
        upsert_autonomy_policy(
            db,
            channel_id=channel.id,
            workspace_id=workspace.id,
            actor="test",
            decision_automation_enabled=True,
            timezone="Not/A/Real/Zone",
        )


def test_enabling_without_timezone_violates_check_constraint(db, channel, workspace):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        upsert_autonomy_policy(
            db,
            channel_id=channel.id,
            workspace_id=workspace.id,
            actor="test",
            decision_automation_enabled=True,
            timezone=None,
        )


def test_policy_upsert_is_a_partial_update(db, channel, workspace):
    _enable_policy(db, channel, workspace, queue_target=1)
    upsert_autonomy_policy(
        db, channel_id=channel.id, workspace_id=workspace.id, actor="test", queue_target=2
    )
    policy = get_autonomy_policy(db, channel.id)
    assert policy.queue_target == 2
    assert policy.decision_automation_enabled is True  # untouched field preserved
    assert policy.timezone == "UTC"


def test_clear_timezone_explicitly_nulls_it_out(db, channel, workspace):
    _enable_policy(db, channel, workspace)
    upsert_autonomy_policy(
        db,
        channel_id=channel.id,
        workspace_id=workspace.id,
        actor="test",
        decision_automation_enabled=False,
        clear_timezone=True,
    )
    policy = get_autonomy_policy(db, channel.id)
    assert policy.timezone is None
    assert policy.decision_automation_enabled is False


def test_clear_timezone_while_still_enabled_violates_check_constraint(db, channel, workspace):
    import sqlite3

    _enable_policy(db, channel, workspace)
    with pytest.raises(sqlite3.IntegrityError):
        upsert_autonomy_policy(
            db,
            channel_id=channel.id,
            workspace_id=workspace.id,
            actor="test",
            clear_timezone=True,
        )


# ---------------------------------------------------------------------------
# Slot uniqueness
# ---------------------------------------------------------------------------


def test_slot_reservation_is_idempotent_per_slot_key(db, channel, workspace):
    utc_dt = datetime(2026, 8, 30, 9, 0, 0, tzinfo=UTC)
    s1 = reserve_slot(
        db,
        channel_id=channel.id,
        workspace_id=workspace.id,
        slot_key="2026-08-30",
        scheduled_for_local="2026-08-30T09:00:00+00:00",
        timezone="UTC",
        scheduled_for_utc=utc_dt,
    )
    s2 = reserve_slot(
        db,
        channel_id=channel.id,
        workspace_id=workspace.id,
        slot_key="2026-08-30",
        scheduled_for_local="2026-08-30T09:00:00+00:00",
        timezone="UTC",
        scheduled_for_utc=utc_dt,
    )
    assert s1.id == s2.id
    rows = db.execute(
        "SELECT COUNT(*) AS n FROM publishing_slots WHERE channel_id=?", (channel.id,)
    ).fetchone()
    assert rows["n"] == 1


def test_two_channels_can_reserve_the_same_slot_key(db, workspace, channel):
    ch2 = cp_repo.create_channel(
        db, ChannelDraft(id=_uid(), workspace_id=workspace.id, name="CH2", slug="ch2", actor="cli")
    )
    db.commit()
    utc_dt = datetime(2026, 8, 30, 9, 0, 0, tzinfo=UTC)
    s1 = reserve_slot(
        db,
        channel_id=channel.id,
        workspace_id=workspace.id,
        slot_key="2026-08-30",
        scheduled_for_local="x",
        timezone="UTC",
        scheduled_for_utc=utc_dt,
    )
    s2 = reserve_slot(
        db,
        channel_id=ch2.id,
        workspace_id=workspace.id,
        slot_key="2026-08-30",
        scheduled_for_local="x",
        timezone="UTC",
        scheduled_for_utc=utc_dt,
    )
    assert s1.id != s2.id  # UNIQUE is (channel_id, slot_key), not slot_key alone


def test_one_brief_cannot_fill_two_active_slots(db, channel, workspace):
    import sqlite3

    utc1 = datetime(2026, 8, 30, 9, 0, 0, tzinfo=UTC)
    utc2 = datetime(2026, 8, 31, 9, 0, 0, tzinfo=UTC)
    s1 = reserve_slot(
        db,
        channel_id=channel.id,
        workspace_id=workspace.id,
        slot_key="2026-08-30",
        scheduled_for_local="x",
        timezone="UTC",
        scheduled_for_utc=utc1,
    )
    s2 = reserve_slot(
        db,
        channel_id=channel.id,
        workspace_id=workspace.id,
        slot_key="2026-08-31",
        scheduled_for_local="x",
        timezone="UTC",
        scheduled_for_utc=utc2,
    )

    db.execute(
        "INSERT INTO experiment_strategy_briefs "
        "(id, opportunity_id, channel_id, planning_run_id, selection_decision_id, "
        "brief_planning_intent, "
        "experiment_type, brief_hash, status, created_at) "
        "VALUES (?, 1, ?, 'run-x', 1, 'market_exploration', 'exploration', ?, "
        "'pending_approval', datetime('now'))",
        (_uid(), channel.id, _uid()),
    )
    brief_id = db.execute("SELECT id FROM experiment_strategy_briefs LIMIT 1").fetchone()["id"]
    db.commit()

    fill_slot(db, s1.id, brief_id=brief_id, selection_decision_id=1, opportunity_id=1)
    with pytest.raises(sqlite3.IntegrityError):
        fill_slot(db, s2.id, brief_id=brief_id, selection_decision_id=1, opportunity_id=1)


def test_filling_an_already_filled_slot_raises(db, channel, workspace):
    utc_dt = datetime(2026, 8, 30, 9, 0, 0, tzinfo=UTC)
    s1 = reserve_slot(
        db,
        channel_id=channel.id,
        workspace_id=workspace.id,
        slot_key="2026-08-30",
        scheduled_for_local="x",
        timezone="UTC",
        scheduled_for_utc=utc_dt,
    )
    fill_slot(db, s1.id, brief_id=None, selection_decision_id=None, opportunity_id=None)
    with pytest.raises(ValueError):
        fill_slot(db, s1.id, brief_id=None, selection_decision_id=None, opportunity_id=None)


# ---------------------------------------------------------------------------
# Full decision cycle
# ---------------------------------------------------------------------------


def test_disabled_channel_returns_disabled(db, channel, workspace):
    result = run_decision_cycle(db, cp_channel_id=channel.id, workspace_id=workspace.id)
    assert result.outcome == DecisionOutcome.DISABLED
    assert list_active_slots(db, channel.id) == []


def test_no_eligible_candidate_leaves_slot_reserved_not_filled(db, channel, workspace):
    _enable_policy(db, channel, workspace)
    result = run_decision_cycle(db, cp_channel_id=channel.id, workspace_id=workspace.id)
    assert result.outcome == DecisionOutcome.NO_ELIGIBLE_CANDIDATE
    slots = list_active_slots(db, channel.id)
    assert len(slots) == 1
    assert slots[0].state == "reserved"
    assert slots[0].brief_id is None


def test_happy_path_selects_and_fills_slot(db, channel, workspace, monkeypatch):
    _enable_policy(db, channel, workspace)
    intel_id = get_intelligence_channel_id(db, channel.id)
    _seed_bare_opportunity(db, intel_id, opp_id=1)
    _patch_eligible(monkeypatch)
    _stub_market_refresh(monkeypatch)

    result = run_decision_cycle(db, cp_channel_id=channel.id, workspace_id=workspace.id)
    assert result.outcome == DecisionOutcome.SELECTED
    assert result.opportunity_id == 1
    assert result.brief_id is not None
    assert result.selection_decision_id is not None

    slots = list_active_slots(db, channel.id)
    assert len(slots) == 1
    assert slots[0].state == "filled"
    assert slots[0].brief_id == result.brief_id


def test_queue_already_satisfied_skips_all_expensive_work(db, channel, workspace, monkeypatch):
    _enable_policy(db, channel, workspace, queue_target=1)
    intel_id = get_intelligence_channel_id(db, channel.id)
    _seed_bare_opportunity(db, intel_id, opp_id=1)
    _patch_eligible(monkeypatch)
    _stub_market_refresh(monkeypatch)

    first = run_decision_cycle(db, cp_channel_id=channel.id, workspace_id=workspace.id)
    assert first.outcome == DecisionOutcome.SELECTED

    # Force a fresh hour bucket so the concurrency lock alone doesn't explain this.
    import app.intelligence.autonomy.decision_cycle as dc_mod

    monkeypatch.setattr(dc_mod, "_now_utc", lambda: datetime(2026, 1, 1, tzinfo=UTC))
    monkeypatch.setattr(dc_mod, "_now_iso", lambda: "2026-01-01T00:00:00")

    def _boom(*a, **kw):
        raise AssertionError(
            "must not run cross-publication learning when queue is already satisfied"
        )

    import app.learning.cross_publication as cpl_mod

    monkeypatch.setattr(cpl_mod, "run_cross_publication_learning", _boom)

    second = run_decision_cycle(db, cp_channel_id=channel.id, workspace_id=workspace.id)
    assert second.outcome == DecisionOutcome.QUEUE_ALREADY_SATISFIED


def test_concurrent_tick_within_same_hour_is_blocked(db, channel, workspace, monkeypatch):
    _enable_policy(db, channel, workspace)
    _stub_market_refresh(monkeypatch)

    first = run_decision_cycle(db, cp_channel_id=channel.id, workspace_id=workspace.id)
    assert first.already_running is False

    second = run_decision_cycle(db, cp_channel_id=channel.id, workspace_id=workspace.id)
    assert second.already_running is True
    assert second.outcome == DecisionOutcome.QUEUE_ALREADY_SATISFIED
    # Only one slot was ever reserved — no duplicate planning/reservation happened.
    assert len(list_active_slots(db, channel.id)) == 1


def test_restart_recovery_resumes_in_flight_slot_not_a_new_one(db, channel, workspace, monkeypatch):
    """Simulates a crash between reserve_slot and fill_slot: the slot is left
    'reserved' with no brief. The next cycle (a fresh hour, simulating a
    process restart later) must resume that SAME slot, not reserve a new one."""
    _enable_policy(db, channel, workspace)
    _stub_market_refresh(monkeypatch)

    first = run_decision_cycle(db, cp_channel_id=channel.id, workspace_id=workspace.id)
    assert first.outcome == DecisionOutcome.NO_ELIGIBLE_CANDIDATE
    slot_after_first = list_active_slots(db, channel.id)[0]

    import app.intelligence.autonomy.decision_cycle as dc_mod

    monkeypatch.setattr(dc_mod, "_now_utc", lambda: datetime(2026, 1, 1, tzinfo=UTC))
    monkeypatch.setattr(dc_mod, "_now_iso", lambda: "2026-01-01T00:00:00")

    intel_id = get_intelligence_channel_id(db, channel.id)
    _seed_bare_opportunity(db, intel_id, opp_id=1)
    _patch_eligible(monkeypatch)

    second = run_decision_cycle(db, cp_channel_id=channel.id, workspace_id=workspace.id)
    assert second.outcome == DecisionOutcome.SELECTED
    assert second.slot_id == slot_after_first.id  # same slot resumed, not a new one

    slots = list_active_slots(db, channel.id)
    assert len(slots) == 1
    assert slots[0].state == "filled"


def test_multi_channel_isolation(db, workspace, channel, monkeypatch):
    ch2 = cp_repo.create_channel(
        db, ChannelDraft(id=_uid(), workspace_id=workspace.id, name="CH2", slug="ch2", actor="cli")
    )
    db.commit()
    bootstrap_intelligence_channel(db, ch2.id, channel_name="CH2")
    db.commit()

    _enable_policy(db, channel, workspace)
    _stub_market_refresh(monkeypatch)
    # ch2 has no policy at all.

    r1 = run_decision_cycle(db, cp_channel_id=channel.id, workspace_id=workspace.id)
    r2 = run_decision_cycle(db, cp_channel_id=ch2.id, workspace_id=workspace.id)

    assert r1.outcome != DecisionOutcome.DISABLED
    assert r2.outcome == DecisionOutcome.DISABLED
    assert len(list_active_slots(db, channel.id)) == 1
    assert len(list_active_slots(db, ch2.id)) == 0


def test_cross_publication_learning_is_invoked(db, channel, workspace, monkeypatch):
    _enable_policy(db, channel, workspace)
    _stub_market_refresh(monkeypatch)

    calls = []
    import app.learning.cross_publication as cpl_mod

    real = cpl_mod.run_cross_publication_learning

    def _spy(conn, *, channel_id, workspace_id=None):
        calls.append(channel_id)
        return real(conn, channel_id=channel_id, workspace_id=workspace_id)

    monkeypatch.setattr(cpl_mod, "run_cross_publication_learning", _spy)

    result = run_decision_cycle(db, cp_channel_id=channel.id, workspace_id=workspace.id)
    assert calls == [channel.id]
    assert result.cross_pub_learning_ran is True


def test_insufficient_learning_does_not_block_bootstrap_selection(
    db, channel, workspace, monkeypatch
):
    """0 baselines (insufficient learning) must not prevent a bootstrap-mode
    selection — bootstrap strategy is market-heavy specifically because
    channel evidence isn't mature yet."""
    _enable_policy(db, channel, workspace)
    intel_id = get_intelligence_channel_id(db, channel.id)
    _seed_bare_opportunity(db, intel_id, opp_id=1)
    _patch_eligible(monkeypatch)
    _stub_market_refresh(monkeypatch)

    result = run_decision_cycle(db, cp_channel_id=channel.id, workspace_id=workspace.id)
    assert result.cross_pub_learning_publication_count == 0  # genuinely insufficient
    assert result.outcome == DecisionOutcome.SELECTED  # proceeded anyway, bootstrap-weighted


def test_fresh_market_intelligence_is_reused_not_refreshed(db, channel, workspace, monkeypatch):
    _enable_policy(db, channel, workspace)
    from app.application.scheduler import create_schedule

    create_schedule(
        db,
        workspace_id=workspace.id,
        name="mr",
        operation_type="market_refresh",
        schedule_type="interval",
        schedule_config={"interval_seconds": 21600},
        actor="test",
        channel_id=channel.id,
    )
    db.execute(
        "UPDATE app_schedule_definitions SET last_run_at=? "
        "WHERE operation_type='market_refresh' AND channel_id=?",
        (datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"), channel.id),
    )
    db.commit()

    def _boom(*a, **kw):
        raise AssertionError("must not call run_market_refresh_cycle when intelligence is fresh")

    import app.intelligence.market.refresh_service as refresh_mod

    monkeypatch.setattr(refresh_mod, "run_market_refresh_cycle", _boom)

    result = run_decision_cycle(db, cp_channel_id=channel.id, workspace_id=workspace.id)
    assert result.market_refresh_status == "reused"


def test_stale_market_intelligence_triggers_refresh(db, channel, workspace, monkeypatch):
    _enable_policy(db, channel, workspace, market_refresh_max_age_hours=1)
    from app.application.scheduler import create_schedule

    create_schedule(
        db,
        workspace_id=workspace.id,
        name="mr",
        operation_type="market_refresh",
        schedule_type="interval",
        schedule_config={"interval_seconds": 21600},
        actor="test",
        channel_id=channel.id,
    )
    db.execute(
        "UPDATE app_schedule_definitions SET last_run_at='2020-01-01T00:00:00' "
        "WHERE operation_type='market_refresh' AND channel_id=?",
        (channel.id,),
    )
    db.commit()

    _stub_market_refresh(monkeypatch)
    result = run_decision_cycle(db, cp_channel_id=channel.id, workspace_id=workspace.id)
    assert result.market_refresh_status == "executed"


def test_degraded_market_refresh_does_not_kill_the_cycle(db, channel, workspace, monkeypatch):
    _enable_policy(db, channel, workspace)
    _stub_market_refresh(monkeypatch, raises=True)

    result = run_decision_cycle(db, cp_channel_id=channel.id, workspace_id=workspace.id)
    assert result.market_refresh_status == "failed"
    assert result.market_refresh_error is not None
    # The cycle still completed (didn't propagate/crash) and produced a real outcome.
    assert result.outcome in (
        DecisionOutcome.NO_ELIGIBLE_CANDIDATE,
        DecisionOutcome.DEGRADED_BUT_PROCEEDED,
    )


def test_bounded_semantic_fit_calls_per_cycle(db, channel, workspace, monkeypatch):
    _enable_policy(db, channel, workspace, semantic_fit_max_evaluations_per_run=2)
    intel_id = get_intelligence_channel_id(db, channel.id)
    for i in range(1, 6):
        _seed_opportunity_with_market_signal(db, intel_id, opp_id=i, topic=f"unique topic {i}")
    _stub_market_refresh(monkeypatch)

    call_count = {"n": 0}

    class _CountingProvider:
        name = "claude"

        def complete(self, request):
            call_count["n"] += 1
            import json

            from app.ai.provider import AIResponse

            return AIResponse(
                raw_text=json.dumps({"score": 0.9, "fit_label": "x", "rationale": "y"}),
                provider_name="claude",
                model=request.model,
                input_tokens=1,
                output_tokens=1,
                duration_ms=1,
                retry_count=0,
                parsed=None,
            )

    result = run_decision_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        ai_provider=_CountingProvider(),
    )
    assert 1 <= call_count["n"] <= 2, (
        "must genuinely call the provider, but never more than the bound"
    )
    assert 1 <= result.semantic_fit_evaluated <= 2
    assert result.semantic_fit_considered == 5


def test_cached_semantic_fit_is_reused_not_recalled(db, channel, workspace, monkeypatch):
    _enable_policy(db, channel, workspace, semantic_fit_max_evaluations_per_run=5)
    intel_id = get_intelligence_channel_id(db, channel.id)
    _seed_opportunity_with_market_signal(db, intel_id, opp_id=1)
    _stub_market_refresh(monkeypatch)

    call_count = {"n": 0}

    class _CountingProvider:
        name = "claude"

        def complete(self, request):
            call_count["n"] += 1
            import json

            from app.ai.provider import AIResponse

            return AIResponse(
                raw_text=json.dumps({"score": 0.9, "fit_label": "x", "rationale": "y"}),
                provider_name="claude",
                model=request.model,
                input_tokens=1,
                output_tokens=1,
                duration_ms=1,
                retry_count=0,
                parsed=None,
            )

    provider = _CountingProvider()
    run_decision_cycle(
        db, cp_channel_id=channel.id, workspace_id=workspace.id, ai_provider=provider
    )
    first_calls = call_count["n"]
    assert first_calls >= 1

    # Force a new hour bucket so the second call is a genuinely independent cycle.
    import app.intelligence.autonomy.decision_cycle as dc_mod

    monkeypatch.setattr(dc_mod, "_now_utc", lambda: datetime(2026, 1, 1, tzinfo=UTC))
    monkeypatch.setattr(dc_mod, "_now_iso", lambda: "2026-01-01T00:00:00")

    # Whatever the first cycle's outcome (SELECTED -> queue satisfied and
    # skipped entirely, or NO_ELIGIBLE_CANDIDATE -> retried but resolved
    # from cache) — the result is persisted, so a second cycle must never
    # spend a second real LLM call for the same opportunity/profile.
    run_decision_cycle(
        db, cp_channel_id=channel.id, workspace_id=workspace.id, ai_provider=provider
    )
    assert call_count["n"] == first_calls
    cache_rows = db.execute(
        "SELECT COUNT(*) AS n FROM opportunity_semantic_fit_results"
    ).fetchone()["n"]
    assert cache_rows >= 1


def test_event_and_operation_audit_emitted(db, channel, workspace, monkeypatch):
    _enable_policy(db, channel, workspace)
    _stub_market_refresh(monkeypatch)

    result = run_decision_cycle(db, cp_channel_id=channel.id, workspace_id=workspace.id)
    assert result.operation_id is not None

    op = db.execute(
        "SELECT * FROM cp_operation_executions WHERE id = ?", (result.operation_id,)
    ).fetchone()
    assert op is not None
    assert op["operation_type"] == "autonomy_decision_cycle"
    assert op["status"] == "completed"
    assert op["idempotency_key"] == result.idempotency_key


def test_publishing_authorization_independence(db, channel, workspace, monkeypatch):
    """Decision automation must function identically whether or not the
    (always-false-in-this-phase) publishing gates are set — the cycle
    never reads them at all."""

    monkeypatch.delenv("ACE_PUBLISHING_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("ACE_RELEASE_PUBLIC_ENABLED", raising=False)
    _enable_policy(db, channel, workspace)
    _stub_market_refresh(monkeypatch)

    result = run_decision_cycle(db, cp_channel_id=channel.id, workspace_id=workspace.id)
    assert result.outcome != DecisionOutcome.FAILED


def test_no_production_or_publishing_side_effects(db, channel, workspace, monkeypatch):
    """Absolute prohibition (section 19): a decision cycle — even a full
    happy-path SELECTED one — must never create a publications row, a
    publishing_jobs row, or an `experiments` row."""
    _enable_policy(db, channel, workspace)
    intel_id = get_intelligence_channel_id(db, channel.id)
    _seed_bare_opportunity(db, intel_id, opp_id=1)
    _patch_eligible(monkeypatch)
    _stub_market_refresh(monkeypatch)

    result = run_decision_cycle(db, cp_channel_id=channel.id, workspace_id=workspace.id)
    assert result.outcome == DecisionOutcome.SELECTED

    assert db.execute("SELECT COUNT(*) AS n FROM publications").fetchone()["n"] == 0
    assert db.execute("SELECT COUNT(*) AS n FROM publishing_jobs").fetchone()["n"] == 0
    assert db.execute("SELECT COUNT(*) AS n FROM experiments").fetchone()["n"] == 0
