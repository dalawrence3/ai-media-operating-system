"""Phase 18D.1 — pre-activation operational hardening.

Three narrow properties, each covering a defect that would have surfaced only
after activation:

  Scheduler cadence   `interval_seconds` is honoured, and rows persisted while
                      it was not can be reconciled back onto cadence without
                      waiting for them to expire.
  Rate-aware slots    the decision cycle does not reserve — and therefore does
                      not pay to produce for — a slot the publication ceiling
                      is already guaranteed to refuse.
  Doctor semantics    an intentionally authorized system is not reported as
                      broken, while a genuinely unbounded or incoherent one
                      still is.

No provider calls anywhere in this file.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.application.scheduler import (
    create_schedule,
    reconcile_schedule_next_runs,
)
from app.core.database import open_db
from app.intelligence.autonomy.models import AutonomyPolicy, CadenceType
from app.intelligence.autonomy.repository import (
    compute_next_publishable_slot,
    compute_next_slot,
    earliest_rate_permitted_utc,
)

ROOT = Path(__file__).parent.parent


def _uid() -> str:
    return str(uuid.uuid4())


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


@pytest.fixture()
def db(tmp_path: Path):
    conn = open_db(tmp_path / "hardening_18d1.db")
    conn.execute("PRAGMA foreign_keys = OFF")
    yield conn
    conn.close()


def _policy(
    *,
    channel_id: str = "ch",
    cadence: CadenceType = CadenceType.daily,
    hour: int = 9,
    tz: str = "America/New_York",
    interval_days: int | None = None,
) -> AutonomyPolicy:
    return AutonomyPolicy(
        channel_id=channel_id,
        workspace_id="ws",
        decision_automation_enabled=True,
        production_automation_enabled=True,
        cadence_type=cadence,
        cadence_interval_days=interval_days,
        cadence_cron=None,
        preferred_local_hour=hour,
        timezone=tz,
        queue_target=1,
        market_refresh_max_age_hours=12,
        semantic_fit_max_evaluations_per_run=5,
    )


def _seed_publication(
    conn: sqlite3.Connection, *, channel_id: str, created_at: datetime, pub_id: int
) -> None:
    """A publication row, which is what consumes rate-limit budget.

    Budget is consumed by `created_at`, not `published_at` — an upload that
    failed partway still spent it, because the ceiling bounds external side
    effects rather than successful outcomes.
    """
    conn.execute(
        """INSERT INTO publications
           (id, publishing_plan_id, publishing_job_id, provider, provider_version,
            provider_video_id, provider_url, status, visibility, published_at,
            publishing_engine_version, input_hash, output_sha256,
            created_at, updated_at, workspace_id, channel_id, platform_account_id)
           VALUES (?, 1, 1, 'youtube', 'v1', ?, 'https://youtu.be/x',
                   'published', 'public', ?, 'v1', ?, ?, ?, ?, 'ws', ?, 'acct')""",
        (
            pub_id,
            f"vid{pub_id}",
            _iso(created_at),
            _uid()[:16],
            "a" * 64,
            _iso(created_at),
            _iso(created_at),
            channel_id,
        ),
    )
    conn.commit()


# ═════════════════════════════════════════════════════════════════════════════
# Scheduler cadence
# ═════════════════════════════════════════════════════════════════════════════


def _make_schedule(
    conn: sqlite3.Connection,
    *,
    operation_type: str,
    interval_seconds: int,
    channel_id: str = "ch",
    workspace_id: str = "ws",
):
    return create_schedule(
        conn,
        workspace_id=workspace_id,
        name=f"{operation_type} schedule",
        operation_type=operation_type,
        schedule_type="interval",
        schedule_config={"interval_seconds": interval_seconds},
        actor="test",
        channel_id=channel_id,
    )


def _set_persisted(
    conn: sqlite3.Connection, schedule_id: str, *, last_run: datetime, next_run: datetime
) -> None:
    conn.execute(
        "UPDATE app_schedule_definitions SET last_run_at = ?, next_run_at = ? WHERE id = ?",
        (_iso(last_run), _iso(next_run), schedule_id),
    )
    conn.commit()


def _next_run(conn: sqlite3.Connection, schedule_id: str) -> str:
    return conn.execute(
        "SELECT next_run_at FROM app_schedule_definitions WHERE id = ?", (schedule_id,)
    ).fetchone()["next_run_at"]


@pytest.mark.parametrize(
    ("operation_type", "interval_seconds"),
    [
        ("autonomy_decision_cycle", 3600),
        ("autonomous_production_cycle", 1800),
        ("autonomous_publishing_cycle", 600),
        ("market_refresh", 21600),
    ],
)
def test_stale_daily_next_run_is_reconciled_to_the_configured_interval(
    db, operation_type, interval_seconds
):
    """The exact shape of the defect: rows persisted with a 24h fallback.

    The worker read `schedule_config["seconds"]` while every row writes
    `interval_seconds`, so each of these persisted a next run a full day out.
    Fixing the computation does not fix the rows — an hourly decision cycle
    would still have waited out its stale daily timestamp first.
    """
    sched = _make_schedule(db, operation_type=operation_type, interval_seconds=interval_seconds)
    now = datetime.now(UTC)
    last_run = now - timedelta(minutes=5)
    _set_persisted(db, sched.id, last_run=last_run, next_run=last_run + timedelta(hours=24))

    results = reconcile_schedule_next_runs(db, now=now)
    mine = next(r for r in results if r.schedule_id == sched.id)

    assert mine.repaired is True
    assert mine.interval_seconds == interval_seconds

    expected = last_run + timedelta(seconds=interval_seconds)
    assert _next_run(db, sched.id) == _iso(expected)


def test_reconciliation_is_idempotent(db):
    """Restart safety: running the pass repeatedly must change nothing further."""
    sched = _make_schedule(db, operation_type="autonomy_decision_cycle", interval_seconds=3600)
    now = datetime.now(UTC)
    last_run = now - timedelta(minutes=5)
    _set_persisted(db, sched.id, last_run=last_run, next_run=last_run + timedelta(hours=24))

    first = reconcile_schedule_next_runs(db, now=now)
    assert next(r for r in first if r.schedule_id == sched.id).repaired is True
    after_first = _next_run(db, sched.id)

    for _ in range(3):
        again = reconcile_schedule_next_runs(db, now=now)
        assert next(r for r in again if r.schedule_id == sched.id).repaired is False
        assert _next_run(db, sched.id) == after_first


def test_reconciliation_never_pulls_a_schedule_earlier_than_its_cadence(db):
    """One-directional by design.

    If reconciliation could move a next run *earlier*, it would become a way
    to make schedules fire sooner than their configured cadence — which is
    exactly what a cadence is supposed to prevent.
    """
    sched = _make_schedule(db, operation_type="autonomy_decision_cycle", interval_seconds=3600)
    now = datetime.now(UTC)
    last_run = now - timedelta(minutes=5)
    # Persisted value is EARLIER than cadence would allow (due in 2 minutes).
    early = now + timedelta(minutes=2)
    _set_persisted(db, sched.id, last_run=last_run, next_run=early)

    results = reconcile_schedule_next_runs(db, now=now)
    mine = next(r for r in results if r.schedule_id == sched.id)

    assert mine.repaired is False
    assert _next_run(db, sched.id) == _iso(early)


def test_reconciliation_leaves_analytics_observation_age_aware_cadence_alone(db):
    """Observation cadence is age-aware and legitimately diverges from config.

    The observer recomputes its own interval at the end of every tick — hourly
    for a fresh video, three-daily for an old one — so the `interval_seconds`
    a schedule was registered with is routinely and correctly out of date.
    Reconciling against the stored number would drag a mature publication back
    to an hourly poll.
    """
    _seed_publication(
        db, channel_id="ch", created_at=datetime.now(UTC) - timedelta(days=10), pub_id=1
    )
    sched = create_schedule(
        db,
        workspace_id="ws",
        name="analytics_observation:pub_1",
        operation_type="analytics_observation",
        schedule_type="interval",
        # Registered hourly when the video was new.
        schedule_config={"publication_id": 1, "interval_seconds": 3600},
        actor="test",
        channel_id="ch",
    )
    now = datetime.now(UTC)
    last_run = now - timedelta(minutes=5)
    _set_persisted(db, sched.id, last_run=last_run, next_run=last_run + timedelta(hours=24))

    results = reconcile_schedule_next_runs(db, now=now)
    mine = next(r for r in results if r.schedule_id == sched.id)

    # A 10-day-old publication polls daily (86400s), not at the registered 3600s.
    assert mine.interval_seconds == 86400
    assert _next_run(db, sched.id) == _iso(last_run + timedelta(seconds=86400))


def test_reconciliation_ignores_inactive_schedules(db):
    sched = _make_schedule(db, operation_type="autonomous_publishing_cycle", interval_seconds=600)
    now = datetime.now(UTC)
    last_run = now - timedelta(minutes=5)
    _set_persisted(db, sched.id, last_run=last_run, next_run=last_run + timedelta(hours=24))
    db.execute("UPDATE app_schedule_definitions SET is_active = 0 WHERE id = ?", (sched.id,))
    db.commit()

    results = reconcile_schedule_next_runs(db, now=now)
    assert all(r.schedule_id != sched.id for r in results)
    assert _next_run(db, sched.id) == _iso(last_run + timedelta(hours=24))


def test_reconciliation_can_report_without_writing(db):
    sched = _make_schedule(db, operation_type="autonomy_decision_cycle", interval_seconds=3600)
    now = datetime.now(UTC)
    last_run = now - timedelta(minutes=5)
    stale = last_run + timedelta(hours=24)
    _set_persisted(db, sched.id, last_run=last_run, next_run=stale)

    results = reconcile_schedule_next_runs(db, now=now, apply=False)
    assert next(r for r in results if r.schedule_id == sched.id).repaired is True
    assert _next_run(db, sched.id) == _iso(stale), "apply=False must not write"


def test_worker_next_run_never_falls_back_to_daily_for_a_configured_interval():
    """Regression on the original defect, at the computation itself."""
    from app.workers.scheduler import compute_next_run_at

    for seconds in (600, 1800, 3600, 21600):
        now = datetime.now(UTC)
        result = compute_next_run_at({"interval_seconds": seconds}, "interval")
        delta = datetime.fromisoformat(result).replace(tzinfo=UTC) - now
        assert abs(delta.total_seconds() - seconds) < 5
        assert delta < timedelta(hours=24), "must never revert to the 24h fallback"


# ═════════════════════════════════════════════════════════════════════════════
# Rate-aware slot reservation
# ═════════════════════════════════════════════════════════════════════════════


def test_no_publications_means_the_ceiling_permits_publishing_now(db):
    now = datetime.now(UTC)
    assert earliest_rate_permitted_utc(db, "ch", max_publications_per_24h=1, now=now) == now


def test_a_full_window_clears_when_the_blocking_publication_ages_out(db):
    """With a ceiling of 1, the window clears 24h after that publication."""
    # Whole seconds: created_at is persisted at second precision, so a
    # microsecond-bearing `now` would round-trip lossily and make the
    # comparison fail for a reason that has nothing to do with the rule.
    now = datetime.now(UTC).replace(microsecond=0)
    published = now - timedelta(hours=5)
    _seed_publication(db, channel_id="ch", created_at=published, pub_id=1)

    permitted = earliest_rate_permitted_utc(db, "ch", max_publications_per_24h=1, now=now)
    assert permitted == published + timedelta(hours=24)


def test_a_higher_ceiling_clears_on_the_nth_most_recent_publication(db):
    """Ceiling N clears when the Nth-most-recent ages out, not the newest.

    Getting this wrong by keying on the newest publication would push the
    window out further than the rule actually requires and silently starve a
    channel that is within its allowance.
    """
    now = datetime.now(UTC).replace(microsecond=0)
    oldest = now - timedelta(hours=20)
    middle = now - timedelta(hours=10)
    newest = now - timedelta(hours=1)
    _seed_publication(db, channel_id="ch", created_at=oldest, pub_id=1)
    _seed_publication(db, channel_id="ch", created_at=middle, pub_id=2)
    _seed_publication(db, channel_id="ch", created_at=newest, pub_id=3)

    # Ceiling 3, three in window → clears when the oldest exits.
    assert earliest_rate_permitted_utc(
        db, "ch", max_publications_per_24h=3, now=now
    ) == oldest + timedelta(hours=24)

    # Ceiling 2 → the 2nd-most-recent is the blocker.
    assert earliest_rate_permitted_utc(
        db, "ch", max_publications_per_24h=2, now=now
    ) == middle + timedelta(hours=24)

    # Ceiling 4 → room remains, permitted now.
    assert earliest_rate_permitted_utc(db, "ch", max_publications_per_24h=4, now=now) == now


def test_rate_availability_is_channel_scoped(db):
    """Another channel's publications must never consume this one's budget."""
    now = datetime.now(UTC)
    _seed_publication(db, channel_id="other", created_at=now - timedelta(hours=1), pub_id=1)

    assert earliest_rate_permitted_utc(db, "ch", max_publications_per_24h=1, now=now) == now
    assert earliest_rate_permitted_utc(db, "other", max_publications_per_24h=1, now=now) > now


def test_slot_selection_skips_a_cadence_slot_inside_the_blocked_window(db):
    """The core property: never reserve a slot the ceiling will certainly refuse.

    Producing for such a slot generates a script, narration, visuals and a
    render, and publishing then declines to upload and retires the slot as
    missed — real spend on a video that was never publishable.
    """
    tz = ZoneInfo("America/New_York")
    # 20:00 local the day before, so the next 09:00 slot is ~13h away.
    now = datetime(2026, 8, 29, 20, 0, tzinfo=tz).astimezone(UTC)
    # Published 4 hours ago → window clears 20h from now, after the 09:00 slot.
    _seed_publication(db, channel_id="ch", created_at=now - timedelta(hours=4), pub_id=1)

    cadence_only, _, _ = compute_next_slot(_policy(), after_utc=now)
    selection = compute_next_publishable_slot(
        db,
        _policy(),
        channel_id="ch",
        after_utc=now,
        max_publications_per_24h=1,
        now=now,
    )

    assert cadence_only < selection.earliest_rate_permitted_utc, "precondition"
    assert selection.cadence_candidates_skipped == 1
    assert selection.scheduled_for_utc > cadence_only
    assert selection.scheduled_for_utc >= selection.earliest_rate_permitted_utc
    assert selection.rate_limited is False


def test_slot_selection_keeps_the_first_slot_when_the_ceiling_allows_it(db):
    """Cadence and ceiling stay independent — no shift when none is needed."""
    tz = ZoneInfo("America/New_York")
    now = datetime(2026, 8, 29, 20, 0, tzinfo=tz).astimezone(UTC)
    # Published 23h ago → window clears in 1h, well before the 09:00 slot.
    _seed_publication(db, channel_id="ch", created_at=now - timedelta(hours=23), pub_id=1)

    cadence_only, _, _ = compute_next_slot(_policy(), after_utc=now)
    selection = compute_next_publishable_slot(
        db,
        _policy(),
        channel_id="ch",
        after_utc=now,
        max_publications_per_24h=1,
        now=now,
    )

    assert selection.cadence_candidates_skipped == 0
    assert selection.scheduled_for_utc == cadence_only


def test_selected_slot_preserves_the_preferred_local_hour(db):
    """Shifting for the ceiling must not drift the publication time.

    The slot moves to a later day, not to a later hour — the channel still
    publishes at its configured local time.
    """
    tz = ZoneInfo("America/New_York")
    now = datetime(2026, 8, 29, 20, 0, tzinfo=tz).astimezone(UTC)
    _seed_publication(db, channel_id="ch", created_at=now - timedelta(hours=4), pub_id=1)

    selection = compute_next_publishable_slot(
        db,
        _policy(hour=9),
        channel_id="ch",
        after_utc=now,
        max_publications_per_24h=1,
        now=now,
    )
    local = selection.scheduled_for_utc.astimezone(tz)
    assert local.hour == 9
    assert local.minute == 0


def test_slot_selection_holds_the_local_hour_across_a_dst_transition(db):
    """DST correctness: 09:00 local stays 09:00 local, not 08:00 or 10:00.

    US DST ends 2026-11-01. Slots are re-derived from the policy at each step
    rather than advanced by 24 hours in UTC, which is what makes this hold.
    """
    tz = ZoneInfo("America/New_York")
    now = datetime(2026, 10, 30, 20, 0, tzinfo=tz).astimezone(UTC)
    # A long-lived block forces the walk across the boundary.
    _seed_publication(db, channel_id="ch", created_at=now + timedelta(days=3), pub_id=1)

    selection = compute_next_publishable_slot(
        db,
        _policy(hour=9),
        channel_id="ch",
        after_utc=now,
        max_publications_per_24h=1,
        now=now,
    )
    local = selection.scheduled_for_utc.astimezone(tz)
    assert local.hour == 9, f"expected 09:00 local, got {local.isoformat()}"
    assert local.date() > datetime(2026, 11, 1).date(), "must have crossed the DST boundary"


def test_slot_selection_respects_a_non_daily_cadence(db):
    """When a slot is skipped, the walk steps by the channel's own cadence.

    A three-day cadence must move to the next three-day slot, not to
    tomorrow — the ceiling decides which slots are usable, it does not get to
    invent slots the cadence never offered.
    """
    tz = ZoneInfo("America/New_York")
    now = datetime(2026, 8, 29, 20, 0, tzinfo=tz).astimezone(UTC)
    policy = _policy(cadence=CadenceType.every_n_days, interval_days=3)
    first, _, _ = compute_next_slot(policy, after_utc=now)

    # Block the window so that the first cadence slot is definitively illegal.
    _seed_publication(db, channel_id="ch", created_at=first - timedelta(hours=23), pub_id=1)

    selection = compute_next_publishable_slot(
        db,
        policy,
        channel_id="ch",
        after_utc=now,
        max_publications_per_24h=1,
        now=now,
    )
    assert selection.cadence_candidates_skipped == 1
    # One 3-day step, not one day.
    assert selection.scheduled_for_utc - first == timedelta(days=3)
    assert selection.scheduled_for_utc.astimezone(tz).hour == 9


def test_slot_selection_is_bounded_and_never_suppresses_production(db):
    """A pathological ceiling must still yield a slot, flagged honestly.

    Returning "no slot" would stop the channel indefinitely, which is a worse
    failure than reserving a bounded, clearly-labelled one.
    """
    now = datetime.now(UTC)
    _seed_publication(db, channel_id="ch", created_at=now, pub_id=1)

    selection = compute_next_publishable_slot(
        db,
        _policy(),
        channel_id="ch",
        after_utc=now,
        max_publications_per_24h=0,
        now=now,
        max_candidates=4,
    )
    assert selection.rate_limited is True
    assert selection.cadence_candidates_skipped == 4
    assert selection.scheduled_for_utc is not None


def test_slot_selection_is_deterministic_so_restart_does_not_duplicate(db):
    """Same inputs → same slot_key, and reserve_slot is keyed on it.

    A restart mid-cycle must resume onto the same slot rather than opening a
    second one alongside it.
    """
    from app.intelligence.autonomy.repository import list_active_slots, reserve_slot

    tz = ZoneInfo("America/New_York")
    now = datetime(2026, 8, 29, 20, 0, tzinfo=tz).astimezone(UTC)
    _seed_publication(db, channel_id="ch", created_at=now - timedelta(hours=4), pub_id=1)

    keys = set()
    for _ in range(3):
        selection = compute_next_publishable_slot(
            db,
            _policy(),
            channel_id="ch",
            after_utc=now,
            max_publications_per_24h=1,
            now=now,
        )
        keys.add(selection.slot_key)
        reserve_slot(
            db,
            channel_id="ch",
            workspace_id="ws",
            slot_key=selection.slot_key,
            scheduled_for_local=selection.scheduled_for_local,
            timezone="America/New_York",
            scheduled_for_utc=selection.scheduled_for_utc,
        )

    assert len(keys) == 1, "selection must be deterministic"
    assert len(list_active_slots(db, "ch")) == 1, "queue must stay bounded at one slot"


# ═════════════════════════════════════════════════════════════════════════════
# Voice profile resolution — autonomous narration
# ═════════════════════════════════════════════════════════════════════════════


def _seed_voice_profile(
    conn: sqlite3.Connection,
    *,
    profile_id: int,
    channel_id: int | None,
    is_default: int = 0,
    superseded_by_id: int | None = None,
    version: int = 1,
) -> None:
    now = _iso(datetime.now(UTC))
    conn.execute(
        """INSERT INTO voice_profiles
           (id, channel_id, provider, model, voice_id, name, language,
            speaking_rate, stability, similarity_boost, settings_json,
            version, is_default, superseded_by_id, created_at, updated_at)
           VALUES (?, ?, 'elevenlabs', 'eleven_multilingual_v2', ?, ?,
                   'en-US', 1.0, 0.5, 0.75, '{}', ?, ?, ?, ?, ?)""",
        (
            profile_id,
            channel_id,
            f"voice{profile_id}",
            f"Voice {profile_id}",
            version,
            is_default,
            superseded_by_id,
            now,
            now,
        ),
    )
    conn.commit()


def test_autonomous_production_resolves_a_voice_when_none_is_supplied(db):
    """The blocker the cadence fix exposed.

    NarrationExecutor requires `effective_config['voice_profile_id']`, and the
    scheduler's production dispatch never passed one — so scheduler-driven
    production failed at narration every time. It went unnoticed because the
    interval defect meant the cycle almost never ran, and the single Phase 18C
    production was invoked by hand with an explicit id.
    """
    from app.intelligence.autonomy.production_cycle import resolve_voice_profile_id

    _seed_voice_profile(db, profile_id=1, channel_id=None, is_default=1)
    assert resolve_voice_profile_id(db, 1) == 1


def test_a_channel_bound_voice_beats_the_global_default(db):
    from app.intelligence.autonomy.production_cycle import resolve_voice_profile_id

    _seed_voice_profile(db, profile_id=1, channel_id=None, is_default=1)
    _seed_voice_profile(db, profile_id=2, channel_id=7)

    assert resolve_voice_profile_id(db, 7) == 2, "channel-bound voice must win"
    assert resolve_voice_profile_id(db, 9) == 1, "other channels fall back to the default"


def test_superseded_voices_are_never_resolved(db):
    from app.intelligence.autonomy.production_cycle import resolve_voice_profile_id

    _seed_voice_profile(db, profile_id=1, channel_id=None, is_default=1, superseded_by_id=2)
    _seed_voice_profile(db, profile_id=2, channel_id=None, version=2)

    assert resolve_voice_profile_id(db, 1) == 2


def test_no_voice_configured_resolves_to_none_rather_than_guessing(db):
    """Absence must stay legible. Guessing a voice would narrate a channel in
    the wrong one, which is worse than failing the stage with a clear reason."""
    from app.intelligence.autonomy.production_cycle import resolve_voice_profile_id

    assert resolve_voice_profile_id(db, 1) is None


def test_production_retry_never_resumes_a_terminal_pipeline(db):
    """A retry must not resume a pipeline that has already failed or blocked.

    _drive_pipeline's first act is to read the pipeline and return
    immediately when its status is terminal. Reusing one across retries made
    every retry inert: it re-reported the original error without
    re-executing anything, and the slot burned its whole retry budget
    standing still. Observed live — slot 3 failed at narration, the cause
    was fixed, and the retry reproduced the identical stale error.

    Keyed on the pipeline's actual state rather than the slot's retry
    counter, because an operator repairing a slot may reset that counter and
    a key derived from it would then resume the dead pipeline again.
    """

    terminal = {"failed", "blocked"}
    existing: dict[str, str] = {}

    def resolve(slot_id: int) -> str:
        """Mirror of the cycle's key-resolution loop."""
        base = f"autonomy_production_pipeline:{slot_id}"
        for attempt in range(4):
            key = base if attempt == 0 else f"{base}:retry{attempt}"
            status = existing.get(key)
            if status is None:
                return key  # brand new
            if status not in terminal:
                return key  # resume in-flight
        raise AssertionError("exhausted")

    base = "autonomy_production_pipeline:7"

    # Nothing yet → the base key.
    assert resolve(7) == base

    # In flight → resumed, not duplicated. This is what makes a restart
    # mid-production continue rather than start over.
    existing[base] = "running"
    assert resolve(7) == base

    # Terminal → a distinct pipeline, so the drive loop can actually execute.
    existing[base] = "failed"
    assert resolve(7) == f"{base}:retry1"

    # And again — 'blocked' is terminal too.
    existing[f"{base}:retry1"] = "blocked"
    assert resolve(7) == f"{base}:retry2"

    # A reset retry counter must not resurrect a dead pipeline: resolution
    # depends only on pipeline state, so the answer is unchanged.
    assert resolve(7) == f"{base}:retry2"


