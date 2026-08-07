"""Tests for pipeline controller, state machine, and prerequisite checking."""

from __future__ import annotations

import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.application import pipeline as pipeline_ctrl
from app.application import state as pipeline_state
from app.application.commands import (
    AdvancePipelineStageCommand,
    FailPipelineStageCommand,
    StartPipelineCommand,
)
from app.application.errors import (
    ChannelNotFoundError,
    CrossWorkspaceAccessError,
    InvalidStageError,
    PipelineAlreadyExistsError,
    PipelineNotFoundError,
    StagePrerequisiteError,
    WorkspacePausedError,
)
from app.control_plane import repository as cp_repo
from app.control_plane.models import (
    ChannelDraft,
    OrganizationDraft,
    WorkspaceDraft,
)
from app.core.database import open_db


def _uid() -> str:
    return str(uuid.uuid4())


NOW = datetime.now(UTC).isoformat()


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
    ws = cp_repo.create_workspace(
        conn,
        WorkspaceDraft(id=_uid(), name="WS", slug="ws", actor="cli", organization_id=org.id),
    )
    return ws


@pytest.fixture
def channel(conn, workspace):
    return cp_repo.create_channel(
        conn,
        ChannelDraft(
            id=_uid(), workspace_id=workspace.id,
            name="Chan", slug="chan", actor="cli",
        ),
    )


def _start_cmd(workspace_id, channel_id, *, key=None, start="research", end="learning"):
    return StartPipelineCommand(
        workspace_id=workspace_id,
        channel_id=channel_id,
        actor="test",
        idempotency_key=key or _uid(),
        start_stage=start,
        end_stage=end,
    )


class TestStartPipeline:
    def test_creates_pipeline_record(self, conn, workspace, channel):
        cmd = _start_cmd(workspace.id, channel.id)
        pv = pipeline_ctrl.start_pipeline(conn, cmd)
        assert pv.id is not None
        assert pv.workspace_id == workspace.id
        assert pv.channel_id == channel.id
        assert pv.status == "running"

    def test_idempotency_raises_on_duplicate_key(self, conn, workspace, channel):
        key = _uid()
        cmd = _start_cmd(workspace.id, channel.id, key=key)
        pipeline_ctrl.start_pipeline(conn, cmd)
        with pytest.raises(PipelineAlreadyExistsError):
            pipeline_ctrl.start_pipeline(conn, cmd)

    def test_unknown_channel_raises(self, conn, workspace):
        cmd = _start_cmd(workspace.id, "nonexistent-channel")
        with pytest.raises(ChannelNotFoundError):
            pipeline_ctrl.start_pipeline(conn, cmd)

    def test_cross_workspace_channel_raises(self, conn):
        org = cp_repo.create_organization(
            conn, OrganizationDraft(id=_uid(), name="O2", slug="o2", actor="cli")
        )
        ws1 = cp_repo.create_workspace(
            conn, WorkspaceDraft(id=_uid(), name="WS1", slug="ws1", actor="cli",
                                 organization_id=org.id)
        )
        ws2 = cp_repo.create_workspace(
            conn, WorkspaceDraft(id=_uid(), name="WS2", slug="ws2", actor="cli",
                                 organization_id=org.id)
        )
        ch2 = cp_repo.create_channel(
            conn, ChannelDraft(id=_uid(), workspace_id=ws2.id, name="Ch2",
                               slug="ch2", actor="cli")
        )
        cmd = _start_cmd(ws1.id, ch2.id)
        with pytest.raises(CrossWorkspaceAccessError):
            pipeline_ctrl.start_pipeline(conn, cmd)

    def test_paused_workspace_raises(self, conn, workspace, channel):
        cp_repo.update_workspace_status(conn, workspace.id, "suspended", "admin")
        cmd = _start_cmd(workspace.id, channel.id)
        with pytest.raises(WorkspacePausedError):
            pipeline_ctrl.start_pipeline(conn, cmd)

    def test_invalid_start_stage_raises(self, conn, workspace, channel):
        cmd = _start_cmd(workspace.id, channel.id, start="bad_stage")
        with pytest.raises(InvalidStageError):
            pipeline_ctrl.start_pipeline(conn, cmd)

    def test_creates_stage_entries(self, conn, workspace, channel):
        cmd = _start_cmd(workspace.id, channel.id, start="research", end="script_generation")
        pv = pipeline_ctrl.start_pipeline(conn, cmd)
        stage_names = {s.stage for s in pv.stages}
        assert "research" in stage_names
        assert "script_generation" in stage_names
        assert "narration" not in stage_names

    def test_emits_pipeline_started_event(self, conn, workspace, channel):
        cmd = _start_cmd(workspace.id, channel.id)
        pipeline_ctrl.start_pipeline(conn, cmd)
        events = conn.execute(
            "SELECT event_type FROM cp_events WHERE workspace_id=? ORDER BY created_at DESC",
            (workspace.id,),
        ).fetchall()
        event_types = {e["event_type"] for e in events}
        assert "pipeline.started" in event_types

    def test_correlation_id_assigned(self, conn, workspace, channel):
        cmd = _start_cmd(workspace.id, channel.id)
        pv = pipeline_ctrl.start_pipeline(conn, cmd)
        assert pv.correlation_id is not None
        assert len(pv.correlation_id) > 0

    def test_custom_correlation_id_preserved(self, conn, workspace, channel):
        custom_corr = _uid()
        cmd = StartPipelineCommand(
            workspace_id=workspace.id, channel_id=channel.id,
            actor="a", idempotency_key=_uid(),
            correlation_id=custom_corr,
        )
        pv = pipeline_ctrl.start_pipeline(conn, cmd)
        assert pv.correlation_id == custom_corr


