"""ace control — Media Operations Control Plane CLI commands."""

from __future__ import annotations

from typing import Annotated

import typer

control_app = typer.Typer(
    name="control",
    help="Media Operations Control Plane — workspaces, channels, accounts, policies.",
    no_args_is_help=True,
)

workspace_app = typer.Typer(name="workspace", help="Workspace management.", no_args_is_help=True)
channel_app = typer.Typer(name="channel", help="Channel management.", no_args_is_help=True)
account_app = typer.Typer(name="account", help="Platform account management.", no_args_is_help=True)
policy_app = typer.Typer(name="policy", help="Automation policy management.", no_args_is_help=True)
experiment_app = typer.Typer(name="experiment", help="Experiment management.", no_args_is_help=True)
events_app = typer.Typer(name="events", help="Event history.", no_args_is_help=True)

control_app.add_typer(workspace_app, name="workspace")
control_app.add_typer(channel_app, name="channel")
control_app.add_typer(account_app, name="account")
control_app.add_typer(policy_app, name="policy")
control_app.add_typer(experiment_app, name="experiment")
control_app.add_typer(events_app, name="events")


def _get_db():
    from app.core.config import get_config
    from app.core.database import open_db
    from app.core.logging import configure_logging

    cfg = get_config()
    configure_logging(cfg.log_level)
    return open_db(cfg.db_path)


# ---------------------------------------------------------------------------
# Workspaces
# ---------------------------------------------------------------------------


@workspace_app.command("create")
def workspace_create(
    name: Annotated[str, typer.Argument(help="Workspace display name")],
    slug: Annotated[str, typer.Argument(help="URL-safe slug (unique)")],
    actor: Annotated[str, typer.Option(help="Actor performing the action")] = "cli",
) -> None:
    """Create a new workspace."""
    from app.control_plane.identity import create_workspace

    db = _get_db()
    ws = create_workspace(db, name=name, slug=slug, actor=actor)
    typer.echo(f"Created workspace {ws.id}  name={ws.name}  slug={ws.slug}")


@workspace_app.command("list")
def workspace_list(
    status: Annotated[str | None, typer.Option(help="Filter by status")] = None,
) -> None:
    """List workspaces."""
    from app.control_plane.services import list_workspaces

    db = _get_db()
    items = list_workspaces(db, status=status)
    if not items:
        typer.echo("No workspaces found.")
        return
    for ws in items:
        typer.echo(f"{ws.id}  {ws.slug:<30}  {ws.status}")


@workspace_app.command("show")
def workspace_show(
    workspace_id: Annotated[str, typer.Argument(help="Workspace ID")],
) -> None:
    """Show workspace details."""
    from app.control_plane.services import get_workspace

    db = _get_db()
    ws = get_workspace(db, workspace_id)
    typer.echo(f"ID:     {ws.id}")
    typer.echo(f"Name:   {ws.name}")
    typer.echo(f"Slug:   {ws.slug}")
    typer.echo(f"Status: {ws.status}")
    typer.echo(f"Actor:  {ws.actor}")


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


@channel_app.command("create")
def channel_create(
    workspace_id: Annotated[str, typer.Argument(help="Parent workspace ID")],
    name: Annotated[str, typer.Argument(help="Channel display name")],
    slug: Annotated[str, typer.Argument(help="URL-safe slug (unique per workspace)")],
    description: Annotated[str | None, typer.Option(help="Optional description")] = None,
    actor: Annotated[str, typer.Option(help="Actor")] = "cli",
) -> None:
    """Create a channel within a workspace."""
    from app.control_plane.orchestrator import provision_channel

    db = _get_db()
    ch = provision_channel(
        db,
        workspace_id=workspace_id,
        name=name,
        slug=slug,
        actor=actor,
        description=description,
    )
    typer.echo(f"Created channel {ch.id}  name={ch.name}  workspace={ch.workspace_id}")


