"""Tests for Phase 12 control plane repository functions."""

from __future__ import annotations

import pytest

from app.control_plane import repository as repo
from app.control_plane.errors import (
    ChannelNotFoundError,
    CredentialProfileNotFoundError,
    DuplicateIdempotencyKeyError,
    ExperimentNotFoundError,
    PlatformAccountNotFoundError,
    PlatformNotFoundError,
    ProviderNotFoundError,
    WorkflowNotFoundError,
    WorkspaceNotFoundError,
)
from app.control_plane.models import (
    AutomationPolicyDraft,
    BudgetPolicyDraft,
    ChannelDraft,
    ControlEventDraft,
    CostRecordDraft,
    CredentialProfileDraft,
    ExperimentDraft,
    HealthRecordDraft,
    OperationExecutionDraft,
    PlatformAccountDraft,
    ProviderRegistryDraft,
    StrategyProfileDraft,
    WorkflowDraft,
    WorkspaceDraft,
)


@pytest.fixture()
def db(tmp_path):
    from app.core.database import open_db

    return open_db(tmp_path / "test.db")


@pytest.fixture()
def workspace(db):
    draft = WorkspaceDraft(id="ws-1", name="Acme", slug="acme", actor="cli")
    return repo.create_workspace(db, draft)


@pytest.fixture()
def channel(db, workspace):
    draft = ChannelDraft(
        id="ch-1", workspace_id=workspace.id, name="Tech", slug="tech", actor="cli"
    )
    return repo.create_channel(db, draft)


@pytest.fixture()
def platform(db):
    repo.ensure_platform(db, "plt-1", "youtube", "YouTube")
    return repo.get_platform_by_key(db, "youtube")


@pytest.fixture()
def account(db, channel, platform):
    draft = PlatformAccountDraft(
        id="acc-1",
        channel_id=channel.id,
        platform_id=platform.id,
        platform_key="youtube",
        external_account_id="UC123",
        display_name="Acme YouTube",
        actor="cli",
    )
    return repo.create_platform_account(db, draft)


class TestWorkspaceRepository:
    def test_create_and_get(self, db):
        draft = WorkspaceDraft(id="ws-a", name="Alpha", slug="alpha", actor="cli")
        ws = repo.create_workspace(db, draft)
        assert ws.id == "ws-a"
        assert ws.name == "Alpha"
        assert ws.slug == "alpha"
        assert ws.status == "active"

    def test_get_not_found(self, db):
        with pytest.raises(WorkspaceNotFoundError):
            repo.get_workspace(db, "nonexistent")

    def test_list_all(self, db, workspace):
        items = repo.list_workspaces(db)
        assert any(w.id == workspace.id for w in items)

    def test_list_by_status(self, db, workspace):
        items = repo.list_workspaces(db, status="active")
        assert any(w.id == workspace.id for w in items)

    def test_update_status(self, db, workspace):
        updated = repo.update_workspace_status(db, workspace.id, "suspended", "admin")
        assert updated.status == "suspended"
        assert updated.actor == "admin"


class TestChannelRepository:
    def test_create_and_get(self, db, workspace):
        draft = ChannelDraft(
            id="ch-x", workspace_id=workspace.id, name="Gaming", slug="gaming", actor="cli"
        )
        ch = repo.create_channel(db, draft)
        assert ch.workspace_id == workspace.id
        assert ch.slug == "gaming"

    def test_get_not_found(self, db):
        with pytest.raises(ChannelNotFoundError):
            repo.get_channel(db, "bad-id")

    def test_list_by_workspace(self, db, workspace, channel):
        items = repo.list_channels_by_workspace(db, workspace.id)
        assert any(c.id == channel.id for c in items)

    def test_update_status(self, db, channel):
        updated = repo.update_channel_status(db, channel.id, "paused", "cli")
        assert updated.status == "paused"


