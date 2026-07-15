"""Command-line interface for the AI Content Production Engine."""

from __future__ import annotations

import sys
from typing import Annotated

import typer

from app import __version__
from app.core.config import get_config
from app.core.database import open_db
from app.core.logging import configure_logging, get_logger
from app.core.models import Run, Script, ScriptStatus, Source, SourceKind, Topic, TopicStatus
from app.core.repository import (
    create_run,
    create_script,
    create_source,
    create_topic,
    delete_source,
    delete_topic,
    list_runs,
    list_scripts,
    list_sources,
    list_topics,
    next_script_version,
    update_run_status,
    update_script_status,
    update_topic,
)

app = typer.Typer(
    name="ace",
    help="AI Content Production Engine command-line interface.",
    no_args_is_help=True,
)
logger = get_logger(__name__)


def _get_db():
    cfg = get_config()
    configure_logging(cfg.log_level)
    return open_db(cfg.db_path)


# ---------------------------------------------------------------------------
# Diagnostic commands
# ---------------------------------------------------------------------------


@app.command()
def version() -> None:
    """Print the installed application version."""
    typer.echo(f"ai-content-engine {__version__}")


@app.command()
def doctor() -> None:
    """Report basic environment diagnostics."""
    typer.echo(f"Python: {sys.version.split()[0]}")
    typer.echo(f"Platform: {sys.platform}")
    typer.echo("Status: OK")


# ---------------------------------------------------------------------------
# Topic commands
# ---------------------------------------------------------------------------

topics_app = typer.Typer(help="Manage content topics.", no_args_is_help=True)
app.add_typer(topics_app, name="topics")


@topics_app.command("add")
def topics_add(
    title: Annotated[str, typer.Argument(help="Topic title.")],
    angle: Annotated[str, typer.Option("--angle", "-a", help="Intended angle.")] = "",
) -> None:
    """Add a new topic."""
    conn = _get_db()
    topic = create_topic(conn, Topic(title=title, angle=angle))
    typer.echo(f"Created topic id={topic.id} title={topic.title!r}")


@topics_app.command("list")
def topics_list(
    status: Annotated[
        TopicStatus | None, typer.Option("--status", "-s", help="Filter by status.")
    ] = None,
) -> None:
    """List topics."""
    conn = _get_db()
    rows = list_topics(conn, status=status)
    if not rows:
        typer.echo("No topics found.")
        return
    for t in rows:
        typer.echo(f"[{t.id}] {t.title!r}  status={t.status.value}  angle={t.angle!r}")


@topics_app.command("archive")
def topics_archive(
    topic_id: Annotated[int, typer.Argument(help="Topic ID to archive.")],
) -> None:
    """Archive a topic (soft-delete)."""
    conn = _get_db()
    from app.core.repository import get_topic

    t = get_topic(conn, topic_id)
    if t is None:
        typer.echo(f"Topic {topic_id} not found.", err=True)
        raise typer.Exit(1)
    t.status = TopicStatus.archived
    update_topic(conn, t)
    typer.echo(f"Topic {topic_id} archived.")


@topics_app.command("delete")
def topics_delete(
    topic_id: Annotated[int, typer.Argument(help="Topic ID to delete permanently.")],
    confirm: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation.")] = False,
) -> None:
    """Permanently delete a topic and all its related data."""
    if not confirm:
        typer.confirm(f"Permanently delete topic {topic_id} and all related data?", abort=True)
    conn = _get_db()
    if not delete_topic(conn, topic_id):
        typer.echo(f"Topic {topic_id} not found.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Topic {topic_id} deleted.")


# ---------------------------------------------------------------------------
# Source commands
# ---------------------------------------------------------------------------

sources_app = typer.Typer(help="Manage source material.", no_args_is_help=True)
app.add_typer(sources_app, name="sources")


@sources_app.command("add")
def sources_add(
    topic_id: Annotated[int, typer.Argument(help="Parent topic ID.")],
    kind: Annotated[SourceKind, typer.Argument(help="Source kind: url | file | note.")],
    reference: Annotated[str, typer.Argument(help="URL, file path, or note text.")],
    notes: Annotated[str, typer.Option("--notes", "-n", help="Optional notes.")] = "",
) -> None:
    """Add a source to a topic."""
    conn = _get_db()
    s = create_source(conn, Source(topic_id=topic_id, kind=kind, reference=reference, notes=notes))
    typer.echo(f"Created source id={s.id} topic_id={topic_id} kind={kind.value}")


@sources_app.command("list")
def sources_list(
    topic_id: Annotated[int, typer.Argument(help="Topic ID.")],
) -> None:
    """List sources for a topic."""
    conn = _get_db()
    rows = list_sources(conn, topic_id)
    if not rows:
        typer.echo("No sources found.")
        return
    for s in rows:
        typer.echo(f"[{s.id}] {s.kind.value}  {s.reference!r}")


