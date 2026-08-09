"""Tests for ApplicationService — the bounded facade."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest

from app.application.authorization import allow_all_auth_hook
from app.application.commands import (
    CreateScheduleCommand,
    StartPipelineCommand,
)
from app.application.composition import build_application_service, reset_composition
from app.application.contracts import (
    ChannelView,
    PipelineView,
    ScheduleView,
    WorkspaceView,
)
from app.application.queries import (
    GetChannelSummaryQuery,
    GetSystemHealthQuery,
    GetWorkspaceSummaryQuery,
    ListPipelinesQuery,
    ListSchedulesQuery,
)
from app.control_plane import repository as cp_repo
from app.control_plane.models import ChannelDraft, OrganizationDraft, WorkspaceDraft
from app.core.database import open_db


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _reset():
    reset_composition()
    yield
    reset_composition()


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


@pytest.fixture
def svc(conn):
    # Inject allow-all hook so test actors (non-system) can dispatch commands.
    return build_application_service(conn, auth_hook=allow_all_auth_hook)


class TestStartPipeline:
    def test_start_pipeline_returns_pipeline_view(self, svc, workspace, channel):
        cmd = StartPipelineCommand(
            workspace_id=workspace.id,
            channel_id=channel.id,
            actor="test",
            idempotency_key=_uid(),
        )
        result = svc.start_pipeline(cmd)
        assert isinstance(result, PipelineView)
        assert result.workspace_id == workspace.id

    def test_idempotency_on_duplicate(self, svc, workspace, channel):
        key = _uid()
        cmd = StartPipelineCommand(
            workspace_id=workspace.id,
            channel_id=channel.id,
            actor="test",
            idempotency_key=key,
        )
        from app.application.errors import PipelineAlreadyExistsError

        svc.start_pipeline(cmd)
        with pytest.raises(PipelineAlreadyExistsError):
            svc.start_pipeline(cmd)


class TestListPipelines:
    def test_list_returns_started_pipeline(self, svc, workspace, channel):
        svc.start_pipeline(
            StartPipelineCommand(
                workspace_id=workspace.id,
                channel_id=channel.id,
                actor="test",
                idempotency_key=_uid(),
            )
        )
        result = svc.list_pipelines(ListPipelinesQuery(workspace_id=workspace.id))
        assert len(result) == 1
        assert all(isinstance(p, PipelineView) for p in result)


class TestGetWorkspaceSummary:
    def test_returns_workspace_view(self, svc, workspace):
        result = svc.get_workspace_summary(GetWorkspaceSummaryQuery(workspace_id=workspace.id))
        assert isinstance(result, WorkspaceView)
        assert result.id == workspace.id

    def test_workspace_view_has_slug(self, svc, workspace):
        result = svc.get_workspace_summary(GetWorkspaceSummaryQuery(workspace_id=workspace.id))
        assert result.slug == "ws"


class TestGetChannelSummary:
    def test_returns_channel_view(self, svc, workspace, channel):
        result = svc.get_channel_summary(
            GetChannelSummaryQuery(workspace_id=workspace.id, channel_id=channel.id)
        )
        assert isinstance(result, ChannelView)
        assert result.id == channel.id

    def test_channel_view_has_slug(self, svc, workspace, channel):
        result = svc.get_channel_summary(
            GetChannelSummaryQuery(workspace_id=workspace.id, channel_id=channel.id)
        )
        assert result.slug == "chan"


class TestGetSystemHealth:
    def test_returns_health_view(self, svc, workspace):
        from app.application.contracts import HealthView

        result = svc.get_system_health(GetSystemHealthQuery(workspace_id=workspace.id))
        assert isinstance(result, HealthView)


class TestCreateScheduleViaService:
    def test_create_schedule(self, svc, workspace):
        cmd = CreateScheduleCommand(
            workspace_id=workspace.id,
            name="Hourly",
            operation_type="publish",
            schedule_type="interval",
            schedule_config={"interval_seconds": 3600},
            actor="admin",
        )
        result = svc.create_schedule(cmd)
        assert isinstance(result, ScheduleView)

    def test_list_schedules(self, svc, workspace):
        svc.create_schedule(
            CreateScheduleCommand(
                workspace_id=workspace.id,
                name="S1",
                operation_type="publish",
                schedule_type="interval",
                schedule_config={"interval_seconds": 3600},
                actor="admin",
            )
        )
        result = svc.list_schedules(ListSchedulesQuery(workspace_id=workspace.id))
        assert len(result) >= 1


class TestServiceAuthorizationContract:
    """Service-level authorization: fail-closed, allow-hook, deny-hook, system actor."""

    def test_no_auth_hook_user_actor_denied(self, conn, workspace, channel):
        from app.application.errors import AuthorizationRequiredError

        svc_strict = build_application_service(conn)  # default_auth_hook (fail-closed)
        cmd = StartPipelineCommand(
            workspace_id=workspace.id,
            channel_id=channel.id,
            actor="user-alice",
            idempotency_key=_uid(),
        )
        with pytest.raises(AuthorizationRequiredError):
            svc_strict.start_pipeline(cmd)

    def test_explicit_allow_hook_permits_user_mutation(self, conn, workspace, channel, svc):
        cmd = StartPipelineCommand(
            workspace_id=workspace.id,
            channel_id=channel.id,
            actor="user-alice",
            idempotency_key=_uid(),
        )
        result = svc.start_pipeline(cmd)
        assert isinstance(result, PipelineView)

    def test_explicit_deny_hook_prevents_mutation_and_no_side_effects(
        self, conn, workspace, channel
    ):
        from app.application.errors import AuthorizationRequiredError

        def deny_hook(c, cmd_type, ws_id, actor):
            raise AuthorizationRequiredError(actor, cmd_type)

        svc_deny = build_application_service(conn, auth_hook=deny_hook)
        cmd = StartPipelineCommand(
            workspace_id=workspace.id,
            channel_id=channel.id,
            actor="any-actor",
            idempotency_key=_uid(),
        )
        with pytest.raises(AuthorizationRequiredError):
            svc_deny.start_pipeline(cmd)

        # Confirm no pipeline row was created.
        rows = conn.execute(
            "SELECT id FROM app_pipeline_executions WHERE workspace_id=?",
            (workspace.id,),
        ).fetchall()
        assert len(rows) == 0

    def test_trusted_system_actor_always_permitted_without_hook(self, conn, workspace, channel):
        svc_strict = build_application_service(conn)  # default fail-closed hook
        cmd = StartPipelineCommand(
            workspace_id=workspace.id,
            channel_id=channel.id,
            actor="system:pipeline",
            idempotency_key=_uid(),
        )
        result = svc_strict.start_pipeline(cmd)
        assert isinstance(result, PipelineView)

    def test_cross_workspace_access_denied(self, conn, workspace, channel):
        from app.application.errors import CrossWorkspaceAccessError

        org2 = cp_repo.create_organization(
            conn,
            OrganizationDraft(id=_uid(), name="Org2", slug="org2", actor="cli"),
        )
        ws2 = cp_repo.create_workspace(
            conn,
            WorkspaceDraft(
                id=_uid(),
                name="WS2",
                slug="ws2",
                actor="cli",
                organization_id=org2.id,
            ),
        )
        cmd = StartPipelineCommand(
            workspace_id=workspace.id,
            channel_id=channel.id,
            actor="system:pipeline",
            idempotency_key=_uid(),
        )
        svc_strict = build_application_service(conn)
        pv = svc_strict.start_pipeline(cmd)

        from app.application.queries import GetPipelineStatusQuery

        with pytest.raises(CrossWorkspaceAccessError):
            svc_strict.get_pipeline_status(
                GetPipelineStatusQuery(pipeline_id=pv.id, workspace_id=ws2.id)
            )