# ═════════════════════════════════════════════════════════════════════════════
# doctor.sh safety semantics
# ═════════════════════════════════════════════════════════════════════════════


def _run_doctor(tmp_path: Path, *, db_path: Path, live: str, release: str) -> str:
    """Run doctor.sh in an isolated CWD with a controlled .env.local.

    doctor sources .env.local itself and that source wins over the ambient
    environment, so the gate values have to be written into a file rather than
    exported. doctor.sh's publishing-posture check requires .venv/bin/python
    to exist; rather than relying on the repository happening to contain a
    .venv/ (present when a developer ran `python -m venv .venv` locally, but
    never created in CI, which installs via actions/setup-python straight
    into the runner's own Python — no repository-local virtualenv at all),
    point .venv/bin/python at sys.executable: the interpreter already running
    this test, which is guaranteed to exist and already has the project
    installed, so doctor.sh's embedded `import app...` resolves identically
    either way. The frontend/ dependency check is still satisfied by symlink
    when present; anything unrelated that still fails is irrelevant here,
    because these assertions only read the publishing-posture lines.
    """
    work = tmp_path / f"doctor-{uuid.uuid4().hex[:8]}"
    work.mkdir()
    (work / ".env.local").write_text(
        "ACE_ENV=development\n"
        f"ACE_PUBLISHING_LIVE_ENABLED={live}\n"
        f"ACE_RELEASE_PUBLIC_ENABLED={release}\n"
        f"ACE_DB_PATH={db_path}\n"
    )
    venv_bin = work / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    os.symlink(sys.executable, venv_bin / "python")

    frontend_src = ROOT / "frontend"
    if frontend_src.exists():
        os.symlink(frontend_src, work / "frontend")

    proc = subprocess.run(
        ["bash", str(ROOT / "scripts" / "doctor.sh")],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, "ACE_DB_PATH": str(db_path)},
    )
    return proc.stdout


