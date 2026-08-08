"""Timezone-aware scheduling validation for publishing plans.

No daemon. No cron installation. No background worker.
This module only validates and represents scheduling intent.
Actual scheduled execution is deferred to Phase 10 or an explicit operator trigger.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.publishing.errors import PublishingValidationError
from app.publishing.models import PublishingScheduleDraft

# Minimal IANA timezone name pattern (validates structure, not membership)
_IANA_TZ_PATTERN = re.compile(r"^[A-Za-z]+(/[A-Za-z_]+)*$")


def validate_schedule(schedule: PublishingScheduleDraft) -> None:
    """Raise PublishingValidationError if the schedule is invalid."""
    from app.publishing.constants import SCHEDULE_TYPES

    if schedule.schedule_type not in SCHEDULE_TYPES:
        raise PublishingValidationError(
            f"Invalid schedule_type {schedule.schedule_type!r}. "
            f"Must be one of: {sorted(SCHEDULE_TYPES)}."
        )

    if schedule.schedule_type == "scheduled":
        if not schedule.scheduled_at:
            raise PublishingValidationError(
                "schedule_type='scheduled' requires scheduled_at to be set."
            )
        _validate_iso_datetime(schedule.scheduled_at)
        _validate_future(schedule.scheduled_at)

    if schedule.timezone is not None:
        if not _IANA_TZ_PATTERN.match(schedule.timezone):
            raise PublishingValidationError(
                f"timezone {schedule.timezone!r} does not look like a valid IANA name."
            )


def _validate_iso_datetime(value: str) -> None:
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise PublishingValidationError(
            f"scheduled_at {value!r} is not a valid ISO 8601 datetime."
        ) from exc


def _validate_future(value: str) -> None:
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        if dt <= now:
            raise PublishingValidationError(f"scheduled_at {value!r} must be in the future.")
    except PublishingValidationError:
        raise
    except ValueError:
        pass  # format already caught in _validate_iso_datetime


def is_scheduled_time_due(schedule: PublishingScheduleDraft) -> bool:
    """Return True if the schedule indicates it's time to publish."""
    if schedule.schedule_type != "scheduled" or not schedule.scheduled_at:
        return schedule.schedule_type == "immediate"
    try:
        dt = datetime.fromisoformat(schedule.scheduled_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return datetime.now(UTC) >= dt
    except ValueError:
        return False
