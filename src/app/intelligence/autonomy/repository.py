"""Phase 18A — repository layer for autonomy_policies and publishing_slots.

Cadence math lives here (compute_next_slot) rather than in the orchestrator,
so it can be unit-tested in isolation from the rest of the decision cycle.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.intelligence.autonomy.models import (
    TERMINAL_PUBLISH_STATUSES,
    AutonomyPolicy,
    PublishingSlot,
    PublishStatus,
)

# SQL fragment + params for "this slot has not left the pipeline". Built once
# so every queue/eligibility query uses exactly the same definition of
# terminal, and adding a terminal status can never update only some of them.
#
# Phase 18E added the second way out: retirement. A slot leaves the pipeline
# either by reaching a terminal publish_status (released / skipped_missed) OR
# by being retired (retired_at IS NOT NULL) because its artifact is
# deterministically unpublishable. Both conditions live in this one fragment
# precisely so no query can honour one and miss the other — the queue deadlock
# this fixes came from exactly that kind of split.
_TERMINAL_PUBLISH_LIST = sorted(TERMINAL_PUBLISH_STATUSES)
_NOT_TERMINAL_SQL = (
    "(retired_at IS NULL AND (publish_status IS NULL OR publish_status NOT IN ("
    + ",".join("?" * len(_TERMINAL_PUBLISH_LIST))
    + ")))"
)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


class InvalidTimezoneError(ValueError):
    """Raised when a timezone string is not a valid IANA zone name."""


def validate_timezone(tz_name: str) -> None:
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise InvalidTimezoneError(f"Unknown IANA timezone: {tz_name!r}") from exc


def _row_to_policy(row: sqlite3.Row) -> AutonomyPolicy:
    return AutonomyPolicy(
        channel_id=row["channel_id"],
        workspace_id=row["workspace_id"],
        decision_automation_enabled=bool(row["decision_automation_enabled"]),
        production_automation_enabled=bool(row["production_automation_enabled"]),
        cadence_type=row["cadence_type"],
        cadence_interval_days=row["cadence_interval_days"],
        cadence_cron=row["cadence_cron"],
        preferred_local_hour=row["preferred_local_hour"],
        timezone=row["timezone"],
        queue_target=row["queue_target"],
        market_refresh_max_age_hours=row["market_refresh_max_age_hours"],
        semantic_fit_max_evaluations_per_run=row["semantic_fit_max_evaluations_per_run"],
        last_decision_at=row["last_decision_at"],
        last_decision_outcome=row["last_decision_outcome"],
        actor=row["actor"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_autonomy_policy(conn: sqlite3.Connection, channel_id: str) -> AutonomyPolicy | None:
    row = conn.execute(
        "SELECT * FROM autonomy_policies WHERE channel_id = ?", (channel_id,)
    ).fetchone()
    return _row_to_policy(row) if row else None


def upsert_autonomy_policy(
    conn: sqlite3.Connection,
    *,
    channel_id: str,
    workspace_id: str,
    actor: str,
    decision_automation_enabled: bool | None = None,
    production_automation_enabled: bool | None = None,
    cadence_type: str | None = None,
    cadence_interval_days: int | None = None,
    cadence_cron: str | None = None,
    preferred_local_hour: int | None = None,
    timezone: str | None = None,
    clear_timezone: bool = False,
    queue_target: int | None = None,
    market_refresh_max_age_hours: int | None = None,
    semantic_fit_max_evaluations_per_run: int | None = None,
) -> AutonomyPolicy:
    """Create or update a channel's autonomy policy. Partial updates: any
    argument left None keeps the existing (or default) value.

    `timezone=None` (the default) leaves the existing timezone untouched —
    it is not itself how you clear one, since None also means "not
    provided" for every other optional field here. Pass clear_timezone=True
    to explicitly null it out (e.g. reverting a temporary/test value).

    Raises InvalidTimezoneError if `timezone` is supplied but not a valid
    IANA zone name. Raises sqlite3.IntegrityError (via the CHECK constraint)
    if the resulting row would have decision_automation_enabled=1 with no
    timezone — this is a deliberate hard stop, not something this function
    silently works around.
    """
    if timezone is not None:
        validate_timezone(timezone)

    existing = get_autonomy_policy(conn, channel_id)
    now = _now_iso()

    if existing is None:
        merged = AutonomyPolicy(
            channel_id=channel_id,
            workspace_id=workspace_id,
            decision_automation_enabled=decision_automation_enabled or False,
            production_automation_enabled=production_automation_enabled or False,
            cadence_type=cadence_type or "daily",
            cadence_interval_days=cadence_interval_days,
            cadence_cron=cadence_cron,
            preferred_local_hour=preferred_local_hour if preferred_local_hour is not None else 9,
            timezone=timezone,
            queue_target=queue_target if queue_target is not None else 1,
            market_refresh_max_age_hours=(
                market_refresh_max_age_hours if market_refresh_max_age_hours is not None else 12
            ),
            semantic_fit_max_evaluations_per_run=(
                semantic_fit_max_evaluations_per_run
                if semantic_fit_max_evaluations_per_run is not None
                else 5
            ),
            actor=actor,
        )
        conn.execute(
            """INSERT INTO autonomy_policies
               (channel_id, workspace_id, decision_automation_enabled,
                production_automation_enabled,
                cadence_type, cadence_interval_days, cadence_cron, preferred_local_hour, timezone,
                queue_target, market_refresh_max_age_hours,
                semantic_fit_max_evaluations_per_run, actor, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                channel_id,
                workspace_id,
                int(merged.decision_automation_enabled),
                int(merged.production_automation_enabled),
                merged.cadence_type.value
                if hasattr(merged.cadence_type, "value")
                else merged.cadence_type,
                merged.cadence_interval_days,
                merged.cadence_cron,
                merged.preferred_local_hour,
                merged.timezone,
                merged.queue_target,
                merged.market_refresh_max_age_hours,
                merged.semantic_fit_max_evaluations_per_run,
                actor,
                now,
                now,
            ),
        )
    else:
        merged = AutonomyPolicy(
            channel_id=channel_id,
            workspace_id=workspace_id,
            decision_automation_enabled=(
                decision_automation_enabled
                if decision_automation_enabled is not None
                else existing.decision_automation_enabled
            ),
            production_automation_enabled=(
                production_automation_enabled
                if production_automation_enabled is not None
                else existing.production_automation_enabled
            ),
            cadence_type=cadence_type or existing.cadence_type,
            cadence_interval_days=(
                cadence_interval_days
                if cadence_interval_days is not None
                else existing.cadence_interval_days
            ),
            cadence_cron=cadence_cron if cadence_cron is not None else existing.cadence_cron,
            preferred_local_hour=(
                preferred_local_hour
                if preferred_local_hour is not None
                else existing.preferred_local_hour
            ),
            timezone=(
                None if clear_timezone else timezone if timezone is not None else existing.timezone
            ),
            queue_target=queue_target if queue_target is not None else existing.queue_target,
            market_refresh_max_age_hours=(
                market_refresh_max_age_hours
                if market_refresh_max_age_hours is not None
                else existing.market_refresh_max_age_hours
            ),
            semantic_fit_max_evaluations_per_run=(
                semantic_fit_max_evaluations_per_run
                if semantic_fit_max_evaluations_per_run is not None
                else existing.semantic_fit_max_evaluations_per_run
            ),
            actor=actor,
        )
        conn.execute(
            """UPDATE autonomy_policies SET
                 decision_automation_enabled=?, production_automation_enabled=?, cadence_type=?,
                 cadence_interval_days=?, cadence_cron=?, preferred_local_hour=?, timezone=?,
                 queue_target=?, market_refresh_max_age_hours=?,
                 semantic_fit_max_evaluations_per_run=?,
                 actor=?, updated_at=?
               WHERE channel_id=?""",
            (
                int(merged.decision_automation_enabled),
                int(merged.production_automation_enabled),
                merged.cadence_type.value
                if hasattr(merged.cadence_type, "value")
                else merged.cadence_type,
                merged.cadence_interval_days,
                merged.cadence_cron,
                merged.preferred_local_hour,
                merged.timezone,
                merged.queue_target,
                merged.market_refresh_max_age_hours,
                merged.semantic_fit_max_evaluations_per_run,
                actor,
                now,
                channel_id,
            ),
        )

    conn.commit()
    return get_autonomy_policy(conn, channel_id)  # type: ignore[return-value]


