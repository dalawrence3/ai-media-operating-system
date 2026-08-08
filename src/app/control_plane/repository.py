"""Phase 12 Control Plane database operations."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.control_plane.constants import (
    ACCOUNT_STATUS_PAUSED,
    PROCESSING_STATUS_PENDING,
    WORKFLOW_RUN_STATUS_RUNNING,
)
from app.control_plane.errors import (
    ChannelNotFoundError,
    CredentialProfileNotFoundError,
    DuplicateIdempotencyKeyError,
    ExperimentNotFoundError,
    OperationNotFoundError,
    PlatformAccountNotFoundError,
    PlatformNotFoundError,
    ProviderNotFoundError,
    WorkflowNotFoundError,
    WorkflowRunNotFoundError,
    WorkspaceNotFoundError,
)
from app.control_plane.models import (
    AnalyticsIdentity,
    AnalyticsIdentityDraft,
    AutomationPolicy,
    AutomationPolicyDraft,
    BudgetPolicy,
    BudgetPolicyDraft,
    Channel,
    ChannelDraft,
    ControlEvent,
    ControlEventDraft,
    CostRecord,
    CostRecordDraft,
    CredentialProfile,
    CredentialProfileDraft,
    EventProcessing,
    Experiment,
    ExperimentAssignment,
    ExperimentDraft,
    ExperimentVariant,
    ExperimentVariantDraft,
    HealthRecord,
    HealthRecordDraft,
    OperationExecution,
    OperationExecutionDraft,
    Organization,
    OrganizationDraft,
    Platform,
    PlatformAccount,
    PlatformAccountDraft,
    ProviderRegistryDraft,
    ProviderRegistryEntry,
    PublishingProfile,
    PublishingProfileDraft,
    StrategyProfile,
    StrategyProfileDraft,
    Workflow,
    WorkflowDraft,
    WorkflowRun,
    Workspace,
    WorkspaceDraft,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _row_to_organization(row: Any) -> Organization:
    return Organization(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        actor=row["actor"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        owner_email=row["owner_email"],
    )


def _row_to_workspace(row: Any) -> Workspace:
    return Workspace(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        status=row["status"],
        actor=row["actor"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        metadata_json=row["metadata_json"],
        organization_id=row["organization_id"],
    )


def _row_to_channel(row: Any) -> Channel:
    return Channel(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        slug=row["slug"],
        status=row["status"],
        actor=row["actor"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        description=row["description"],
        metadata_json=row["metadata_json"],
    )


def _row_to_platform(row: Any) -> Platform:
    return Platform(
        id=row["id"],
        platform_key=row["platform_key"],
        display_name=row["display_name"],
        is_active=bool(row["is_active"]),
        capabilities_json=row["capabilities_json"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_platform_account(row: Any) -> PlatformAccount:
    return PlatformAccount(
        id=row["id"],
        channel_id=row["channel_id"],
        platform_id=row["platform_id"],
        platform_key=row["platform_key"],
        external_account_id=row["external_account_id"],
        display_name=row["display_name"],
        status=row["status"],
        credential_profile_id=row["credential_profile_id"],
        actor=row["actor"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        metadata_json=row["metadata_json"],
    )


def _row_to_credential_profile(row: Any) -> CredentialProfile:
    return CredentialProfile(
        id=row["id"],
        workspace_id=row["workspace_id"],
        display_name=row["display_name"],
        credential_type=row["credential_type"],
        status=row["status"],
        external_ref=row["external_ref"],
        actor=row["actor"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        expires_at=(datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None),
    )


def _row_to_automation_policy(row: Any) -> AutomationPolicy:
    return AutomationPolicy(
        id=row["id"],
        scope=row["scope"],
        scope_id=row["scope_id"],
        automation_level=row["automation_level"],
        allowed_actions_json=row["allowed_actions_json"],
        actor=row["actor"],
        created_at=datetime.fromisoformat(row["created_at"]),
        is_active=bool(row["is_active"]),
    )


def _row_to_strategy_profile(row: Any) -> StrategyProfile:
    return StrategyProfile(
        id=row["id"],
        channel_id=row["channel_id"],
        version=row["version"],
        config_json=row["config_json"],
        actor=row["actor"],
        created_at=datetime.fromisoformat(row["created_at"]),
        is_active=bool(row["is_active"]),
    )


def _row_to_publishing_profile(row: Any) -> PublishingProfile:
    return PublishingProfile(
        id=row["id"],
        platform_account_id=row["platform_account_id"],
        config_json=row["config_json"],
        is_active=bool(row["is_active"]),
        actor=row["actor"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_analytics_identity(row: Any) -> AnalyticsIdentity:
    return AnalyticsIdentity(
        id=row["id"],
        platform_account_id=row["platform_account_id"],
        analytics_provider_key=row["analytics_provider_key"],
        analytics_account_id=row["analytics_account_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        metadata_json=row["metadata_json"],
    )


def _row_to_control_event(row: Any) -> ControlEvent:
    return ControlEvent(
        id=row["id"],
        event_type=row["event_type"],
        workspace_id=row["workspace_id"],
        actor=row["actor"],
        payload_json=row["payload_json"],
        correlation_id=row["correlation_id"],
        causation_id=row["causation_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        channel_id=row["channel_id"],
        platform_account_id=row["platform_account_id"],
        source_engine=row["source_engine"],
        source_entity_id=row["source_entity_id"],
        schema_version=row["schema_version"] or "1",
        experiment_id=row["experiment_id"],
    )


def _row_to_event_processing(row: Any) -> EventProcessing:
    return EventProcessing(
        id=row["id"],
        event_id=row["event_id"],
        handler_key=row["handler_key"],
        status=row["status"],
        attempt_count=row["attempt_count"],
        last_attempt_at=(
            datetime.fromisoformat(row["last_attempt_at"]) if row["last_attempt_at"] else None
        ),
        completed_at=(datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None),
        error_message=row["error_message"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_workflow(row: Any) -> Workflow:
    return Workflow(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        trigger_event_type=row["trigger_event_type"],
        conditions_json=row["conditions_json"],
        actions_json=row["actions_json"],
        status=row["status"],
        actor=row["actor"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_workflow_run(row: Any) -> WorkflowRun:
    return WorkflowRun(
        id=row["id"],
        workflow_id=row["workflow_id"],
        trigger_event_id=row["trigger_event_id"],
        status=row["status"],
        result_json=row["result_json"],
        error_message=row["error_message"],
        started_at=datetime.fromisoformat(row["started_at"]),
        completed_at=(datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None),
    )


def _row_to_experiment(row: Any) -> Experiment:
    return Experiment(
        id=row["id"],
        workspace_id=row["workspace_id"],
        channel_id=row["channel_id"],
        name=row["name"],
        hypothesis=row["hypothesis"],
        status=row["status"],
        primary_metric=row["primary_metric"],
        actor=row["actor"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        activated_at=(datetime.fromisoformat(row["activated_at"]) if row["activated_at"] else None),
        concluded_at=(datetime.fromisoformat(row["concluded_at"]) if row["concluded_at"] else None),
        secondary_metrics_json=row["secondary_metrics_json"],
        guardrails_json=row["guardrails_json"],
        min_sample_size=row["min_sample_size"],
    )


def _row_to_experiment_variant(row: Any) -> ExperimentVariant:
    return ExperimentVariant(
        id=row["id"],
        experiment_id=row["experiment_id"],
        name=row["name"],
        variant_type=row["variant_type"],
        description=row["description"],
        config_json=row["config_json"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_experiment_assignment(row: Any) -> ExperimentAssignment:
    return ExperimentAssignment(
        id=row["id"],
        experiment_id=row["experiment_id"],
        variant_id=row["variant_id"],
        unit_id=row["unit_id"],
        status=row["status"],
        assigned_at=datetime.fromisoformat(row["assigned_at"]),
    )


def _row_to_operation_execution(row: Any) -> OperationExecution:
    return OperationExecution(
        id=row["id"],
        operation_type=row["operation_type"],
        workspace_id=row["workspace_id"],
        channel_id=row["channel_id"],
        platform_account_id=row["platform_account_id"],
        idempotency_key=row["idempotency_key"],
        status=row["status"],
        actor=row["actor"],
        correlation_id=row["correlation_id"],
        source_event_id=row["source_event_id"],
        input_json=row["input_json"],
        output_json=row["output_json"],
        error_message=row["error_message"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        engine=row["engine"],
        attempt_count=row["attempt_count"] if row["attempt_count"] is not None else 1,
        target_entity_id=row["target_entity_id"],
        target_entity_type=row["target_entity_type"],
        error_category=row["error_category"],
    )


def _row_to_cost_record(row: Any) -> CostRecord:
    return CostRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        channel_id=row["channel_id"],
        platform_account_id=row["platform_account_id"],
        operation_execution_id=row["operation_execution_id"],
        provider_key=row["provider_key"],
        cost_unit=row["cost_unit"],
        quantity=row["quantity"],
        usd_equivalent=row["usd_equivalent"],
        description=row["description"],
        recorded_at=datetime.fromisoformat(row["recorded_at"]),
        engine=row["engine"],
        experiment_id=row["experiment_id"],
        entity_id=row["entity_id"],
        entity_type=row["entity_type"],
    )


def _row_to_budget_policy(row: Any) -> BudgetPolicy:
    return BudgetPolicy(
        id=row["id"],
        scope=row["scope"],
        scope_id=row["scope_id"],
        period=row["period"],
        limit_usd=row["limit_usd"],
        warning_threshold=row["warning_threshold"],
        on_exceed_action=row["on_exceed_action"],
        actor=row["actor"],
        created_at=datetime.fromisoformat(row["created_at"]),
        is_active=bool(row["is_active"]),
    )


def _row_to_health_record(row: Any) -> HealthRecord:
    return HealthRecord(
        id=row["id"],
        entity_type=row["entity_type"],
        entity_id=row["entity_id"],
        status=row["status"],
        detail=row["detail"],
        recorded_by=row["recorded_by"],
        recorded_at=datetime.fromisoformat(row["recorded_at"]),
    )


def _row_to_provider_entry(row: Any) -> ProviderRegistryEntry:
    return ProviderRegistryEntry(
        id=row["id"],
        provider_key=row["provider_key"],
        domain=row["domain"],
        display_name=row["display_name"],
        status=row["status"],
        capabilities_json=row["capabilities_json"],
        registered_at=datetime.fromisoformat(row["registered_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        quota_json=row["quota_json"],
        cost_metadata_json=row["cost_metadata_json"],
        version_info=row["version_info"],
    )


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


def create_organization(conn: Any, draft: OrganizationDraft) -> Organization:
    now = _now().isoformat()
    conn.execute(
        """
        INSERT INTO cp_organizations
            (id, name, slug, owner_email, actor, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (draft.id, draft.name, draft.slug, draft.owner_email, draft.actor, now, now),
    )
    row = conn.execute("SELECT * FROM cp_organizations WHERE id = ?", (draft.id,)).fetchone()
    return _row_to_organization(row)


