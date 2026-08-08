"""Tests for Phase 12 event bus — idempotency, dispatch, dead-letter."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.control_plane import event_bus
from app.control_plane.events import emit_event
from app.control_plane.models import ControlEvent

_DT = datetime(2026, 8, 7, tzinfo=UTC)


@pytest.fixture()
def db(tmp_path):
    from app.core.database import open_db

    return open_db(tmp_path / "bus.db")


@pytest.fixture()
def workspace(db):
    from app.control_plane.identity import create_workspace

    return create_workspace(db, name="W", slug="w", actor="cli")


@pytest.fixture(autouse=True)
def clear_handlers():
    event_bus.clear_handlers()
    yield
    event_bus.clear_handlers()


class TestEmitEvent:
    def test_emit_creates_event(self, db, workspace):
        ev = emit_event(
            db,
            event_type="workspace.created",
            workspace_id=workspace.id,
            actor="cli",
            payload={"workspace_id": workspace.id},
        )
        assert ev.event_type == "workspace.created"
        assert ev.workspace_id == workspace.id

    def test_invalid_event_type_raises(self, db, workspace):
        from app.control_plane.errors import InvalidEventTypeError

        with pytest.raises(InvalidEventTypeError):
            emit_event(
                db,
                event_type="totally.fake.event",
                workspace_id=workspace.id,
                actor="cli",
            )


class TestDispatchEvent:
    def test_handler_called(self, db, workspace):
        calls = []

        def handler(conn, event: ControlEvent) -> None:
            calls.append(event.event_type)

        event_bus.register_handler("workspace.created", "test_handler", handler)
        ev = emit_event(db, event_type="workspace.created", workspace_id=workspace.id, actor="cli")
        event_bus.dispatch_event(db, ev)
        assert len(calls) == 1
        assert calls[0] == "workspace.created"

    def test_idempotent_delivery(self, db, workspace):
        calls = []

        def handler(conn, event: ControlEvent) -> None:
            calls.append(1)

        event_bus.register_handler("workspace.created", "idem_handler", handler)
        ev = emit_event(db, event_type="workspace.created", workspace_id=workspace.id, actor="cli")
        event_bus.dispatch_event(db, ev)
        event_bus.dispatch_event(db, ev)
        assert len(calls) == 1

    def test_failed_handler_marks_failed(self, db, workspace):
        def failing_handler(conn, event: ControlEvent) -> None:
            raise ValueError("simulated failure")

        event_bus.register_handler("channel.created", "fail_handler", failing_handler)
        ev = emit_event(db, event_type="channel.created", workspace_id=workspace.id, actor="cli")
        results = event_bus.dispatch_event(db, ev)
        assert len(results) == 1
        assert results[0].status in ("failed", "dead_lettered")

    def test_dead_letter_after_max_attempts(self, db, workspace):
        from app.control_plane.constants import MAX_DELIVERY_ATTEMPTS

        call_count = [0]

        def failing_handler(conn, event: ControlEvent) -> None:
            call_count[0] += 1
            raise ValueError("always fails")

        event_bus.register_handler("channel.paused", "dl_handler", failing_handler)
        ev = emit_event(db, event_type="channel.paused", workspace_id=workspace.id, actor="cli")
        for _ in range(MAX_DELIVERY_ATTEMPTS):
            event_bus.dispatch_event(db, ev)

        rows = db.execute(
            "SELECT status FROM cp_event_processing WHERE event_id = ? AND handler_key = ?",
            (ev.id, "dl_handler"),
        ).fetchall()
        statuses = [r[0] for r in rows]
        assert any(s == "dead_lettered" for s in statuses)

    def test_no_handlers_returns_empty(self, db, workspace):
        ev = emit_event(
            db, event_type="workspace.suspended", workspace_id=workspace.id, actor="cli"
        )
        results = event_bus.dispatch_event(db, ev)
        assert results == []

    def test_multiple_handlers_independent(self, db, workspace):
        results_a = []
        results_b = []

        event_bus.register_handler(
            "workspace.archived", "handler_a", lambda c, e: results_a.append(1)
        )
        event_bus.register_handler(
            "workspace.archived", "handler_b", lambda c, e: results_b.append(1)
        )

        ev = emit_event(db, event_type="workspace.archived", workspace_id=workspace.id, actor="cli")
        event_bus.dispatch_event(db, ev)
        assert len(results_a) == 1
        assert len(results_b) == 1