@channel_app.command("list")
def channel_list(
    workspace_id: Annotated[str, typer.Argument(help="Workspace ID")],
) -> None:
    """List channels in a workspace."""
    from app.control_plane.services import list_channels

    db = _get_db()
    items = list_channels(db, workspace_id)
    if not items:
        typer.echo("No channels found.")
        return
    for ch in items:
        typer.echo(f"{ch.id}  {ch.slug:<30}  {ch.status}")


# ---------------------------------------------------------------------------
# Platform accounts
# ---------------------------------------------------------------------------


@account_app.command("connect")
def account_connect(
    channel_id: Annotated[str, typer.Argument(help="Channel ID")],
    platform_key: Annotated[str, typer.Argument(help="Platform key (youtube, tiktok, ...)")],
    external_account_id: Annotated[str, typer.Argument(help="External platform account ID")],
    display_name: Annotated[str, typer.Argument(help="Human-readable account name")],
    actor: Annotated[str, typer.Option(help="Actor")] = "cli",
) -> None:
    """Connect a platform account to a channel."""
    from app.control_plane.orchestrator import connect_platform_account

    db = _get_db()
    acc = connect_platform_account(
        db,
        channel_id=channel_id,
        platform_key=platform_key,
        external_account_id=external_account_id,
        display_name=display_name,
        actor=actor,
    )
    typer.echo(f"Connected {acc.id}  platform={acc.platform_key}  status={acc.status}")


@account_app.command("list")
def account_list(
    channel_id: Annotated[str, typer.Argument(help="Channel ID")],
) -> None:
    """List platform accounts for a channel."""
    from app.control_plane.services import list_platform_accounts

    db = _get_db()
    items = list_platform_accounts(db, channel_id)
    if not items:
        typer.echo("No accounts found.")
        return
    for acc in items:
        typer.echo(f"{acc.id}  {acc.platform_key:<15}  {acc.status}")


@account_app.command("pause")
def account_pause(
    account_id: Annotated[str, typer.Argument(help="Platform account ID")],
    actor: Annotated[str, typer.Option(help="Actor")] = "cli",
) -> None:
    """Pause a platform account."""
    from app.control_plane.accounts import pause_account

    db = _get_db()
    acc = pause_account(db, account_id, actor)
    typer.echo(f"Paused {acc.id}  status={acc.status}")


@account_app.command("resume")
def account_resume(
    account_id: Annotated[str, typer.Argument(help="Platform account ID")],
    actor: Annotated[str, typer.Option(help="Actor")] = "cli",
) -> None:
    """Resume a paused platform account."""
    from app.control_plane.accounts import resume_account

    db = _get_db()
    acc = resume_account(db, account_id, actor)
    typer.echo(f"Resumed {acc.id}  status={acc.status}")


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------


@policy_app.command("set")
def policy_set(
    scope: Annotated[str, typer.Argument(help="scope: workspace / channel / platform_account")],
    scope_id: Annotated[str, typer.Argument(help="ID of the scope entity")],
    automation_level: Annotated[str, typer.Argument(help="manual / supervised / autonomous")],
    actor: Annotated[str, typer.Option(help="Actor")] = "cli",
) -> None:
    """Set automation policy for a scope."""
    from app.control_plane.policies import set_policy

    db = _get_db()
    policy = set_policy(
        db,
        scope=scope,
        scope_id=scope_id,
        automation_level=automation_level,
        allowed_actions=[],
        actor=actor,
    )
    typer.echo(f"Policy {policy.id}  level={policy.automation_level}  active={policy.is_active}")


@policy_app.command("effective")
def policy_effective(
    workspace_id: Annotated[str, typer.Argument(help="Workspace ID")],
    channel_id: Annotated[str | None, typer.Option(help="Channel ID (optional)")] = None,
    account_id: Annotated[str | None, typer.Option(help="Platform account ID (optional)")] = None,
) -> None:
    """Show effective automation level for a scope hierarchy."""
    from app.control_plane.services import get_effective_policy

    db = _get_db()
    level = get_effective_policy(
        db, workspace_id, channel_id=channel_id, platform_account_id=account_id
    )
    typer.echo(f"Effective automation level: {level}")


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------