class TestPlatformRepository:
    def test_ensure_and_get(self, db):
        repo.ensure_platform(db, "plt-yt", "youtube", "YouTube")
        plt = repo.get_platform_by_key(db, "youtube")
        assert plt.platform_key == "youtube"
        assert plt.display_name == "YouTube"
        assert plt.is_active

    def test_ensure_idempotent(self, db):
        repo.ensure_platform(db, "p1", "youtube", "YouTube")
        repo.ensure_platform(db, "p2", "youtube", "YouTube Updated")
        plt = repo.get_platform_by_key(db, "youtube")
        assert plt.display_name == "YouTube"

    def test_get_not_found(self, db):
        with pytest.raises(PlatformNotFoundError):
            repo.get_platform_by_key(db, "nonexistent_platform")

    def test_list_platforms(self, db):
        repo.ensure_platform(db, "p1", "youtube", "YouTube")
        repo.ensure_platform(db, "p2", "tiktok", "TikTok")
        items = repo.list_platforms(db)
        keys = [p.platform_key for p in items]
        assert "youtube" in keys
        assert "tiktok" in keys


class TestPlatformAccountRepository:
    def test_create_and_get(self, db, account):
        fetched = repo.get_platform_account(db, account.id)
        assert fetched.platform_key == "youtube"
        assert fetched.status == "connected"

    def test_get_not_found(self, db):
        with pytest.raises(PlatformAccountNotFoundError):
            repo.get_platform_account(db, "bad")

    def test_list_by_channel(self, db, channel, account):
        items = repo.list_platform_accounts_by_channel(db, channel.id)
        assert any(a.id == account.id for a in items)

    def test_update_status(self, db, account):
        updated = repo.update_platform_account_status(db, account.id, "paused", "cli")
        assert updated.status == "paused"

    def test_pause_helper(self, db, account):
        updated = repo.pause_platform_account(db, account.id, "cli")
        assert updated.status == "paused"


class TestCredentialProfileRepository:
    def test_create_and_get(self, db, workspace):
        draft = CredentialProfileDraft(
            id="cp-1",
            workspace_id=workspace.id,
            display_name="YouTube OAuth",
            credential_type="oauth2",
            external_ref="ref-001",
            actor="cli",
        )
        profile = repo.create_credential_profile(db, draft)
        assert profile.credential_type == "oauth2"
        assert profile.status == "active"

    def test_get_not_found(self, db):
        with pytest.raises(CredentialProfileNotFoundError):
            repo.get_credential_profile(db, "nope")

    def test_list_by_workspace(self, db, workspace):
        draft = CredentialProfileDraft(
            id="cp-2",
            workspace_id=workspace.id,
            display_name="Key",
            credential_type="api_key",
            external_ref="ref-002",
            actor="cli",
        )
        repo.create_credential_profile(db, draft)
        items = repo.list_credential_profiles(db, workspace.id)
        assert any(p.id == "cp-2" for p in items)

    def test_update_status(self, db, workspace):
        draft = CredentialProfileDraft(
            id="cp-3",
            workspace_id=workspace.id,
            display_name="K",
            credential_type="api_key",
            external_ref="r",
            actor="cli",
        )
        profile = repo.create_credential_profile(db, draft)
        updated = repo.update_credential_status(db, profile.id, "expired", "cli")
        assert updated.status == "expired"


class TestAutomationPolicyRepository:
    def test_create_and_get_active(self, db, workspace):
        draft = AutomationPolicyDraft(
            id="pol-1",
            scope="workspace",
            scope_id=workspace.id,
            automation_level="supervised",
            allowed_actions=["pause_account"],
            actor="cli",
        )
        policy = repo.create_automation_policy(db, draft)
        assert policy.automation_level == "supervised"
        assert policy.is_active

        active = repo.get_active_policy_for_scope(db, "workspace", workspace.id)
        assert active is not None
        assert active.id == policy.id

    def test_new_policy_deactivates_old(self, db, workspace):
        draft1 = AutomationPolicyDraft(
            id="pol-1",
            scope="workspace",
            scope_id=workspace.id,
            automation_level="manual",
            allowed_actions=[],
            actor="cli",
        )
        draft2 = AutomationPolicyDraft(
            id="pol-2",
            scope="workspace",
            scope_id=workspace.id,
            automation_level="autonomous",
            allowed_actions=[],
            actor="cli",
        )
        repo.create_automation_policy(db, draft1)
        repo.create_automation_policy(db, draft2)
        active = repo.get_active_policy_for_scope(db, "workspace", workspace.id)
        assert active is not None
        assert active.id == "pol-2"

    def test_no_policy_returns_none(self, db):
        result = repo.get_active_policy_for_scope(db, "workspace", "nobody")
        assert result is None


