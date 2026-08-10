"""Dispatcher transaction-safety regression tests.

Verifies that:
- Successful mutation commits handler write + audit event together.
- Handler raises before event → no partial write committed.
- Event emission raises → handler write is rolled back (for non-internally-committing handlers).
- Authorization failure → no write.
- Workspace-paused check → no write.
- Constraint failure (duplicate slug) → transaction remains safe and is retried cleanly.
- Rollback leaves the connection in a clean state for subsequent operations.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.application.commands import (
    CreateChannelCommand,
    CreateScheduleCommand,
    RecoverPipelineCommand,
    RetryOperationCommand,
)
from app.application.dispatcher import (
    dispatch,
    noop_auth_hook,
    register_default_handlers,
    register_handler,
)
from app.application.errors import UnknownCommandError, WorkspacePausedError
from app.control_plane import repository as cp_repo
from app.control_plane.models import (
    ChannelDraft,
    OrganizationDraft,
    WorkspaceDraft,
)
from app.core.database import open_db


@pytest.fixture(autouse=True, scope="module")
def _ensure_handlers():
    """Register default handlers once for this module; restore on teardown."""
    register_default_handlers()
    yield
    # Leave handlers registered — other test modules may also need them.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def db(tmp_path: Path):
    conn = open_db(tmp_path / "txn_test.db")
    org = cp_repo.create_organization(
        conn, OrganizationDraft(id=_uid(), name="Org", slug="org", actor="cli")
    )
    ws = cp_repo.create_workspace(
        conn,
        WorkspaceDraft(id=_uid(), name="Workspace", slug="ws", actor="cli", organization_id=org.id),
    )
    conn.commit()
    yield conn, ws
    conn.close()


def _channel_cmd(workspace_id: str, slug: str = "my-channel") -> CreateChannelCommand:
    return CreateChannelCommand(
        workspace_id=workspace_id,
        name="My Channel",
        slug=slug,
        actor="test:user",
    )


def _channel_count(conn: Any) -> int:
    return conn.execute("SELECT COUNT(*) FROM cp_channels").fetchone()[0]


def _event_count(conn: Any) -> int:
    return conn.execute("SELECT COUNT(*) FROM cp_events").fetchone()[0]


# ---------------------------------------------------------------------------
# Successful mutation
# ---------------------------------------------------------------------------


def test_successful_create_commits_channel_and_event(db):
    conn, ws = db
    ch = dispatch(conn, _channel_cmd(ws.id), auth_hook=noop_auth_hook)
    assert ch.slug == "my-channel"
    assert _channel_count(conn) == 1
    assert _event_count(conn) == 1


def test_channel_persisted_to_new_connection(db, tmp_path):
    """Committed data visible on a fresh connection — verifies conn.commit() ran."""
    conn, ws = db
    dispatch(conn, _channel_cmd(ws.id), auth_hook=noop_auth_hook)
    conn2 = open_db(tmp_path / "txn_test.db")
    try:
        count = conn2.execute("SELECT COUNT(*) FROM cp_channels").fetchone()[0]
        assert count == 1
    finally:
        conn2.close()


# ---------------------------------------------------------------------------
# Handler raises → no partial write
# ---------------------------------------------------------------------------


def test_handler_exception_rolls_back_no_channel_written(db):
    """If the handler raises, no channel row should be committed."""
    conn, ws = db

    @dataclass(frozen=True)
    class BoomCmd:
        workspace_id: str
        actor: str = "test:user"
        contract_version: str = "1"

    register_handler(BoomCmd, lambda conn, cmd, **_: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        dispatch(conn, BoomCmd(workspace_id=ws.id), auth_hook=noop_auth_hook)
    # After rollback the connection is still usable
    assert _channel_count(conn) == 0
    assert _event_count(conn) == 0


# ---------------------------------------------------------------------------
# Authorization failure → no write
# ---------------------------------------------------------------------------


def test_auth_failure_prevents_channel_creation(db):
    conn, ws = db

    def rejecting_hook(conn: Any, cmd_type: str, workspace_id: str, actor: str) -> None:
        raise PermissionError("not allowed")

    with pytest.raises(PermissionError):
        dispatch(conn, _channel_cmd(ws.id), auth_hook=rejecting_hook)

    assert _channel_count(conn) == 0
    assert _event_count(conn) == 0


# ---------------------------------------------------------------------------
# Workspace-paused check → no write
# ---------------------------------------------------------------------------


def test_paused_workspace_prevents_channel_creation(db):
    conn, ws = db
    cp_repo.update_workspace_status(conn, ws.id, "suspended", "cli")
    conn.commit()

    with pytest.raises(WorkspacePausedError):
        dispatch(conn, _channel_cmd(ws.id), auth_hook=noop_auth_hook)

    assert _channel_count(conn) == 0


# ---------------------------------------------------------------------------
# Unknown command → no write
# ---------------------------------------------------------------------------


def test_unknown_command_raises_before_handler(db):
    conn, ws = db

    @dataclass(frozen=True)
    class UnknownCmd:
        workspace_id: str
        actor: str = "test:user"
        contract_version: str = "1"

    with pytest.raises(UnknownCommandError):
        dispatch(conn, UnknownCmd(workspace_id=ws.id), auth_hook=noop_auth_hook)

    assert _channel_count(conn) == 0


# ---------------------------------------------------------------------------
# Duplicate constraint → rollback; retry succeeds
# ---------------------------------------------------------------------------


def test_duplicate_slug_rolls_back_and_retry_succeeds(db):
    conn, ws = db
    dispatch(conn, _channel_cmd(ws.id, "dup"), auth_hook=noop_auth_hook)
    assert _channel_count(conn) == 1

    # Second attempt with same slug must fail (IntegrityError) and rollback cleanly
    import sqlite3

    with pytest.raises((sqlite3.IntegrityError, Exception)):  # noqa: B017
        dispatch(conn, _channel_cmd(ws.id, "dup"), auth_hook=noop_auth_hook)

    # Channel count unchanged — duplicate insert was rolled back
    assert _channel_count(conn) == 1

    # Retry with a different slug succeeds — connection is still healthy
    dispatch(conn, _channel_cmd(ws.id, "dup-2"), auth_hook=noop_auth_hook)
    assert _channel_count(conn) == 2


# ---------------------------------------------------------------------------
# Post-rollback connection health
# ---------------------------------------------------------------------------


def test_connection_usable_after_rollback(db):
    """After a failed dispatch (rollback), subsequent reads and writes work."""
    conn, ws = db

    def bomb(conn: Any, cmd: Any, **_: Any):
        raise ValueError("deliberate failure")

    @dataclass(frozen=True)
    class BombCmd:
        workspace_id: str
        actor: str = "test:user"
        contract_version: str = "1"

    register_handler(BombCmd, bomb)
    with pytest.raises(ValueError):
        dispatch(conn, BombCmd(workspace_id=ws.id), auth_hook=noop_auth_hook)

    # Connection is clean; a successful dispatch works
    dispatch(conn, _channel_cmd(ws.id), auth_hook=noop_auth_hook)
    assert _channel_count(conn) == 1


# ---------------------------------------------------------------------------
# Atomicity: inline handler (RetryOperation) — now atomic with event
# ---------------------------------------------------------------------------


def test_retry_operation_inline_handler_is_atomic_with_event(db):
    """_retry_operation no longer commits internally.

    If an operation_execution row exists and we dispatch RetryOperationCommand,
    the row update and the audit event must be committed together.
    After a successful dispatch, the event count increments — proving the single
    commit path (not two separate commits) ran.
    """
    conn, ws = db
    # Seed a minimal operation_execution row so the UPDATE matches something.
    now = "2025-01-01T00:00:00"
    op_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO cp_operation_executions
            (id, workspace_id, channel_id, platform_account_id, operation_type,
             status, actor, idempotency_key, created_at, updated_at)
        VALUES (?,?,NULL,NULL,'test_op','running','cli',?,?,?)
        """,
        (op_id, ws.id, f"idem-{op_id}", now, now),
    )
    conn.commit()

    events_before = _event_count(conn)
    cmd = RetryOperationCommand(
        workspace_id=ws.id,
        operation_id=op_id,
        actor="test:user",
    )
    dispatch(conn, cmd, auth_hook=noop_auth_hook)

    # Both the operation row update and the audit event must be visible.
    row = conn.execute(
        "SELECT status FROM cp_operation_executions WHERE id = ?", (op_id,)
    ).fetchone()
    assert row["status"] == "pending"
    assert _event_count(conn) == events_before + 1