def get_organization(conn: Any, organization_id: str) -> Organization:
    row = conn.execute("SELECT * FROM cp_organizations WHERE id = ?", (organization_id,)).fetchone()
    if not row:
        raise Exception(f"Organization not found: {organization_id}")
    return _row_to_organization(row)


def list_organizations(conn: Any) -> list[Organization]:
    rows = conn.execute("SELECT * FROM cp_organizations ORDER BY created_at ASC").fetchall()
    return [_row_to_organization(r) for r in rows]


def create_workspace(conn: Any, draft: WorkspaceDraft) -> Workspace:
    now = _now().isoformat()
    conn.execute(
        """
        INSERT INTO cp_workspaces
            (id, name, slug, status, actor, metadata_json, created_at, updated_at,
             organization_id)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            draft.id,
            draft.name,
            draft.slug,
            draft.status,
            draft.actor,
            draft.metadata_json,
            now,
            now,
            draft.organization_id,
        ),
    )
    return get_workspace(conn, draft.id)


def get_workspace(conn: Any, workspace_id: str) -> Workspace:
    row = conn.execute("SELECT * FROM cp_workspaces WHERE id = ?", (workspace_id,)).fetchone()
    if not row:
        raise WorkspaceNotFoundError(workspace_id)
    return _row_to_workspace(row)


def list_workspaces(conn: Any, status: str | None = None) -> list[Workspace]:
    if status:
        rows = conn.execute(
            "SELECT * FROM cp_workspaces WHERE status = ? ORDER BY created_at ASC", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM cp_workspaces ORDER BY created_at ASC").fetchall()
    return [_row_to_workspace(r) for r in rows]


def update_workspace_status(conn: Any, workspace_id: str, status: str, actor: str) -> Workspace:
    now = _now().isoformat()
    conn.execute(
        "UPDATE cp_workspaces SET status = ?, actor = ?, updated_at = ? WHERE id = ?",
        (status, actor, now, workspace_id),
    )
    return get_workspace(conn, workspace_id)


# ---------------------------------------------------------------------------
# Channel
# ---------------------------------------------------------------------------


def create_channel(conn: Any, draft: ChannelDraft) -> Channel:
    now = _now().isoformat()
    conn.execute(
        """
        INSERT INTO cp_channels
            (id, workspace_id, name, slug, status, actor, description, metadata_json,
             created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            draft.id,
            draft.workspace_id,
            draft.name,
            draft.slug,
            draft.status,
            draft.actor,
            draft.description,
            draft.metadata_json,
            now,
            now,
        ),
    )
    return get_channel(conn, draft.id)