class TestStrategyProfileRepository:
    def test_create_and_activate(self, db, channel):
        draft = StrategyProfileDraft(
            id="sp-1", channel_id=channel.id, version=1, config={"target_ctr": 0.05}, actor="cli"
        )
        sp = repo.create_strategy_profile(db, draft)
        assert sp.version == 1
        assert sp.is_active

        active = repo.get_active_strategy_for_channel(db, channel.id)
        assert active is not None
        assert active.id == "sp-1"

    def test_new_strategy_deactivates_old(self, db, channel):
        draft1 = StrategyProfileDraft(
            id="sp-1", channel_id=channel.id, version=1, config={}, actor="cli"
        )
        draft2 = StrategyProfileDraft(
            id="sp-2", channel_id=channel.id, version=2, config={"x": 1}, actor="cli"
        )
        repo.create_strategy_profile(db, draft1)
        repo.create_strategy_profile(db, draft2)
        active = repo.get_active_strategy_for_channel(db, channel.id)
        assert active is not None
        assert active.id == "sp-2"

    def test_no_strategy_returns_none(self, db, channel):
        result = repo.get_active_strategy_for_channel(db, channel.id)
        assert result is None


class TestEventRepository:
    def test_create_and_get(self, db, workspace):
        draft = ControlEventDraft(
            id="ev-1",
            event_type="workspace.created",
            workspace_id=workspace.id,
            actor="cli",
            payload={"workspace_id": workspace.id},
        )
        event = repo.create_event(db, draft)
        assert event.id == "ev-1"
        assert event.payload == {"workspace_id": workspace.id}

    def test_list_by_workspace(self, db, workspace):
        draft = ControlEventDraft(
            id="ev-2",
            event_type="channel.created",
            workspace_id=workspace.id,
            actor="cli",
            payload={},
        )
        repo.create_event(db, draft)
        items = repo.list_events_by_workspace(db, workspace.id)
        assert any(e.id == "ev-2" for e in items)

    def test_list_filtered_by_type(self, db, workspace):
        repo.create_event(
            db,
            ControlEventDraft(
                id="ev-3",
                event_type="channel.created",
                workspace_id=workspace.id,
                actor="cli",
                payload={},
            ),
        )
        repo.create_event(
            db,
            ControlEventDraft(
                id="ev-4",
                event_type="workspace.suspended",
                workspace_id=workspace.id,
                actor="cli",
                payload={},
            ),
        )
        items = repo.list_events_by_workspace(db, workspace.id, event_type="channel.created")
        ids = [e.id for e in items]
        assert "ev-3" in ids
        assert "ev-4" not in ids


class TestWorkflowRepository:
    def test_create_and_get(self, db, workspace):
        draft = WorkflowDraft(
            id="wf-1",
            workspace_id=workspace.id,
            name="Pause on degraded",
            trigger_event_type="health.degraded",
            conditions=[{"field": "status", "operator": "equals", "value": "degraded"}],
            actions=[{"action_type": "notify", "params": {"message": "Alert"}}],
            actor="cli",
        )
        wf = repo.create_workflow(db, draft)
        assert wf.name == "Pause on degraded"
        assert wf.status == "draft"

    def test_get_not_found(self, db):
        with pytest.raises(WorkflowNotFoundError):
            repo.get_workflow(db, "nope")

    def test_list_active_for_trigger(self, db, workspace):
        draft = WorkflowDraft(
            id="wf-2",
            workspace_id=workspace.id,
            name="W2",
            trigger_event_type="account.status_changed",
            conditions=[],
            actions=[],
            actor="cli",
            status="active",
        )
        repo.create_workflow(db, draft)
        items = repo.list_active_workflows_for_trigger(db, workspace.id, "account.status_changed")
        assert any(w.id == "wf-2" for w in items)

    def test_update_status(self, db, workspace):
        draft = WorkflowDraft(
            id="wf-3",
            workspace_id=workspace.id,
            name="W3",
            trigger_event_type="health.degraded",
            conditions=[],
            actions=[],
            actor="cli",
        )
        wf = repo.create_workflow(db, draft)
        updated = repo.update_workflow_status(db, wf.id, "active", "cli")
        assert updated.status == "active"


