"""Tests for Phase 12 orchestrator, services, jobs, and monitoring."""

from __future__ import annotations

import pytest

from app.control_plane import repository as repo
from app.control_plane.jobs import (
    complete_operation,
    fail_operation,
    start_operation,
    supersede_operation,
)
from app.control_plane.monitoring import (
    find_dead_lettered_events,
    find_stuck_operations,
    workspace_health_summary,
)
from app.control_plane.orchestrator import (
    connect_platform_account,
    provision_channel,
    provision_workspace,
)
from app.control_plane.reviews import build_review_queue
from app.control_plane.services import (
    get_cost_summary,
    get_effective_policy,
    get_event_history,
    get_review_queue,
    list_channels,
    list_platform_accounts,
    list_workspaces,
)


@pytest.fixture()
def db(tmp_path):
    from app.core.database import open_db
    return open_db(tmp_path / "orch.db")


class TestOrchestrator:
    def test_provision_workspace(self, db):
        ws = provision_workspace(db, name="Acme", slug="acme", actor="cli")
        assert ws.id
        assert ws.name == "Acme"
        assert ws.status == "active"

    def test_provision_workspace_emits_event(self, db):
        ws = provision_workspace(db, name="Acme2", slug="acme2", actor="cli")
        events = repo.list_events_by_workspace(db, ws.id)
        assert any(e.event_type == "workspace.created" for e in events)

    def test_provision_channel(self, db):
        ws = provision_workspace(db, name="Corp", slug="corp", actor="cli")
        ch = provision_channel(
            db, workspace_id=ws.id, name="Tech", slug="tech", actor="cli"
        )
        assert ch.workspace_id == ws.id
        events = repo.list_events_by_workspace(db, ws.id)
        assert any(e.event_type == "channel.created" for e in events)

    def test_connect_platform_account(self, db):
        ws = provision_workspace(db, name="C", slug="c", actor="cli")
        ch = provision_channel(db, workspace_id=ws.id, name="N", slug="n", actor="cli")
        repo.ensure_platform(db, "plt-1", "youtube", "YouTube")

        acc = connect_platform_account(
            db,
            channel_id=ch.id,
            platform_key="youtube",
            external_account_id="UC-001",
            display_name="Main YT",
            actor="cli",
        )
        assert acc.status == "connected"
        events = repo.list_events_by_workspace(db, ws.id)
        assert any(e.event_type == "platform_account.connected" for e in events)


class TestServices:
    def test_list_workspaces(self, db):
        provision_workspace(db, name="A", slug="a", actor="cli")
        provision_workspace(db, name="B", slug="b", actor="cli")
        items = list_workspaces(db)
        assert len(items) >= 2

    def test_list_channels(self, db):
        ws = provision_workspace(db, name="W", slug="w", actor="cli")
        provision_channel(db, workspace_id=ws.id, name="C1", slug="c1", actor="cli")
        provision_channel(db, workspace_id=ws.id, name="C2", slug="c2", actor="cli")
        items = list_channels(db, ws.id)
        assert len(items) == 2

    def test_list_platform_accounts(self, db):
        ws = provision_workspace(db, name="W2", slug="w2", actor="cli")
        ch = provision_channel(db, workspace_id=ws.id, name="C", slug="c", actor="cli")
        repo.ensure_platform(db, "plt-1", "youtube", "YouTube")
        connect_platform_account(
            db, channel_id=ch.id, platform_key="youtube",
            external_account_id="UC-1", display_name="YT1", actor="cli"
        )
        items = list_platform_accounts(db, ch.id)
        assert len(items) == 1

    def test_get_effective_policy_defaults_manual(self, db):
        ws = provision_workspace(db, name="W3", slug="w3", actor="cli")
        level = get_effective_policy(db, ws.id)
        assert level == "manual"

    def test_get_event_history(self, db):
        ws = provision_workspace(db, name="W4", slug="w4", actor="cli")
        events = get_event_history(db, ws.id)
        assert len(events) >= 1
        assert events[0].event_type == "workspace.created"

    def test_get_cost_summary_empty(self, db):
        ws = provision_workspace(db, name="W5", slug="w5", actor="cli")
        summary = get_cost_summary(db, ws.id)
        assert summary["total_usd"] == 0.0
        assert summary["record_count"] == 0


