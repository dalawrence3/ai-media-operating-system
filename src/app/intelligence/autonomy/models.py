"""Phase 18A/18B — typed models for the autonomy decision and production cycles.

AutonomyPolicy and PublishingSlot are pydantic models (API-facing, like
StrategyProfile). DecisionCycleResult / ProductionCycleResult are plain
dataclasses (internal orchestration results, like ExperimentPortfolioPlan)
— never serialized directly to the DB, only reported/logged/tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class CadenceType(StrEnum):
    every_12h = "every_12h"
    daily = "daily"
    every_n_days = "every_n_days"
    weekly = "weekly"
    custom_cron = "custom_cron"


class SlotState(StrEnum):
    reserved = "reserved"
    filled = "filled"
    cancelled = "cancelled"
    expired = "expired"


class ProductionStatus(StrEnum):
    """Phase 18B — production progress for a filled slot. None on the slot
    itself (not a member of this enum) means production hasn't started."""

    queued = "queued"
    producing = "producing"
    ready = "ready"
    failed = "failed"


class PublishStatus(StrEnum):
    """Phase 18C — publishing progress for a READY slot.

    `uploaded` is a real, durable resting state, not a transient one: the
    video exists privately on the provider and only the public release
    remains. A cycle interrupted there resumes at release rather than
    re-uploading.
    """

    pending = "pending"  # due, authorized, not yet started
    publishing = "publishing"  # lease held, work in flight
    uploaded = "uploaded"  # private on provider, not yet public
    released = "released"  # public, terminal success
    failed = "failed"
    skipped_missed = "skipped_missed"  # deadline+grace passed; needs rescheduling
    blocked = "blocked"  # authorization/health refused it


# Phase 18D — publish states after which a slot has permanently left the
# pipeline. `slot.state` deliberately stays 'filled' for these (the historical
# record must not be rewritten — a missed slot has to remain visible as a
# missed slot), so "is this slot still occupying the channel's queue?" is
# answered by publish_status, not by state.
#
# Without this distinction a released slot counts against queue_target
# forever, and the decision cycle returns QUEUE_ALREADY_SATISFIED for the
# rest of the channel's life.
TERMINAL_PUBLISH_STATUSES: frozenset[str] = frozenset(
    {
        PublishStatus.released.value,
        PublishStatus.skipped_missed.value,
    }
)

# Phase 18E — retirement is the SECOND way a slot leaves the pipeline, and it
# is a column (publishing_slots.retired_at) rather than a publish_status value.
#
# It is a different axis: publish_status tracks progress toward publication,
# retirement records that progress stopped permanently and why. A slot retired
# for artifact quality never progressed anywhere, so there is no progress value
# that honestly describes it.
#
# Anything asking "does this slot still occupy the queue?" must test BOTH. The
# repository builds one SQL fragment covering both so the two conditions can
# never be updated in isolation — see _NOT_TERMINAL_SQL.


class PublishFailureCategory(StrEnum):
    """Canonical publishing failure taxonomy (section 17).

    The retryable/terminal split is what bounds the retry loop; the
    *_STATE_UNCERTAIN case is deliberately neither, because the safe action
    is reconciliation rather than either retrying or giving up.
    """

    PREUPLOAD_VALIDATION_FAILED = "PREUPLOAD_VALIDATION_FAILED"
    UPLOAD_FAILED_RETRYABLE = "UPLOAD_FAILED_RETRYABLE"
    UPLOAD_FAILED_TERMINAL = "UPLOAD_FAILED_TERMINAL"
    UPLOAD_STATE_UNCERTAIN = "UPLOAD_STATE_UNCERTAIN"
    RELEASE_FAILED_RETRYABLE = "RELEASE_FAILED_RETRYABLE"
    RELEASE_FAILED_TERMINAL = "RELEASE_FAILED_TERMINAL"
    PROVIDER_HEALTH_BLOCKED = "PROVIDER_HEALTH_BLOCKED"
    RATE_LIMIT_BLOCKED = "RATE_LIMIT_BLOCKED"
    AUTHORIZATION_BLOCKED = "AUTHORIZATION_BLOCKED"
    MISSED_SLOT = "MISSED_SLOT"
    # Phase 18E — the artifact itself is deterministically unpublishable.
    ARTIFACT_QUALITY_BLOCKED = "ARTIFACT_QUALITY_BLOCKED"