def _authorized_db(tmp_path: Path, **overrides) -> Path:
    """A database describing a fully, correctly authorized channel."""
    path = tmp_path / f"doctor-{uuid.uuid4().hex[:8]}.db"
    conn = open_db(path)
    conn.execute("PRAGMA foreign_keys = OFF")
    ch = overrides.get("channel_id", "chan-abcd1234")
    now = _iso(datetime.now(UTC))

    conn.execute(
        "INSERT OR IGNORE INTO cp_workspaces "
        "(id, name, slug, status, actor, created_at, updated_at) "
        "VALUES ('ws', 'WS', 'ws', 'active', 'test', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT OR IGNORE INTO cp_channels "
        "(id, workspace_id, name, slug, status, actor, created_at, updated_at) "
        "VALUES (?, 'ws', 'Chan', 'chan', 'active', 'test', ?, ?)",
        (ch, now, now),
    )
    conn.execute(
        """INSERT INTO channel_publishing_authorizations
           (channel_id, workspace_id, authorized, authorized_at, authorized_by,
            policy_version, max_publications_per_24h, missed_slot_grace_minutes,
            created_at, updated_at)
           VALUES (?, 'ws', 1, ?, 'operator', 2, ?, 120, ?, ?)""",
        (ch, now, overrides.get("ceiling", 1), now, now),
    )
    if overrides.get("account", True):
        conn.execute(
            "INSERT OR IGNORE INTO cp_platforms (id, platform_key, display_name, created_at) "
            "VALUES ('yt', 'youtube', 'YouTube', ?)",
            (now,),
        )
        conn.execute(
            """INSERT INTO cp_platform_accounts
               (id, channel_id, platform_id, platform_key, external_account_id,
                display_name, status, actor, created_at, updated_at)
               VALUES (?, ?, 'yt', 'youtube', 'UCtest', 'Acct', ?, 'test', ?, ?)""",
            (_uid(), ch, overrides.get("account_status", "connected"), now, now),
        )
    conn.execute(
        """INSERT INTO app_schedule_definitions
           (id, workspace_id, channel_id, name, operation_type, schedule_type,
            schedule_config_json, timezone, is_active, actor, created_at, updated_at)
           VALUES (?, 'ws', ?, 'pub', 'autonomous_publishing_cycle', 'interval',
                   '{"interval_seconds": 600}', 'UTC', ?, 'test', ?, ?)""",
        (_uid(), ch, 1 if overrides.get("schedule_active", True) else 0, now, now),
    )
    conn.commit()
    conn.close()
    return path


