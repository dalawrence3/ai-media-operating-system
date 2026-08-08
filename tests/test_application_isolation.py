"""Cross-workspace isolation tests.

Every query and command must be verified against workspace scope.
No workspace may access another workspace's data.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest

from app.application import pipeline as pipeline_ctrl
from app.application import scheduler as sched
from app.application import state as pipeline_state
from app.application.commands import (
    AdvancePipelineStageCommand,
    FailPipelineStageCommand,
    StartPipelineCommand,
)
from app.application.errors import CrossWorkspaceAccessError
from app.control_plane import repository as cp_repo
from app.control_plane.models import ChannelDraft, OrganizationDraft, WorkspaceDraft
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
def org(conn):
    return cp_repo.create_organization(
        conn, OrganizationDraft(id=_uid(), name="Org", slug="org", actor="cli")
    )


@pytest.fixture
def ws_a(conn, org):
    return cp_repo.create_workspace(
        conn,
        WorkspaceDraft(id=_uid(), name="WSA", slug="ws-a", actor="cli", organization_id=org.id),
    )


@pytest.fixture
def ws_b(conn, org):
    return cp_repo.create_workspace(
        conn,
        WorkspaceDraft(id=_uid(), name="WSB", slug="ws-b", actor="cli", organization_id=org.id),
    )


@pytest.fixture
def ch_a(conn, ws_a):
    return cp_repo.create_channel(
        conn,
        ChannelDraft(id=_uid(), workspace_id=ws_a.id, name="ChanA", slug="chan-a", actor="cli"),
    )


@pytest.fixture
def ch_b(conn, ws_b):
    return cp_repo.create_channel(
        conn,
        ChannelDraft(id=_uid(), workspace_id=ws_b.id, name="ChanB", slug="chan-b", actor="cli"),
    )


def _start(conn, ws, ch):
    return pipeline_ctrl.start_pipeline(
        conn,
        StartPipelineCommand(
            workspace_id=ws.id,
            channel_id=ch.id,
            actor="test",
            idempotency_key=_uid(),
        ),
    )


class TestPipelineIsolation:
    def test_channel_from_other_workspace_rejected(self, conn, ws_a, ch_b):
        cmd = StartPipelineCommand(
            workspace_id=ws_a.id,
            channel_id=ch_b.id,
            actor="test",
            idempotency_key=_uid(),
        )
        with pytest.raises(CrossWorkspaceAccessError):
            pipeline_ctrl.start_pipeline(conn, cmd)

    def test_advance_pipeline_from_other_workspace_rejected(self, conn, ws_a, ch_a, ws_b):
        pv = _start(conn, ws_a, ch_a)
        cmd = AdvancePipelineStageCommand(
            pipeline_id=pv.id,
            workspace_id=ws_b.id,
            stage="research",
            actor="test",
        )
        with pytest.raises(CrossWorkspaceAccessError):
            pipeline_ctrl.advance_pipeline(conn, cmd)

    def test_fail_pipeline_from_other_workspace_rejected(self, conn, ws_a, ch_a, ws_b):
        pv = _start(conn, ws_a, ch_a)
        cmd = FailPipelineStageCommand(
            pipeline_id=pv.id,
            workspace_id=ws_b.id,
            stage="research",
            error_message="err",
            actor="test",
        )
        with pytest.raises(CrossWorkspaceAccessError):
            pipeline_ctrl.fail_pipeline(conn, cmd)

    def test_cancel_pipeline_from_other_workspace_rejected(self, conn, ws_a, ch_a, ws_b):
        pv = _start(conn, ws_a, ch_a)
        with pytest.raises(CrossWorkspaceAccessError):
            pipeline_ctrl.cancel_pipeline(conn, pv.id, ws_b.id, "admin", "reason")

    def test_list_pipelines_scoped(self, conn, ws_a, ch_a, ws_b, ch_b):
        _start(conn, ws_a, ch_a)
        _start(conn, ws_b, ch_b)
        pipelines_a = pipeline_state.list_pipelines(conn, ws_a.id)
        pipelines_b = pipeline_state.list_pipelines(conn, ws_b.id)
        assert len(pipelines_a) == 1
        assert len(pipelines_b) == 1
        assert pipelines_a[0].workspace_id == ws_a.id
        assert pipelines_b[0].workspace_id == ws_b.id

    def test_no_cross_workspace_pipeline_listing(self, conn, ws_a, ch_a, ws_b):
        pv = _start(conn, ws_a, ch_a)
        pipelines_b = pipeline_state.list_pipelines(conn, ws_b.id)
        assert not any(p.id == pv.id for p in pipelines_b)


class TestSchedulerIsolation:
    def test_pause_schedule_from_other_workspace_rejected(self, conn, ws_a, ws_b):
        sv = sched.create_schedule(
            conn,
            workspace_id=ws_a.id,
            name="S",
            operation_type="publish",
            schedule_type="interval",
            schedule_config={"interval_seconds": 3600},
            actor="admin",
        )
        from app.application.errors import ScheduleNotFoundError

        with pytest.raises(ScheduleNotFoundError):
            sched.pause_schedule(conn, sv.id, ws_b.id)

    def test_delete_schedule_from_other_workspace_rejected(self, conn, ws_a, ws_b):
        sv = sched.create_schedule(
            conn,
            workspace_id=ws_a.id,
            name="S",
            operation_type="publish",
            schedule_type="interval",
            schedule_config={"interval_seconds": 3600},
            actor="admin",
        )
        from app.application.errors import ScheduleNotFoundError

        with pytest.raises(ScheduleNotFoundError):
            sched.delete_schedule(conn, sv.id, ws_b.id)