class TestExperimentRepository:
    def test_create_and_get(self, db, workspace, channel):
        draft = ExperimentDraft(
            id="exp-1",
            workspace_id=workspace.id,
            channel_id=channel.id,
            name="CTR test",
            hypothesis="A > B",
            primary_metric="ctr",
            actor="cli",
        )
        exp = repo.create_experiment(db, draft)
        assert exp.status == "draft"

    def test_get_not_found(self, db):
        with pytest.raises(ExperimentNotFoundError):
            repo.get_experiment(db, "bad")

    def test_activate(self, db, workspace, channel):
        draft = ExperimentDraft(
            id="exp-2",
            workspace_id=workspace.id,
            channel_id=channel.id,
            name="E2",
            hypothesis="H",
            primary_metric="retention",
            actor="cli",
        )
        repo.create_experiment(db, draft)
        exp = repo.activate_experiment(db, "exp-2")
        assert exp.status == "active"
        assert exp.activated_at is not None

    def test_conclude(self, db, workspace, channel):
        draft = ExperimentDraft(
            id="exp-3",
            workspace_id=workspace.id,
            channel_id=channel.id,
            name="E3",
            hypothesis="H",
            primary_metric="retention",
            actor="cli",
        )
        repo.create_experiment(db, draft)
        repo.activate_experiment(db, "exp-3")
        exp = repo.conclude_experiment(db, "exp-3")
        assert exp.status == "concluded"
        assert exp.concluded_at is not None


class TestOperationExecutionRepository:
    def test_create_and_get(self, db, workspace):
        draft = OperationExecutionDraft(
            id="op-1",
            operation_type="publish",
            workspace_id=workspace.id,
            idempotency_key="key-xyz",
            actor="cli",
        )
        op = repo.create_operation_execution(db, draft)
        assert op.status == "pending"

    def test_duplicate_idempotency_key_raises(self, db, workspace):
        draft = OperationExecutionDraft(
            id="op-1",
            operation_type="publish",
            workspace_id=workspace.id,
            idempotency_key="same-key",
            actor="cli",
        )
        repo.create_operation_execution(db, draft)
        draft2 = OperationExecutionDraft(
            id="op-2",
            operation_type="publish",
            workspace_id=workspace.id,
            idempotency_key="same-key",
            actor="cli",
        )
        with pytest.raises(DuplicateIdempotencyKeyError) as exc_info:
            repo.create_operation_execution(db, draft2)
        assert exc_info.value.idempotency_key == "same-key"
        assert exc_info.value.existing_operation_id == "op-1"

    def test_update_status(self, db, workspace):
        draft = OperationExecutionDraft(
            id="op-3",
            operation_type="ingest",
            workspace_id=workspace.id,
            idempotency_key="k3",
            actor="cli",
        )
        op = repo.create_operation_execution(db, draft)
        updated = repo.update_operation_status(db, op.id, "completed", output_data={"result": "ok"})
        assert updated.status == "completed"

    def test_get_by_idempotency_key(self, db, workspace):
        draft = OperationExecutionDraft(
            id="op-4",
            operation_type="pub",
            workspace_id=workspace.id,
            idempotency_key="findme",
            actor="cli",
        )
        repo.create_operation_execution(db, draft)
        found = repo.get_operation_by_idempotency_key(db, "findme")
        assert found is not None
        assert found.id == "op-4"

    def test_missing_key_returns_none(self, db):
        result = repo.get_operation_by_idempotency_key(db, "notexist")
        assert result is None


class TestCostRecordRepository:
    def test_create_and_sum(self, db, workspace):
        from datetime import UTC, datetime

        draft1 = CostRecordDraft(
            id="cr-1",
            workspace_id=workspace.id,
            provider_key="claude",
            cost_unit="tokens",
            quantity=1000,
            usd_equivalent=0.003,
        )
        draft2 = CostRecordDraft(
            id="cr-2",
            workspace_id=workspace.id,
            provider_key="claude",
            cost_unit="tokens",
            quantity=2000,
            usd_equivalent=0.006,
        )
        repo.create_cost_record(db, draft1)
        repo.create_cost_record(db, draft2)

        since = datetime(2020, 1, 1, tzinfo=UTC)
        total = repo.sum_cost_usd_by_workspace(db, workspace.id, since)
        assert abs(total - 0.009) < 0.0001

    def test_list_by_workspace(self, db, workspace):
        draft = CostRecordDraft(
            id="cr-3",
            workspace_id=workspace.id,
            provider_key="tts",
            cost_unit="characters",
            quantity=500,
            usd_equivalent=0.001,
        )
        repo.create_cost_record(db, draft)
        items = repo.list_cost_records_by_workspace(db, workspace.id)
        assert any(r.id == "cr-3" for r in items)