def get_channel(conn: Any, channel_id: str) -> Channel:
    row = conn.execute("SELECT * FROM cp_channels WHERE id = ?", (channel_id,)).fetchone()
    if not row:
        raise ChannelNotFoundError(channel_id)
    return _row_to_channel(row)


def list_channels_by_workspace(
    conn: Any, workspace_id: str, status: str | None = None
) -> list[Channel]:
    if status:
        rows = conn.execute(
            "SELECT * FROM cp_channels "
            "WHERE workspace_id = ? AND status = ? ORDER BY created_at ASC",
            (workspace_id, status),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM cp_channels WHERE workspace_id = ? ORDER BY created_at ASC",
            (workspace_id,),
        ).fetchall()
    return [_row_to_channel(r) for r in rows]


def update_channel_status(conn: Any, channel_id: str, status: str, actor: str) -> Channel:
    now = _now().isoformat()
    conn.execute(
        "UPDATE cp_channels SET status = ?, actor = ?, updated_at = ? WHERE id = ?",
        (status, actor, now, channel_id),
    )
    return get_channel(conn, channel_id)


# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------


def get_platform_by_key(conn: Any, platform_key: str) -> Platform:
    row = conn.execute(
        "SELECT * FROM cp_platforms WHERE platform_key = ?", (platform_key,)
    ).fetchone()
    if not row:
        raise PlatformNotFoundError(platform_key)
    return _row_to_platform(row)


def list_platforms(conn: Any) -> list[Platform]:
    rows = conn.execute("SELECT * FROM cp_platforms ORDER BY platform_key ASC").fetchall()
    return [_row_to_platform(r) for r in rows]


def ensure_platform(conn: Any, platform_id: str, platform_key: str, display_name: str) -> None:
    existing = conn.execute(
        "SELECT id FROM cp_platforms WHERE platform_key = ?", (platform_key,)
    ).fetchone()
    if not existing:
        now = _now().isoformat()
        conn.execute(
            "INSERT INTO cp_platforms (id, platform_key, display_name, is_active, created_at) "
            "VALUES (?,?,?,1,?)",
            (platform_id, platform_key, display_name, now),
        )


# ---------------------------------------------------------------------------
# Platform Account
# ---------------------------------------------------------------------------


def create_platform_account(conn: Any, draft: PlatformAccountDraft) -> PlatformAccount:
    now = _now().isoformat()
    conn.execute(
        """
        INSERT INTO cp_platform_accounts
            (id, channel_id, platform_id, platform_key, external_account_id,
             display_name, status, credential_profile_id, actor, metadata_json,
             created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            draft.id,
            draft.channel_id,
            draft.platform_id,
            draft.platform_key,
            draft.external_account_id,
            draft.display_name,
            draft.status,
            draft.credential_profile_id,
            draft.actor,
            draft.metadata_json,
            now,
            now,
        ),
    )
    return get_platform_account(conn, draft.id)


def get_platform_account(conn: Any, account_id: str) -> PlatformAccount:
    row = conn.execute("SELECT * FROM cp_platform_accounts WHERE id = ?", (account_id,)).fetchone()
    if not row:
        raise PlatformAccountNotFoundError(account_id)
    return _row_to_platform_account(row)


def list_platform_accounts_by_channel(
    conn: Any, channel_id: str, status: str | None = None
) -> list[PlatformAccount]:
    if status:
        rows = conn.execute(
            "SELECT * FROM cp_platform_accounts WHERE channel_id = ? AND status = ? "
            "ORDER BY created_at ASC",
            (channel_id, status),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM cp_platform_accounts WHERE channel_id = ? ORDER BY created_at ASC",
            (channel_id,),
        ).fetchall()
    return [_row_to_platform_account(r) for r in rows]


def update_platform_account_status(
    conn: Any, account_id: str, status: str, actor: str
) -> PlatformAccount:
    now = _now().isoformat()
    conn.execute(
        "UPDATE cp_platform_accounts SET status = ?, actor = ?, updated_at = ? WHERE id = ?",
        (status, actor, now, account_id),
    )
    return get_platform_account(conn, account_id)


def pause_platform_account(conn: Any, account_id: str, actor: str) -> PlatformAccount:
    return update_platform_account_status(conn, account_id, ACCOUNT_STATUS_PAUSED, actor)


# ---------------------------------------------------------------------------
# Credential Profiles
# ---------------------------------------------------------------------------


def create_credential_profile(conn: Any, draft: CredentialProfileDraft) -> CredentialProfile:
    now = _now().isoformat()
    conn.execute(
        """
        INSERT INTO cp_credential_profiles
            (id, workspace_id, display_name, credential_type, status, external_ref,
             actor, expires_at, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            draft.id,
            draft.workspace_id,
            draft.display_name,
            draft.credential_type,
            draft.status,
            draft.external_ref,
            draft.actor,
            draft.expires_at.isoformat() if draft.expires_at else None,
            now,
            now,
        ),
    )
    return get_credential_profile(conn, draft.id)


