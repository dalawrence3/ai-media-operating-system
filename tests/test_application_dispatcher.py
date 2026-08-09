"""Tests for the command dispatcher (bus)."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest

from app.application import dispatcher as bus
from app.application.authorization import allow_all_auth_hook
from app.application.commands import (
    StartPipelineCommand,
)
from app.application.errors import AuthorizationRequiredError, UnknownCommandError
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
def reset_handlers():
    """Reset dispatcher handler table before each test."""
    bus._HANDLERS.clear()
    yield
    bus._HANDLERS.clear()


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


class TestRegisterHandler:
    def test_register_and_dispatch(self, conn, workspace, channel):
        results = []

        def my_handler(conn, cmd, *, registry=None, **kw):
            results.append(cmd)
            return {"ok": True}

        bus.register_handler(StartPipelineCommand, my_handler)
        cmd = StartPipelineCommand(
            workspace_id=workspace.id,
            channel_id=channel.id,
            actor="test",
            idempotency_key=_uid(),
        )
        # Inject allow-all hook because actor="test" is not a system actor.
        bus.dispatch(conn, cmd, registry=ExtensionRegistry(), auth_hook=allow_all_auth_hook)
        assert len(results) == 1
        assert results[0] is cmd

    def test_unknown_command_raises(self, conn, workspace):
        class UnknownCmd:
            workspace_id = workspace.id

        with pytest.raises(UnknownCommandError):
            bus.dispatch(
                conn, UnknownCmd(), registry=ExtensionRegistry(), auth_hook=allow_all_auth_hook
            )

    def test_handler_duplicate_raises_without_replace(self):
        def h1(conn, cmd, **kw):
            pass

        def h2(conn, cmd, **kw):
            pass

        bus.register_handler(StartPipelineCommand, h1)
        with pytest.raises(RuntimeError, match="replace=True"):
            bus.register_handler(StartPipelineCommand, h2)

    def test_handler_overwrite_with_replace_flag(self):
        def h1(conn, cmd, **kw):
            pass

        def h2(conn, cmd, **kw):
            pass

        bus.register_handler(StartPipelineCommand, h1)
        bus.register_handler(StartPipelineCommand, h2, replace=True)
        assert bus._HANDLERS[StartPipelineCommand] is h2


class TestDefaultHandlers:
    def test_register_default_handlers_populates(self, conn):
        bus.register_default_handlers()
        assert StartPipelineCommand in bus._HANDLERS

    def test_register_default_handlers_idempotent(self, conn):
        bus.register_default_handlers()
        initial_count = len(bus._HANDLERS)
        # Second call must succeed (replace=True is used internally).
        bus.register_default_handlers()
        assert len(bus._HANDLERS) == initial_count


class TestDispatchWithDefaults:
    def test_start_pipeline_dispatches(self, conn, workspace, channel):
        from app.application.composition import build_application_service, reset_composition

        reset_composition()
        # Inject allow-all hook so non-system actor can dispatch in tests.
        svc = build_application_service(conn, auth_hook=allow_all_auth_hook)
        cmd = StartPipelineCommand(
            workspace_id=workspace.id,
            channel_id=channel.id,
            actor="test",
            idempotency_key=_uid(),
        )
        result = svc.start_pipeline(cmd)
        assert result is not None
        assert result.workspace_id == workspace.id


class TestAuthorizationContract:
    def test_system_actor_always_permitted(self, conn, workspace, channel):
        bus.register_default_handlers()
        cmd = StartPipelineCommand(
            workspace_id=workspace.id,
            channel_id=channel.id,
            actor="system:pipeline",
            idempotency_key=_uid(),
        )
        result = bus.dispatch(conn, cmd, registry=ExtensionRegistry())
        assert result.workspace_id == workspace.id

    def test_non_system_actor_without_hook_fails_closed(self, conn, workspace, channel):
        bus.register_default_handlers()
        cmd = StartPipelineCommand(
            workspace_id=workspace.id,
            channel_id=channel.id,
            actor="user-alice",
            idempotency_key=_uid(),
        )
        with pytest.raises(AuthorizationRequiredError):
            bus.dispatch(conn, cmd, registry=ExtensionRegistry())

    def test_injected_deny_hook_prevents_side_effects(self, conn, workspace, channel):
        from app.application.errors import AuthorizationRequiredError as ARE

        def deny_hook(c, cmd_type, ws_id, actor):
            raise ARE(actor, cmd_type)

        bus.register_default_handlers()
        cmd = StartPipelineCommand(
            workspace_id=workspace.id,
            channel_id=channel.id,
            actor="any-actor",
            idempotency_key=_uid(),
        )
        with pytest.raises(ARE):
            bus.dispatch(conn, cmd, registry=ExtensionRegistry(), auth_hook=deny_hook)

        # Confirm no pipeline was created.
        rows = conn.execute(
            "SELECT id FROM app_pipeline_executions WHERE workspace_id=?",
            (workspace.id,),
        ).fetchall()
        assert len(rows) == 0

    def test_injected_allow_hook_permits_user_mutation(self, conn, workspace, channel):
        bus.register_default_handlers()
        cmd = StartPipelineCommand(
            workspace_id=workspace.id,
            channel_id=channel.id,
            actor="user-alice",
            idempotency_key=_uid(),
        )
        result = bus.dispatch(
            conn, cmd, registry=ExtensionRegistry(), auth_hook=allow_all_auth_hook
        )
        assert result.workspace_id == workspace.id
