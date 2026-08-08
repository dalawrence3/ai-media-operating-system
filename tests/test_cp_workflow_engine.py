"""Tests for Phase 12 workflow engine — condition evaluation and action dispatch."""

from __future__ import annotations

from datetime import UTC, datetime

from app.control_plane.models import (
    ControlEvent,
    Workflow,
)
from app.control_plane.workflow_engine import (
    _evaluate_condition,
    _get_nested,
    evaluate_workflow,
    execute_actions,
    run_workflow,
)

_DT = datetime(2026, 8, 7, 10, 0, 0, tzinfo=UTC)


def _make_event(payload: dict) -> ControlEvent:
    import json

    return ControlEvent(
        id="ev-1",
        event_type="health.degraded",
        workspace_id="ws-1",
        actor="cli",
        payload_json=json.dumps(payload),
        correlation_id=None,
        causation_id=None,
        created_at=_DT,
    )


def _make_workflow(conditions, actions, status="active") -> Workflow:
    import json

    return Workflow(
        id="wf-1",
        workspace_id="ws-1",
        name="Test",
        trigger_event_type="health.degraded",
        conditions_json=json.dumps(conditions),
        actions_json=json.dumps(actions),
        status=status,
        actor="cli",
        created_at=_DT,
        updated_at=_DT,
    )


class TestGetNested:
    def test_top_level(self):
        assert _get_nested({"a": 1}, "a") == 1

    def test_nested(self):
        assert _get_nested({"a": {"b": 2}}, "a.b") == 2

    def test_missing(self):
        assert _get_nested({}, "x") is None

    def test_deeply_nested(self):
        assert _get_nested({"a": {"b": {"c": "yes"}}}, "a.b.c") == "yes"


class TestEvaluateCondition:
    def test_equals_true(self):
        cond = {"field": "status", "operator": "equals", "value": "degraded"}
        assert _evaluate_condition(cond, {"status": "degraded"})

    def test_equals_false(self):
        cond = {"field": "status", "operator": "equals", "value": "degraded"}
        assert not _evaluate_condition(cond, {"status": "healthy"})

    def test_not_equals(self):
        cond = {"field": "x", "operator": "not_equals", "value": "a"}
        assert _evaluate_condition(cond, {"x": "b"})
        assert not _evaluate_condition(cond, {"x": "a"})

    def test_greater_than(self):
        cond = {"field": "count", "operator": "greater_than", "value": 5}
        assert _evaluate_condition(cond, {"count": 10})
        assert not _evaluate_condition(cond, {"count": 3})

    def test_less_than(self):
        cond = {"field": "count", "operator": "less_than", "value": 5}
        assert _evaluate_condition(cond, {"count": 2})
        assert not _evaluate_condition(cond, {"count": 10})

    def test_in(self):
        cond = {"field": "status", "operator": "in", "value": ["a", "b"]}
        assert _evaluate_condition(cond, {"status": "a"})
        assert not _evaluate_condition(cond, {"status": "c"})

    def test_not_in(self):
        cond = {"field": "status", "operator": "not_in", "value": ["a", "b"]}
        assert _evaluate_condition(cond, {"status": "c"})
        assert not _evaluate_condition(cond, {"status": "a"})

    def test_exists_true(self):
        cond = {"field": "account_id", "operator": "exists"}
        assert _evaluate_condition(cond, {"account_id": "abc"})

    def test_exists_false(self):
        cond = {"field": "account_id", "operator": "exists"}
        assert not _evaluate_condition(cond, {})

    def test_boolean_true(self):
        cond = {"field": "is_active", "operator": "boolean"}
        assert _evaluate_condition(cond, {"is_active": True})
        assert not _evaluate_condition(cond, {"is_active": False})

    def test_numeric_string_comparison(self):
        cond = {"field": "ratio", "operator": "greater_than", "value": "0.5"}
        assert _evaluate_condition(cond, {"ratio": "0.9"})

    def test_invalid_numeric_returns_false(self):
        cond = {"field": "x", "operator": "greater_than", "value": 5}
        assert not _evaluate_condition(cond, {"x": "not_a_number"})


class TestEvaluateWorkflow:
    def test_all_conditions_pass(self):
        wf = _make_workflow(
            conditions=[
                {"field": "status", "operator": "equals", "value": "degraded"},
                {"field": "entity_type", "operator": "equals", "value": "channel"},
            ],
            actions=[{"action_type": "notify", "params": {}}],
        )
        event = _make_event({"status": "degraded", "entity_type": "channel"})
        result = evaluate_workflow(wf, event)
        assert result.matched
        assert len(result.conditions_passed) == 2
        assert len(result.conditions_failed) == 0
        assert len(result.actions_to_execute) == 1

    def test_one_condition_fails(self):
        wf = _make_workflow(
            conditions=[
                {"field": "status", "operator": "equals", "value": "degraded"},
                {"field": "entity_type", "operator": "equals", "value": "workspace"},
            ],
            actions=[{"action_type": "notify", "params": {}}],
        )
        event = _make_event({"status": "degraded", "entity_type": "channel"})
        result = evaluate_workflow(wf, event)
        assert not result.matched
        assert len(result.conditions_failed) == 1
        assert result.actions_to_execute == []

    def test_no_conditions_always_matches(self):
        wf = _make_workflow(conditions=[], actions=[{"action_type": "notify", "params": {}}])
        event = _make_event({})
        result = evaluate_workflow(wf, event)
        assert result.matched