def get_credential_profile(conn: Any, profile_id: str) -> CredentialProfile:
    row = conn.execute(
        "SELECT * FROM cp_credential_profiles WHERE id = ?", (profile_id,)
    ).fetchone()
    if not row:
        raise CredentialProfileNotFoundError(profile_id)
    return _row_to_credential_profile(row)


def list_credential_profiles(
    conn: Any, workspace_id: str, status: str | None = None
) -> list[CredentialProfile]:
    if status:
        rows = conn.execute(
            "SELECT * FROM cp_credential_profiles WHERE workspace_id = ? AND status = ? "
            "ORDER BY created_at ASC",
            (workspace_id, status),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM cp_credential_profiles WHERE workspace_id = ? ORDER BY created_at ASC",
            (workspace_id,),
        ).fetchall()
    return [_row_to_credential_profile(r) for r in rows]


def update_credential_status(
    conn: Any, profile_id: str, status: str, actor: str
) -> CredentialProfile:
    now = _now().isoformat()
    conn.execute(
        "UPDATE cp_credential_profiles SET status = ?, actor = ?, updated_at = ? WHERE id = ?",
        (status, actor, now, profile_id),
    )
    return get_credential_profile(conn, profile_id)


# ---------------------------------------------------------------------------
# Automation Policies
# ---------------------------------------------------------------------------


def create_automation_policy(conn: Any, draft: AutomationPolicyDraft) -> AutomationPolicy:
    now = _now().isoformat()
    conn.execute(
        "UPDATE cp_automation_policies SET is_active = 0 "
        "WHERE scope = ? AND scope_id = ? AND is_active = 1",
        (draft.scope, draft.scope_id),
    )
    conn.execute(
        """
        INSERT INTO cp_automation_policies
            (id, scope, scope_id, automation_level, allowed_actions_json, actor,
             created_at, is_active)
        VALUES (?,?,?,?,?,?,?,1)
        """,
        (
            draft.id,
            draft.scope,
            draft.scope_id,
            draft.automation_level,
            json.dumps(sorted(draft.allowed_actions)),
            draft.actor,
            now,
        ),
    )
    return get_automation_policy(conn, draft.id)


def get_automation_policy(conn: Any, policy_id: str) -> AutomationPolicy:
    row = conn.execute("SELECT * FROM cp_automation_policies WHERE id = ?", (policy_id,)).fetchone()
    if not row:
        raise Exception(f"Automation policy not found: {policy_id}")
    return _row_to_automation_policy(row)


def get_active_policy_for_scope(conn: Any, scope: str, scope_id: str) -> AutomationPolicy | None:
    row = conn.execute(
        "SELECT * FROM cp_automation_policies WHERE scope = ? AND scope_id = ? AND is_active = 1",
        (scope, scope_id),
    ).fetchone()
    if not row:
        return None
    return _row_to_automation_policy(row)


# ---------------------------------------------------------------------------
# Strategy Profiles
# ---------------------------------------------------------------------------


def create_strategy_profile(conn: Any, draft: StrategyProfileDraft) -> StrategyProfile:
    now = _now().isoformat()
    conn.execute(
        "UPDATE cp_strategy_profiles SET is_active = 0 WHERE channel_id = ? AND is_active = 1",
        (draft.channel_id,),
    )
    conn.execute(
        """
        INSERT INTO cp_strategy_profiles
            (id, channel_id, version, config_json, actor, created_at, is_active)
        VALUES (?,?,?,?,?,?,1)
        """,
        (
            draft.id,
            draft.channel_id,
            draft.version,
            json.dumps(draft.config, sort_keys=True),
            draft.actor,
            now,
        ),
    )
    return get_strategy_profile(conn, draft.id)


def get_strategy_profile(conn: Any, profile_id: str) -> StrategyProfile:
    row = conn.execute("SELECT * FROM cp_strategy_profiles WHERE id = ?", (profile_id,)).fetchone()
    if not row:
        raise Exception(f"Strategy profile not found: {profile_id}")
    return _row_to_strategy_profile(row)


def get_active_strategy_for_channel(conn: Any, channel_id: str) -> StrategyProfile | None:
    row = conn.execute(
        "SELECT * FROM cp_strategy_profiles WHERE channel_id = ? AND is_active = 1",
        (channel_id,),
    ).fetchone()
    if not row:
        return None
    return _row_to_strategy_profile(row)