@sources_app.command("delete")
def sources_delete(
    source_id: Annotated[int, typer.Argument(help="Source ID to delete.")],
    confirm: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation.")] = False,
) -> None:
    """Delete a source."""
    if not confirm:
        typer.confirm(f"Delete source {source_id}?", abort=True)
    conn = _get_db()
    if not delete_source(conn, source_id):
        typer.echo(f"Source {source_id} not found.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Source {source_id} deleted.")


# ---------------------------------------------------------------------------
# Script commands
# ---------------------------------------------------------------------------

scripts_app = typer.Typer(help="Manage scripts.", no_args_is_help=True)
app.add_typer(scripts_app, name="scripts")


@scripts_app.command("add")
def scripts_add(
    topic_id: Annotated[int, typer.Argument(help="Parent topic ID.")],
    body: Annotated[str, typer.Argument(help="Script body text.")],
) -> None:
    """Add a new script version for a topic."""
    conn = _get_db()
    version = next_script_version(conn, topic_id)
    s = create_script(conn, Script(topic_id=topic_id, version=version, body=body))
    typer.echo(f"Created script id={s.id} topic_id={topic_id} version={version}")


@scripts_app.command("list")
def scripts_list(
    topic_id: Annotated[int, typer.Argument(help="Topic ID.")],
) -> None:
    """List scripts for a topic."""
    conn = _get_db()
    rows = list_scripts(conn, topic_id)
    if not rows:
        typer.echo("No scripts found.")
        return
    for s in rows:
        preview = s.body[:60].replace("\n", " ")
        typer.echo(f"[{s.id}] v{s.version}  status={s.status.value}  {preview!r}")


@scripts_app.command("approve")
def scripts_approve(
    script_id: Annotated[int, typer.Argument(help="Script ID to approve.")],
) -> None:
    """Mark a script as approved."""
    conn = _get_db()
    s = update_script_status(conn, script_id, ScriptStatus.approved)
    if s is None:
        typer.echo(f"Script {script_id} not found.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Script {script_id} approved.")


@scripts_app.command("reject")
def scripts_reject(
    script_id: Annotated[int, typer.Argument(help="Script ID to reject.")],
) -> None:
    """Mark a script as rejected."""
    conn = _get_db()
    s = update_script_status(conn, script_id, ScriptStatus.rejected)
    if s is None:
        typer.echo(f"Script {script_id} not found.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Script {script_id} rejected.")


# ---------------------------------------------------------------------------
# Run commands
# ---------------------------------------------------------------------------

runs_app = typer.Typer(help="Manage production runs.", no_args_is_help=True)
app.add_typer(runs_app, name="runs")


@runs_app.command("create")
def runs_create(
    topic_id: Annotated[int, typer.Argument(help="Topic ID for the run.")],
    script_id: Annotated[
        int | None, typer.Option("--script-id", help="Optional script ID.")
    ] = None,
) -> None:
    """Create a new run for a topic."""
    conn = _get_db()
    r = create_run(conn, Run(topic_id=topic_id, script_id=script_id))
    typer.echo(f"Created run id={r.id} topic_id={topic_id} status={r.status.value}")


@runs_app.command("list")
def runs_list(
    topic_id: Annotated[int, typer.Argument(help="Topic ID.")],
) -> None:
    """List runs for a topic."""
    conn = _get_db()
    rows = list_runs(conn, topic_id)
    if not rows:
        typer.echo("No runs found.")
        return
    for r in rows:
        typer.echo(f"[{r.id}]  status={r.status.value}  script_id={r.script_id}")


@runs_app.command("update-status")
def runs_update_status(
    run_id: Annotated[int, typer.Argument(help="Run ID.")],
    status: Annotated[str, typer.Argument(help="New status: pending|running|completed|failed.")],
    error: Annotated[str | None, typer.Option("--error", help="Error message.")] = None,
) -> None:
    """Update the status of a run."""
    from app.core.models import RunStatus

    try:
        s = RunStatus(status)
    except ValueError:
        typer.echo(f"Invalid status {status!r}.", err=True)
        raise typer.Exit(1) from None
    conn = _get_db()
    r = update_run_status(conn, run_id, s, error=error)
    if r is None:
        typer.echo(f"Run {run_id} not found.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Run {run_id} status={r.status.value}")


# ---------------------------------------------------------------------------
# AI commands (Phase 2)
# ---------------------------------------------------------------------------

ai_app = typer.Typer(help="AI provider diagnostics.", no_args_is_help=True)
app.add_typer(ai_app, name="ai")

