"""Tests for schedule CRUD, eligibility checks, and cron computation."""

from __future__ import annotations

import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.application import scheduler as sched
from app.application.errors import ScheduleNotFoundError
from app.control_plane import repository as cp_repo
from app.control_plane.models import OrganizationDraft, WorkspaceDraft
from app.core.database import open_db


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def conn():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    c = open_db(path)
    yield c
    c.close()
    path.unlink(missing_ok=True)


@pytest.fixture
def workspace(conn):
    org = cp_repo.create_organization(
        conn, OrganizationDraft(id=_uid(), name="Org", slug="org", actor="cli")
    )
    return cp_repo.create_workspace(
        conn,
        WorkspaceDraft(id=_uid(), name="WS", slug="ws", actor="cli", organization_id=org.id),
    )


def _create_interval(conn, workspace_id, *, seconds: int = 3600):
    return sched.create_schedule(
        conn,
        workspace_id=workspace_id,
        name="Daily Publish",
        operation_type="publish",
        schedule_type="interval",
        schedule_config={"interval_seconds": seconds},
        actor="admin",
    )


class TestCreateSchedule:
    def test_creates_schedule(self, conn, workspace):
        sv = _create_interval(conn, workspace.id)
        assert sv.id is not None
        assert sv.workspace_id == workspace.id
        assert sv.is_active is True

    def test_schedule_type_preserved(self, conn, workspace):
        sv = _create_interval(conn, workspace.id)
        assert sv.schedule_type == "interval"

    def test_cron_schedule_creates(self, conn, workspace):
        sv = sched.create_schedule(
            conn,
            workspace_id=workspace.id,
            name="Midnight",
            operation_type="report",
            schedule_type="cron",
            schedule_config={"cron_expr": "@daily"},
            actor="admin",
        )
        assert sv.schedule_type == "cron"
        assert sv.next_run_at is not None

    def test_once_schedule_creates(self, conn, workspace):
        run_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        sv = sched.create_schedule(
            conn,
            workspace_id=workspace.id,
            name="One-off",
            operation_type="export",
            schedule_type="once",
            schedule_config={"run_at": run_at},
            actor="admin",
        )
        assert sv.schedule_type == "once"

    def test_interval_next_run_set(self, conn, workspace):
        sv = _create_interval(conn, workspace.id, seconds=60)
        assert sv.next_run_at is not None

    def test_invalid_schedule_type_raises(self, conn, workspace):
        from app.application.errors import InvalidScheduleTypeError
        with pytest.raises(InvalidScheduleTypeError):
            sched.create_schedule(
                conn,
                workspace_id=workspace.id,
                name="Bad",
                operation_type="x",
                schedule_type="bad_type",
                schedule_config={},
                actor="admin",
            )


class TestGetSchedule:
    def test_get_existing(self, conn, workspace):
        sv = _create_interval(conn, workspace.id)
        fetched = sched.get_schedule(conn, sv.id)
        assert fetched.id == sv.id

    def test_get_nonexistent_raises(self, conn, workspace):
        with pytest.raises(ScheduleNotFoundError):
            sched.get_schedule(conn, "bad-id")


class TestListSchedules:
    def test_lists_for_workspace(self, conn, workspace):
        _create_interval(conn, workspace.id)
        _create_interval(conn, workspace.id)
        schedules = sched.list_schedules(conn, workspace.id)
        assert len(schedules) == 2

    def test_list_excludes_other_workspaces(self, conn, workspace):
        org = cp_repo.create_organization(
            conn, OrganizationDraft(id=_uid(), name="O2", slug="o2", actor="cli")
        )
        ws2 = cp_repo.create_workspace(
            conn, WorkspaceDraft(id=_uid(), name="W2", slug="w2", actor="cli",
                                 organization_id=org.id)
        )
        _create_interval(conn, workspace.id)
        _create_interval(conn, ws2.id)
        result = sched.list_schedules(conn, workspace.id)
        assert len(result) == 1

    def test_filter_active_only(self, conn, workspace):
        sv = _create_interval(conn, workspace.id)
        _create_interval(conn, workspace.id)
        sched.pause_schedule(conn, sv.id, workspace.id)
        active = sched.list_schedules(conn, workspace.id, is_active=True)
        assert all(s.is_active for s in active)


class TestPauseResumeSchedule:
    def test_pause_marks_inactive(self, conn, workspace):
        sv = _create_interval(conn, workspace.id)
        updated = sched.pause_schedule(conn, sv.id, workspace.id)
        assert updated.is_active is False

    def test_resume_marks_active(self, conn, workspace):
        sv = _create_interval(conn, workspace.id)
        sched.pause_schedule(conn, sv.id, workspace.id)
        updated = sched.resume_schedule(conn, sv.id, workspace.id)
        assert updated.is_active is True

    def test_pause_nonexistent_raises(self, conn, workspace):
        with pytest.raises(ScheduleNotFoundError):
            sched.pause_schedule(conn, "bad-id", workspace.id)

    def test_pause_cross_workspace_raises(self, conn, workspace):
        sv = _create_interval(conn, workspace.id)
        with pytest.raises(ScheduleNotFoundError):
            sched.pause_schedule(conn, sv.id, "other-ws")


class TestDeleteSchedule:
    def test_delete_removes_schedule(self, conn, workspace):
        sv = _create_interval(conn, workspace.id)
        sched.delete_schedule(conn, sv.id, workspace.id)
        with pytest.raises(ScheduleNotFoundError):
            sched.get_schedule(conn, sv.id)

    def test_delete_nonexistent_raises(self, conn, workspace):
        with pytest.raises(ScheduleNotFoundError):
            sched.delete_schedule(conn, "bad-id", workspace.id)


class TestEligibleSchedules:
    def test_interval_eligible_after_next_run(self, conn, workspace):
        sv = _create_interval(conn, workspace.id, seconds=1)
        future = datetime.now(UTC) + timedelta(seconds=10)
        eligible = sched.eligible_schedules(conn, workspace.id, now=future)
        ids = {s.id for s in eligible}
        assert sv.id in ids

    def test_inactive_schedule_not_eligible(self, conn, workspace):
        sv = _create_interval(conn, workspace.id, seconds=1)
        sched.pause_schedule(conn, sv.id, workspace.id)
        future = datetime.now(UTC) + timedelta(seconds=10)
        eligible = sched.eligible_schedules(conn, workspace.id, now=future)
        assert all(s.id != sv.id for s in eligible)

    def test_not_yet_due_not_eligible(self, conn, workspace):
        _create_interval(conn, workspace.id, seconds=86400)
        now = datetime.now(UTC)
        eligible = sched.eligible_schedules(conn, workspace.id, now=now)
        assert eligible == []


class TestRecordRun:
    def test_record_run_advances_next_run(self, conn, workspace):
        sv = _create_interval(conn, workspace.id, seconds=60)
        original_next = sv.next_run_at
        updated = sched.record_run(conn, sv.id)
        assert updated.last_run_at is not None
        assert updated.next_run_at != original_next