def list_strategy_profiles_by_channel(conn: Any, channel_id: str) -> list[StrategyProfile]:
    rows = conn.execute(
        "SELECT * FROM cp_strategy_profiles WHERE channel_id = ? ORDER BY version DESC",
        (channel_id,),
    ).fetchall()
    return [_row_to_strategy_profile(r) for r in rows]


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def create_event(conn: Any, draft: ControlEventDraft) -> ControlEvent:
    now = _now().isoformat()
    conn.execute(
        """
        INSERT INTO cp_events
            (id, event_type, workspace_id, actor, payload_json,
             correlation_id, causation_id, created_at,
             channel_id, platform_account_id, source_engine, source_entity_id,
             schema_version, experiment_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            draft.id,
            draft.event_type,
            draft.workspace_id,
            draft.actor,
            json.dumps(draft.payload, sort_keys=True),
            draft.correlation_id,
            draft.causation_id,
            now,
            draft.channel_id,
            draft.platform_account_id,
            draft.source_engine,
            draft.source_entity_id,
            "1",
            draft.experiment_id,
        ),
    )
    return get_event(conn, draft.id)


def get_event(conn: Any, event_id: str) -> ControlEvent:
    row = conn.execute("SELECT * FROM cp_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        raise Exception(f"Event not found: {event_id}")
    return _row_to_control_event(row)


def list_events_by_workspace(
    conn: Any, workspace_id: str, event_type: str | None = None, limit: int = 100
) -> list[ControlEvent]:
    if event_type:
        rows = conn.execute(
            "SELECT * FROM cp_events WHERE workspace_id = ? AND event_type = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (workspace_id, event_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM cp_events WHERE workspace_id = ? ORDER BY created_at DESC LIMIT ?",
            (workspace_id, limit),
        ).fetchall()
    return [_row_to_control_event(r) for r in rows]


def create_event_processing(
    conn: Any, processing_id: str, event_id: str, handler_key: str
) -> EventProcessing:
    now = _now().isoformat()
    conn.execute(
        """
        INSERT INTO cp_event_processing
            (id, event_id, handler_key, status, attempt_count, created_at)
        VALUES (?,?,?,?,0,?)
        """,
        (processing_id, event_id, handler_key, PROCESSING_STATUS_PENDING, now),
    )
    row = conn.execute(
        "SELECT * FROM cp_event_processing WHERE id = ?", (processing_id,)
    ).fetchone()
    return _row_to_event_processing(row)


def update_event_processing_status(
    conn: Any,
    processing_id: str,
    status: str,
    error_message: str | None = None,
) -> EventProcessing:
    now = _now().isoformat()
    completed_at = now if status in ("completed", "dead_lettered") else None
    conn.execute(
        """
        UPDATE cp_event_processing
        SET status = ?, attempt_count = attempt_count + 1,
            last_attempt_at = ?, completed_at = ?, error_message = ?
        WHERE id = ?
        """,
        (status, now, completed_at, error_message, processing_id),
    )
    row = conn.execute(
        "SELECT * FROM cp_event_processing WHERE id = ?", (processing_id,)
    ).fetchone()
    return _row_to_event_processing(row)


def list_pending_event_processing(
    conn: Any, handler_key: str | None = None, limit: int = 50
) -> list[EventProcessing]:
    if handler_key:
        rows = conn.execute(
            "SELECT * FROM cp_event_processing WHERE status = 'pending' AND handler_key = ? "
            "ORDER BY created_at ASC LIMIT ?",
            (handler_key, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM cp_event_processing WHERE status = 'pending' "
            "ORDER BY created_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_event_processing(r) for r in rows]


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------


def create_workflow(conn: Any, draft: WorkflowDraft) -> Workflow:
    now = _now().isoformat()
    conn.execute(
        """
        INSERT INTO cp_workflows
            (id, workspace_id, name, trigger_event_type, conditions_json,
             actions_json, status, actor, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            draft.id,
            draft.workspace_id,
            draft.name,
            draft.trigger_event_type,
            json.dumps(draft.conditions, sort_keys=True),
            json.dumps(draft.actions, sort_keys=True),
            draft.status,
            draft.actor,
            now,
            now,
        ),
    )
    return get_workflow(conn, draft.id)


def get_workflow(conn: Any, workflow_id: str) -> Workflow:
    row = conn.execute("SELECT * FROM cp_workflows WHERE id = ?", (workflow_id,)).fetchone()
    if not row:
        raise WorkflowNotFoundError(workflow_id)
    return _row_to_workflow(row)


def list_workflows_by_workspace(
    conn: Any, workspace_id: str, status: str | None = None
) -> list[Workflow]:
    if status:
        rows = conn.execute(
            "SELECT * FROM cp_workflows WHERE workspace_id = ? AND status = ? "
            "ORDER BY created_at ASC",
            (workspace_id, status),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM cp_workflows WHERE workspace_id = ? ORDER BY created_at ASC",
            (workspace_id,),
        ).fetchall()
    return [_row_to_workflow(r) for r in rows]


def list_active_workflows_for_trigger(
    conn: Any, workspace_id: str, event_type: str
) -> list[Workflow]:
    rows = conn.execute(
        "SELECT * FROM cp_workflows WHERE workspace_id = ? AND trigger_event_type = ? "
        "AND status = 'active'",
        (workspace_id, event_type),
    ).fetchall()
    return [_row_to_workflow(r) for r in rows]


def update_workflow_status(conn: Any, workflow_id: str, status: str, actor: str) -> Workflow:
    now = _now().isoformat()
    conn.execute(
        "UPDATE cp_workflows SET status = ?, actor = ?, updated_at = ? WHERE id = ?",
        (status, actor, now, workflow_id),
    )
    return get_workflow(conn, workflow_id)


def create_workflow_run(
    conn: Any, run_id: str, workflow_id: str, trigger_event_id: str
) -> WorkflowRun:
    now = _now().isoformat()
    conn.execute(
        """
        INSERT INTO cp_workflow_runs
            (id, workflow_id, trigger_event_id, status, started_at)
        VALUES (?,?,?,?,?)
        """,
        (run_id, workflow_id, trigger_event_id, WORKFLOW_RUN_STATUS_RUNNING, now),
    )
    return get_workflow_run(conn, run_id)


