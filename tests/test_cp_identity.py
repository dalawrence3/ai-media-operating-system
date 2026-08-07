"""Tests for Phase 12 identity and account management."""

from __future__ import annotations

import pytest

from app.control_plane import repository as repo
from app.control_plane.accounts import (
    connect_account,
    disconnect_account,
    mark_credential_expiring,
    mark_credential_invalid,
    mark_quota_limited,
    pause_account,
    resume_account,
)
from app.control_plane.credentials import (
    create_credential_profile,
    mark_credential_expired,
    reactivate_credential,
    revoke_credential,
)
from app.control_plane.errors import (
    ExperimentAlreadyActiveError,
    ExperimentNotActiveError,
    InvalidAutomationLevelError,
    InvalidWorkflowActionError,
    InvalidWorkflowConditionError,
    PolicyViolationError,
)
from app.control_plane.experiments import (
    activate_experiment,
    add_variant,
    assign_unit,
    conclude_experiment,
    create_experiment,
)
from app.control_plane.identity import (
    archive_channel,
    archive_workspace,
    create_channel,
    create_workspace,
    pause_channel,
    suspend_workspace,
)
from app.control_plane.policies import (
    assert_action_permitted,
    resolve_effective_level,
    set_policy,
)
from app.control_plane.strategies import create_strategy_profile, get_active_strategy
from app.control_plane.workflows import (
    activate_workflow,
    create_workflow,
    pause_workflow,
)


@pytest.fixture()
def db(tmp_path):
    from app.core.database import open_db
    return open_db(tmp_path / "test.db")


@pytest.fixture()
def workspace(db):
    return create_workspace(db, name="Acme Corp", slug="acme", actor="cli")


@pytest.fixture()
def channel(db, workspace):
    return create_channel(
        db, workspace_id=workspace.id, name="Tech Channel", slug="tech", actor="cli"
    )


@pytest.fixture()
def platform(db):
    repo.ensure_platform(db, "plt-1", "youtube", "YouTube")
    return repo.get_platform_by_key(db, "youtube")


class TestIdentityWorkspace:
    def test_create_workspace(self, db):
        ws = create_workspace(db, name="Test Corp", slug="testcorp", actor="cli")
        assert ws.name == "Test Corp"
        assert ws.slug == "testcorp"
        assert ws.status == "active"

    def test_suspend_workspace(self, db, workspace):
        ws = suspend_workspace(db, workspace.id, "admin")
        assert ws.status == "suspended"

    def test_archive_workspace(self, db, workspace):
        ws = archive_workspace(db, workspace.id, "admin")
        assert ws.status == "archived"


class TestIdentityChannel:
    def test_create_channel(self, db, workspace):
        ch = create_channel(
            db, workspace_id=workspace.id, name="Gaming", slug="gaming", actor="cli"
        )
        assert ch.workspace_id == workspace.id
        assert ch.name == "Gaming"
        assert ch.status == "active"

    def test_pause_channel(self, db, channel):
        ch = pause_channel(db, channel.id, "cli")
        assert ch.status == "paused"

    def test_archive_channel(self, db, channel):
        ch = archive_channel(db, channel.id, "cli")
        assert ch.status == "archived"


class TestAccountManagement:
    def test_connect_account(self, db, channel, platform):
        acc = connect_account(
            db,
            channel_id=channel.id,
            platform_key="youtube",
            external_account_id="UC-999",
            display_name="Test YouTube",
            actor="cli",
        )
        assert acc.status == "connected"
        assert acc.platform_key == "youtube"

    def test_disconnect_account(self, db, channel, platform):
        acc = connect_account(
            db, channel_id=channel.id, platform_key="youtube",
            external_account_id="UC-001", display_name="YT", actor="cli"
        )
        updated = disconnect_account(db, acc.id, "cli")
        assert updated.status == "disconnected"

    def test_pause_and_resume(self, db, channel, platform):
        acc = connect_account(
            db, channel_id=channel.id, platform_key="youtube",
            external_account_id="UC-002", display_name="YT2", actor="cli"
        )
        paused = pause_account(db, acc.id, "cli")
        assert paused.status == "paused"
        resumed = resume_account(db, acc.id, "cli")
        assert resumed.status == "connected"

    def test_mark_credential_statuses(self, db, channel, platform):
        acc = connect_account(
            db, channel_id=channel.id, platform_key="youtube",
            external_account_id="UC-003", display_name="YT3", actor="cli"
        )
        updated = mark_credential_invalid(db, acc.id, "cli")
        assert updated.status == "credential_invalid"
        updated2 = mark_credential_expiring(db, acc.id, "cli")
        assert updated2.status == "credential_expiring"
        updated3 = mark_quota_limited(db, acc.id, "cli")
        assert updated3.status == "quota_limited"