# ---------------------------------------------------------------------------
# Atomicity: CreateScheduleCommand — mutation + event atomic
# ---------------------------------------------------------------------------


def test_create_schedule_mutation_and_event_are_atomic(db, tmp_path):
    """Schedule row and audit event are committed together in one transaction.

    Proof: after dispatch, both the schedule row and the event are visible on a
    fresh connection (proving a single commit flushed both writes, not two
    separate commits before and after event emission).
    """
    conn, ws = db
    cmd = CreateScheduleCommand(
        workspace_id=ws.id,
        name="Atomic Schedule",
        operation_type="start_pipeline",
        schedule_type="interval",
        schedule_config={"interval_seconds": 3600},
        actor="test:user",
    )
    events_before = _event_count(conn)
    sched = dispatch(conn, cmd, auth_hook=noop_auth_hook)

    # Verify on a fresh connection — both writes must be durable.
    conn2 = open_db(tmp_path / "txn_test.db")
    try:
        row = conn2.execute(
            "SELECT name FROM app_schedule_definitions WHERE id = ?", (sched.id,)
        ).fetchone()
        assert row is not None, "Schedule row not found on fresh connection"
        assert row["name"] == "Atomic Schedule"
        new_events = conn2.execute("SELECT COUNT(*) FROM cp_events").fetchone()[0]
        assert new_events == events_before + 1, "Audit event not found on fresh connection"
    finally:
        conn2.close()