class TestAdvancePipeline:
    def test_advances_stage_to_completed(self, conn, workspace, channel):
        pv = pipeline_ctrl.start_pipeline(
            conn, _start_cmd(workspace.id, channel.id, start="research", end="script_generation")
        )
        cmd = AdvancePipelineStageCommand(
            pipeline_id=pv.id, workspace_id=workspace.id,
            stage="research", actor="test",
        )
        updated = pipeline_ctrl.advance_pipeline(conn, cmd)
        research_stage = next(s for s in updated.stages if s.stage == "research")
        assert research_stage.status == "completed"

    def test_review_required_stage_parks_at_waiting(self, conn, workspace, channel):
        pv = pipeline_ctrl.start_pipeline(
            conn,
            _start_cmd(workspace.id, channel.id, start="script_generation", end="script_generation")
        )
        cmd = AdvancePipelineStageCommand(
            pipeline_id=pv.id, workspace_id=workspace.id,
            stage="script_generation", actor="test",
        )
        updated = pipeline_ctrl.advance_pipeline(conn, cmd)
        assert updated.status == "waiting_for_review"

    def test_prerequisite_check_fails(self, conn, workspace, channel):
        pv = pipeline_ctrl.start_pipeline(
            conn, _start_cmd(workspace.id, channel.id)
        )
        cmd = AdvancePipelineStageCommand(
            pipeline_id=pv.id, workspace_id=workspace.id,
            stage="script_generation", actor="test",
        )
        with pytest.raises(StagePrerequisiteError):
            pipeline_ctrl.advance_pipeline(conn, cmd)

    def test_cross_workspace_pipeline_raises(self, conn, workspace, channel):
        pv = pipeline_ctrl.start_pipeline(conn, _start_cmd(workspace.id, channel.id))
        cmd = AdvancePipelineStageCommand(
            pipeline_id=pv.id, workspace_id="other-workspace",
            stage="research", actor="test",
        )
        with pytest.raises(CrossWorkspaceAccessError):
            pipeline_ctrl.advance_pipeline(conn, cmd)

    def test_pipeline_completes_when_last_stage_done(self, conn, workspace, channel):
        pv = pipeline_ctrl.start_pipeline(
            conn, _start_cmd(workspace.id, channel.id, start="analytics", end="analytics")
        )
        cmd = AdvancePipelineStageCommand(
            pipeline_id=pv.id, workspace_id=workspace.id,
            stage="analytics", actor="test",
        )
        updated = pipeline_ctrl.advance_pipeline(conn, cmd)
        assert updated.status == "completed"


