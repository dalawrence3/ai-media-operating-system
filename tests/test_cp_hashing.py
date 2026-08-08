"""Tests for Phase 12 control plane hashing."""

from __future__ import annotations

from app.control_plane.hashing import (
    ChannelHashInput,
    CredentialProfileHashInput,
    EventHashInput,
    ExperimentHashInput,
    OperationHashInput,
    WorkflowHashInput,
    WorkspaceHashInput,
    compute_channel_hash,
    compute_credential_hash,
    compute_event_hash,
    compute_experiment_hash,
    compute_operation_idempotency_key,
    compute_workflow_hash,
    compute_workspace_hash,
)


class TestWorkspaceHash:
    def test_deterministic(self):
        inp = WorkspaceHashInput(name="Acme", slug="acme", actor="cli")
        assert compute_workspace_hash(inp) == compute_workspace_hash(inp)

    def test_different_inputs_different_hashes(self):
        a = WorkspaceHashInput(name="Acme", slug="acme", actor="cli")
        b = WorkspaceHashInput(name="Acme2", slug="acme", actor="cli")
        assert compute_workspace_hash(a) != compute_workspace_hash(b)

    def test_hex_length(self):
        inp = WorkspaceHashInput(name="X", slug="x", actor="cli")
        h = compute_workspace_hash(inp)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


class TestChannelHash:
    def test_deterministic(self):
        inp = ChannelHashInput(workspace_id="ws-1", name="Tech", slug="tech", actor="cli")
        assert compute_channel_hash(inp) == compute_channel_hash(inp)

    def test_slug_matters(self):
        a = ChannelHashInput(workspace_id="ws-1", name="Tech", slug="tech", actor="cli")
        b = ChannelHashInput(workspace_id="ws-1", name="Tech", slug="tech2", actor="cli")
        assert compute_channel_hash(a) != compute_channel_hash(b)


class TestCredentialHash:
    def test_deterministic(self):
        inp = CredentialProfileHashInput(
            workspace_id="ws-1",
            display_name="YouTube OAuth",
            credential_type="oauth2",
            external_ref="ref-123",
            actor="cli",
        )
        assert compute_credential_hash(inp) == compute_credential_hash(inp)

    def test_different_refs(self):
        a = CredentialProfileHashInput(
            workspace_id="ws-1",
            display_name="YT",
            credential_type="oauth2",
            external_ref="ref-1",
            actor="cli",
        )
        b = CredentialProfileHashInput(
            workspace_id="ws-1",
            display_name="YT",
            credential_type="oauth2",
            external_ref="ref-2",
            actor="cli",
        )
        assert compute_credential_hash(a) != compute_credential_hash(b)


class TestEventHash:
    def test_deterministic(self):
        inp = EventHashInput(
            event_type="workspace.created",
            workspace_id="ws-1",
            actor="cli",
            payload={"key": "val"},
            correlation_id=None,
        )
        assert compute_event_hash(inp) == compute_event_hash(inp)

    def test_payload_order_independent(self):
        a = EventHashInput(
            event_type="x",
            workspace_id="ws-1",
            actor="cli",
            payload={"a": 1, "b": 2},
            correlation_id=None,
        )
        b = EventHashInput(
            event_type="x",
            workspace_id="ws-1",
            actor="cli",
            payload={"b": 2, "a": 1},
            correlation_id=None,
        )
        assert compute_event_hash(a) == compute_event_hash(b)


class TestWorkflowHash:
    def test_deterministic(self):
        inp = WorkflowHashInput(
            workspace_id="ws-1",
            name="Pause on error",
            trigger_event_type="health.degraded",
            conditions=[{"field": "status", "operator": "equals", "value": "degraded"}],
            actions=[{"action_type": "pause_account", "params": {}}],
            actor="cli",
        )
        assert compute_workflow_hash(inp) == compute_workflow_hash(inp)


class TestExperimentHash:
    def test_deterministic(self):
        inp = ExperimentHashInput(
            workspace_id="ws-1",
            channel_id="ch-1",
            name="CTR test",
            hypothesis="Thumbnail A beats B",
            primary_metric="ctr",
            actor="cli",
        )
        assert compute_experiment_hash(inp) == compute_experiment_hash(inp)

    def test_name_matters(self):
        a = ExperimentHashInput(
            workspace_id="ws-1",
            channel_id="ch-1",
            name="A",
            hypothesis="H",
            primary_metric="ctr",
            actor="cli",
        )
        b = ExperimentHashInput(
            workspace_id="ws-1",
            channel_id="ch-1",
            name="B",
            hypothesis="H",
            primary_metric="ctr",
            actor="cli",
        )
        assert compute_experiment_hash(a) != compute_experiment_hash(b)


class TestOperationHash:
    def test_deterministic(self):
        inp = OperationHashInput(
            operation_type="publish",
            workspace_id="ws-1",
            actor="cli",
            input_data={"video_id": "v-1"},
        )
        assert compute_operation_idempotency_key(inp) == compute_operation_idempotency_key(inp)

    def test_none_input_data(self):
        inp = OperationHashInput(
            operation_type="publish", workspace_id="ws-1", actor="cli", input_data=None
        )
        key = compute_operation_idempotency_key(inp)
        assert len(key) == 64

    def test_different_ops(self):
        a = OperationHashInput("publish", "ws-1", "cli", None)
        b = OperationHashInput("ingest", "ws-1", "cli", None)
        assert compute_operation_idempotency_key(a) != compute_operation_idempotency_key(b)