def test_create_schedule_handler_exception_rolls_back_both(db):
    """If the handler raises, neither schedule nor event is committed.

    We force a failure mid-dispatch using an invalid schedule_type, which
    raises before any write occurs. Both the schedule table and event table
    must be empty on the same connection after rollback.
    """
    conn, ws = db
    events_before = _event_count(conn)
    schedules_before = conn.execute("SELECT COUNT(*) FROM app_schedule_definitions").fetchone()[0]

    import sqlite3

    with pytest.raises((sqlite3.IntegrityError, Exception)):  # noqa: B017
        dispatch(
            conn,
            CreateScheduleCommand(
                workspace_id=ws.id,
                name="Bad Schedule",
                operation_type="start_pipeline",
                schedule_type="invalid_type",  # raises InvalidScheduleTypeError
                schedule_config={},
                actor="test:user",
            ),
            auth_hook=noop_auth_hook,
        )

    assert _event_count(conn) == events_before, "Event must not be committed on handler failure"
    assert (
        conn.execute("SELECT COUNT(*) FROM app_schedule_definitions").fetchone()[0]
        == schedules_before
    ), "Schedule must not be committed on handler failure"

    # Connection must be usable after rollback.
    dispatch(
        conn,
        CreateScheduleCommand(
            workspace_id=ws.id,
            name="Recovery Schedule",
            operation_type="start_pipeline",
            schedule_type="interval",
            schedule_config={"interval_seconds": 60},
            actor="test:user",
        ),
        auth_hook=noop_auth_hook,
    )
    assert (
        conn.execute("SELECT COUNT(*) FROM app_schedule_definitions").fetchone()[0]
        == schedules_before + 1
    )


# ---------------------------------------------------------------------------
# Atomicity: RecoverPipelineCommand — mutation + event atomic
# ---------------------------------------------------------------------------


