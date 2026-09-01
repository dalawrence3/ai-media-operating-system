"""Phase 16.1 Section 11 — Workspace split regression tests.

Validates that the dispatcher's CrossWorkspaceAccessError guard prevents
cross-workspace channel access, which is the root-cause vector responsible
for the historical Dev Studio / local-dev lineage split in Publication 1.

Root cause (verified in Phase 16.1):
  - `dev:driver` actor passed Dev Studio workspace_id when calling create_pipeline()
  - `state.py:create_pipeline()` writes workspace_id directly from the caller — no defaulting
  - Publication 1 got local-dev workspace because it was created via the
    local-dev channel (623a13aa)
  - All 27 app_pipeline_executions landed in Dev Studio because dev:driver used Dev Studio workspace
    and Dev Studio channel (c6b3176a) — the guard never triggered because the
    channel matched the workspace

These tests verify that if a caller tries to use a channel from workspace A while claiming
workspace B, `CrossWorkspaceAccessError` is raised before any DB write occurs.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest

from app.application import dispatcher as bus
from app.application.authorization import allow_all_auth_hook
from app.application.commands import StartPipelineCommand
from app.application.errors import CrossWorkspaceAccessError
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


@pytest.fixture(autouse=True)
def _reset_handlers():
    bus._HANDLERS.clear()
    yield
    bus._HANDLERS.clear()


def _make_org_and_workspace(conn, name_suffix: str) -> tuple:
    org = cp_repo.create_organization(
        conn,
        OrganizationDraft(
            id=_uid(), name=f"Org-{name_suffix}", slug=f"org-{name_suffix}", actor="cli"
        ),
    )
    ws = cp_repo.create_workspace(
        conn,
        WorkspaceDraft(
            id=_uid(),
            name=f"WS-{name_suffix}",
            slug=f"ws-{name_suffix}",
            actor="cli",
            organization_id=org.id,
        ),
    )
    return org, ws


def _make_channel(conn, workspace_id: str, suffix: str):
    return cp_repo.create_channel(
        conn,
        ChannelDraft(
            id=_uid(),
            workspace_id=workspace_id,
            name=f"Chan-{suffix}",
            slug=f"chan-{suffix}",
            actor="cli",
        ),
    )


def _noop_handler(conn, cmd, *, registry=None, **kw):
    return {"ok": True}


# ---------------------------------------------------------------------------
# Test 1 — Cross-workspace channel access is blocked
# ---------------------------------------------------------------------------


def test_cross_workspace_channel_raises_error(conn):
    """
    Workspace A owns channel-A. Attempt to start a pipeline in workspace B
    using channel-A → CrossWorkspaceAccessError must be raised before any
    pipeline row is written.
    """
    _, ws_a = _make_org_and_workspace(conn, "A")
    _, ws_b = _make_org_and_workspace(conn, "B")
    channel_a = _make_channel(conn, ws_a.id, "A")

    bus.register_handler(StartPipelineCommand, _noop_handler)

    cmd = StartPipelineCommand(
        workspace_id=ws_b.id,  # claims workspace B
        channel_id=channel_a.id,  # but channel belongs to workspace A
        actor="test:driver",
        idempotency_key=_uid(),
    )

    with pytest.raises(CrossWorkspaceAccessError) as exc_info:
        bus.dispatch(conn, cmd, registry=ExtensionRegistry(), auth_hook=allow_all_auth_hook)

    assert exc_info.value.entity == "Channel"
    assert exc_info.value.entity_id == channel_a.id
    assert exc_info.value.workspace_id == ws_b.id


# ---------------------------------------------------------------------------
# Test 2 — No pipeline row is written on cross-workspace rejection
# ---------------------------------------------------------------------------


def test_cross_workspace_channel_no_pipeline_row_written(conn):
    """
    Verify that the rejected command leaves zero pipeline execution rows
    in app_pipeline_executions — the rollback path is clean.
    """
    _, ws_a = _make_org_and_workspace(conn, "A2")
    _, ws_b = _make_org_and_workspace(conn, "B2")
    channel_a = _make_channel(conn, ws_a.id, "A2")

    bus.register_handler(StartPipelineCommand, _noop_handler)

    cmd = StartPipelineCommand(
        workspace_id=ws_b.id,
        channel_id=channel_a.id,
        actor="test:driver",
        idempotency_key=_uid(),
    )

    with pytest.raises(CrossWorkspaceAccessError):
        bus.dispatch(conn, cmd, registry=ExtensionRegistry(), auth_hook=allow_all_auth_hook)

    row_count = conn.execute(
        "SELECT COUNT(*) FROM app_pipeline_executions WHERE workspace_id = ?",
        (ws_b.id,),
    ).fetchone()[0]
    assert row_count == 0, "No pipeline row should be written when cross-workspace guard fires"


# ---------------------------------------------------------------------------
# Test 3 — Same-workspace channel is allowed through
# ---------------------------------------------------------------------------


def test_same_workspace_channel_is_allowed(conn):
    """
    Verify that a channel belonging to the asserted workspace is NOT rejected.
    This is the success path — channel and workspace are self-consistent.
    """
    _, ws = _make_org_and_workspace(conn, "Same")
    channel = _make_channel(conn, ws.id, "Same")

    dispatched: list = []

    def capture_handler(conn, cmd, *, registry=None, **kw):
        dispatched.append(cmd)
        return {"ok": True}

    bus.register_handler(StartPipelineCommand, capture_handler)

    cmd = StartPipelineCommand(
        workspace_id=ws.id,
        channel_id=channel.id,
        actor="test:driver",
        idempotency_key=_uid(),
    )
    bus.dispatch(conn, cmd, registry=ExtensionRegistry(), auth_hook=allow_all_auth_hook)

    assert len(dispatched) == 1


# ---------------------------------------------------------------------------
# Test 4 — Two channels in the same workspace do NOT trigger cross-workspace error
# ---------------------------------------------------------------------------


def test_two_channels_same_workspace_no_conflict(conn):
    """
    Two channels in workspace A — using either one for a pipeline in workspace A
    must not trigger CrossWorkspaceAccessError.
    """
    _, ws = _make_org_and_workspace(conn, "Multi")
    channel_1 = _make_channel(conn, ws.id, "Multi1")
    channel_2 = _make_channel(conn, ws.id, "Multi2")

    dispatched: list = []

    def capture(conn, cmd, *, registry=None, **kw):
        dispatched.append(cmd.channel_id)
        return {"ok": True}

    bus.register_handler(StartPipelineCommand, capture)
    registry = ExtensionRegistry()

    for ch in (channel_1, channel_2):
        cmd = StartPipelineCommand(
            workspace_id=ws.id,
            channel_id=ch.id,
            actor="test:driver",
            idempotency_key=_uid(),
        )
        bus.dispatch(conn, cmd, registry=registry, auth_hook=allow_all_auth_hook)

    assert len(dispatched) == 2
    assert set(dispatched) == {channel_1.id, channel_2.id}