class TestCredentialProfiles:
    def test_create_profile(self, db, workspace):
        profile = create_credential_profile(
            db,
            workspace_id=workspace.id,
            display_name="YouTube OAuth",
            credential_type="oauth2",
            external_ref="secret-vault-ref-001",
            actor="cli",
        )
        assert profile.status == "active"
        assert profile.external_ref == "secret-vault-ref-001"
        assert profile.credential_type == "oauth2"

    def test_revoke_credential(self, db, workspace):
        profile = create_credential_profile(
            db, workspace_id=workspace.id, display_name="K",
            credential_type="api_key", external_ref="ref", actor="cli"
        )
        updated = revoke_credential(db, profile.id, "admin")
        assert updated.status == "revoked"

    def test_expire_and_reactivate(self, db, workspace):
        profile = create_credential_profile(
            db, workspace_id=workspace.id, display_name="K",
            credential_type="api_key", external_ref="ref2", actor="cli"
        )
        expired = mark_credential_expired(db, profile.id, "cli")
        assert expired.status == "expired"
        active = reactivate_credential(db, profile.id, "cli")
        assert active.status == "active"


class TestPolicies:
    def test_set_and_resolve(self, db, workspace):
        set_policy(
            db,
            scope="workspace",
            scope_id=workspace.id,
            automation_level="supervised",
            allowed_actions=["pause_account"],
            actor="cli",
        )
        level = resolve_effective_level(db, workspace.id)
        assert level == "supervised"

    def test_manual_is_most_restrictive(self, db, workspace):
        set_policy(
            db, scope="workspace", scope_id=workspace.id,
            automation_level="autonomous", allowed_actions=[], actor="cli"
        )
        channel = create_channel(
            db, workspace_id=workspace.id, name="C", slug="c", actor="cli"
        )
        set_policy(
            db, scope="channel", scope_id=channel.id,
            automation_level="manual", allowed_actions=[], actor="cli"
        )
        level = resolve_effective_level(db, workspace.id, channel_id=channel.id)
        assert level == "manual"

    def test_invalid_level_raises(self, db, workspace):
        with pytest.raises(InvalidAutomationLevelError):
            set_policy(
                db, scope="workspace", scope_id=workspace.id,
                automation_level="superauto", allowed_actions=[], actor="cli"
            )

    def test_manual_blocks_actions(self, db, workspace):
        set_policy(
            db, scope="workspace", scope_id=workspace.id,
            automation_level="manual", allowed_actions=[], actor="cli"
        )
        with pytest.raises(PolicyViolationError):
            assert_action_permitted(db, "publish", workspace_id=workspace.id)

    def test_no_policy_defaults_to_manual(self, db):
        level = resolve_effective_level(db, "nonexistent-ws")
        assert level == "manual"


class TestStrategies:
    def test_create_versioned(self, db, channel):
        sp = create_strategy_profile(
            db, channel_id=channel.id, config={"target_ctr": 0.05}, actor="cli"
        )
        assert sp.version == 1
        assert sp.is_active

    def test_second_profile_increments_version(self, db, channel):
        create_strategy_profile(
            db, channel_id=channel.id, config={"v": 1}, actor="cli"
        )
        sp2 = create_strategy_profile(
            db, channel_id=channel.id, config={"v": 2}, actor="cli"
        )
        assert sp2.version == 2

    def test_get_active_returns_latest(self, db, channel):
        create_strategy_profile(db, channel_id=channel.id, config={}, actor="cli")
        create_strategy_profile(db, channel_id=channel.id, config={"x": 1}, actor="cli")
        active = get_active_strategy(db, channel.id)
        assert active is not None
        assert active.version == 2