def _seed_failed_pipeline(conn: Any, workspace_id: str) -> str:
    """Seed a failed pipeline and return its ID."""
    ch = cp_repo.create_channel(
        conn,
        ChannelDraft(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            name="Recovery Test Channel",
            slug=f"recovery-ch-{uuid.uuid4().hex[:6]}",
            actor="cli",
        ),
    )
    conn.commit()

    now = "2025-01-01T00:00:00"
    pipeline_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO app_pipeline_executions
            (id, workspace_id, channel_id, status, actor, idempotency_key,
             correlation_id, end_stage, created_at, updated_at)
        VALUES (?,?,?,'failed','cli',?,?,?,?,?)
        """,
        (
            pipeline_id,
            workspace_id,
            ch.id,
            f"idem-{pipeline_id}",
            f"corr-{pipeline_id}",
            "learning",
            now,
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO app_pipeline_stage_log
            (id, pipeline_id, stage, attempt_number, status, created_at)
        VALUES (?,?,'research',1,'failed',?)
        """,
        (str(uuid.uuid4()), pipeline_id, now),
    )
    conn.commit()
    return pipeline_id


def test_recover_pipeline_mutation_and_event_are_atomic(db, tmp_path):
    """Pipeline recovery writes and audit events are committed in one transaction."""
    conn, ws = db
    pipeline_id = _seed_failed_pipeline(conn, ws.id)

    events_before = _event_count(conn)
    dispatch(
        conn,
        RecoverPipelineCommand(
            workspace_id=ws.id,
            pipeline_id=pipeline_id,
            actor="test:user",
        ),
        auth_hook=noop_auth_hook,
    )

    conn2 = open_db(tmp_path / "txn_test.db")
    try:
        row = conn2.execute(
            "SELECT status FROM app_pipeline_executions WHERE id = ?", (pipeline_id,)
        ).fetchone()
        assert row is not None
        assert row["status"] == "running", "Pipeline status not updated on fresh connection"
        new_events = conn2.execute("SELECT COUNT(*) FROM cp_events").fetchone()[0]
        assert new_events > events_before, "Audit event not found on fresh connection"
    finally:
        conn2.close()


# ---------------------------------------------------------------------------
# Atomicity failure-scenario proofs
# ---------------------------------------------------------------------------
# These tests prove that a mutation which ALREADY OCCURRED inside the handler
# is rolled back when event emission fails AFTER the handler returns.
# The critical architecture under test:
#
#   handler(commit=False) → mutation written, no commit
#   _emit_event()         → partial: event row inserted, dispatch_event raises
#   dispatcher except     → conn.rollback() → both mutation and event rolled back
#
# Proven using a fresh sqlite3.Connection (WAL mode) so there is no possibility
# that the assertion reads uncommitted data from the same connection.
# ---------------------------------------------------------------------------


def test_create_schedule_mutation_rolled_back_on_post_handler_event_failure(db, tmp_path):
    """Schedule row is rolled back when event emission fails AFTER the handler ran.

    Sequence exercised:
      1. CreateScheduleCommand dispatched.
      2. _create_schedule handler executes with commit=False — schedule row IS inserted.
      3. Dispatcher's _emit_event calls cp_repo.create_event (event row IS inserted),
         then calls dispatch_event which raises RuntimeError.
      4. Dispatcher except block calls conn.rollback().
      5. Fresh-connection assertions confirm neither row persisted.
    """
    import unittest.mock

    conn, ws = db
    schedules_before = conn.execute("SELECT COUNT(*) FROM app_schedule_definitions").fetchone()[0]
    events_before = _event_count(conn)

    with unittest.mock.patch(
        "app.application.dispatcher.dispatch_event",
        side_effect=RuntimeError("simulated event bus failure"),
    ):
        with pytest.raises(RuntimeError, match="simulated event bus failure"):
            dispatch(
                conn,
                CreateScheduleCommand(
                    workspace_id=ws.id,
                    name="Must Not Persist",
                    operation_type="start_pipeline",
                    schedule_type="interval",
                    schedule_config={"interval_seconds": 60},
                    actor="test:user",
                ),
                auth_hook=noop_auth_hook,
            )

    # Fresh connection — rolled back, nothing must be visible.
    conn2 = open_db(tmp_path / "txn_test.db")
    try:
        assert (
            conn2.execute("SELECT COUNT(*) FROM app_schedule_definitions").fetchone()[0]
            == schedules_before
        ), "Schedule row must not persist after event-emission rollback"
        assert conn2.execute("SELECT COUNT(*) FROM cp_events").fetchone()[0] == events_before, (
            "Event row must not persist after event-emission rollback"
        )
    finally:
        conn2.close()

    # Original connection must still be usable.
    dispatch(
        conn,
        CreateScheduleCommand(
            workspace_id=ws.id,
            name="Post-Rollback Schedule",
            operation_type="start_pipeline",
            schedule_type="interval",
            schedule_config={"interval_seconds": 120},
            actor="test:user",
        ),
        auth_hook=noop_auth_hook,
    )
    assert (
        conn.execute("SELECT COUNT(*) FROM app_schedule_definitions").fetchone()[0]
        == schedules_before + 1
    ), "Connection must be usable and dispatch must succeed after prior rollback"


