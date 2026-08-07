"""Phase 12 Control Plane models."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Frozen Pydantic records (read from DB)
# ---------------------------------------------------------------------------


class Organization(BaseModel, frozen=True):
    id: str
    name: str
    slug: str
    actor: str
    created_at: datetime
    updated_at: datetime
    owner_email: str | None = None


class Workspace(BaseModel, frozen=True):
    id: str
    name: str
    slug: str
    status: str
    actor: str
    created_at: datetime
    updated_at: datetime
    metadata_json: str | None = None
    organization_id: str | None = None

    @property
    def metadata(self) -> dict[str, Any]:
        if not self.metadata_json:
            return {}
        return json.loads(self.metadata_json)


class Channel(BaseModel, frozen=True):
    id: str
    workspace_id: str
    name: str
    slug: str
    status: str
    actor: str
    created_at: datetime
    updated_at: datetime
    description: str | None = None
    metadata_json: str | None = None


class Platform(BaseModel, frozen=True):
    id: str
    platform_key: str
    display_name: str
    is_active: bool
    capabilities_json: str | None = None
    created_at: datetime

    @property
    def capabilities(self) -> list[str]:
        if not self.capabilities_json:
            return []
        return json.loads(self.capabilities_json)


class PlatformAccount(BaseModel, frozen=True):
    id: str
    channel_id: str
    platform_id: str
    platform_key: str
    external_account_id: str
    display_name: str
    status: str
    credential_profile_id: str | None
    actor: str
    created_at: datetime
    updated_at: datetime
    metadata_json: str | None = None


class CredentialProfile(BaseModel, frozen=True):
    """Safe credential metadata only. No secrets are stored here."""

    id: str
    workspace_id: str
    display_name: str
    credential_type: str
    status: str
    external_ref: str
    actor: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None


class AutomationPolicy(BaseModel, frozen=True):
    id: str
    scope: str
    scope_id: str
    automation_level: str
    allowed_actions_json: str
    actor: str
    created_at: datetime
    is_active: bool

    @property
    def allowed_actions(self) -> list[str]:
        return json.loads(self.allowed_actions_json)


class StrategyProfile(BaseModel, frozen=True):
    id: str
    channel_id: str
    version: int
    config_json: str
    actor: str
    created_at: datetime
    is_active: bool

    @property
    def config(self) -> dict[str, Any]:
        return json.loads(self.config_json)


class PublishingProfile(BaseModel, frozen=True):
    id: str
    platform_account_id: str
    config_json: str
    is_active: bool
    actor: str
    created_at: datetime
    updated_at: datetime

    @property
    def config(self) -> dict[str, Any]:
        return json.loads(self.config_json)


class AnalyticsIdentity(BaseModel, frozen=True):
    id: str
    platform_account_id: str
    analytics_provider_key: str
    analytics_account_id: str
    created_at: datetime
    metadata_json: str | None = None


class ControlEvent(BaseModel, frozen=True):
    id: str
    event_type: str
    workspace_id: str
    actor: str
    payload_json: str
    correlation_id: str | None
    causation_id: str | None
    created_at: datetime
    channel_id: str | None = None
    platform_account_id: str | None = None
    source_engine: str | None = None
    source_entity_id: str | None = None
    schema_version: str = "1"
    experiment_id: str | None = None

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json)


class EventProcessing(BaseModel, frozen=True):
    id: str
    event_id: str
    handler_key: str
    status: str
    attempt_count: int
    last_attempt_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    created_at: datetime


class WorkflowCondition(BaseModel, frozen=True):
    field: str
    operator: str
    value: Any


class WorkflowAction(BaseModel, frozen=True):
    action_type: str
    params_json: str

    @property
    def params(self) -> dict[str, Any]:
        return json.loads(self.params_json)


class Workflow(BaseModel, frozen=True):
    id: str
    workspace_id: str
    name: str
    trigger_event_type: str
    conditions_json: str
    actions_json: str
    status: str
    actor: str
    created_at: datetime
    updated_at: datetime

    @property
    def conditions(self) -> list[dict[str, Any]]:
        return json.loads(self.conditions_json)

    @property
    def actions(self) -> list[dict[str, Any]]:
        return json.loads(self.actions_json)


class WorkflowRun(BaseModel, frozen=True):
    id: str
    workflow_id: str
    trigger_event_id: str
    status: str
    result_json: str | None
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None


class Experiment(BaseModel, frozen=True):
    id: str
    workspace_id: str
    channel_id: str
    name: str
    hypothesis: str
    status: str
    primary_metric: str
    actor: str
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None
    concluded_at: datetime | None
    secondary_metrics_json: str | None = None
    guardrails_json: str | None = None
    min_sample_size: int | None = None

    @property
    def secondary_metrics(self) -> list[str]:
        if not self.secondary_metrics_json:
            return []
        return json.loads(self.secondary_metrics_json)

    @property
    def guardrails(self) -> list[dict[str, Any]]:
        if not self.guardrails_json:
            return []
        return json.loads(self.guardrails_json)


class ExperimentVariant(BaseModel, frozen=True):
    id: str
    experiment_id: str
    name: str
    variant_type: str
    description: str | None
    config_json: str | None
    created_at: datetime


class ExperimentAssignment(BaseModel, frozen=True):
    id: str
    experiment_id: str
    variant_id: str
    unit_id: str
    status: str
    assigned_at: datetime


class OperationExecution(BaseModel, frozen=True):
    id: str
    operation_type: str
    workspace_id: str
    channel_id: str | None
    platform_account_id: str | None
    idempotency_key: str
    status: str
    actor: str
    correlation_id: str | None
    source_event_id: str | None
    input_json: str | None
    output_json: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    engine: str | None = None
    attempt_count: int = 1
    target_entity_id: str | None = None
    target_entity_type: str | None = None
    error_category: str | None = None


class CostRecord(BaseModel, frozen=True):
    id: str
    workspace_id: str
    channel_id: str | None
    platform_account_id: str | None
    operation_execution_id: str | None
    provider_key: str
    cost_unit: str
    quantity: float
    usd_equivalent: float
    description: str | None
    recorded_at: datetime
    engine: str | None = None
    experiment_id: str | None = None
    entity_id: str | None = None
    entity_type: str | None = None


class BudgetPolicy(BaseModel, frozen=True):
    id: str
    scope: str
    scope_id: str
    period: str
    limit_usd: float
    warning_threshold: float
    on_exceed_action: str
    actor: str
    created_at: datetime
    is_active: bool


class HealthRecord(BaseModel, frozen=True):
    id: str
    entity_type: str
    entity_id: str
    status: str
    detail: str | None
    recorded_by: str
    recorded_at: datetime


class ProviderRegistryEntry(BaseModel, frozen=True):
    id: str
    provider_key: str
    domain: str
    display_name: str
    status: str
    capabilities_json: str | None
    registered_at: datetime
    updated_at: datetime
    quota_json: str | None = None
    cost_metadata_json: str | None = None
    version_info: str | None = None

    @property
    def capabilities(self) -> list[str]:
        if not self.capabilities_json:
            return []
        return json.loads(self.capabilities_json)

    @property
    def quota(self) -> dict[str, Any]:
        if not self.quota_json:
            return {}
        return json.loads(self.quota_json)

    @property
    def cost_metadata(self) -> dict[str, Any]:
        if not self.cost_metadata_json:
            return {}
        return json.loads(self.cost_metadata_json)


# ---------------------------------------------------------------------------
# Mutable draft dataclasses (used when creating new records)
# ---------------------------------------------------------------------------


@dataclass
class OrganizationDraft:
    id: str
    name: str
    slug: str
    actor: str
    owner_email: str | None = None


@dataclass
class WorkspaceDraft:
    id: str
    name: str
    slug: str
    actor: str
    status: str = "active"
    metadata_json: str | None = None
    organization_id: str | None = None


@dataclass
class ChannelDraft:
    id: str
    workspace_id: str
    name: str
    slug: str
    actor: str
    status: str = "active"
    description: str | None = None
    metadata_json: str | None = None


@dataclass
class PlatformAccountDraft:
    id: str
    channel_id: str
    platform_id: str
    platform_key: str
    external_account_id: str
    display_name: str
    actor: str
    status: str = "connected"
    credential_profile_id: str | None = None
    metadata_json: str | None = None


@dataclass
class CredentialProfileDraft:
    id: str
    workspace_id: str
    display_name: str
    credential_type: str
    external_ref: str
    actor: str
    status: str = "active"
    expires_at: datetime | None = None


@dataclass
class AutomationPolicyDraft:
    id: str
    scope: str
    scope_id: str
    automation_level: str
    allowed_actions: list[str]
    actor: str


@dataclass
class StrategyProfileDraft:
    id: str
    channel_id: str
    version: int
    config: dict[str, Any]
    actor: str


@dataclass
class PublishingProfileDraft:
    id: str
    platform_account_id: str
    actor: str
    config: dict[str, Any] = field(default_factory=dict)
    is_active: bool = True


@dataclass
class AnalyticsIdentityDraft:
    id: str
    platform_account_id: str
    analytics_provider_key: str
    analytics_account_id: str
    metadata: dict[str, Any] | None = None


@dataclass
class ControlEventDraft:
    id: str
    event_type: str
    workspace_id: str
    actor: str
    payload: dict[str, Any]
    correlation_id: str | None = None
    causation_id: str | None = None
    channel_id: str | None = None
    platform_account_id: str | None = None
    source_engine: str | None = None
    source_entity_id: str | None = None
    experiment_id: str | None = None


@dataclass
class WorkflowDraft:
    id: str
    workspace_id: str
    name: str
    trigger_event_type: str
    conditions: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    actor: str
    status: str = "draft"


@dataclass
class ExperimentDraft:
    id: str
    workspace_id: str
    channel_id: str
    name: str
    hypothesis: str
    primary_metric: str
    actor: str
    status: str = "draft"
    secondary_metrics: list[str] = field(default_factory=list)
    guardrails: list[dict[str, Any]] = field(default_factory=list)
    min_sample_size: int | None = None


@dataclass
class ExperimentVariantDraft:
    id: str
    experiment_id: str
    name: str
    variant_type: str
    description: str | None = None
    config: dict[str, Any] | None = None


@dataclass
class OperationExecutionDraft:
    id: str
    operation_type: str
    workspace_id: str
    idempotency_key: str
    actor: str
    channel_id: str | None = None
    platform_account_id: str | None = None
    correlation_id: str | None = None
    source_event_id: str | None = None
    input_data: dict[str, Any] | None = None
    engine: str | None = None
    target_entity_id: str | None = None
    target_entity_type: str | None = None


@dataclass
class CostRecordDraft:
    id: str
    workspace_id: str
    provider_key: str
    cost_unit: str
    quantity: float
    usd_equivalent: float
    channel_id: str | None = None
    platform_account_id: str | None = None
    operation_execution_id: str | None = None
    description: str | None = None
    engine: str | None = None
    experiment_id: str | None = None
    entity_id: str | None = None
    entity_type: str | None = None


@dataclass
class BudgetPolicyDraft:
    id: str
    scope: str
    scope_id: str
    period: str
    limit_usd: float
    actor: str
    warning_threshold: float = 0.8
    on_exceed_action: str = "warn"


@dataclass
class HealthRecordDraft:
    id: str
    entity_type: str
    entity_id: str
    status: str
    recorded_by: str
    detail: str | None = None


@dataclass
class ProviderRegistryDraft:
    id: str
    provider_key: str
    domain: str
    display_name: str
    capabilities: list[str] = field(default_factory=list)
    status: str = "active"
    quota: dict[str, Any] | None = None
    cost_metadata: dict[str, Any] | None = None
    version_info: str | None = None


# ---------------------------------------------------------------------------
# Structured workflow evaluation helpers
# ---------------------------------------------------------------------------


@dataclass
class WorkflowEvaluationResult:
    workflow_id: str
    matched: bool
    conditions_passed: list[str]
    conditions_failed: list[str]
    actions_to_execute: list[dict[str, Any]]


@dataclass
class WorkflowRunResult:
    run_id: str
    workflow_id: str
    success: bool
    actions_executed: list[str]
    error_message: str | None = None