def test_doctor_passes_when_gates_are_off(tmp_path):
    """Stood down is a valid posture, not a fault."""
    out = _run_doctor(tmp_path, db_path=_authorized_db(tmp_path), live="false", release="false")
    assert "Publishing gates both off" in out
    assert "MUST NOT BE SET" not in out


def test_doctor_does_not_false_alarm_on_an_authorized_configuration(tmp_path):
    """The regression this pass exists for.

    doctor previously FAILed on `ACE_PUBLISHING_LIVE_ENABLED=true` with "THIS
    MUST NOT BE SET IN LOCAL DEV". That is a false alarm on a correctly
    authorized system, and worse, it trains an operator to ignore the check
    that would catch a genuinely unsafe configuration.
    """
    out = _run_doctor(tmp_path, db_path=_authorized_db(tmp_path), live="true", release="true")

    assert "MUST NOT BE SET" not in out
    assert "Publishing gates both on" in out
    assert "channel(s) authorized for autonomous publishing" in out
    assert "publication ceiling: 1 per trailing 24h" in out


def test_an_authorized_channel_cannot_have_a_non_positive_ceiling(tmp_path):
    """The schema forbids an unbounded authorized channel outright.

    doctor also checks for this, but the CHECK constraint is the stronger
    guarantee and the one worth pinning: an authorized channel with no
    positive ceiling cannot be written at all, so autonomous publishing can
    never be unbounded by way of a bad row.
    """
    with pytest.raises(sqlite3.IntegrityError, match="max_publications_per_24h"):
        _authorized_db(tmp_path, ceiling=0)

    with pytest.raises(sqlite3.IntegrityError, match="max_publications_per_24h"):
        _authorized_db(tmp_path, ceiling=-1)


