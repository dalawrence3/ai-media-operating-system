"""State machine guards for publishing plan, job, and publication lifecycles."""

from __future__ import annotations

from app.publishing.constants import (
    JOB_TRANSITIONS,
    PLAN_TRANSITIONS,
    PUB_TRANSITIONS,
)
from app.publishing.errors import (
    IllegalJobTransitionError,
    IllegalPublicationTransitionError,
    IllegalPublishingPlanTransitionError,
)


def check_plan_transition(from_status: str, to_status: str) -> None:
    allowed = PLAN_TRANSITIONS.get(from_status, frozenset())
    if to_status not in allowed:
        raise IllegalPublishingPlanTransitionError(
            f"Cannot transition publishing plan from {from_status!r} to {to_status!r}. "
            f"Allowed: {sorted(allowed) or 'none'}."
        )


def check_job_transition(from_status: str, to_status: str) -> None:
    allowed = JOB_TRANSITIONS.get(from_status, frozenset())
    if to_status not in allowed:
        raise IllegalJobTransitionError(
            f"Cannot transition publishing job from {from_status!r} to {to_status!r}. "
            f"Allowed: {sorted(allowed) or 'none'}."
        )


def check_publication_transition(from_status: str, to_status: str) -> None:
    allowed = PUB_TRANSITIONS.get(from_status, frozenset())
    if to_status not in allowed:
        raise IllegalPublicationTransitionError(
            f"Cannot transition publication from {from_status!r} to {to_status!r}. "
            f"Allowed: {sorted(allowed) or 'none'}."
        )