class TestJobControl:
    def test_start_and_complete_operation(self, db):
        ws = provision_workspace(db, name="WJ", slug="wj", actor="cli")
        op = start_operation(
            db,
            operation_type="publish",
            workspace_id=ws.id,
            actor="cli",
            input_data={"video_id": "v-1"},
        )
        assert op.status == "pending"
        completed = complete_operation(db, op.id, output_data={"published": True})
        assert completed.status == "completed"

    def test_idempotent_start_operation(self, db):
        ws = provision_workspace(db, name="WI", slug="wi", actor="cli")
        op1 = start_operation(
            db, operation_type="ingest", workspace_id=ws.id, actor="cli"
        )
        op2 = start_operation(
            db, operation_type="ingest", workspace_id=ws.id, actor="cli"
        )
        assert op1.id == op2.id

    def test_fail_operation(self, db):
        ws = provision_workspace(db, name="WF", slug="wf", actor="cli")
        op = start_operation(
            db, operation_type="publish", workspace_id=ws.id, actor="cli",
            input_data={"video_id": "v-2"},
        )
        failed = fail_operation(db, op.id, "Connection timeout")
        assert failed.status == "failed"
        assert failed.error_message == "Connection timeout"

    def test_supersede_operation(self, db):
        ws = provision_workspace(db, name="WS", slug="ws", actor="cli")
        op = start_operation(
            db, operation_type="render", workspace_id=ws.id, actor="cli",
            input_data={"script_id": "s-1"},
        )
        sup = supersede_operation(db, op.id)
        assert sup.status == "superseded"

    def test_custom_idempotency_key(self, db):
        ws = provision_workspace(db, name="WK", slug="wk", actor="cli")
        op1 = start_operation(
            db, operation_type="export", workspace_id=ws.id, actor="cli",
            idempotency_key="export-2026-08-07-001"
        )
        op2 = start_operation(
            db, operation_type="export", workspace_id=ws.id, actor="cli",
            idempotency_key="export-2026-08-07-001"
        )
        assert op1.id == op2.id


class TestMonitoring:
    def test_find_stuck_operations_empty(self, db):
        provision_workspace(db, name="WM", slug="wm", actor="cli")
        stuck = find_stuck_operations(db, older_than_minutes=0)
        assert isinstance(stuck, list)

    def test_find_dead_lettered_empty(self, db):
        items = find_dead_lettered_events(db)
        assert isinstance(items, list)

    def test_workspace_health_summary(self, db):
        ws = provision_workspace(db, name="WH", slug="wh", actor="cli")
        summary = workspace_health_summary(db, ws.id)
        assert summary["workspace_id"] == ws.id
        assert summary["channel_count"] == 0

    def test_workspace_health_summary_with_channels(self, db):
        ws = provision_workspace(db, name="WH2", slug="wh2", actor="cli")
        provision_channel(db, workspace_id=ws.id, name="C1", slug="c1", actor="cli")
        provision_channel(db, workspace_id=ws.id, name="C2", slug="c2", actor="cli")
        summary = workspace_health_summary(db, ws.id)
        assert summary["channel_count"] == 2


class TestReviewQueue:
    def test_empty_review_queue(self, db):
        ws = provision_workspace(db, name="WR", slug="wr", actor="cli")
        items = get_review_queue(db, ws.id)
        assert isinstance(items, list)

    def test_review_queue_returns_list(self, db):
        ws = provision_workspace(db, name="WR2", slug="wr2", actor="cli")
        from app.control_plane.health import record_health
        record_health(
            db, entity_type="platform_account", entity_id="acc-fail",
            status="failed", recorded_by="monitor"
        )
        items = build_review_queue(db, ws.id)
        assert isinstance(items, list)
        health_items = [i for i in items if i.item_type == "health_issue"]
        assert len(health_items) >= 1
