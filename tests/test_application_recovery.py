"""Tests for pipeline recovery, replay event, and dead-letter finding."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest

from app.application import pipeline as pipeline_ctrl
from app.application import recovery as rec
from app.application.commands import FailPipelineStageCommand, StartPipelineCommand
from app.application.errors import (
    CrossWorkspaceAccessError,
    PipelineNotRecoverableError,
)
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
def workspace(conn):
    org = cp_repo.create_organization(
        conn, OrganizationDraft(id=_uid(), name="Org", slug="org", actor="cli")
    )
    return cp_repo.create_workspace(
        conn,
        WorkspaceDraft(id=_uid(), name="WS", slug="ws", actor="cli", organization_id=org.id),
    )


@pytest.fixture
def channel(conn, workspace):
    return cp_repo.create_channel(
        conn,
        ChannelDraft(id=_uid(), workspace_id=workspace.id, name="Chan", slug="chan", actor="cli"),
    )


def _start(conn, workspace, channel, *, start="research", end="learning"):
    return pipeline_ctrl.start_pipeline(
        conn,
        StartPipelineCommand(
            workspace_id=workspace.id,
            channel_id=channel.id,
            actor="test",
            idempotency_key=_uid(),
            start_stage=start,
            end_stage=end,
        ),
    )


def _fail(conn, pv, stage, workspace):
    return pipeline_ctrl.fail_pipeline(
        conn,
        FailPipelineStageCommand(
            pipeline_id=pv.id,
            workspace_id=workspace.id,
            stage=stage,
            error_message="simulated failure",
            actor="engine",
        ),
    )


class TestFindRecoverablePipelines:
    def test_failed_pipeline_appears(self, conn, workspace, channel):
        pv = _start(conn, workspace, channel)
        _fail(conn, pv, "research", workspace)
        found = rec.find_recoverable_pipelines(conn, workspace.id)
        assert any(p.id == pv.id for p in found)

    def test_running_pipeline_not_recoverable(self, conn, workspace, channel):
        pv = _start(conn, workspace, channel)
        found = rec.find_recoverable_pipelines(conn, workspace.id)
        assert not any(p.id == pv.id for p in found)

    def test_paused_pipeline_is_recoverable(self, conn, workspace, channel):
        pv = _start(conn, workspace, channel)
        pipeline_ctrl.pause_pipeline(conn, pv.id, workspace.id, "admin")
        found = rec.find_recoverable_pipelines(conn, workspace.id)
        assert any(p.id == pv.id for p in found)


class TestRecoverPipeline:
    def test_recover_failed_pipeline_resets_to_running(self, conn, workspace, channel):
        pv = _start(conn, workspace, channel)
        _fail(conn, pv, "research", workspace)
        recovered = rec.recover_pipeline(conn, pv.id, workspace.id, "admin")
        assert recovered.status == "running"

    def test_recover_from_specific_stage(self, conn, workspace, channel):
        pv = _start(conn, workspace, channel, start="research", end="script_generation")
        _fail(conn, pv, "research", workspace)
        recovered = rec.recover_pipeline(conn, pv.id, workspace.id, "admin", from_stage="research")
        assert recovered.current_stage == "research"
        assert recovered.status == "running"

    def test_recover_non_recoverable_status_raises(self, conn, workspace, channel):
        pv = _start(conn, workspace, channel)
        pipeline_ctrl.cancel_pipeline(conn, pv.id, workspace.id, "admin", "reason")
        with pytest.raises(PipelineNotRecoverableError):
            rec.recover_pipeline(conn, pv.id, workspace.id, "admin")

    def test_cross_workspace_raises(self, conn, workspace, channel):
        pv = _start(conn, workspace, channel)
        _fail(conn, pv, "research", workspace)
        with pytest.raises(CrossWorkspaceAccessError):
            rec.recover_pipeline(conn, pv.id, "other-workspace", "admin")

    def test_recover_emits_event(self, conn, workspace, channel):
        pv = _start(conn, workspace, channel)
        _fail(conn, pv, "research", workspace)
        rec.recover_pipeline(conn, pv.id, workspace.id, "admin")
        events = conn.execute(
            "SELECT event_type FROM cp_events WHERE workspace_id=?",
            (workspace.id,),
        ).fetchall()
        types = {e["event_type"] for e in events}
        assert "pipeline.recovered" in types


class TestFindDeadLetteredEvents:
    def test_returns_list(self, conn, workspace, channel):
        result = rec.find_dead_lettered_events(conn, workspace.id)
        assert isinstance(result, list)

    def test_no_dead_letters_initially(self, conn, workspace, channel):
        result = rec.find_dead_lettered_events(conn, workspace.id)
        assert result == []


class TestReplayEventIdempotency:
    def test_replay_blocked_for_unknown_event(self, conn, workspace, channel):
        from app.application.errors import ReplayBlockedError

        with pytest.raises(ReplayBlockedError):
            rec.replay_event(conn, "nonexistent-event-id", workspace.id, "admin")

    def test_replay_blocked_for_non_allowlisted_type(self, conn, workspace, channel):
        from app.application.errors import ReplayBlockedError
        from app.control_plane import repository as cp_repo
        from app.control_plane.models import ControlEventDraft

        draft = ControlEventDraft(
            id=_uid(),
            event_type="command.dispatched",  # not in REPLAYABLE_EVENT_TYPES
            workspace_id=workspace.id,
            actor="test",
            payload={},
        )
        event = cp_repo.create_event(conn, draft)
        with pytest.raises(ReplayBlockedError, match="allowlist"):
            rec.replay_event(conn, event.id, workspace.id, "admin")

    def test_stage_history_preserved_after_recovery(self, conn, workspace, channel):
        from app.application import state as pipeline_state

        pv = _start(conn, workspace, channel, start="research", end="research")
        _fail(conn, pv, "research", workspace)
        rec.recover_pipeline(conn, pv.id, workspace.id, "admin")
        history = pipeline_state.get_stage_history(conn, pv.id, "research")
        assert len(history) == 2
        assert history[0].status == "failed"
        assert history[1].status == "running"
        assert history[0].attempt_number == 1
        assert history[1].attempt_number == 2
