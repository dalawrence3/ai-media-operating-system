"""Tests for Phase 12 control plane models."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.control_plane.models import (
    AutomationPolicy,
    Channel,
    ChannelDraft,
    ControlEvent,
    ExperimentDraft,
    HealthRecordDraft,
    ProviderRegistryDraft,
    StrategyProfile,
    WorkflowEvaluationResult,
    WorkflowRunResult,
    Workspace,
    WorkspaceDraft,
)

_TS = "2026-08-07T10:00:00+00:00"
_DT = datetime.fromisoformat(_TS)


class TestWorkspace:
    def test_frozen(self):
        ws = Workspace(
            id="ws-1",
            name="Acme",
            slug="acme",
            status="active",
            actor="cli",
            created_at=_DT,
            updated_at=_DT,
        )
        with pytest.raises((TypeError, ValidationError)):
            ws.name = "Other"  # type: ignore[misc]

    def test_metadata_property_empty(self):
        ws = Workspace(
            id="ws-1",
            name="Acme",
            slug="acme",
            status="active",
            actor="cli",
            created_at=_DT,
            updated_at=_DT,
        )
        assert ws.metadata == {}

    def test_metadata_property_parsed(self):
        ws = Workspace(
            id="ws-1",
            name="Acme",
            slug="acme",
            status="active",
            actor="cli",
            created_at=_DT,
            updated_at=_DT,
            metadata_json='{"key": "value"}',
        )
        assert ws.metadata == {"key": "value"}


class TestChannel:
    def test_frozen(self):
        ch = Channel(
            id="ch-1",
            workspace_id="ws-1",
            name="Tech",
            slug="tech",
            status="active",
            actor="cli",
            created_at=_DT,
            updated_at=_DT,
        )
        with pytest.raises((TypeError, ValidationError)):
            ch.name = "Other"  # type: ignore[misc]


class TestControlEvent:
    def test_payload_property(self):
        ev = ControlEvent(
            id="ev-1",
            event_type="workspace.created",
            workspace_id="ws-1",
            actor="cli",
            payload_json='{"x": 1}',
            correlation_id=None,
            causation_id=None,
            created_at=_DT,
        )
        assert ev.payload == {"x": 1}

    def test_frozen(self):
        ev = ControlEvent(
            id="ev-1",
            event_type="workspace.created",
            workspace_id="ws-1",
            actor="cli",
            payload_json="{}",
            correlation_id=None,
            causation_id=None,
            created_at=_DT,
        )
        with pytest.raises((TypeError, ValidationError)):
            ev.actor = "other"  # type: ignore[misc]


class TestAutomationPolicy:
    def test_allowed_actions_property(self):
        policy = AutomationPolicy(
            id="p-1",
            scope="workspace",
            scope_id="ws-1",
            automation_level="supervised",
            allowed_actions_json='["pause_account","notify"]',
            actor="cli",
            created_at=_DT,
            is_active=True,
        )
        assert "pause_account" in policy.allowed_actions
        assert "notify" in policy.allowed_actions


class TestStrategyProfile:
    def test_config_property(self):
        sp = StrategyProfile(
            id="sp-1",
            channel_id="ch-1",
            version=1,
            config_json='{"target_ctr": 0.05}',
            actor="cli",
            created_at=_DT,
            is_active=True,
        )
        assert sp.config == {"target_ctr": 0.05}


class TestWorkspaceDraft:
    def test_mutable(self):
        draft = WorkspaceDraft(id="ws-1", name="Acme", slug="acme", actor="cli")
        draft.name = "Acme Corp"
        assert draft.name == "Acme Corp"

    def test_default_status(self):
        draft = WorkspaceDraft(id="ws-1", name="X", slug="x", actor="cli")
        assert draft.status == "active"


class TestChannelDraft:
    def test_fields(self):
        draft = ChannelDraft(id="ch-1", workspace_id="ws-1", name="Tech", slug="tech", actor="cli")
        assert draft.workspace_id == "ws-1"
        assert draft.status == "active"


class TestExperimentDraft:
    def test_default_status(self):
        draft = ExperimentDraft(
            id="e-1",
            workspace_id="ws-1",
            channel_id="ch-1",
            name="CTR test",
            hypothesis="Thumbnail A beats B",
            primary_metric="ctr",
            actor="cli",
        )
        assert draft.status == "draft"


class TestWorkflowEvaluationResult:
    def test_matched_false_when_conditions_fail(self):
        result = WorkflowEvaluationResult(
            workflow_id="wf-1",
            matched=False,
            conditions_passed=[],
            conditions_failed=["status.equals"],
            actions_to_execute=[],
        )
        assert not result.matched
        assert result.actions_to_execute == []


class TestWorkflowRunResult:
    def test_success_fields(self):
        result = WorkflowRunResult(
            run_id="r-1",
            workflow_id="wf-1",
            success=True,
            actions_executed=["pause_account:acc-1"],
        )
        assert result.error_message is None
        assert len(result.actions_executed) == 1


class TestHealthRecordDraft:
    def test_fields(self):
        draft = HealthRecordDraft(
            id="h-1",
            entity_type="channel",
            entity_id="ch-1",
            status="degraded",
            recorded_by="monitor",
        )
        assert draft.detail is None


class TestProviderRegistryDraft:
    def test_default_status(self):
        draft = ProviderRegistryDraft(
            id="pr-1",
            provider_key="claude",
            domain="ai",
            display_name="Claude",
        )
        assert draft.status == "active"
        assert draft.capabilities == []