def record_decision_outcome(
    conn: sqlite3.Connection, channel_id: str, outcome: str, *, at: str | None = None
) -> None:
    conn.execute(
        "UPDATE autonomy_policies SET last_decision_at=?, last_decision_outcome=?, updated_at=? "
        "WHERE channel_id=?",
        (at or _now_iso(), outcome, _now_iso(), channel_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Cadence math
# ---------------------------------------------------------------------------


def compute_next_slot(
    policy: AutonomyPolicy,
    *,
    after_utc: datetime,
    min_lead_hours: float = 1.0,
) -> tuple[datetime, str, str]:
    """Compute the next publish slot strictly after `after_utc` (plus a
    minimum lead time so a slot is never reserved for "right now").

    Returns (scheduled_for_utc, scheduled_for_local_iso, slot_key).

    daily / every_n_days / weekly: one slot per calendar day (in the
    channel's local timezone) at preferred_local_hour. every_n_days and
    weekly compute purely forward from `after_utc` — not anchored to a
    fixed epoch — so cadence drift is possible across policy edits; that is
    an accepted simplification for Phase 18A (documented, not hidden).

    every_12h: two slots per local day, at preferred_local_hour and
    preferred_local_hour + 12 (mod 24).

    custom_cron is not evaluated here — Phase 18A stores the field for
    forward compatibility but does not implement cron computation.
    """
    if policy.timezone is None:
        raise ValueError("compute_next_slot requires a configured timezone")
    tz = ZoneInfo(policy.timezone)

    earliest = after_utc + timedelta(hours=min_lead_hours)
    earliest_local = earliest.astimezone(tz)

    cadence = (
        policy.cadence_type.value if hasattr(policy.cadence_type, "value") else policy.cadence_type
    )

    if cadence == "every_12h":
        anchors = [policy.preferred_local_hour, (policy.preferred_local_hour + 12) % 24]
        candidate_date = earliest_local.date()
        for _ in range(4):  # at most 2 days out covers both anchors safely
            for hour in sorted(anchors):
                candidate = datetime(
                    candidate_date.year,
                    candidate_date.month,
                    candidate_date.day,
                    hour,
                    0,
                    0,
                    tzinfo=tz,
                )
                if candidate > earliest_local:
                    utc_dt = candidate.astimezone(UTC)
                    slot_key = candidate.strftime("%Y-%m-%dT%H")
                    return utc_dt, candidate.isoformat(), slot_key
            candidate_date = candidate_date + timedelta(days=1)
        raise RuntimeError("compute_next_slot: no every_12h candidate found within search window")

    step_days = 1
    if cadence == "every_n_days":
        step_days = policy.cadence_interval_days or 1
    elif cadence == "weekly":
        step_days = 7
    elif cadence == "custom_cron":
        raise NotImplementedError("custom_cron slot computation is not implemented in Phase 18A")

    candidate_date = earliest_local.date()
    for _ in range(400 // max(step_days, 1) + 2):
        candidate = datetime(
            candidate_date.year,
            candidate_date.month,
            candidate_date.day,
            policy.preferred_local_hour,
            0,
            0,
            tzinfo=tz,
        )
        if candidate > earliest_local:
            utc_dt = candidate.astimezone(UTC)
            slot_key = candidate.strftime("%Y-%m-%d")
            return utc_dt, candidate.isoformat(), slot_key
        candidate_date = candidate_date + timedelta(days=step_days)
    raise RuntimeError("compute_next_slot: no candidate found within search window")


def _slot_key_is_spent(conn: sqlite3.Connection, channel_id: str, slot_key: str) -> bool:
    """Whether this cadence key already belongs to a slot that has left the pipeline.

    slot_key is UNIQUE per channel, and terminal/retired slots are kept
    forever as history rather than being rewritten or deleted. So a key whose
    slot has left the pipeline is spent: `reserve_slot` would hand back that
    historical row, and `fill_slot` would then refuse it because its state is
    already 'filled' — which is precisely how retiring a future-dated slot
    deadlocked the decision cycle before this check existed.

    Skipping the key advances the cadence to the next date, which is the same
    resolution `reschedule_slot_to_new_time` reaches for a missed slot: the
    history keeps its slot, the replacement gets its own.

    A slot that exists and is still ACTIVE is deliberately not spent — that is
    the ordinary resume-an-in-flight-slot case reserve_slot exists to serve.
    """
    row = conn.execute(
        f"""SELECT 1 FROM publishing_slots
            WHERE channel_id = ? AND slot_key = ? AND NOT {_NOT_TERMINAL_SQL}""",
        (channel_id, slot_key, *_TERMINAL_PUBLISH_LIST),
    ).fetchone()
    return row is not None


def earliest_rate_permitted_utc(
    conn: sqlite3.Connection,
    channel_id: str,
    *,
    max_publications_per_24h: int,
    now: datetime | None = None,
) -> datetime:
    """The earliest instant at which this channel may publish again.

    The ceiling is "at most N publications in the trailing 24 hours". When
    fewer than N exist in the window the answer is simply now. When the
    window is full, the ceiling clears the moment the Nth-most-recent
    publication ages out of it — so the answer is that publication's
    timestamp plus 24 hours, not the newest one's.

    Counted by `created_at` to match count_publications_last_24h exactly: an
    upload that failed partway still consumed budget, because the ceiling
    bounds external side effects rather than successful outcomes. Computing
    availability from a different column than the enforcement uses would let
    the planner reserve a slot the authorization layer then refuses.

    This is a read-only projection of an existing rule. It does not raise,
    lower, or reinterpret the ceiling.
    """
    if now is None:
        now = datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    if max_publications_per_24h <= 0:
        # A zero ceiling means "never" — surface it as unreachable rather
        # than silently treating it as unlimited.
        return datetime.max.replace(tzinfo=UTC)

    cutoff = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
    rows = conn.execute(
        """SELECT created_at FROM publications
           WHERE channel_id = ? AND deleted_at IS NULL AND created_at >= ?
           ORDER BY created_at DESC""",
        (channel_id, cutoff),
    ).fetchall()

    if len(rows) < max_publications_per_24h:
        return now

    # rows[N-1] is the oldest publication still holding a slot in the window.
    blocking = rows[max_publications_per_24h - 1]["created_at"]
    try:
        blocking_dt = datetime.fromisoformat(blocking.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return now
    if blocking_dt.tzinfo is None:
        blocking_dt = blocking_dt.replace(tzinfo=UTC)
    return blocking_dt + timedelta(hours=24)


@dataclass(frozen=True)
class SlotSelection:
    """A chosen slot plus why it, rather than an earlier one, was chosen."""

    scheduled_for_utc: datetime
    scheduled_for_local: str
    slot_key: str
    cadence_candidates_skipped: int
    earliest_rate_permitted_utc: datetime
    rate_limited: bool


def compute_next_publishable_slot(
    conn: sqlite3.Connection,
    policy: AutonomyPolicy,
    *,
    channel_id: str,
    after_utc: datetime,
    max_publications_per_24h: int,
    min_lead_hours: float = 1.0,
    max_candidates: int = 32,
    now: datetime | None = None,
) -> SlotSelection:
    """The next cadence slot this channel could actually publish in.

    Cadence and the rate ceiling stay conceptually independent — this does
    not blend them into one schedule. It walks the cadence's own slots in
    order and returns the first that is not already guaranteed to be refused
    by the ceiling. The cadence decides *when slots exist*; the ceiling
    decides *which of them are usable*.

    Reserving a slot the ceiling will certainly block is not merely untidy:
    the production cycle would generate a script, narration, visuals and a
    render for it, and the publishing cycle would then refuse to upload and
    retire the slot as missed. That spends real money to produce a video that
    was never publishable.

    Slots are walked by re-deriving each one from the policy rather than by
    adding 24 hours, so a daily 09:00 local slot stays at 09:00 local across
    a DST boundary instead of drifting to 08:00 or 10:00.

    Never returns "no slot": if the ceiling is unreachable within
    `max_candidates` cadence steps the furthest candidate is returned with
    `rate_limited=True`, so the caller reserves something honest and bounded
    rather than suppressing production indefinitely. `max_candidates` also
    bounds the walk against a pathological policy.
    """
    if now is None:
        now = datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    permitted_from = earliest_rate_permitted_utc(
        conn,
        channel_id,
        max_publications_per_24h=max_publications_per_24h,
        now=now,
    )

    utc_dt, local_iso, slot_key = compute_next_slot(
        policy, after_utc=after_utc, min_lead_hours=min_lead_hours
    )
    skipped = 0
    for _ in range(max_candidates):
        if utc_dt >= permitted_from and not _slot_key_is_spent(conn, channel_id, slot_key):
            return SlotSelection(
                scheduled_for_utc=utc_dt,
                scheduled_for_local=local_iso,
                slot_key=slot_key,
                cadence_candidates_skipped=skipped,
                earliest_rate_permitted_utc=permitted_from,
                rate_limited=False,
            )
        skipped += 1
        # Advance by asking the policy for the slot after this one, so the
        # local preferred hour is re-derived each step and DST is handled by the
        # same code path that produced the first candidate.
        utc_dt, local_iso, slot_key = compute_next_slot(
            policy, after_utc=utc_dt, min_lead_hours=0.0
        )

    return SlotSelection(
        scheduled_for_utc=utc_dt,
        scheduled_for_local=local_iso,
        slot_key=slot_key,
        cadence_candidates_skipped=skipped,
        earliest_rate_permitted_utc=permitted_from,
        rate_limited=True,
    )


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------


def _opt(row: sqlite3.Row, column: str) -> object | None:
    """Read a column that may not exist on this row.

    Slot rows are also constructed in tests and by older code paths from
    partial SELECTs; a missing retirement column means "not retired", which is
    the correct reading, not an error.
    """
    try:
        return row[column]
    except (IndexError, KeyError):
        return None


def _row_to_slot(row: sqlite3.Row) -> PublishingSlot:
    return PublishingSlot(
        id=row["id"],
        channel_id=row["channel_id"],
        workspace_id=row["workspace_id"],
        slot_key=row["slot_key"],
        scheduled_for_local=row["scheduled_for_local"],
        timezone=row["timezone"],
        scheduled_for_utc=row["scheduled_for_utc"],
        state=row["state"],
        brief_id=row["brief_id"],
        selection_decision_id=row["selection_decision_id"],
        opportunity_id=row["opportunity_id"],
        reserved_at=row["reserved_at"],
        filled_at=row["filled_at"],
        cancelled_at=row["cancelled_at"],
        cancellation_reason=row["cancellation_reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        experiment_id=row["experiment_id"],
        production_status=row["production_status"],
        production_pipeline_id=row["production_pipeline_id"],
        production_publishing_plan_id=row["production_publishing_plan_id"],
        production_started_at=row["production_started_at"],
        production_ready_at=row["production_ready_at"],
        production_failed_at=row["production_failed_at"],
        production_failed_stage=row["production_failed_stage"],
        production_error=row["production_error"],
        production_retry_count=row["production_retry_count"],
        publish_status=row["publish_status"],
        publication_id=row["publication_id"],
        publish_provider_video_id=row["publish_provider_video_id"],
        publish_started_at=row["publish_started_at"],
        publish_uploaded_at=row["publish_uploaded_at"],
        publish_released_at=row["publish_released_at"],
        publish_failed_at=row["publish_failed_at"],
        publish_failure_category=row["publish_failure_category"],
        publish_error=row["publish_error"],
        publish_retry_count=row["publish_retry_count"],
        rescheduled_from_slot_id=row["rescheduled_from_slot_id"],
        retired_at=_opt(row, "retired_at"),
        retirement_reason=_opt(row, "retirement_reason"),
    )


def get_slot(conn: sqlite3.Connection, slot_id: int) -> PublishingSlot | None:
    row = conn.execute("SELECT * FROM publishing_slots WHERE id = ?", (slot_id,)).fetchone()
    return _row_to_slot(row) if row else None


def list_active_slots(conn: sqlite3.Connection, channel_id: str) -> list[PublishingSlot]:
    """Slots still occupying this channel's bounded queue.

    A slot whose publish_status is terminal (released or skipped_missed) has
    left the pipeline and no longer consumes queue capacity, even though its
    row keeps state='filled' as the historical record of what happened.
    """
    rows = conn.execute(
        "SELECT * FROM publishing_slots WHERE channel_id = ? AND state IN ('reserved', 'filled') "
        f"AND {_NOT_TERMINAL_SQL} "
        "ORDER BY scheduled_for_utc ASC",
        (channel_id, *_TERMINAL_PUBLISH_LIST),
    ).fetchall()
    return [_row_to_slot(r) for r in rows]


def list_slots_for_channel(
    conn: sqlite3.Connection, channel_id: str, *, limit: int = 50
) -> list[PublishingSlot]:
    """Every slot for the channel, terminal ones included — the historical view.

    Distinct from list_active_slots, which answers the queue-capacity
    question. Read-only surfaces that need the full record (operator UI,
    audits) use this so terminal slots stay visible.
    """
    rows = conn.execute(
        "SELECT * FROM publishing_slots WHERE channel_id = ? "
        "ORDER BY scheduled_for_utc DESC LIMIT ?",
        (channel_id, limit),
    ).fetchall()
    return [_row_to_slot(r) for r in rows]


def reserve_slot(
    conn: sqlite3.Connection,
    *,
    channel_id: str,
    workspace_id: str,
    slot_key: str,
    scheduled_for_local: str,
    timezone: str,
    scheduled_for_utc: datetime,
) -> PublishingSlot:
    """Idempotent reservation: if a slot already exists for (channel_id,
    slot_key) — reserved or otherwise — return it unchanged rather than
    inserting a duplicate. The UNIQUE(channel_id, slot_key) constraint is
    the hard guarantee; this pre-check just avoids a raised exception on
    the common, expected case of re-checking an already-reserved slot."""
    existing = conn.execute(
        "SELECT * FROM publishing_slots WHERE channel_id = ? AND slot_key = ?",
        (channel_id, slot_key),
    ).fetchone()
    if existing is not None:
        return _row_to_slot(existing)

    now = _now_iso()
    cur = conn.execute(
        """INSERT INTO publishing_slots
           (channel_id, workspace_id, slot_key, scheduled_for_local, timezone,
            scheduled_for_utc, state, reserved_at, created_at, updated_at)
           VALUES (?,?,?,?,?,?,'reserved',?,?,?)""",
        (
            channel_id,
            workspace_id,
            slot_key,
            scheduled_for_local,
            timezone,
            scheduled_for_utc.strftime("%Y-%m-%dT%H:%M:%S"),
            now,
            now,
            now,
        ),
    )
    conn.commit()
    return get_slot(conn, cur.lastrowid)  # type: ignore[return-value, arg-type]


def fill_slot(
    conn: sqlite3.Connection,
    slot_id: int,
    *,
    brief_id: str,
    selection_decision_id: int,
    opportunity_id: int,
) -> PublishingSlot:
    """Fill a reserved slot with a selected brief. Guarded by the current
    state — only a 'reserved' slot can be filled, preventing a double-fill
    race (the UPDATE affects zero rows if state has already changed)."""
    now = _now_iso()
    cur = conn.execute(
        """UPDATE publishing_slots
           SET state='filled', brief_id=?, selection_decision_id=?, opportunity_id=?,
               filled_at=?, updated_at=?
           WHERE id=? AND state='reserved'""",
        (brief_id, selection_decision_id, opportunity_id, now, now, slot_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        current = get_slot(conn, slot_id)
        raise ValueError(
            f"Slot {slot_id} could not be filled — expected state 'reserved', "
            f"found {current.state if current else 'missing'!r}"
        )
    return get_slot(conn, slot_id)  # type: ignore[return-value]


def cancel_slot(conn: sqlite3.Connection, slot_id: int, reason: str) -> PublishingSlot:
    now = _now_iso()
    conn.execute(
        "UPDATE publishing_slots SET state='cancelled', cancelled_at=?, cancellation_reason=?, "
        "updated_at=? WHERE id=?",
        (now, reason, now, slot_id),
    )
    conn.commit()
    return get_slot(conn, slot_id)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Production tracking (Phase 18B)
# ---------------------------------------------------------------------------


def find_slot_needing_production(
    conn: sqlite3.Connection,
    channel_id: str,
    *,
    max_retries: int = 2,
) -> PublishingSlot | None:
    """The cheap check the scheduler and orchestrator both use: is there a
    filled slot for this channel that production hasn't finished?

    'ready' is always excluded — terminal, done. 'failed' is retried
    automatically, but only up to max_retries times (bounded — section 14/
    15's "retries must be bounded"); once exhausted, a failed slot stops
    being picked up entirely, and a later operator/scheduling decision
    handles it, not an automatic infinite re-attempt.

    Slots with a terminal publish_status are excluded. A missed slot whose
    production lineage was handed off to its replacement has its production
    columns cleared, which would otherwise make it look like fresh work and
    send the pipeline off to re-produce a retired slot on every tick."""
    row = conn.execute(
        f"""SELECT * FROM publishing_slots
           WHERE channel_id = ? AND state = 'filled'
             AND {_NOT_TERMINAL_SQL}
             AND (
               production_status IS NULL
               OR production_status IN ('queued', 'producing')
               OR (production_status = 'failed' AND production_retry_count < ?)
             )
           ORDER BY scheduled_for_utc ASC LIMIT 1""",
        (channel_id, *_TERMINAL_PUBLISH_LIST, max_retries),
    ).fetchone()
    return _row_to_slot(row) if row else None


def start_slot_production(
    conn: sqlite3.Connection,
    slot_id: int,
    *,
    experiment_id: str,
    pipeline_id: str,
) -> PublishingSlot:
    """Record that production has begun for this slot. Idempotent: setting
    the same experiment_id/pipeline_id again is a no-op; production_started_at
    is only ever set once (COALESCE), so a resumed cycle doesn't reset the
    original start time used for deadline tracking."""
    now = _now_iso()
    conn.execute(
        """UPDATE publishing_slots SET
             experiment_id = ?, production_pipeline_id = ?,
             production_status = 'producing',
             production_started_at = COALESCE(production_started_at, ?),
             updated_at = ?
           WHERE id = ?""",
        (experiment_id, pipeline_id, now, now, slot_id),
    )
    conn.commit()
    return get_slot(conn, slot_id)  # type: ignore[return-value]


def mark_slot_production_ready(
    conn: sqlite3.Connection,
    slot_id: int,
    *,
    publishing_plan_id: int,
) -> PublishingSlot:
    now = _now_iso()
    conn.execute(
        """UPDATE publishing_slots SET
             production_status = 'ready', production_publishing_plan_id = ?,
             production_ready_at = ?, updated_at = ?
           WHERE id = ?""",
        (publishing_plan_id, now, now, slot_id),
    )
    conn.commit()
    return get_slot(conn, slot_id)  # type: ignore[return-value]


def mark_slot_production_failed(
    conn: sqlite3.Connection,
    slot_id: int,
    *,
    stage: str,
    error: str,
    increment_retry: bool = True,
) -> PublishingSlot:
    now = _now_iso()
    conn.execute(
        f"""UPDATE publishing_slots SET
             production_status = 'failed', production_failed_stage = ?, production_error = ?,
             production_failed_at = ?, updated_at = ?
             {", production_retry_count = production_retry_count + 1" if increment_retry else ""}
           WHERE id = ?""",
        (stage, error, now, now, slot_id),
    )
    conn.commit()
    return get_slot(conn, slot_id)  # type: ignore[return-value]


def reset_slot_production_for_retry(conn: sqlite3.Connection, slot_id: int) -> PublishingSlot:
    """Clear the terminal 'failed' status so the next cycle resumes the
    existing pipeline (never a new one — the pipeline itself is untouched;
    only this slot's own status returns to 'producing')."""
    now = _now_iso()
    conn.execute(
        """UPDATE publishing_slots SET
             production_status = 'producing', production_failed_stage = NULL,
             production_error = NULL, updated_at = ?
           WHERE id = ?""",
        (now, slot_id),
    )
    conn.commit()
    return get_slot(conn, slot_id)  # type: ignore[return-value]


# ── Phase 18C: publishing state ───────────────────────────────────────────────

MAX_PUBLISH_RETRIES = 3


def find_slot_ready_to_publish(
    conn: sqlite3.Connection,
    channel_id: str,
    *,
    max_retries: int = MAX_PUBLISH_RETRIES,
) -> PublishingSlot | None:
    """Return the next slot that publishing should consider for this channel.

    Deliberately returns slots that are not yet due as well as due ones: the
    caller decides due-ness, because it is the caller that also owns the
    grace-window policy. Returning only due slots here would scatter that
    policy across two layers.

    Excludes terminal publish states (released / skipped_missed), RETIRED
    slots, and slots that have exhausted their retry budget, so a permanently
    broken slot cannot pin the cycle forever.

    The retirement exclusion comes from the shared _NOT_TERMINAL_SQL fragment
    rather than from this function's own status list. That list is an
    allow-list of in-flight states, so a new terminal condition would be
    silently admitted by it unless the shared fragment also applies.
    """
    row = conn.execute(
        f"""SELECT * FROM publishing_slots
           WHERE channel_id = ?
             AND state = 'filled'
             AND production_status = 'ready'
             AND {_NOT_TERMINAL_SQL}
             AND (
               publish_status IS NULL
               OR publish_status IN ('pending', 'publishing', 'uploaded', 'blocked')
               OR (publish_status = 'failed' AND publish_retry_count < ?)
             )
           ORDER BY scheduled_for_utc ASC
           LIMIT 1""",
        (channel_id, *_TERMINAL_PUBLISH_LIST, max_retries),
    ).fetchone()
    return _row_to_slot(row) if row else None


def start_slot_publishing(conn: sqlite3.Connection, slot_id: int) -> PublishingSlot:
    """Mark a slot as actively publishing. Idempotent.

    publish_started_at is preserved across resumes via COALESCE so the
    duration of a multi-attempt publish stays measurable from its true start.
    """
    now = _now_iso()
    conn.execute(
        """UPDATE publishing_slots
           SET publish_status = 'publishing',
               publish_started_at = COALESCE(publish_started_at, ?),
               publish_failure_category = NULL,
               publish_error = NULL,
               updated_at = ?
           WHERE id = ?""",
        (now, now, slot_id),
    )
    conn.commit()
    slot = get_slot(conn, slot_id)
    assert slot is not None
    return slot


def mark_slot_uploaded(
    conn: sqlite3.Connection,
    slot_id: int,
    *,
    publication_id: int | None,
    provider_video_id: str,
) -> PublishingSlot:
    """Record that the video exists privately on the provider.

    A durable resting state: if the process dies here, the next cycle resumes
    at release rather than re-uploading.
    """
    now = _now_iso()
    conn.execute(
        """UPDATE publishing_slots
           SET publish_status = 'uploaded',
               publication_id = COALESCE(?, publication_id),
               publish_provider_video_id = ?,
               publish_uploaded_at = COALESCE(publish_uploaded_at, ?),
               updated_at = ?
           WHERE id = ?""",
        (publication_id, provider_video_id, now, now, slot_id),
    )
    conn.commit()
    slot = get_slot(conn, slot_id)
    assert slot is not None
    return slot


def mark_slot_released(
    conn: sqlite3.Connection, slot_id: int, *, publication_id: int | None = None
) -> PublishingSlot:
    """Terminal success: the video is public."""
    now = _now_iso()
    conn.execute(
        """UPDATE publishing_slots
           SET publish_status = 'released',
               publication_id = COALESCE(?, publication_id),
               publish_released_at = COALESCE(publish_released_at, ?),
               publish_failure_category = NULL,
               publish_error = NULL,
               updated_at = ?
           WHERE id = ?""",
        (publication_id, now, now, slot_id),
    )
    conn.commit()
    slot = get_slot(conn, slot_id)
    assert slot is not None
    return slot


def mark_slot_publish_failed(
    conn: sqlite3.Connection,
    slot_id: int,
    *,
    category: str,
    error: str,
    increment_retry: bool = True,
) -> PublishingSlot:
    """Record a publishing failure with its canonical category.

    An already-uploaded slot keeps `uploaded` status rather than regressing
    to `failed`: the private video genuinely exists on the provider, and
    forgetting that is how duplicates happen. The failure detail is still
    recorded for the operator.
    """
    now = _now_iso()
    current = get_slot(conn, slot_id)
    keep_uploaded = current is not None and current.publish_status == PublishStatus.uploaded
    new_status = "uploaded" if keep_uploaded else "failed"

    conn.execute(
        f"""UPDATE publishing_slots
            SET publish_status = '{new_status}',
                publish_failed_at = ?,
                publish_failure_category = ?,
                publish_error = ?,
                publish_retry_count = publish_retry_count + ?,
                updated_at = ?
            WHERE id = ?""",
        (now, category, error[:2000], 1 if increment_retry else 0, now, slot_id),
    )
    conn.commit()
    slot = get_slot(conn, slot_id)
    assert slot is not None
    return slot


def mark_slot_publish_blocked(
    conn: sqlite3.Connection, slot_id: int, *, category: str, reason: str
) -> PublishingSlot:
    """Record that authorization or health refused this slot.

    Blocking never consumes retry budget: a revoked authorization is an
    operator decision, not a failure the system should give up on. Restoring
    authorization must let the slot proceed normally.
    """
    now = _now_iso()
    current = get_slot(conn, slot_id)
    keep_uploaded = current is not None and current.publish_status == PublishStatus.uploaded
    new_status = "uploaded" if keep_uploaded else "blocked"

    conn.execute(
        f"""UPDATE publishing_slots
            SET publish_status = '{new_status}',
                publish_failure_category = ?,
                publish_error = ?,
                updated_at = ?
            WHERE id = ?""",
        (category, reason[:2000], now, slot_id),
    )
    conn.commit()
    slot = get_slot(conn, slot_id)
    assert slot is not None
    return slot


def mark_slot_missed(conn: sqlite3.Connection, slot_id: int, *, reason: str) -> PublishingSlot:
    """Mark a slot whose deadline plus grace window has elapsed.

    Terminal for this slot. The produced artifact is NOT destroyed — it stays
    attached, and `reschedule_slot_to_new_time` can move it to a fresh future
    slot. Publishing hours or days late without an explicit operator decision
    is exactly what section 10 forbids.
    """
    now = _now_iso()
    conn.execute(
        """UPDATE publishing_slots
           SET publish_status = 'skipped_missed',
               publish_failure_category = 'MISSED_SLOT',
               publish_error = ?,
               updated_at = ?
           WHERE id = ?""",
        (reason[:2000], now, slot_id),
    )
    conn.commit()
    slot = get_slot(conn, slot_id)
    assert slot is not None
    return slot


def retire_slot(
    conn: sqlite3.Connection,
    slot_id: int,
    *,
    category: str,
    reason: str,
) -> PublishingSlot:
    """Permanently retire a slot whose artifact cannot be published.

    Deliberately shaped like `mark_slot_missed` — terminal marker, failure
    category, truthful reason, everything else preserved — because it is the
    same lifecycle event with a different cause, and inventing a parallel
    lifecycle for it was the alternative worth avoiding.

    Two differences from the missed-slot path, both intentional:

    * It NEVER increments publish_retry_count. The verdict is a property of
      the artifact, so retrying cannot change it; spending retry budget would
      only delay the terminal state while the slot pins the queue.

    * There is no reschedule counterpart. `reschedule_slot_to_new_time` exists
      because a missed artifact is perfectly good and merely late. A retired
      artifact is not good, so moving it to a later slot would publish exactly
      the thing that was refused.

    History is preserved in full: scheduled_for_utc, slot_key, experiment_id,
    the publishing plan, the render manifest and the visual assessment all stay
    attached, and state remains 'filled'. The row keeps saying what was
    attempted and why it stopped.

    Idempotent: retiring an already-retired slot leaves the original
    retired_at and reason intact, so a restart mid-cycle cannot rewrite when
    or why the retirement happened.
    """
    now = _now_iso()
    conn.execute(
        """UPDATE publishing_slots
           SET retired_at = COALESCE(retired_at, ?),
               retirement_reason = COALESCE(retirement_reason, ?),
               publish_status = 'failed',
               publish_failure_category = COALESCE(publish_failure_category, ?),
               publish_error = COALESCE(publish_error, ?),
               updated_at = ?
           WHERE id = ?""",
        (now, reason[:2000], category, reason[:2000], now, slot_id),
    )
    conn.commit()
    slot = get_slot(conn, slot_id)
    assert slot is not None
    return slot


def reschedule_slot_to_new_time(
    conn: sqlite3.Connection,
    slot_id: int,
    *,
    new_scheduled_for_utc: str,
    new_scheduled_for_local: str,
    new_slot_key: str,
    timezone: str,
    actor: str,
) -> PublishingSlot:
    """Move a missed slot's ready artifact onto a new future slot.

    Creates a NEW slot row rather than rewriting the old one's timestamp.
    The original keeps its `skipped_missed` status and original scheduled
    time, preserving honest audit history (section 22): the system must not
    be able to pretend a slot was never missed.

    The production lineage (experiment, pipeline, publishing plan) transfers
    to the new slot; the old slot releases its experiment_id so the unique
    index on it is not violated.
    """
    old = get_slot(conn, slot_id)
    if old is None:
        raise ValueError(f"Slot {slot_id} not found.")

    now = _now_iso()

    # Hand the production lineage over to the new slot — a MOVE, not a copy.
    #
    # experiment_id and brief_id must be released because unique indexes allow
    # only one slot each. The production_* fields carry no such index, and
    # leaving them behind is a live hazard rather than harmless history: a slot
    # with state='filled' AND production_status='ready' AND a publishing plan
    # still satisfies find_slot_ready_to_publish(). The old slot would keep
    # competing for the same artifact, and because candidates are ordered by
    # scheduled_for_utc ASC the *missed* slot would be selected ahead of its own
    # replacement — precisely the "do not publish the stale slot" case this
    # function exists to prevent.
    #
    # publish_status is set to skipped_missed so the old row is terminal and
    # self-explanatory. What stays untouched is what makes the audit honest:
    # its original scheduled_for_utc, slot_key, and timestamps.
    conn.execute(
        """UPDATE publishing_slots
           SET experiment_id = NULL,
               brief_id = NULL,
               production_status = NULL,
               production_publishing_plan_id = NULL,
               production_pipeline_id = NULL,
               publish_status = 'skipped_missed',
               publish_failure_category = COALESCE(publish_failure_category, 'MISSED_SLOT'),
               updated_at = ?
           WHERE id = ?""",
        (now, slot_id),
    )

    conn.execute(
        """INSERT INTO publishing_slots
           (channel_id, workspace_id, slot_key, scheduled_for_local, timezone,
            scheduled_for_utc, state, brief_id, selection_decision_id, opportunity_id,
            reserved_at, filled_at, created_at, updated_at,
            experiment_id, production_status, production_pipeline_id,
            production_publishing_plan_id, production_started_at, production_ready_at,
            publish_status, rescheduled_from_slot_id)
           VALUES (?, ?, ?, ?, ?, ?, 'filled', ?, ?, ?, ?, ?, ?, ?,
                   ?, 'ready', ?, ?, ?, ?, NULL, ?)""",
        (
            old.channel_id,
            old.workspace_id,
            new_slot_key,
            new_scheduled_for_local,
            timezone,
            new_scheduled_for_utc,
            old.brief_id,
            old.selection_decision_id,
            old.opportunity_id,
            now,
            now,
            now,
            now,
            old.experiment_id,
            old.production_pipeline_id,
            old.production_publishing_plan_id,
            old.production_started_at,
            old.production_ready_at,
            slot_id,
        ),
    )
    new_slot_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    conn.execute(
        """UPDATE publishing_slots
           SET publish_error = COALESCE(publish_error, '')
                   || ' | Rescheduled to slot ' || ? || ' by ' || ?,
               updated_at = ?
           WHERE id = ?""",
        (new_slot_id, actor, now, slot_id),
    )
    conn.commit()

    new_slot = get_slot(conn, new_slot_id)
    assert new_slot is not None
    return new_slot
