"""Tests for the unified review queue."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest

from app.application import pipeline as pipeline_ctrl
from app.application import review as review_mod
from app.application.commands import StartPipelineCommand
from app.application.registry import ExtensionRegistry
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


def _start(conn, ws, ch, *, start="script_generation", end="script_generation"):
    return pipeline_ctrl.start_pipeline(
        conn,
        StartPipelineCommand(
            workspace_id=ws.id,
            channel_id=ch.id,
            actor="test",
            idempotency_key=_uid(),
            start_stage=start,
            end_stage=end,
        ),
    )


class TestGetReviewQueue:
    def test_returns_list(self, conn, workspace):
        result = review_mod.get_review_queue(conn, workspace.id)
        assert isinstance(result, list)

    def test_empty_queue_initially(self, conn, workspace):
        result = review_mod.get_review_queue(conn, workspace.id)
        assert result == []

    def test_waiting_pipeline_appears_in_queue(self, conn, workspace, channel):
        pv = _start(conn, workspace, channel)
        # Advance script_generation — it's a review-required stage → parks at waiting.
        from app.application import pipeline as pc
        from app.application.commands import AdvancePipelineStageCommand
        updated = pc.advance_pipeline(
            conn,
            AdvancePipelineStageCommand(
                pipeline_id=pv.id,
                workspace_id=workspace.id,
                stage="script_generation",
                actor="engine",
            ),
        )
        assert updated.status == "waiting_for_review"
        queue = review_mod.get_review_queue(conn, workspace.id)
        ids = [item.item_id for item in queue]
        assert pv.id in ids

    def test_queue_scoped_to_workspace(self, conn, workspace, channel):
        org = cp_repo.create_organization(
            conn, OrganizationDraft(id=_uid(), name="O2", slug="o2", actor="cli")
        )
        ws2 = cp_repo.create_workspace(
            conn, WorkspaceDraft(id=_uid(), name="W2", slug="w2", actor="cli",
                                 organization_id=org.id)
        )
        queue_ws1 = review_mod.get_review_queue(conn, workspace.id)
        queue_ws2 = review_mod.get_review_queue(conn, ws2.id)
        assert queue_ws1 == []
        assert queue_ws2 == []


class TestApproveReviewItem:
    def test_approve_pipeline_review_advances(self, conn, workspace, channel):
        from app.application import pipeline as pc
        from app.application.commands import AdvancePipelineStageCommand

        pv = _start(conn, workspace, channel)
        pc.advance_pipeline(
            conn,
            AdvancePipelineStageCommand(
                pipeline_id=pv.id,
                workspace_id=workspace.id,
                stage="script_generation",
                actor="engine",
            ),
        )
        registry = ExtensionRegistry()
        result = review_mod.approve_review_item(
            conn, "pipeline_review", pv.id, workspace.id, "reviewer", registry=registry
        )
        assert result is not None

    def test_approve_unknown_item_type_raises(self, conn, workspace):
        from app.application.errors import UnknownCommandError
        registry = ExtensionRegistry()
        with pytest.raises(UnknownCommandError):
            review_mod.approve_review_item(
                conn, "unknown_type", "fake-id", workspace.id, "reviewer", registry=registry
            )