class TestExperiments:
    def test_create_draft(self, db, workspace, channel):
        exp = create_experiment(
            db, workspace_id=workspace.id, channel_id=channel.id,
            name="CTR test", hypothesis="A > B", primary_metric="ctr", actor="cli"
        )
        assert exp.status == "draft"
        assert exp.activated_at is None

    def test_add_variant(self, db, workspace, channel):
        exp = create_experiment(
            db, workspace_id=workspace.id, channel_id=channel.id,
            name="E", hypothesis="H", primary_metric="m", actor="cli"
        )
        variant = add_variant(
            db, experiment_id=exp.id, name="Control",
            variant_type="control", description="Baseline"
        )
        assert variant.variant_type == "control"

    def test_cannot_add_variant_after_activate(self, db, workspace, channel):
        exp = create_experiment(
            db, workspace_id=workspace.id, channel_id=channel.id,
            name="E2", hypothesis="H", primary_metric="m", actor="cli"
        )
        add_variant(db, experiment_id=exp.id, name="A", variant_type="control")
        activate_experiment(db, exp.id)
        with pytest.raises(ExperimentAlreadyActiveError):
            add_variant(db, experiment_id=exp.id, name="B", variant_type="treatment")

    def test_activate_makes_immutable(self, db, workspace, channel):
        exp = create_experiment(
            db, workspace_id=workspace.id, channel_id=channel.id,
            name="E3", hypothesis="H", primary_metric="m", actor="cli"
        )
        activated = activate_experiment(db, exp.id)
        assert activated.status == "active"
        assert activated.activated_at is not None
        with pytest.raises(ExperimentAlreadyActiveError):
            activate_experiment(db, exp.id)

    def test_conclude_experiment(self, db, workspace, channel):
        exp = create_experiment(
            db, workspace_id=workspace.id, channel_id=channel.id,
            name="E4", hypothesis="H", primary_metric="m", actor="cli"
        )
        activate_experiment(db, exp.id)
        concluded = conclude_experiment(db, exp.id)
        assert concluded.status == "concluded"
        assert concluded.concluded_at is not None

    def test_conclude_requires_active(self, db, workspace, channel):
        exp = create_experiment(
            db, workspace_id=workspace.id, channel_id=channel.id,
            name="E5", hypothesis="H", primary_metric="m", actor="cli"
        )
        with pytest.raises(ExperimentNotActiveError):
            conclude_experiment(db, exp.id)

    def test_assign_unit_idempotent(self, db, workspace, channel):
        exp = create_experiment(
            db, workspace_id=workspace.id, channel_id=channel.id,
            name="E6", hypothesis="H", primary_metric="m", actor="cli"
        )
        add_variant(db, experiment_id=exp.id, name="Control", variant_type="control")
        activate_experiment(db, exp.id)

        a1 = assign_unit(db, exp.id, "unit-123")
        a2 = assign_unit(db, exp.id, "unit-123")
        assert a1.id == a2.id
        assert a1.unit_id == "unit-123"


class TestWorkflows:
    def test_create_workflow(self, db, workspace):
        wf = create_workflow(
            db,
            workspace_id=workspace.id,
            name="Pause on degraded",
            trigger_event_type="health.degraded",
            conditions=[{"field": "status", "operator": "equals", "value": "degraded"}],
            actions=[{"action_type": "notify", "params": {"message": "Alert"}}],
            actor="cli",
        )
        assert wf.status == "draft"
        assert wf.name == "Pause on degraded"

    def test_invalid_condition_raises(self, db, workspace):
        with pytest.raises(InvalidWorkflowConditionError):
            create_workflow(
                db,
                workspace_id=workspace.id,
                name="Bad",
                trigger_event_type="health.degraded",
                conditions=[{"field": "x", "operator": "unknown_op"}],
                actions=[],
                actor="cli",
            )

    def test_invalid_action_raises(self, db, workspace):
        with pytest.raises(InvalidWorkflowActionError):
            create_workflow(
                db,
                workspace_id=workspace.id,
                name="Bad",
                trigger_event_type="health.degraded",
                conditions=[],
                actions=[{"action_type": "fly_to_moon"}],
                actor="cli",
            )

    def test_activate_workflow(self, db, workspace):
        wf = create_workflow(
            db, workspace_id=workspace.id, name="W",
            trigger_event_type="health.degraded",
            conditions=[], actions=[], actor="cli"
        )
        updated = activate_workflow(db, wf.id, "cli")
        assert updated.status == "active"

    def test_pause_workflow(self, db, workspace):
        wf = create_workflow(
            db, workspace_id=workspace.id, name="W2",
            trigger_event_type="health.degraded",
            conditions=[], actions=[], actor="cli"
        )
        activate_workflow(db, wf.id, "cli")
        paused = pause_workflow(db, wf.id, "cli")
        assert paused.status == "paused"