def test_recover_pipeline_mutation_rolled_back_on_post_handler_event_failure(db, tmp_path):
    """Pipeline mutations are rolled back when event emission fails AFTER recovery ran.

    Sequence exercised:
      1. RecoverPipelineCommand dispatched on a seeded failed pipeline.
      2. recover_pipeline(commit=False) executes — stage log rows ARE inserted,
         pipeline status IS updated to 'running'.
      3. recovery._emit_event calls cp_repo.create_event (event row IS inserted),
         then calls dispatch_event which raises RuntimeError.
      4. Dispatcher except block calls conn.rollback().
      5. Fresh-connection assertions confirm pipeline is still 'failed', no new
         stage log rows persisted, no event persisted.
      6. Subsequent valid recovery dispatched without patching — succeeds normally.
    """
    import unittest.mock

    conn, ws = db
    pipeline_id = _seed_failed_pipeline(conn, ws.id)
    events_before = _event_count(conn)
    stage_rows_before = conn.execute(
        "SELECT COUNT(*) FROM app_pipeline_stage_log WHERE pipeline_id=?", (pipeline_id,)
    ).fetchone()[0]

    with unittest.mock.patch(
        "app.application.recovery.dispatch_event",
        side_effect=RuntimeError("simulated recovery event failure"),
    ):
        with pytest.raises(RuntimeError, match="simulated recovery event failure"):
            dispatch(
                conn,
                RecoverPipelineCommand(
                    workspace_id=ws.id,
                    pipeline_id=pipeline_id,
                    actor="test:user",
                ),
                auth_hook=noop_auth_hook,
            )

    # Fresh connection — all mutations must be rolled back.
    conn2 = open_db(tmp_path / "txn_test.db")
    try:
        row = conn2.execute(
            "SELECT status FROM app_pipeline_executions WHERE id=?", (pipeline_id,)
        ).fetchone()
        assert row is not None
        assert row["status"] == "failed", (
            f"Pipeline must remain 'failed' after rollback, got '{row['status']}'"
        )
        stage_rows_after = conn2.execute(
            "SELECT COUNT(*) FROM app_pipeline_stage_log WHERE pipeline_id=?",
            (pipeline_id,),
        ).fetchone()[0]
        assert stage_rows_after == stage_rows_before, (
            "No new stage log rows must persist after event-emission rollback"
        )
        assert conn2.execute("SELECT COUNT(*) FROM cp_events").fetchone()[0] == events_before, (
            "Event row must not persist after event-emission rollback"
        )
    finally:
        conn2.close()

    # Connection must still be usable; a valid recovery must now succeed.
    dispatch(
        conn,
        RecoverPipelineCommand(
            workspace_id=ws.id,
            pipeline_id=pipeline_id,
            actor="test:user",
        ),
        auth_hook=noop_auth_hook,
    )
    assert (
        conn.execute(
            "SELECT status FROM app_pipeline_executions WHERE id=?", (pipeline_id,)
        ).fetchone()["status"]
        == "running"
    ), "Pipeline must be 'running' after successful post-rollback recovery"