def get_workflow_run(conn: Any, run_id: str) -> WorkflowRun:
    row = conn.execute("SELECT * FROM cp_workflow_runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        raise WorkflowRunNotFoundError(run_id)
    return _row_to_workflow_run(row)


def complete_workflow_run(
    conn: Any,
    run_id: str,
    success: bool,
    result: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> WorkflowRun:
    now = _now().isoformat()
    status = "completed" if success else "failed"
    conn.execute(
        """
        UPDATE cp_workflow_runs
        SET status = ?, result_json = ?, error_message = ?, completed_at = ?
        WHERE id = ?
        """,
        (status, json.dumps(result) if result else None, error_message, now, run_id),
    )
    return get_workflow_run(conn, run_id)


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------


def create_experiment(conn: Any, draft: ExperimentDraft) -> Experiment:
    now = _now().isoformat()
    conn.execute(
        """
        INSERT INTO cp_experiments
            (id, workspace_id, channel_id, name, hypothesis, status,
             primary_metric, actor, created_at, updated_at,
             secondary_metrics_json, guardrails_json, min_sample_size)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            draft.id,
            draft.workspace_id,
            draft.channel_id,
            draft.name,
            draft.hypothesis,
            draft.status,
            draft.primary_metric,
            draft.actor,
            now,
            now,
            json.dumps(draft.secondary_metrics) if draft.secondary_metrics else None,
            json.dumps(draft.guardrails) if draft.guardrails else None,
            draft.min_sample_size,
        ),
    )
    return get_experiment(conn, draft.id)


def get_experiment(conn: Any, experiment_id: str) -> Experiment:
    row = conn.execute("SELECT * FROM cp_experiments WHERE id = ?", (experiment_id,)).fetchone()
    if not row:
        raise ExperimentNotFoundError(experiment_id)
    return _row_to_experiment(row)


def activate_experiment(conn: Any, experiment_id: str) -> Experiment:
    now = _now().isoformat()
    conn.execute(
        "UPDATE cp_experiments SET status = 'active', activated_at = ?, updated_at = ? "
        "WHERE id = ?",
        (now, now, experiment_id),
    )
    return get_experiment(conn, experiment_id)


def conclude_experiment(conn: Any, experiment_id: str) -> Experiment:
    now = _now().isoformat()
    conn.execute(
        "UPDATE cp_experiments SET status = 'concluded', concluded_at = ?, updated_at = ? "
        "WHERE id = ?",
        (now, now, experiment_id),
    )
    return get_experiment(conn, experiment_id)


def cancel_experiment(conn: Any, experiment_id: str) -> Experiment:
    now = _now().isoformat()
    conn.execute(
        "UPDATE cp_experiments SET status = 'cancelled', updated_at = ? WHERE id = ?",
        (now, experiment_id),
    )
    return get_experiment(conn, experiment_id)


def list_experiments_by_workspace(
    conn: Any, workspace_id: str, status: str | None = None
) -> list[Experiment]:
    if status:
        rows = conn.execute(
            "SELECT * FROM cp_experiments WHERE workspace_id = ? AND status = ? "
            "ORDER BY created_at DESC",
            (workspace_id, status),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM cp_experiments WHERE workspace_id = ? ORDER BY created_at DESC",
            (workspace_id,),
        ).fetchall()
    return [_row_to_experiment(r) for r in rows]


def create_experiment_variant(conn: Any, draft: ExperimentVariantDraft) -> ExperimentVariant:
    now = _now().isoformat()
    conn.execute(
        """
        INSERT INTO cp_experiment_variants
            (id, experiment_id, name, variant_type, description, config_json, created_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            draft.id,
            draft.experiment_id,
            draft.name,
            draft.variant_type,
            draft.description,
            json.dumps(draft.config, sort_keys=True) if draft.config else None,
            now,
        ),
    )
    row = conn.execute("SELECT * FROM cp_experiment_variants WHERE id = ?", (draft.id,)).fetchone()
    return _row_to_experiment_variant(row)


def list_variants_by_experiment(conn: Any, experiment_id: str) -> list[ExperimentVariant]:
    rows = conn.execute(
        "SELECT * FROM cp_experiment_variants WHERE experiment_id = ? ORDER BY created_at ASC",
        (experiment_id,),
    ).fetchall()
    return [_row_to_experiment_variant(r) for r in rows]


def get_or_create_assignment(
    conn: Any,
    assignment_id: str,
    experiment_id: str,
    variant_id: str,
    unit_id: str,
) -> ExperimentAssignment:
    existing = conn.execute(
        "SELECT * FROM cp_experiment_assignments WHERE experiment_id = ? AND unit_id = ?",
        (experiment_id, unit_id),
    ).fetchone()
    if existing:
        return _row_to_experiment_assignment(existing)
    now = _now().isoformat()
    conn.execute(
        """
        INSERT INTO cp_experiment_assignments
            (id, experiment_id, variant_id, unit_id, status, assigned_at)
        VALUES (?,?,?,?,?,?)
        """,
        (assignment_id, experiment_id, variant_id, unit_id, "active", now),
    )
    row = conn.execute(
        "SELECT * FROM cp_experiment_assignments WHERE id = ?", (assignment_id,)
    ).fetchone()
    return _row_to_experiment_assignment(row)


# ---------------------------------------------------------------------------
# Operation Executions
# ---------------------------------------------------------------------------


def create_operation_execution(conn: Any, draft: OperationExecutionDraft) -> OperationExecution:
    existing = conn.execute(
        "SELECT * FROM cp_operation_executions WHERE idempotency_key = ?",
        (draft.idempotency_key,),
    ).fetchone()
    if existing:
        raise DuplicateIdempotencyKeyError(draft.idempotency_key, existing["id"])
    now = _now().isoformat()
    conn.execute(
        """
        INSERT INTO cp_operation_executions
            (id, operation_type, workspace_id, channel_id, platform_account_id,
             idempotency_key, status, actor, correlation_id, source_event_id,
             input_json, created_at, updated_at,
             engine, target_entity_id, target_entity_type)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            draft.id,
            draft.operation_type,
            draft.workspace_id,
            draft.channel_id,
            draft.platform_account_id,
            draft.idempotency_key,
            "pending",
            draft.actor,
            draft.correlation_id,
            draft.source_event_id,
            json.dumps(draft.input_data) if draft.input_data else None,
            now,
            now,
            draft.engine,
            draft.target_entity_id,
            draft.target_entity_type,
        ),
    )
    return get_operation_execution(conn, draft.id)


def get_operation_execution(conn: Any, operation_id: str) -> OperationExecution:
    row = conn.execute(
        "SELECT * FROM cp_operation_executions WHERE id = ?", (operation_id,)
    ).fetchone()
    if not row:
        raise OperationNotFoundError(operation_id)
    return _row_to_operation_execution(row)


def get_operation_by_idempotency_key(conn: Any, idempotency_key: str) -> OperationExecution | None:
    row = conn.execute(
        "SELECT * FROM cp_operation_executions WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    if not row:
        return None
    return _row_to_operation_execution(row)


def update_operation_status(
    conn: Any,
    operation_id: str,
    status: str,
    output_data: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> OperationExecution:
    now = _now().isoformat()
    conn.execute(
        """
        UPDATE cp_operation_executions
        SET status = ?, output_json = ?, error_message = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            json.dumps(output_data) if output_data else None,
            error_message,
            now,
            operation_id,
        ),
    )
    return get_operation_execution(conn, operation_id)


# ---------------------------------------------------------------------------
# Cost Records
# ---------------------------------------------------------------------------


def create_cost_record(conn: Any, draft: CostRecordDraft) -> CostRecord:
    now = _now().isoformat()
    conn.execute(
        """
        INSERT INTO cp_cost_records
            (id, workspace_id, channel_id, platform_account_id, operation_execution_id,
             provider_key, cost_unit, quantity, usd_equivalent, description, recorded_at,
             engine, experiment_id, entity_id, entity_type)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            draft.id,
            draft.workspace_id,
            draft.channel_id,
            draft.platform_account_id,
            draft.operation_execution_id,
            draft.provider_key,
            draft.cost_unit,
            draft.quantity,
            draft.usd_equivalent,
            draft.description,
            now,
            draft.engine,
            draft.experiment_id,
            draft.entity_id,
            draft.entity_type,
        ),
    )
    row = conn.execute("SELECT * FROM cp_cost_records WHERE id = ?", (draft.id,)).fetchone()
    return _row_to_cost_record(row)


def sum_cost_usd_by_workspace(conn: Any, workspace_id: str, since: datetime) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(usd_equivalent),0) AS total FROM cp_cost_records "
        "WHERE workspace_id = ? AND recorded_at >= ?",
        (workspace_id, since.isoformat()),
    ).fetchone()
    return float(row[0])


def sum_cost_usd_by_channel(conn: Any, channel_id: str, since: datetime) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(usd_equivalent),0) AS total FROM cp_cost_records "
        "WHERE channel_id = ? AND recorded_at >= ?",
        (channel_id, since.isoformat()),
    ).fetchone()
    return float(row[0])


def sum_cost_usd_by_account(conn: Any, platform_account_id: str, since: datetime) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(usd_equivalent),0) AS total FROM cp_cost_records "
        "WHERE platform_account_id = ? AND recorded_at >= ?",
        (platform_account_id, since.isoformat()),
    ).fetchone()
    return float(row[0])


def list_cost_records_by_workspace(
    conn: Any, workspace_id: str, limit: int = 100
) -> list[CostRecord]:
    rows = conn.execute(
        "SELECT * FROM cp_cost_records WHERE workspace_id = ? ORDER BY recorded_at DESC LIMIT ?",
        (workspace_id, limit),
    ).fetchall()
    return [_row_to_cost_record(r) for r in rows]


# ---------------------------------------------------------------------------
# Budget Policies
# ---------------------------------------------------------------------------


def create_budget_policy(conn: Any, draft: BudgetPolicyDraft) -> BudgetPolicy:
    now = _now().isoformat()
    conn.execute(
        "UPDATE cp_budget_policies SET is_active = 0 "
        "WHERE scope = ? AND scope_id = ? AND is_active = 1",
        (draft.scope, draft.scope_id),
    )
    conn.execute(
        """
        INSERT INTO cp_budget_policies
            (id, scope, scope_id, period, limit_usd, warning_threshold,
             on_exceed_action, actor, created_at, is_active)
        VALUES (?,?,?,?,?,?,?,?,?,1)
        """,
        (
            draft.id,
            draft.scope,
            draft.scope_id,
            draft.period,
            draft.limit_usd,
            draft.warning_threshold,
            draft.on_exceed_action,
            draft.actor,
            now,
        ),
    )
    row = conn.execute("SELECT * FROM cp_budget_policies WHERE id = ?", (draft.id,)).fetchone()
    return _row_to_budget_policy(row)


def get_active_budget_for_scope(conn: Any, scope: str, scope_id: str) -> BudgetPolicy | None:
    row = conn.execute(
        "SELECT * FROM cp_budget_policies WHERE scope = ? AND scope_id = ? AND is_active = 1",
        (scope, scope_id),
    ).fetchone()
    if not row:
        return None
    return _row_to_budget_policy(row)


# ---------------------------------------------------------------------------
# Health Records
# ---------------------------------------------------------------------------


def create_health_record(conn: Any, draft: HealthRecordDraft) -> HealthRecord:
    now = _now().isoformat()
    conn.execute(
        """
        INSERT INTO cp_health_records
            (id, entity_type, entity_id, status, detail, recorded_by, recorded_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            draft.id,
            draft.entity_type,
            draft.entity_id,
            draft.status,
            draft.detail,
            draft.recorded_by,
            now,
        ),
    )
    row = conn.execute("SELECT * FROM cp_health_records WHERE id = ?", (draft.id,)).fetchone()
    return _row_to_health_record(row)


def get_latest_health_record(conn: Any, entity_type: str, entity_id: str) -> HealthRecord | None:
    row = conn.execute(
        "SELECT * FROM cp_health_records WHERE entity_type = ? AND entity_id = ? "
        "ORDER BY recorded_at DESC LIMIT 1",
        (entity_type, entity_id),
    ).fetchone()
    if not row:
        return None
    return _row_to_health_record(row)


def list_health_records(
    conn: Any, entity_type: str | None = None, status: str | None = None, limit: int = 100
) -> list[HealthRecord]:
    query = "SELECT * FROM cp_health_records WHERE 1=1"
    params: list[Any] = []
    if entity_type:
        query += " AND entity_type = ?"
        params.append(entity_type)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY recorded_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [_row_to_health_record(r) for r in rows]


# ---------------------------------------------------------------------------
# Provider Registry
# ---------------------------------------------------------------------------


def register_provider(conn: Any, draft: ProviderRegistryDraft) -> ProviderRegistryEntry:
    now = _now().isoformat()
    existing = conn.execute(
        "SELECT id FROM cp_provider_registry WHERE provider_key = ?",
        (draft.provider_key,),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE cp_provider_registry SET display_name = ?, status = ?, "
            "capabilities_json = ?, quota_json = ?, cost_metadata_json = ?, "
            "version_info = ?, updated_at = ? WHERE provider_key = ?",
            (
                draft.display_name,
                draft.status,
                json.dumps(sorted(draft.capabilities)),
                json.dumps(draft.quota) if draft.quota else None,
                json.dumps(draft.cost_metadata) if draft.cost_metadata else None,
                draft.version_info,
                now,
                draft.provider_key,
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO cp_provider_registry
                (id, provider_key, domain, display_name, status, capabilities_json,
                 registered_at, updated_at, quota_json, cost_metadata_json, version_info)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                draft.id,
                draft.provider_key,
                draft.domain,
                draft.display_name,
                draft.status,
                json.dumps(sorted(draft.capabilities)),
                now,
                now,
                json.dumps(draft.quota) if draft.quota else None,
                json.dumps(draft.cost_metadata) if draft.cost_metadata else None,
                draft.version_info,
            ),
        )
    row = conn.execute(
        "SELECT * FROM cp_provider_registry WHERE provider_key = ?", (draft.provider_key,)
    ).fetchone()
    return _row_to_provider_entry(row)


def get_provider_by_key(conn: Any, provider_key: str) -> ProviderRegistryEntry:
    row = conn.execute(
        "SELECT * FROM cp_provider_registry WHERE provider_key = ?", (provider_key,)
    ).fetchone()
    if not row:
        raise ProviderNotFoundError(provider_key)
    return _row_to_provider_entry(row)


def list_providers_by_domain(conn: Any, domain: str) -> list[ProviderRegistryEntry]:
    rows = conn.execute(
        "SELECT * FROM cp_provider_registry WHERE domain = ? ORDER BY provider_key ASC",
        (domain,),
    ).fetchall()
    return [_row_to_provider_entry(r) for r in rows]


def update_provider_health(conn: Any, provider_key: str, status: str) -> ProviderRegistryEntry:
    now = _now().isoformat()
    conn.execute(
        "UPDATE cp_provider_registry SET status = ?, updated_at = ? WHERE provider_key = ?",
        (status, now, provider_key),
    )
    return get_provider_by_key(conn, provider_key)


# ---------------------------------------------------------------------------
# Publishing Profiles
# ---------------------------------------------------------------------------


def create_publishing_profile(conn: Any, draft: PublishingProfileDraft) -> PublishingProfile:
    import json as _json

    now = _now().isoformat()
    conn.execute(
        "UPDATE cp_publishing_profiles SET is_active = 0, updated_at = ? "
        "WHERE platform_account_id = ? AND is_active = 1",
        (now, draft.platform_account_id),
    )
    conn.execute(
        """
        INSERT INTO cp_publishing_profiles
            (id, platform_account_id, config_json, is_active, actor, created_at, updated_at)
        VALUES (?,?,?,1,?,?,?)
        """,
        (
            draft.id,
            draft.platform_account_id,
            _json.dumps(draft.config, sort_keys=True),
            draft.actor,
            now,
            now,
        ),
    )
    row = conn.execute("SELECT * FROM cp_publishing_profiles WHERE id = ?", (draft.id,)).fetchone()
    return _row_to_publishing_profile(row)


def get_active_publishing_profile(conn: Any, platform_account_id: str) -> PublishingProfile | None:
    row = conn.execute(
        "SELECT * FROM cp_publishing_profiles "
        "WHERE platform_account_id = ? AND is_active = 1 LIMIT 1",
        (platform_account_id,),
    ).fetchone()
    if not row:
        return None
    return _row_to_publishing_profile(row)


def list_publishing_profiles(conn: Any, platform_account_id: str) -> list[PublishingProfile]:
    rows = conn.execute(
        "SELECT * FROM cp_publishing_profiles WHERE platform_account_id = ? "
        "ORDER BY created_at DESC",
        (platform_account_id,),
    ).fetchall()
    return [_row_to_publishing_profile(r) for r in rows]


# ---------------------------------------------------------------------------
# Analytics Identities
# ---------------------------------------------------------------------------


def create_analytics_identity(conn: Any, draft: AnalyticsIdentityDraft) -> AnalyticsIdentity:
    import json as _json

    now = _now().isoformat()
    existing = conn.execute(
        "SELECT id FROM cp_analytics_identities "
        "WHERE platform_account_id = ? AND analytics_provider_key = ?",
        (draft.platform_account_id, draft.analytics_provider_key),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE cp_analytics_identities SET analytics_account_id = ?, metadata_json = ? "
            "WHERE platform_account_id = ? AND analytics_provider_key = ?",
            (
                _json.dumps(draft.metadata) if draft.metadata else None,
                draft.analytics_account_id,
                draft.platform_account_id,
                draft.analytics_provider_key,
            ),
        )
        row = conn.execute(
            "SELECT * FROM cp_analytics_identities WHERE id = ?", (existing["id"],)
        ).fetchone()
    else:
        conn.execute(
            """
            INSERT INTO cp_analytics_identities
                (id, platform_account_id, analytics_provider_key, analytics_account_id,
                 metadata_json, created_at)
            VALUES (?,?,?,?,?,?)
            """,
            (
                draft.id,
                draft.platform_account_id,
                draft.analytics_provider_key,
                draft.analytics_account_id,
                _json.dumps(draft.metadata) if draft.metadata else None,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM cp_analytics_identities WHERE id = ?", (draft.id,)
        ).fetchone()
    return _row_to_analytics_identity(row)


def list_analytics_identities(conn: Any, platform_account_id: str) -> list[AnalyticsIdentity]:
    rows = conn.execute(
        "SELECT * FROM cp_analytics_identities WHERE platform_account_id = ? "
        "ORDER BY created_at ASC",
        (platform_account_id,),
    ).fetchall()
    return [_row_to_analytics_identity(r) for r in rows]