class TestBudgetPolicyRepository:
    def test_create_and_get_active(self, db, workspace):
        draft = BudgetPolicyDraft(
            id="bp-1",
            scope="workspace",
            scope_id=workspace.id,
            period="monthly",
            limit_usd=100.0,
            actor="cli",
        )
        policy = repo.create_budget_policy(db, draft)
        assert policy.limit_usd == 100.0
        assert policy.is_active

        active = repo.get_active_budget_for_scope(db, "workspace", workspace.id)
        assert active is not None
        assert active.id == "bp-1"

    def test_new_policy_deactivates_old(self, db, workspace):
        draft1 = BudgetPolicyDraft(
            id="bp-1",
            scope="workspace",
            scope_id=workspace.id,
            period="monthly",
            limit_usd=100.0,
            actor="cli",
        )
        draft2 = BudgetPolicyDraft(
            id="bp-2",
            scope="workspace",
            scope_id=workspace.id,
            period="monthly",
            limit_usd=200.0,
            actor="cli",
        )
        repo.create_budget_policy(db, draft1)
        repo.create_budget_policy(db, draft2)
        active = repo.get_active_budget_for_scope(db, "workspace", workspace.id)
        assert active is not None
        assert active.id == "bp-2"


class TestHealthRecordRepository:
    def test_create_and_get_latest(self, db):
        draft1 = HealthRecordDraft(
            id="hr-1",
            entity_type="channel",
            entity_id="ch-1",
            status="healthy",
            recorded_by="monitor",
        )
        draft2 = HealthRecordDraft(
            id="hr-2",
            entity_type="channel",
            entity_id="ch-1",
            status="degraded",
            recorded_by="monitor",
            detail="Quota low",
        )
        repo.create_health_record(db, draft1)
        repo.create_health_record(db, draft2)

        latest = repo.get_latest_health_record(db, "channel", "ch-1")
        assert latest is not None
        assert latest.status == "degraded"

    def test_no_record_returns_none(self, db):
        result = repo.get_latest_health_record(db, "channel", "nobody")
        assert result is None

    def test_list_by_status(self, db):
        draft = HealthRecordDraft(
            id="hr-3",
            entity_type="provider",
            entity_id="claude",
            status="unavailable",
            recorded_by="monitor",
        )
        repo.create_health_record(db, draft)
        items = repo.list_health_records(db, status="unavailable")
        assert any(h.id == "hr-3" for h in items)


class TestProviderRegistryRepository:
    def test_register_and_get(self, db):
        draft = ProviderRegistryDraft(
            id="pr-1",
            provider_key="claude",
            domain="ai",
            display_name="Claude",
            capabilities=["text_generation"],
        )
        entry = repo.register_provider(db, draft)
        assert entry.provider_key == "claude"
        assert "text_generation" in entry.capabilities

    def test_register_idempotent_update(self, db):
        draft = ProviderRegistryDraft(
            id="pr-1", provider_key="claude", domain="ai", display_name="Claude"
        )
        repo.register_provider(db, draft)
        draft2 = ProviderRegistryDraft(
            id="pr-2", provider_key="claude", domain="ai", display_name="Claude v2"
        )
        repo.register_provider(db, draft2)
        entry = repo.get_provider_by_key(db, "claude")
        assert entry.display_name == "Claude v2"

    def test_get_not_found(self, db):
        with pytest.raises(ProviderNotFoundError):
            repo.get_provider_by_key(db, "nonexistent_provider")

    def test_list_by_domain(self, db):
        repo.register_provider(
            db,
            ProviderRegistryDraft(
                id="p1", provider_key="claude", domain="ai", display_name="Claude"
            ),
        )
        repo.register_provider(
            db,
            ProviderRegistryDraft(
                id="p2", provider_key="deepmind-tts", domain="tts", display_name="DeepMind TTS"
            ),
        )
        ai_providers = repo.list_providers_by_domain(db, "ai")
        assert any(p.provider_key == "claude" for p in ai_providers)
        assert not any(p.provider_key == "deepmind-tts" for p in ai_providers)

    def test_update_health(self, db):
        repo.register_provider(
            db,
            ProviderRegistryDraft(
                id="pr-1", provider_key="elevenlabs", domain="tts", display_name="ElevenLabs"
            ),
        )
        updated = repo.update_provider_health(db, "elevenlabs", "degraded")
        assert updated.status == "degraded"