class TestFailPipeline:
    def test_fail_stage_marks_pipeline_failed(self, conn, workspace, channel):
        pv = pipeline_ctrl.start_pipeline(conn, _start_cmd(workspace.id, channel.id))
        cmd = FailPipelineStageCommand(
            pipeline_id=pv.id, workspace_id=workspace.id,
            stage="research", error_message="network error", actor="engine",
        )
        updated = pipeline_ctrl.fail_pipeline(conn, cmd)
        assert updated.status == "failed"
        research = next(s for s in updated.stages if s.stage == "research")
        assert research.status == "failed"
        assert research.error_message == "network error"

    def test_emits_stage_failed_event(self, conn, workspace, channel):
        pv = pipeline_ctrl.start_pipeline(conn, _start_cmd(workspace.id, channel.id))
        pipeline_ctrl.fail_pipeline(
            conn,
            FailPipelineStageCommand(
                pipeline_id=pv.id, workspace_id=workspace.id,
                stage="research", error_message="err", actor="engine",
            ),
        )
        events = conn.execute(
            "SELECT event_type FROM cp_events WHERE workspace_id=?",
            (workspace.id,),
        ).fetchall()
        types = {e["event_type"] for e in events}
        assert "pipeline.stage_failed" in types


class TestPauseCancelPipeline:
    def test_pause_pipeline(self, conn, workspace, channel):
        pv = pipeline_ctrl.start_pipeline(conn, _start_cmd(workspace.id, channel.id))
        updated = pipeline_ctrl.pause_pipeline(conn, pv.id, workspace.id, "admin")
        assert updated.status == "paused"

    def test_resume_pipeline(self, conn, workspace, channel):
        pv = pipeline_ctrl.start_pipeline(conn, _start_cmd(workspace.id, channel.id))
        pipeline_ctrl.pause_pipeline(conn, pv.id, workspace.id, "admin")
        updated = pipeline_ctrl.resume_pipeline(conn, pv.id, workspace.id, "admin")
        assert updated.status == "running"

    def test_cancel_pipeline(self, conn, workspace, channel):
        pv = pipeline_ctrl.start_pipeline(conn, _start_cmd(workspace.id, channel.id))
        updated = pipeline_ctrl.cancel_pipeline(
            conn, pv.id, workspace.id, "admin", "no longer needed"
        )
        assert updated.status == "cancelled"


class TestGetPipeline:
    def test_not_found_raises(self, conn):
        with pytest.raises(PipelineNotFoundError):
            pipeline_state.get_pipeline(conn, "nonexistent")

    def test_list_pipelines_scoped(self, conn, workspace, channel):
        pipeline_ctrl.start_pipeline(conn, _start_cmd(workspace.id, channel.id))
        result = pipeline_state.list_pipelines(conn, workspace.id)
        assert len(result) == 1

    def test_list_pipelines_filter_by_status(self, conn, workspace, channel):
        pv = pipeline_ctrl.start_pipeline(conn, _start_cmd(workspace.id, channel.id))
        pipeline_ctrl.pause_pipeline(conn, pv.id, workspace.id, "a")
        running = pipeline_state.list_pipelines(conn, workspace.id, status="running")
        paused = pipeline_state.list_pipelines(conn, workspace.id, status="paused")
        assert len(running) == 0
        assert len(paused) == 1