@experiment_app.command("create")
def experiment_create(
    workspace_id: Annotated[str, typer.Argument()],
    channel_id: Annotated[str, typer.Argument()],
    name: Annotated[str, typer.Argument()],
    hypothesis: Annotated[str, typer.Argument()],
    primary_metric: Annotated[str, typer.Argument()],
    actor: Annotated[str, typer.Option()] = "cli",
) -> None:
    """Create a new experiment (draft status)."""
    from app.control_plane.experiments import create_experiment

    db = _get_db()
    exp = create_experiment(
        db,
        workspace_id=workspace_id,
        channel_id=channel_id,
        name=name,
        hypothesis=hypothesis,
        primary_metric=primary_metric,
        actor=actor,
    )
    typer.echo(f"Created experiment {exp.id}  name={exp.name}  status={exp.status}")


@experiment_app.command("activate")
def experiment_activate(
    experiment_id: Annotated[str, typer.Argument()],
) -> None:
    """Activate an experiment (immutable once active)."""
    from app.control_plane.experiments import activate_experiment

    db = _get_db()
    exp = activate_experiment(db, experiment_id)
    typer.echo(f"Activated {exp.id}  status={exp.status}")


@experiment_app.command("conclude")
def experiment_conclude(
    experiment_id: Annotated[str, typer.Argument()],
) -> None:
    """Conclude an active experiment."""
    from app.control_plane.experiments import conclude_experiment

    db = _get_db()
    exp = conclude_experiment(db, experiment_id)
    typer.echo(f"Concluded {exp.id}  status={exp.status}")


@experiment_app.command("list")
def experiment_list(
    workspace_id: Annotated[str, typer.Argument()],
    status: Annotated[str | None, typer.Option()] = None,
) -> None:
    """List experiments for a workspace."""
    from app.control_plane import repository as repo

    db = _get_db()
    items = repo.list_experiments_by_workspace(db, workspace_id, status=status)
    if not items:
        typer.echo("No experiments found.")
        return
    for exp in items:
        typer.echo(f"{exp.id}  {exp.name:<30}  {exp.status}")


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@events_app.command("list")
def events_list(
    workspace_id: Annotated[str, typer.Argument()],
    event_type: Annotated[str | None, typer.Option("--type")] = None,
    limit: Annotated[int, typer.Option()] = 20,
) -> None:
    """List recent events for a workspace."""
    from app.control_plane.services import get_event_history

    db = _get_db()
    items = get_event_history(db, workspace_id, event_type=event_type, limit=limit)
    for ev in items:
        ts = ev.created_at.strftime("%Y-%m-%dT%H:%M:%S")
        typer.echo(f"{ts}  {ev.event_type:<40}  {ev.id}")


# ---------------------------------------------------------------------------
# Top-level review queue / costs
# ---------------------------------------------------------------------------


@control_app.command("review-queue")
def review_queue(
    workspace_id: Annotated[str, typer.Argument()],
) -> None:
    """Show the review queue for a workspace."""
    from app.control_plane.services import get_review_queue

    db = _get_db()
    items = get_review_queue(db, workspace_id)
    if not items:
        typer.echo("Review queue is empty.")
        return
    for item in items:
        typer.echo(f"[{item.severity.upper():<6}] {item.item_type:<25}  {item.summary}")


@control_app.command("costs")
def costs_summary(
    workspace_id: Annotated[str, typer.Argument()],
) -> None:
    """Show cost summary for a workspace."""
    from app.control_plane.services import get_cost_summary

    db = _get_db()
    summary = get_cost_summary(db, workspace_id)
    typer.echo(f"Total USD: {summary['total_usd']:.4f}")
    typer.echo(f"Records:   {summary['record_count']}")
    for provider, usd in summary.get("by_provider", {}).items():
        typer.echo(f"  {provider:<20}  ${usd:.4f}")
