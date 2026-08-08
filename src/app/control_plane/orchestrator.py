"""Control Plane orchestrator — coordinates all CP subsystems."""

from __future__ import annotations

from typing import Any

from app.control_plane import event_bus, events, workflows
from app.control_plane import repository as repo
from app.control_plane.constants import (
    EVENT_ACCOUNT_CONNECTED,
    EVENT_CHANNEL_CREATED,
    EVENT_WORKSPACE_CREATED,
)
from app.control_plane.models import Channel, PlatformAccount, Workspace
from app.control_plane.workflow_engine import run_workflow


def provision_workspace(
    conn: Any,
    *,
    name: str,
    slug: str,
    actor: str,
) -> Workspace:
    from app.control_plane.identity import create_workspace as _create

    workspace = _create(conn, name=name, slug=slug, actor=actor)
    event = events.emit_event(
        conn,
        event_type=EVENT_WORKSPACE_CREATED,
        workspace_id=workspace.id,
        actor=actor,
        payload={"workspace_id": workspace.id, "name": name},
    )
    event_bus.dispatch_event(conn, event)
    return workspace


def provision_channel(
    conn: Any,
    *,
    workspace_id: str,
    name: str,
    slug: str,
    actor: str,
    description: str | None = None,
) -> Channel:
    from app.control_plane.identity import create_channel as _create

    channel = _create(
        conn,
        workspace_id=workspace_id,
        name=name,
        slug=slug,
        actor=actor,
        description=description,
    )
    event = events.emit_event(
        conn,
        event_type=EVENT_CHANNEL_CREATED,
        workspace_id=workspace_id,
        actor=actor,
        payload={"channel_id": channel.id, "workspace_id": workspace_id},
    )
    event_bus.dispatch_event(conn, event)
    return channel


def connect_platform_account(
    conn: Any,
    *,
    channel_id: str,
    platform_key: str,
    external_account_id: str,
    display_name: str,
    actor: str,
    credential_profile_id: str | None = None,
) -> PlatformAccount:
    from app.control_plane.accounts import connect_account

    channel = repo.get_channel(conn, channel_id)
    account = connect_account(
        conn,
        channel_id=channel_id,
        platform_key=platform_key,
        external_account_id=external_account_id,
        display_name=display_name,
        actor=actor,
        credential_profile_id=credential_profile_id,
    )
    event = events.emit_event(
        conn,
        event_type=EVENT_ACCOUNT_CONNECTED,
        workspace_id=channel.workspace_id,
        actor=actor,
        payload={
            "account_id": account.id,
            "channel_id": channel_id,
            "platform_key": platform_key,
        },
    )
    active_workflows = repo.list_active_workflows_for_trigger(
        conn, channel.workspace_id, EVENT_ACCOUNT_CONNECTED
    )
    for wf in active_workflows:
        run = workflows.start_workflow_run(conn, wf.id, event.id)
        run_workflow(conn, wf, event, run)

    event_bus.dispatch_event(conn, event)
    return account


def process_pending_events(conn: Any, limit: int = 20) -> int:
    pending = repo.list_pending_event_processing(conn, limit=limit)
    processed = 0
    for proc in pending:
        event = repo.get_event(conn, proc.event_id)
        event_bus.dispatch_event(conn, event)
        processed += 1
    return processed
