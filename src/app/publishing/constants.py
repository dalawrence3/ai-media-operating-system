"""Phase 9 publishing constants — lifecycle values, transitions, capabilities."""

from __future__ import annotations

PUBLISHING_ENGINE_VERSION = "1.0.0"
PUBLISHING_METADATA_VERSION = "1.0.0"

# ── Publishing Plan status ────────────────────────────────────────────────────
PLAN_STATUS_DRAFT = "draft"
PLAN_STATUS_APPROVED = "approved"
PLAN_STATUS_REJECTED = "rejected"

PLAN_STATUSES = frozenset({PLAN_STATUS_DRAFT, PLAN_STATUS_APPROVED, PLAN_STATUS_REJECTED})

# Valid plan transitions (supersession uses superseded_at field, not a status)
PLAN_TRANSITIONS: dict[str, frozenset[str]] = {
    PLAN_STATUS_DRAFT: frozenset({PLAN_STATUS_APPROVED, PLAN_STATUS_REJECTED}),
    PLAN_STATUS_APPROVED: frozenset(),
    PLAN_STATUS_REJECTED: frozenset(),
}

# ── Publishing Job status ─────────────────────────────────────────────────────
JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_RETRY_SCHEDULED = "retry_scheduled"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_CANCELLED = "cancelled"

JOB_STATUSES = frozenset(
    {
        JOB_STATUS_QUEUED,
        JOB_STATUS_RUNNING,
        JOB_STATUS_RETRY_SCHEDULED,
        JOB_STATUS_COMPLETED,
        JOB_STATUS_FAILED,
        JOB_STATUS_CANCELLED,
    }
)

JOB_TRANSITIONS: dict[str, frozenset[str]] = {
    JOB_STATUS_QUEUED: frozenset({JOB_STATUS_RUNNING, JOB_STATUS_CANCELLED}),
    JOB_STATUS_RUNNING: frozenset({JOB_STATUS_COMPLETED, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}),
    JOB_STATUS_RETRY_SCHEDULED: frozenset({JOB_STATUS_RUNNING, JOB_STATUS_CANCELLED}),
    JOB_STATUS_COMPLETED: frozenset(),
    JOB_STATUS_FAILED: frozenset({JOB_STATUS_RETRY_SCHEDULED}),
    JOB_STATUS_CANCELLED: frozenset(),
}

# ── Publication status ────────────────────────────────────────────────────────
PUB_STATUS_UPLOADING = "uploading"
PUB_STATUS_UPLOADED = "uploaded"
PUB_STATUS_SCHEDULED = "scheduled"
PUB_STATUS_PUBLISHED = "published"
PUB_STATUS_FAILED = "failed"
PUB_STATUS_DELETED = "deleted"

PUB_STATUSES = frozenset(
    {
        PUB_STATUS_UPLOADING,
        PUB_STATUS_UPLOADED,
        PUB_STATUS_SCHEDULED,
        PUB_STATUS_PUBLISHED,
        PUB_STATUS_FAILED,
        PUB_STATUS_DELETED,
    }
)

PUB_TRANSITIONS: dict[str, frozenset[str]] = {
    PUB_STATUS_UPLOADING: frozenset({PUB_STATUS_UPLOADED, PUB_STATUS_FAILED}),
    PUB_STATUS_UPLOADED: frozenset({PUB_STATUS_PUBLISHED, PUB_STATUS_SCHEDULED, PUB_STATUS_FAILED}),
    PUB_STATUS_SCHEDULED: frozenset({PUB_STATUS_PUBLISHED, PUB_STATUS_FAILED, PUB_STATUS_DELETED}),
    PUB_STATUS_PUBLISHED: frozenset({PUB_STATUS_DELETED}),
    PUB_STATUS_FAILED: frozenset(),
    PUB_STATUS_DELETED: frozenset(),
}

# ── Visibility ────────────────────────────────────────────────────────────────
VISIBILITY_PRIVATE = "private"
VISIBILITY_UNLISTED = "unlisted"
VISIBILITY_PUBLIC = "public"

VISIBILITY_OPTIONS = frozenset({VISIBILITY_PRIVATE, VISIBILITY_UNLISTED, VISIBILITY_PUBLIC})

# ── Schedule types ────────────────────────────────────────────────────────────
SCHEDULE_IMMEDIATE = "immediate"
SCHEDULE_SCHEDULED = "scheduled"
SCHEDULE_MANUAL = "manual"

SCHEDULE_TYPES = frozenset({SCHEDULE_IMMEDIATE, SCHEDULE_SCHEDULED, SCHEDULE_MANUAL})

# ── Review event types (append-only) ─────────────────────────────────────────
EVENT_PLAN_PREPARED = "plan_prepared"
EVENT_PLAN_APPROVED = "plan_approved"
EVENT_PLAN_REJECTED = "plan_rejected"
EVENT_METADATA_REJECTED = "metadata_rejected"
EVENT_JOB_QUEUED = "job_queued"
EVENT_JOB_STARTED = "job_started"
EVENT_JOB_COMPLETED = "job_completed"
EVENT_JOB_FAILED = "job_failed"
EVENT_RETRY_REQUESTED = "retry_requested"
EVENT_SCHEDULE_CHANGED = "schedule_changed"
EVENT_PUBLICATION_APPROVED = "publication_approved"
EVENT_PUBLICATION_REJECTED = "publication_rejected"
EVENT_CANCELLATION_REQUESTED = "cancellation_requested"
EVENT_SUPERSEDED = "superseded"

EVENT_TYPES = frozenset(
    {
        EVENT_PLAN_PREPARED,
        EVENT_PLAN_APPROVED,
        EVENT_PLAN_REJECTED,
        EVENT_METADATA_REJECTED,
        EVENT_JOB_QUEUED,
        EVENT_JOB_STARTED,
        EVENT_JOB_COMPLETED,
        EVENT_JOB_FAILED,
        EVENT_RETRY_REQUESTED,
        EVENT_SCHEDULE_CHANGED,
        EVENT_PUBLICATION_APPROVED,
        EVENT_PUBLICATION_REJECTED,
        EVENT_CANCELLATION_REQUESTED,
        EVENT_SUPERSEDED,
    }
)

# ── Providers ─────────────────────────────────────────────────────────────────
PROVIDER_YOUTUBE = "youtube"
PROVIDER_FAKE = "fake"

# ── Rejection / reason codes ──────────────────────────────────────────────────
REASON_CONTENT_POLICY = "content_policy"
REASON_QUALITY = "quality"
REASON_METADATA = "metadata"
REASON_SCHEDULING = "scheduling"
REASON_PROVIDER_ERROR = "provider_error"
REASON_DUPLICATE = "duplicate"
REASON_OTHER = "other"

REASON_CODES = frozenset(
    {
        REASON_CONTENT_POLICY,
        REASON_QUALITY,
        REASON_METADATA,
        REASON_SCHEDULING,
        REASON_PROVIDER_ERROR,
        REASON_DUPLICATE,
        REASON_OTHER,
    }
)

# ── Retry policy ──────────────────────────────────────────────────────────────
MAX_RETRY_ATTEMPTS = 3
