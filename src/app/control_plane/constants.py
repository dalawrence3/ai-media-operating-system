"""Phase 12 Control Plane constants."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------
WORKSPACE_STATUS_ACTIVE = "active"
WORKSPACE_STATUS_SUSPENDED = "suspended"
WORKSPACE_STATUS_ARCHIVED = "archived"
WORKSPACE_STATUSES = frozenset(
    {WORKSPACE_STATUS_ACTIVE, WORKSPACE_STATUS_SUSPENDED, WORKSPACE_STATUS_ARCHIVED}
)

# ---------------------------------------------------------------------------
# Channel (cp_channel — distinct from Phase 3 intelligence `channels` table)
# ---------------------------------------------------------------------------
CHANNEL_STATUS_ACTIVE = "active"
CHANNEL_STATUS_PAUSED = "paused"
CHANNEL_STATUS_ARCHIVED = "archived"
CHANNEL_STATUSES = frozenset(
    {CHANNEL_STATUS_ACTIVE, CHANNEL_STATUS_PAUSED, CHANNEL_STATUS_ARCHIVED}
)

# ---------------------------------------------------------------------------
# Platforms
# ---------------------------------------------------------------------------
PLATFORM_YOUTUBE = "youtube"
PLATFORM_TIKTOK = "tiktok"
PLATFORM_INSTAGRAM = "instagram"
PLATFORM_LINKEDIN = "linkedin"
PLATFORM_X = "x"
KNOWN_PLATFORMS = frozenset(
    {
        PLATFORM_YOUTUBE,
        PLATFORM_TIKTOK,
        PLATFORM_INSTAGRAM,
        PLATFORM_LINKEDIN,
        PLATFORM_X,
    }
)

# ---------------------------------------------------------------------------
# Platform account status
# ---------------------------------------------------------------------------
ACCOUNT_STATUS_CONNECTED = "connected"
ACCOUNT_STATUS_DISCONNECTED = "disconnected"
ACCOUNT_STATUS_CREDENTIAL_INVALID = "credential_invalid"
ACCOUNT_STATUS_CREDENTIAL_EXPIRING = "credential_expiring"
ACCOUNT_STATUS_QUOTA_LIMITED = "quota_limited"
ACCOUNT_STATUS_PAUSED = "paused"
PLATFORM_ACCOUNT_STATUSES = frozenset(
    {
        ACCOUNT_STATUS_CONNECTED,
        ACCOUNT_STATUS_DISCONNECTED,
        ACCOUNT_STATUS_CREDENTIAL_INVALID,
        ACCOUNT_STATUS_CREDENTIAL_EXPIRING,
        ACCOUNT_STATUS_QUOTA_LIMITED,
        ACCOUNT_STATUS_PAUSED,
    }
)

# ---------------------------------------------------------------------------
# Credential profiles (safe metadata — no secrets stored)
# ---------------------------------------------------------------------------
CREDENTIAL_TYPE_OAUTH2 = "oauth2"
CREDENTIAL_TYPE_API_KEY = "api_key"
CREDENTIAL_TYPE_SERVICE_ACCOUNT = "service_account"
CREDENTIAL_TYPES = frozenset(
    {CREDENTIAL_TYPE_OAUTH2, CREDENTIAL_TYPE_API_KEY, CREDENTIAL_TYPE_SERVICE_ACCOUNT}
)

CREDENTIAL_STATUS_ACTIVE = "active"
CREDENTIAL_STATUS_EXPIRING = "expiring"
CREDENTIAL_STATUS_EXPIRED = "expired"
CREDENTIAL_STATUS_REVOKED = "revoked"
CREDENTIAL_STATUS_PENDING_VALIDATION = "pending_validation"
CREDENTIAL_STATUSES = frozenset(
    {
        CREDENTIAL_STATUS_ACTIVE,
        CREDENTIAL_STATUS_EXPIRING,
        CREDENTIAL_STATUS_EXPIRED,
        CREDENTIAL_STATUS_REVOKED,
        CREDENTIAL_STATUS_PENDING_VALIDATION,
    }
)

# ---------------------------------------------------------------------------
# Automation levels
# ---------------------------------------------------------------------------
AUTOMATION_MANUAL = "manual"
AUTOMATION_SUPERVISED = "supervised"
AUTOMATION_AUTONOMOUS = "autonomous"
AUTOMATION_LEVELS = frozenset({AUTOMATION_MANUAL, AUTOMATION_SUPERVISED, AUTOMATION_AUTONOMOUS})

# Scopes an automation policy may be attached to
POLICY_SCOPE_WORKSPACE = "workspace"
POLICY_SCOPE_CHANNEL = "channel"
POLICY_SCOPE_PLATFORM_ACCOUNT = "platform_account"
POLICY_SCOPES = frozenset(
    {POLICY_SCOPE_WORKSPACE, POLICY_SCOPE_CHANNEL, POLICY_SCOPE_PLATFORM_ACCOUNT}
)

# ---------------------------------------------------------------------------
# Domain events
# ---------------------------------------------------------------------------
EVENT_WORKSPACE_CREATED = "workspace.created"
EVENT_WORKSPACE_SUSPENDED = "workspace.suspended"
EVENT_WORKSPACE_ARCHIVED = "workspace.archived"

EVENT_CHANNEL_CREATED = "channel.created"
EVENT_CHANNEL_PAUSED = "channel.paused"
EVENT_CHANNEL_ARCHIVED = "channel.archived"

EVENT_ACCOUNT_CONNECTED = "platform_account.connected"
EVENT_ACCOUNT_DISCONNECTED = "platform_account.disconnected"
EVENT_ACCOUNT_CREDENTIAL_UPDATED = "platform_account.credential_updated"
EVENT_ACCOUNT_STATUS_CHANGED = "platform_account.status_changed"
EVENT_ACCOUNT_PAUSED = "platform_account.paused"
EVENT_ACCOUNT_RESUMED = "platform_account.resumed"

EVENT_CREDENTIAL_CREATED = "credential.created"
EVENT_CREDENTIAL_EXPIRING = "credential.expiring"
EVENT_CREDENTIAL_EXPIRED = "credential.expired"
EVENT_CREDENTIAL_REVOKED = "credential.revoked"

EVENT_POLICY_UPDATED = "automation_policy.updated"
EVENT_STRATEGY_UPDATED = "strategy_profile.updated"

EVENT_WORKFLOW_TRIGGERED = "workflow.triggered"
EVENT_WORKFLOW_COMPLETED = "workflow.completed"
EVENT_WORKFLOW_FAILED = "workflow.failed"

EVENT_EXPERIMENT_ACTIVATED = "experiment.activated"
EVENT_EXPERIMENT_CONCLUDED = "experiment.concluded"
EVENT_EXPERIMENT_CANCELLED = "experiment.cancelled"

EVENT_OPERATION_STARTED = "operation.started"
EVENT_OPERATION_COMPLETED = "operation.completed"
EVENT_OPERATION_FAILED = "operation.failed"
EVENT_OPERATION_SUPERSEDED = "operation.superseded"

EVENT_BUDGET_WARNING = "budget.warning"
EVENT_BUDGET_EXCEEDED = "budget.exceeded"

EVENT_HEALTH_DEGRADED = "health.degraded"
EVENT_HEALTH_RESTORED = "health.restored"

EVENT_EMERGENCY_STOP_TRIGGERED = "emergency_stop.triggered"
EVENT_EMERGENCY_STOP_LIFTED = "emergency_stop.lifted"

ALL_EVENT_TYPES = frozenset(
    {
        EVENT_WORKSPACE_CREATED,
        EVENT_WORKSPACE_SUSPENDED,
        EVENT_WORKSPACE_ARCHIVED,
        EVENT_CHANNEL_CREATED,
        EVENT_CHANNEL_PAUSED,
        EVENT_CHANNEL_ARCHIVED,
        EVENT_ACCOUNT_CONNECTED,
        EVENT_ACCOUNT_DISCONNECTED,
        EVENT_ACCOUNT_CREDENTIAL_UPDATED,
        EVENT_ACCOUNT_STATUS_CHANGED,
        EVENT_ACCOUNT_PAUSED,
        EVENT_ACCOUNT_RESUMED,
        EVENT_CREDENTIAL_CREATED,
        EVENT_CREDENTIAL_EXPIRING,
        EVENT_CREDENTIAL_EXPIRED,
        EVENT_CREDENTIAL_REVOKED,
        EVENT_POLICY_UPDATED,
        EVENT_STRATEGY_UPDATED,
        EVENT_WORKFLOW_TRIGGERED,
        EVENT_WORKFLOW_COMPLETED,
        EVENT_WORKFLOW_FAILED,
        EVENT_EXPERIMENT_ACTIVATED,
        EVENT_EXPERIMENT_CONCLUDED,
        EVENT_EXPERIMENT_CANCELLED,
        EVENT_OPERATION_STARTED,
        EVENT_OPERATION_COMPLETED,
        EVENT_OPERATION_FAILED,
        EVENT_OPERATION_SUPERSEDED,
        EVENT_BUDGET_WARNING,
        EVENT_BUDGET_EXCEEDED,
        EVENT_HEALTH_DEGRADED,
        EVENT_HEALTH_RESTORED,
        EVENT_EMERGENCY_STOP_TRIGGERED,
        EVENT_EMERGENCY_STOP_LIFTED,
    }
)

# ---------------------------------------------------------------------------
# Event processing
# ---------------------------------------------------------------------------
PROCESSING_STATUS_PENDING = "pending"
PROCESSING_STATUS_PROCESSING = "processing"
PROCESSING_STATUS_COMPLETED = "completed"
PROCESSING_STATUS_FAILED = "failed"
PROCESSING_STATUS_DEAD_LETTERED = "dead_lettered"
EVENT_PROCESSING_STATUSES = frozenset(
    {
        PROCESSING_STATUS_PENDING,
        PROCESSING_STATUS_PROCESSING,
        PROCESSING_STATUS_COMPLETED,
        PROCESSING_STATUS_FAILED,
        PROCESSING_STATUS_DEAD_LETTERED,
    }
)

MAX_DELIVERY_ATTEMPTS = 3

# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------
WORKFLOW_STATUS_DRAFT = "draft"
WORKFLOW_STATUS_ACTIVE = "active"
WORKFLOW_STATUS_PAUSED = "paused"
WORKFLOW_STATUS_ARCHIVED = "archived"
WORKFLOW_STATUSES = frozenset(
    {
        WORKFLOW_STATUS_DRAFT,
        WORKFLOW_STATUS_ACTIVE,
        WORKFLOW_STATUS_PAUSED,
        WORKFLOW_STATUS_ARCHIVED,
    }
)

WORKFLOW_RUN_STATUS_RUNNING = "running"
WORKFLOW_RUN_STATUS_COMPLETED = "completed"
WORKFLOW_RUN_STATUS_FAILED = "failed"
WORKFLOW_RUN_STATUSES = frozenset(
    {
        WORKFLOW_RUN_STATUS_RUNNING,
        WORKFLOW_RUN_STATUS_COMPLETED,
        WORKFLOW_RUN_STATUS_FAILED,
    }
)

# Supported condition operators
CONDITION_OP_EQUALS = "equals"
CONDITION_OP_NOT_EQUALS = "not_equals"
CONDITION_OP_GREATER_THAN = "greater_than"
CONDITION_OP_LESS_THAN = "less_than"
CONDITION_OP_IN = "in"
CONDITION_OP_NOT_IN = "not_in"
CONDITION_OP_EXISTS = "exists"
CONDITION_OP_BOOLEAN = "boolean"
WORKFLOW_CONDITION_OPERATORS = frozenset(
    {
        CONDITION_OP_EQUALS,
        CONDITION_OP_NOT_EQUALS,
        CONDITION_OP_GREATER_THAN,
        CONDITION_OP_LESS_THAN,
        CONDITION_OP_IN,
        CONDITION_OP_NOT_IN,
        CONDITION_OP_EXISTS,
        CONDITION_OP_BOOLEAN,
    }
)

# Supported workflow actions (structured — no eval)
ACTION_PAUSE_ACCOUNT = "pause_account"
ACTION_RESUME_ACCOUNT = "resume_account"
ACTION_NOTIFY = "notify"
ACTION_UPDATE_POLICY = "update_policy"
ACTION_QUEUE_REVIEW = "queue_review"
ACTION_TRIGGER_WORKFLOW = "trigger_workflow"
WORKFLOW_ACTION_TYPES = frozenset(
    {
        ACTION_PAUSE_ACCOUNT,
        ACTION_RESUME_ACCOUNT,
        ACTION_NOTIFY,
        ACTION_UPDATE_POLICY,
        ACTION_QUEUE_REVIEW,
        ACTION_TRIGGER_WORKFLOW,
    }
)

# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------
EXPERIMENT_STATUS_DRAFT = "draft"
EXPERIMENT_STATUS_ACTIVE = "active"
EXPERIMENT_STATUS_PAUSED = "paused"
EXPERIMENT_STATUS_CONCLUDED = "concluded"
EXPERIMENT_STATUS_CANCELLED = "cancelled"
EXPERIMENT_STATUSES = frozenset(
    {
        EXPERIMENT_STATUS_DRAFT,
        EXPERIMENT_STATUS_ACTIVE,
        EXPERIMENT_STATUS_PAUSED,
        EXPERIMENT_STATUS_CONCLUDED,
        EXPERIMENT_STATUS_CANCELLED,
    }
)

# Statuses that are immutable (no further edits allowed)
EXPERIMENT_IMMUTABLE_STATUSES = frozenset(
    {
        EXPERIMENT_STATUS_ACTIVE,
        EXPERIMENT_STATUS_CONCLUDED,
        EXPERIMENT_STATUS_CANCELLED,
    }
)

VARIANT_TYPE_CONTROL = "control"
VARIANT_TYPE_TREATMENT = "treatment"
VARIANT_TYPES = frozenset({VARIANT_TYPE_CONTROL, VARIANT_TYPE_TREATMENT})

ASSIGNMENT_STATUS_ACTIVE = "active"
ASSIGNMENT_STATUS_EXCLUDED = "excluded"
ASSIGNMENT_STATUSES = frozenset({ASSIGNMENT_STATUS_ACTIVE, ASSIGNMENT_STATUS_EXCLUDED})

# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
OPERATION_STATUS_PENDING = "pending"
OPERATION_STATUS_RUNNING = "running"
OPERATION_STATUS_COMPLETED = "completed"
OPERATION_STATUS_FAILED = "failed"
OPERATION_STATUS_SUPERSEDED = "superseded"
OPERATION_STATUSES = frozenset(
    {
        OPERATION_STATUS_PENDING,
        OPERATION_STATUS_RUNNING,
        OPERATION_STATUS_COMPLETED,
        OPERATION_STATUS_FAILED,
        OPERATION_STATUS_SUPERSEDED,
    }
)

# ---------------------------------------------------------------------------
# Costs
# ---------------------------------------------------------------------------
COST_UNIT_USD = "usd"
COST_UNIT_TOKENS = "tokens"
COST_UNIT_CHARACTERS = "characters"
COST_UNIT_REQUESTS = "requests"
COST_UNITS = frozenset({COST_UNIT_USD, COST_UNIT_TOKENS, COST_UNIT_CHARACTERS, COST_UNIT_REQUESTS})

BUDGET_SCOPE_WORKSPACE = "workspace"
BUDGET_SCOPE_CHANNEL = "channel"
BUDGET_SCOPE_PLATFORM_ACCOUNT = "platform_account"
BUDGET_SCOPES = frozenset(
    {BUDGET_SCOPE_WORKSPACE, BUDGET_SCOPE_CHANNEL, BUDGET_SCOPE_PLATFORM_ACCOUNT}
)

BUDGET_PERIOD_DAILY = "daily"
BUDGET_PERIOD_WEEKLY = "weekly"
BUDGET_PERIOD_MONTHLY = "monthly"
BUDGET_PERIODS = frozenset({BUDGET_PERIOD_DAILY, BUDGET_PERIOD_WEEKLY, BUDGET_PERIOD_MONTHLY})

BUDGET_ACTION_WARN = "warn"
BUDGET_ACTION_PAUSE = "pause"
BUDGET_ACTION_BLOCK = "block"
BUDGET_ACTIONS = frozenset({BUDGET_ACTION_WARN, BUDGET_ACTION_PAUSE, BUDGET_ACTION_BLOCK})

# Fraction of limit that triggers a warning event
BUDGET_WARNING_THRESHOLD = 0.8

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
HEALTH_STATUS_HEALTHY = "healthy"
HEALTH_STATUS_DEGRADED = "degraded"
HEALTH_STATUS_UNAVAILABLE = "unavailable"
HEALTH_STATUS_CREDENTIAL_EXPIRED = "credential_expired"
HEALTH_STATUS_QUOTA_LIMITED = "quota_limited"
HEALTH_STATUS_PAUSED = "paused"
HEALTH_STATUS_FAILED = "failed"
HEALTH_STATUSES = frozenset(
    {
        HEALTH_STATUS_HEALTHY,
        HEALTH_STATUS_DEGRADED,
        HEALTH_STATUS_UNAVAILABLE,
        HEALTH_STATUS_CREDENTIAL_EXPIRED,
        HEALTH_STATUS_QUOTA_LIMITED,
        HEALTH_STATUS_PAUSED,
        HEALTH_STATUS_FAILED,
    }
)

ENTITY_TYPE_WORKSPACE = "workspace"
ENTITY_TYPE_CHANNEL = "channel"
ENTITY_TYPE_PLATFORM_ACCOUNT = "platform_account"
ENTITY_TYPE_PROVIDER = "provider"
ENTITY_TYPE_ENGINE = "engine"
ENTITY_TYPE_WORKFLOW = "workflow"
ENTITY_TYPE_CREDENTIAL_PROFILE = "credential_profile"
HEALTH_ENTITY_TYPES = frozenset(
    {
        ENTITY_TYPE_WORKSPACE,
        ENTITY_TYPE_CHANNEL,
        ENTITY_TYPE_PLATFORM_ACCOUNT,
        ENTITY_TYPE_PROVIDER,
        ENTITY_TYPE_ENGINE,
        ENTITY_TYPE_WORKFLOW,
        ENTITY_TYPE_CREDENTIAL_PROFILE,
    }
)

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------
PROVIDER_DOMAIN_AI = "ai"
PROVIDER_DOMAIN_TTS = "tts"
PROVIDER_DOMAIN_PUBLISHING = "publishing"
PROVIDER_DOMAIN_ANALYTICS = "analytics"
PROVIDER_DOMAIN_ASSET = "asset"
PROVIDER_DOMAIN_STORAGE = "storage"
PROVIDER_DOMAIN_NOTIFICATION = "notification"
PROVIDER_DOMAINS = frozenset(
    {
        PROVIDER_DOMAIN_AI,
        PROVIDER_DOMAIN_TTS,
        PROVIDER_DOMAIN_PUBLISHING,
        PROVIDER_DOMAIN_ANALYTICS,
        PROVIDER_DOMAIN_ASSET,
        PROVIDER_DOMAIN_STORAGE,
        PROVIDER_DOMAIN_NOTIFICATION,
    }
)

PROVIDER_STATUS_ACTIVE = "active"
PROVIDER_STATUS_DEGRADED = "degraded"
PROVIDER_STATUS_INACTIVE = "inactive"
PROVIDER_STATUSES = frozenset(
    {PROVIDER_STATUS_ACTIVE, PROVIDER_STATUS_DEGRADED, PROVIDER_STATUS_INACTIVE}
)

# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------
REVIEW_ITEM_TYPE_WORKFLOW_EXCEPTION = "workflow_exception"
REVIEW_ITEM_TYPE_BUDGET_EXCEEDED = "budget_exceeded"
REVIEW_ITEM_TYPE_HEALTH_DEGRADED = "health_degraded"
REVIEW_ITEM_TYPE_CREDENTIAL_EXPIRING = "credential_expiring"
REVIEW_ITEM_TYPE_OPERATION_FAILED = "operation_failed"
REVIEW_ITEM_TYPES = frozenset(
    {
        REVIEW_ITEM_TYPE_WORKFLOW_EXCEPTION,
        REVIEW_ITEM_TYPE_BUDGET_EXCEEDED,
        REVIEW_ITEM_TYPE_HEALTH_DEGRADED,
        REVIEW_ITEM_TYPE_CREDENTIAL_EXPIRING,
        REVIEW_ITEM_TYPE_OPERATION_FAILED,
    }
)

REVIEW_STATUS_OPEN = "open"
REVIEW_STATUS_ACKNOWLEDGED = "acknowledged"
REVIEW_STATUS_RESOLVED = "resolved"
REVIEW_STATUSES = frozenset(
    {REVIEW_STATUS_OPEN, REVIEW_STATUS_ACKNOWLEDGED, REVIEW_STATUS_RESOLVED}
)