class TestStageAttemptHistory:
    """Stage history is preserved across retries — UNIQUE(pipeline_id, stage, attempt_number)."""

    def _fail_stage(self, conn, pv, stage, workspace):
        pipeline_ctrl.fail_pipeline(
            conn,
            FailPipelineStageCommand(
                pipeline_id=pv.id,
                workspace_id=workspace.id,
                stage=stage,
                error_message="network error",
                actor="engine",
            ),
        )

    def test_initial_attempt_number_is_one(self, conn, workspace, channel):
        pv = pipeline_ctrl.start_pipeline(
            conn, _start_cmd(workspace.id, channel.id, start="research", end="research")
        )
        research = next(s for s in pv.stages if s.stage == "research")
        assert research.attempt_number == 1

    def test_fail_then_recover_creates_new_attempt(self, conn, workspace, channel):
        from app.application import recovery as rec

        pv = pipeline_ctrl.start_pipeline(
            conn, _start_cmd(workspace.id, channel.id, start="research", end="research")
        )
        self._fail_stage(conn, pv, "research", workspace)
        rec.recover_pipeline(conn, pv.id, workspace.id, "admin")
        history = pipeline_state.get_stage_history(conn, pv.id, "research")
        assert len(history) == 2
        assert history[0].attempt_number == 1
        assert history[1].attempt_number == 2

    def test_original_failed_attempt_preserved(self, conn, workspace, channel):
        from app.application import recovery as rec

        pv = pipeline_ctrl.start_pipeline(
            conn, _start_cmd(workspace.id, channel.id, start="research", end="research")
        )
        self._fail_stage(conn, pv, "research", workspace)
        rec.recover_pipeline(conn, pv.id, workspace.id, "admin")
        history = pipeline_state.get_stage_history(conn, pv.id, "research")
        original = history[0]
        assert original.status == "failed"
        assert original.error_message == "network error"

    def test_new_attempt_starts_as_running(self, conn, workspace, channel):
        from app.application import recovery as rec

        pv = pipeline_ctrl.start_pipeline(
            conn, _start_cmd(workspace.id, channel.id, start="research", end="research")
        )
        self._fail_stage(conn, pv, "research", workspace)
        recovered = rec.recover_pipeline(conn, pv.id, workspace.id, "admin")
        current = next(s for s in recovered.stages if s.stage == "research")
        assert current.attempt_number == 2
        assert current.status == "running"

    def test_pipeline_view_shows_latest_attempt(self, conn, workspace, channel):
        from app.application import recovery as rec

        pv = pipeline_ctrl.start_pipeline(
            conn, _start_cmd(workspace.id, channel.id, start="research", end="research")
        )
        self._fail_stage(conn, pv, "research", workspace)
        recovered = rec.recover_pipeline(conn, pv.id, workspace.id, "admin")
        research = next(s for s in recovered.stages if s.stage == "research")
        # PipelineView shows latest attempt (2), not the original failure.
        assert research.status == "running"
        assert research.attempt_number == 2

    def test_advance_after_recovery_targets_latest_attempt(self, conn, workspace, channel):
        from app.application import recovery as rec

        pv = pipeline_ctrl.start_pipeline(
            conn, _start_cmd(workspace.id, channel.id, start="research", end="research")
        )
        self._fail_stage(conn, pv, "research", workspace)
        recovered = rec.recover_pipeline(conn, pv.id, workspace.id, "admin")
        done = pipeline_ctrl.advance_pipeline(
            conn,
            AdvancePipelineStageCommand(
                pipeline_id=recovered.id,
                workspace_id=workspace.id,
                stage="research",
                actor="engine",
            ),
        )
        research = next(s for s in done.stages if s.stage == "research")
        assert research.status == "completed"
        assert research.attempt_number == 2
        # Original failure row still intact.
        history = pipeline_state.get_stage_history(conn, pv.id, "research")
        assert history[0].status == "failed"
        assert history[1].status == "completed"