RETRYABLE_PUBLISH_FAILURES = frozenset(
    {
        PublishFailureCategory.UPLOAD_FAILED_RETRYABLE,
        PublishFailureCategory.RELEASE_FAILED_RETRYABLE,
        PublishFailureCategory.PREUPLOAD_VALIDATION_FAILED,
    }
)

# Phase 18E — failures that are a property of the ARTIFACT, not of the attempt.
#
# The distinction that matters for retry budget: a provider outage or a network
# error might succeed on the next tick, so spending a retry on it is rational.
# A render that is 84% typeset narration will still be 84% typeset narration on
# the next tick — the input has not changed, the policy has not changed, so the
# verdict cannot change. Spending retries there burns budget to re-derive a
# known answer, and worse, leaves the slot in a non-terminal 'failed' state
# that pins the channel's queue.
#
# These retire the slot instead: no provider call, no retry consumed, terminal.
DETERMINISTIC_ARTIFACT_FAILURES: frozenset[PublishFailureCategory] = frozenset(
    {
        PublishFailureCategory.ARTIFACT_QUALITY_BLOCKED,
    }
)


class PublishOutcome(StrEnum):
    """Outcome of one run_publishing_cycle() call."""

    RELEASED = "RELEASED"
    UPLOADED_PENDING_RELEASE = "UPLOADED_PENDING_RELEASE"
    NOT_DUE = "NOT_DUE"
    NO_SLOT_TO_PUBLISH = "NO_SLOT_TO_PUBLISH"
    BLOCKED = "BLOCKED"
    MISSED = "MISSED"
    FAILED = "FAILED"
    DISABLED = "DISABLED"
    ALREADY_RUNNING = "ALREADY_RUNNING"
    NEEDS_RECONCILIATION = "NEEDS_RECONCILIATION"
    # Phase 18E — the artifact was deterministically unpublishable and the slot
    # was retired. Distinct from FAILED, which implies "try again".
    RETIRED = "RETIRED"


class DeadlineStatus(StrEnum):
    """How production timing compares to the slot's reserved publish time —
    informational only; never used to compromise validation (section 10)."""

    comfortably_ahead = "comfortably_ahead"
    approaching = "approaching"
    late = "late"
    missed = "missed"


class DecisionOutcome(StrEnum):
    """Typed result of one run_decision_cycle() call."""

    SELECTED = "selected"
    NO_ELIGIBLE_CANDIDATE = "no_eligible_candidate"
    QUEUE_ALREADY_SATISFIED = "queue_already_satisfied"
    WAITING_FOR_FRESH_DATA = "waiting_for_fresh_data"
    DEGRADED_BUT_PROCEEDED = "degraded_but_proceeded"
    FAILED = "failed"
    DISABLED = "disabled"