ai_prompts_app = typer.Typer(help="Inspect the prompt registry.", no_args_is_help=True)
ai_app.add_typer(ai_prompts_app, name="prompts")


@ai_prompts_app.command("list")
def ai_prompts_list() -> None:
    """List all available versioned prompts."""
    from app.ai.registry import PromptRegistry

    registry = PromptRegistry()
    prompts = registry.list_all()
    if not prompts:
        typer.echo("No prompts found.")
        return
    for p in prompts:
        typer.echo(f"{p.name}  v{p.version}  —  {p.description}")


@ai_prompts_app.command("show")
def ai_prompts_show(
    name: Annotated[str, typer.Argument(help="Prompt name.")],
    version: Annotated[str, typer.Argument(help="Prompt version.")] = "1",
) -> None:
    """Show metadata and content for a specific prompt version."""
    from app.ai.errors import PromptMetadataError, PromptNotFoundError
    from app.ai.registry import PromptRegistry

    registry = PromptRegistry()
    try:
        p = registry.get(name, version)
    except PromptNotFoundError:
        typer.echo(f"Prompt {name!r} v{version} not found.", err=True)
        raise typer.Exit(1) from None
    except PromptMetadataError as exc:
        typer.echo(f"Prompt metadata error: {exc}", err=True)
        raise typer.Exit(1) from None

    typer.echo(f"Name:        {p.name}")
    typer.echo(f"Version:     {p.version}")
    typer.echo(f"Description: {p.description}")
    typer.echo("")
    typer.echo("── System ──")
    typer.echo(p.system.strip())
    typer.echo("")
    typer.echo("── User template ──")
    typer.echo(p.user_template.strip())


@ai_app.command("demo")
def ai_demo(
    text: Annotated[
        str, typer.Option("--text", "-t", help="Text to echo.")
    ] = "Hello from Phase 2!",
    live: Annotated[
        bool,
        typer.Option(
            "--live", help="Use the live Claude provider (requires ACE_ANTHROPIC_API_KEY)."
        ),  # noqa: E501
    ] = False,
) -> None:
    """Run the demo-echo prompt and display the structured result.

    Uses the fake provider by default.  Pass --live to use Claude (credentials
    required; live API calls are not verified during Phase 2).
    """
    from datetime import UTC, datetime

    from app.ai.errors import AIError
    from app.ai.fake import FakeProvider
    from app.ai.registry import PromptRegistry
    from app.ai.schemas import EchoOutput
    from app.ai.usage import record_ai_call

    cfg = get_config()
    configure_logging(cfg.log_level)

    if live and not cfg.dry_run:
        if not cfg.anthropic_api_key:
            typer.echo("Error: ACE_ANTHROPIC_API_KEY is not set. Cannot use --live mode.", err=True)
            raise typer.Exit(1)
        typer.echo(
            "Note: --live mode is implemented but live API calls are not executed "
            "during Phase 2 verification. Use the fake provider instead.",
            err=True,
        )
        raise typer.Exit(1)

    registry = PromptRegistry()
    prompt = registry.get("demo-echo", "1")
    user = prompt.format_user(text=text)

    from app.ai.provider import AIRequest

    request = AIRequest(
        system=prompt.system,
        user=user,
        model="fake",
        response_schema=EchoOutput,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
    )

    import json as _json

    provider = FakeProvider(output=_json.dumps({"echo": text}))
    started = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")

    try:
        response = provider.complete(request)
        status = "success"
        error_cat = None
        error_msg = None
    except AIError as exc:
        typer.echo(f"AI error: {exc}", err=True)
        conn = _get_db()
        record_ai_call(
            conn,
            request,
            None,
            status="failed",
            error_category=type(exc).__name__,
            error_message=str(exc),
            started_at=started,
        )
        raise typer.Exit(1) from None

    conn = _get_db()
    call_id = record_ai_call(
        conn,
        request,
        response,
        status=status,
        error_category=error_cat,
        error_message=error_msg,
        started_at=started,
    )

    typer.echo(f"Provider:      {response.provider_name}")
    typer.echo(f"Model:         {response.model}")
    typer.echo(f"Prompt:        {prompt.name} v{prompt.version}")
    typer.echo(f"Input tokens:  {response.input_tokens}")
    typer.echo(f"Output tokens: {response.output_tokens}")
    typer.echo(f"Duration:      {response.duration_ms} ms")
    typer.echo(f"Retries:       {response.retry_count}")
    typer.echo(f"Usage id:      {call_id}")
    typer.echo("")
    if response.parsed is not None:
        typer.echo(f"Result: {response.parsed.model_dump_json(indent=2)}")
    else:
        typer.echo(f"Raw: {response.raw_text}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