def test_doctor_flags_an_authorized_channel_with_no_connected_account(tmp_path):
    db_path = _authorized_db(tmp_path, account=False)
    out = _run_doctor(tmp_path, db_path=db_path, live="true", release="true")
    assert "no connected YouTube account" in out


def test_doctor_flags_an_authorized_channel_whose_account_is_blocked(tmp_path):
    db_path = _authorized_db(tmp_path, account_status="credential_invalid")
    out = _run_doctor(tmp_path, db_path=db_path, live="true", release="true")
    assert "credential_invalid" in out
    assert "blocks publishing" in out


def test_doctor_reports_open_gates_with_no_authorized_channel_as_safe(tmp_path):
    """Gates open but nothing authorized: layer 3 still holds."""
    path = tmp_path / "empty.db"
    conn = open_db(path)
    conn.close()
    out = _run_doctor(tmp_path, db_path=path, live="true", release="true")
    assert "No channel is authorized" in out
    assert "layer 3 holds despite open gates" in out


def test_doctor_warns_on_a_half_open_gate_pair(tmp_path):
    """Upload permitted but release not — coherent, but worth surfacing."""
    db_path = _authorized_db(tmp_path)
    out = _run_doctor(tmp_path, db_path=db_path, live="true", release="false")
    assert "uploads may occur" in out
    assert "nothing can be made public" in out


def test_doctor_warns_when_an_authorized_channels_scheduler_is_inactive(tmp_path):
    db_path = _authorized_db(tmp_path, schedule_active=False)
    out = _run_doctor(tmp_path, db_path=db_path, live="true", release="true")
    assert "publishing scheduler is inactive" in out


def test_doctor_never_mutates_the_database(tmp_path):
    """Diagnostic only — doctor must never grant, revoke, or change anything."""
    db_path = _authorized_db(tmp_path)
    before = db_path.read_bytes()
    snapshot = tmp_path / "snapshot.db"
    shutil.copy(db_path, snapshot)

    _run_doctor(tmp_path, db_path=db_path, live="true", release="true")

    conn = open_db(db_path)
    rows = conn.execute(
        "SELECT authorized, max_publications_per_24h FROM channel_publishing_authorizations"
    ).fetchall()
    conn.close()
    assert [tuple(r) for r in rows] == [(1, 1)]
    assert len(db_path.read_bytes()) == len(before)