class AutonomyPolicy(BaseModel):
    """One channel's decision-automation configuration.

    Mutable, single-row-per-channel (like analytics_observation_state) —
    NOT versioned like cp_strategy_profiles. This is an operator toggle/
    setting, not an audited strategic decision. Every change is still
    recorded as a cp_events row for audit.

    decision_automation_enabled can never be true while timezone is None —
    enforced by a CHECK constraint at the schema level, not just here.

    production_automation_enabled (Phase 18B) is a third, independent
    control: it only permits spending resources to generate media
    artifacts for an already-filled slot. It grants no upload authority —
    that stays structurally impossible regardless of this flag (see
    app.intelligence.autonomy.production_cycle's module docstring).
    """

    model_config = ConfigDict(from_attributes=True)

    channel_id: str
    workspace_id: str
    decision_automation_enabled: bool = False
    production_automation_enabled: bool = False
    cadence_type: CadenceType = CadenceType.daily
    cadence_interval_days: int | None = None
    cadence_cron: str | None = None
    preferred_local_hour: int = 9
    timezone: str | None = None
    queue_target: int = 1
    market_refresh_max_age_hours: int = 12
    semantic_fit_max_evaluations_per_run: int = 5
    last_decision_at: str | None = None
    last_decision_outcome: str | None = None
    actor: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PublishingSlot(BaseModel):
    """A reserved future publish time, upstream of any Experiment row.

    `brief_id` references experiment_strategy_briefs — the existing
    artifact representing "a selected, concrete, market-grounded idea."
    Actual Experiment row creation remains downstream and human-gated
    (Phase 14E's own design), so a slot never references `experiments`.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    channel_id: str
    workspace_id: str
    slot_key: str
    scheduled_for_local: str
    timezone: str
    scheduled_for_utc: str
    state: SlotState
    brief_id: str | None = None
    selection_decision_id: int | None = None
    opportunity_id: int | None = None
    reserved_at: str
    filled_at: str | None = None
    cancelled_at: str | None = None
    cancellation_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # Phase 18B — production tracking. experiment_id is set once the slot's
    # brief has been materialized into a canonical Experiment; production_status
    # is None until production_cycle first touches this slot.
    experiment_id: str | None = None
    production_status: ProductionStatus | None = None
    production_pipeline_id: str | None = None
    production_publishing_plan_id: int | None = None
    production_started_at: str | None = None
    production_ready_at: str | None = None
    production_failed_at: str | None = None
    production_failed_stage: str | None = None
    production_error: str | None = None
    production_retry_count: int = 0

    # Phase 18C — publishing tracking. publish_status is None until the
    # publishing cycle first considers this slot.
    publish_status: PublishStatus | None = None
    publication_id: int | None = None
    publish_provider_video_id: str | None = None
    publish_started_at: str | None = None
    publish_uploaded_at: str | None = None
    publish_released_at: str | None = None
    publish_failed_at: str | None = None
    publish_failure_category: str | None = None
    publish_error: str | None = None
    publish_retry_count: int = 0
    rescheduled_from_slot_id: int | None = None

    # Phase 18E — terminal retirement (see TERMINAL_PUBLISH_STATUSES above).
    retired_at: str | None = None
    retirement_reason: str | None = None

    @property
    def retired(self) -> bool:
        return self.retired_at is not None


@dataclass
class DecisionCycleResult:
    """Outcome of one run_decision_cycle() call. Every field reflects what
    genuinely happened — nothing fabricated when a step is skipped."""

    channel_id: str
    workspace_id: str
    started_at: str
    completed_at: str = ""
    outcome: DecisionOutcome = DecisionOutcome.FAILED
    reason: str = ""

    operation_id: str | None = None
    idempotency_key: str | None = None
    already_running: bool = False

    # "reused" | "executed" | "unavailable" | "failed" | "skipped"
    market_refresh_status: str = "skipped"
    market_refresh_error: str | None = None

    cross_pub_learning_ran: bool = False
    cross_pub_learning_publication_count: int = 0

    semantic_fit_considered: int = 0
    semantic_fit_evaluated: int = 0
    semantic_fit_cache_hits: int = 0
    semantic_fit_eligible: int = 0

    eligible_count: int = 0
    planning_run_id: str | None = None
    selection_decision_id: int | None = None
    opportunity_id: int | None = None
    brief_id: str | None = None
    slot_id: int | None = None

    # How many cadence slots were skipped because they fell inside the
    # channel's trailing-24h publication ceiling window. 0 is the normal
    # case; a non-zero value means the reserved slot is deliberately later
    # than the cadence alone would have chosen.
    rate_limited_slot_shift: int = 0

    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.outcome not in (DecisionOutcome.FAILED,)


class ProductionOutcome(StrEnum):
    """Typed result of one run_production_cycle() call."""

    READY = "ready"
    STILL_IN_PROGRESS = "still_in_progress"  # blocked on a recoverable prerequisite
    NO_SLOT_TO_PRODUCE = "no_slot_to_produce"
    FAILED = "failed"
    DISABLED = "disabled"
    ALREADY_RUNNING = "already_running"


@dataclass
class ProductionCycleResult:
    """Outcome of one run_production_cycle() call. Every field reflects what
    genuinely happened — nothing fabricated when a stage is skipped."""

    channel_id: str
    workspace_id: str
    slot_id: int | None
    started_at: str
    completed_at: str = ""
    outcome: ProductionOutcome = ProductionOutcome.FAILED
    reason: str = ""

    operation_id: str | None = None
    idempotency_key: str | None = None
    already_running: bool = False

    experiment_id: str | None = None
    topic_id: int | None = None
    pipeline_id: str | None = None
    publishing_plan_id: int | None = None

    stages_completed: list[str] = field(default_factory=list)
    failed_stage: str | None = None
    error_category: str | None = None
    error_message: str | None = None
    retry_count: int = 0

    deadline_status: DeadlineStatus | None = None

    preflight_passed: bool | None = None
    preflight_errors: list[str] = field(default_factory=list)

    # Phase 18E — the render's measured visual composition verdict.
    # None means no assessment existed (a pre-18E render, or an assessment
    # write that failed); that is deliberately distinct from "pass".
    visual_quality_status: str | None = None
    visual_quality_findings: list[str] = field(default_factory=list)

    # Coarse external-call accounting by category — counts of artifacts
    # created that imply a real spend, not a metered token/dollar total.
    llm_calls: int = 0
    tts_runs: int = 0
    visual_provider_calls: int = 0

    # Voice actually used for narration — resolved from the channel when the
    # caller did not name one. None means none could be resolved.
    voice_profile_id: int | None = None

    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.outcome not in (ProductionOutcome.FAILED,)


@dataclass
class PublishingCycleResult:
    """Outcome of one run_publishing_cycle() call.

    Records what genuinely happened, including the uncomfortable cases: an
    upload whose provider outcome is unknown, or a video that went public but
    whose local write failed. Both are reported honestly rather than being
    collapsed into a generic failure.
    """

    channel_id: str
    workspace_id: str
    slot_id: int | None
    started_at: str
    completed_at: str = ""
    outcome: PublishOutcome = PublishOutcome.FAILED
    reason: str = ""

    operation_id: str | None = None
    idempotency_key: str | None = None
    already_running: bool = False

    # Authorization snapshot at the moment of the decision.
    global_publishing_enabled: bool = False
    global_release_enabled: bool = False
    channel_authorized: bool = False
    blocked_by: list[str] = field(default_factory=list)

    publications_last_24h: int = 0
    max_publications_per_24h: int = 0

    publishing_plan_id: int | None = None
    publication_id: int | None = None
    provider_video_id: str | None = None
    experiment_id: str | None = None

    uploaded: bool = False
    released: bool = False
    preflight_passed: bool | None = None
    preflight_errors: list[str] = field(default_factory=list)

    # Phase 18E — re-read at upload time. READY yesterday is not READY today,
    # and that applies to visual quality as much as to the metadata.
    visual_quality_status: str | None = None
    # Set when the cycle retired the slot instead of failing it.
    retired: bool = False
    retirement_reason: str | None = None

    failure_category: str | None = None
    error_message: str | None = None
    retry_count: int = 0

    deadline_status: DeadlineStatus | None = None
    observation_schedule_id: str | None = None
    # Experiment ledger state after the post-release handoff. None when the
    # cycle did not reach release, or when the publication carries no
    # experiment in its lineage.
    experiment_status: str | None = None

    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.outcome not in (PublishOutcome.FAILED, PublishOutcome.NEEDS_RECONCILIATION)