class TestExecuteActions:
    def test_notify_action(self, tmp_path):
        from app.core.database import open_db

        db = open_db(tmp_path / "db.db")
        event = _make_event({})
        actions = [
            {"action_type": "notify", "params": {"message": "Test alert"}, "params_json": "{}"}
        ]
        executed = execute_actions(db, actions, event)
        assert any("notify" in a for a in executed)

    def test_queue_review_action(self, tmp_path):
        from app.core.database import open_db

        db = open_db(tmp_path / "db.db")
        event = _make_event({})
        actions = [{"action_type": "queue_review", "params": {"item_type": "health_degraded"}}]
        executed = execute_actions(db, actions, event)
        assert any("queue_review" in a for a in executed)

    def test_pause_account_action(self, tmp_path):
        from app.control_plane import repository as repo
        from app.control_plane.accounts import connect_account
        from app.control_plane.identity import create_channel, create_workspace
        from app.core.database import open_db

        db = open_db(tmp_path / "db.db")
        ws = create_workspace(db, name="W", slug="w", actor="cli")
        ch = create_channel(db, workspace_id=ws.id, name="C", slug="c", actor="cli")
        repo.ensure_platform(db, "plt-1", "youtube", "YouTube")
        acc = connect_account(
            db,
            channel_id=ch.id,
            platform_key="youtube",
            external_account_id="UC-999",
            display_name="YT",
            actor="cli",
        )
        event = _make_event({"account_id": acc.id})
        actions = [{"action_type": "pause_account", "params": {"platform_account_id": acc.id}}]
        executed = execute_actions(db, actions, event)
        assert any("pause_account" in a for a in executed)
        updated = repo.get_platform_account(db, acc.id)
        assert updated.status == "paused"


class TestRunWorkflow:
    def test_matched_workflow_runs_actions(self, tmp_path):
        from app.control_plane.workflows import (
            activate_workflow,
            create_workflow,
            start_workflow_run,
        )
        from app.core.database import open_db

        db = open_db(tmp_path / "db.db")
        from app.control_plane.identity import create_workspace

        ws = create_workspace(db, name="W", slug="w", actor="cli")

        event_draft = ControlEvent(
            id="ev-99",
            event_type="health.degraded",
            workspace_id=ws.id,
            actor="cli",
            payload_json="{}",
            correlation_id=None,
            causation_id=None,
            created_at=_DT,
        )
        # Store event manually
        db.execute(
            "INSERT INTO cp_events "
            "(id, event_type, workspace_id, actor, payload_json, "
            "correlation_id, causation_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("ev-99", "health.degraded", ws.id, "cli", "{}", None, None, _DT.isoformat()),
        )
        db.commit()

        wf = create_workflow(
            db,
            workspace_id=ws.id,
            name="Alert",
            trigger_event_type="health.degraded",
            conditions=[],
            actions=[{"action_type": "notify", "params": {"message": "Hi"}}],
            actor="cli",
        )
        activate_workflow(db, wf.id, "cli")
        run = start_workflow_run(db, wf.id, "ev-99")
        result = run_workflow(db, wf, event_draft, run)
        assert result.success
        assert len(result.actions_executed) >= 1

    def test_unmatched_workflow_succeeds_with_no_actions(self, tmp_path):
        from app.control_plane import repository as repo
        from app.control_plane.workflows import create_workflow, start_workflow_run
        from app.core.database import open_db

        db = open_db(tmp_path / "db.db")
        from app.control_plane.identity import create_workspace

        ws = create_workspace(db, name="W2", slug="w2", actor="cli")

        db.execute(
            "INSERT INTO cp_events "
            "(id, event_type, workspace_id, actor, payload_json, "
            "correlation_id, causation_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("ev-88", "health.degraded", ws.id, "cli", "{}", None, None, _DT.isoformat()),
        )
        db.commit()

        wf = create_workflow(
            db,
            workspace_id=ws.id,
            name="Cond",
            trigger_event_type="health.degraded",
            conditions=[{"field": "status", "operator": "equals", "value": "never"}],
            actions=[{"action_type": "notify", "params": {}}],
            actor="cli",
        )
        workflow_obj = repo.get_workflow(db, wf.id)
        run = start_workflow_run(db, wf.id, "ev-88")
        from app.control_plane.models import ControlEvent

        event = ControlEvent(
            id="ev-88",
            event_type="health.degraded",
            workspace_id=ws.id,
            actor="cli",
            payload_json="{}",
            correlation_id=None,
            causation_id=None,
            created_at=_DT,
        )
        result = run_workflow(db, workflow_obj, event, run)
        assert result.success
        assert result.actions_executed == []
