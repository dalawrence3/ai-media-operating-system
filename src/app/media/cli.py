"""Phase 8 Rendering Engine CLI sub-app."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

render_app = typer.Typer(
    help="Rendering Engine: compose, render, validate, and review video renders.",
    no_args_is_help=True,
)


def _get_db():
    from app.core.config import get_config
    from app.core.database import open_db
    from app.core.logging import configure_logging

    cfg = get_config()
    configure_logging(cfg.log_level)
    return open_db(cfg.db_path)


def _artifacts_path() -> Path:
    from app.core.config import get_config

    return Path(get_config().artifacts_path)


# ---------------------------------------------------------------------------
# ace render plan <scene_manifest_id>
# ---------------------------------------------------------------------------


@render_app.command("plan")
def render_plan(
    scene_manifest_id: Annotated[int, typer.Argument(help="Scene manifest ID.")],
    width: Annotated[int, typer.Option("--width", help="Output width (px).")] = 1080,
    height: Annotated[int, typer.Option("--height", help="Output height (px).")] = 1920,
    fps: Annotated[int, typer.Option("--fps", help="Frames per second.")] = 30,
    caption_burn_in: Annotated[
        bool, typer.Option("--captions/--no-captions", help="Burn captions into video.")
    ] = False,
    experiment_id: Annotated[
        str | None, typer.Option("--experiment-id", help="Experiment label.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Build manifest without persisting.")
    ] = False,
) -> None:
    """Compose a RenderManifest from the approved scene manifest SCENE_MANIFEST_ID."""
    from app.media.compositor import SceneInputBuilder, build_render_manifest
    from app.media.repository import get_or_create_render_manifest
    from app.scenes.repository import get_scene_manifest_full_by_id

    conn = _get_db()
    approved = get_scene_manifest_full_by_id(conn, scene_manifest_id)
    if approved is None:
        typer.echo(
            f"Scene manifest {scene_manifest_id} is not approved or not found.", err=True
        )
        raise typer.Exit(1)

    builder = SceneInputBuilder(conn)
    scene_inputs = builder.build(approved)

    draft = build_render_manifest(
        scene_manifest_id=approved.manifest_id,
        scene_manifest_input_hash=approved.input_hash,
        narration_run_id=approved.narration_run_id,
        narration_input_hash=builder.narration_input_hash,
        caption_run_id=approved.caption_run_id,
        topic_id=approved.topic_id,
        plan_id=approved.plan_id,
        script_id=approved.script_id,
        scenes=scene_inputs,
        width=width,
        height=height,
        fps=fps,
        caption_burn_in=caption_burn_in,
        experiment_id=experiment_id,
    )

    if dry_run:
        typer.echo(f"[dry-run] RenderManifest hash: {draft.input_hash}")
        typer.echo(f"  scenes:        {draft.total_scene_count}")
        typer.echo(f"  duration_ms:   {draft.total_duration_ms}")
        typer.echo(f"  resolution:    {draft.width}x{draft.height}")
        typer.echo(f"  fps:           {draft.fps}")
        for s in draft.scenes:
            asset_label = s.primary_asset.local_path if s.primary_asset else "(placeholder)"
            audio_label = s.audio_path or "(silent)"
            typer.echo(
                f"  scene {s.scene_index:2d}: audio={audio_label!r}  asset={asset_label!r}"
            )
        return

    manifest, created = get_or_create_render_manifest(conn, draft)
    conn.commit()
    verb = "Created" if created else "Found existing"
    typer.echo(f"{verb} render manifest id={manifest.id} hash={manifest.input_hash}")
    typer.echo(f"  status:        {manifest.status}")
    typer.echo(f"  scenes:        {manifest.total_scene_count}")
    typer.echo(f"  duration_ms:   {manifest.total_duration_ms}")
    typer.echo(f"  resolution:    {manifest.width}x{manifest.height}")


# ---------------------------------------------------------------------------
# ace render start <render_manifest_id>
# ---------------------------------------------------------------------------


@render_app.command("start")
def render_start(
    render_manifest_id: Annotated[int, typer.Argument(help="Render manifest ID.")],
    output: Annotated[
        str | None,
        typer.Option("--output", "-o", help="Output MP4 path (default: artifacts dir)."),
    ] = None,
    video_codec: Annotated[
        str, typer.Option("--video-codec", help="FFmpeg video codec.")
    ] = "libx264",
    audio_codec: Annotated[
        str, typer.Option("--audio-codec", help="FFmpeg audio codec.")
    ] = "aac",
    crf: Annotated[int, typer.Option("--crf", help="Constant rate factor (0–51).")] = 23,
    preset: Annotated[
        str, typer.Option("--preset", help="FFmpeg encoding preset.")
    ] = "medium",
    audio_bitrate: Annotated[
        str, typer.Option("--audio-bitrate", help="Audio bitrate (e.g. '128k').")
    ] = "128k",
    allow_placeholders: Annotated[
        bool,
        typer.Option(
            "--allow-placeholders",
            help="[DEV ONLY] Allow placeholder slides for unresolved assets.",
        ),
    ] = False,
) -> None:
    """Execute a render for RENDER_MANIFEST_ID using FFmpeg."""
    import tempfile

    from app.media.backend import (
        FFMPEG_BACKEND_VERSION,
        FFmpegRenderBackend,
        check_ffmpeg_available,
    )
    from app.media.compositor import SceneInputBuilder
    from app.media.errors import (
        FFmpegNotFoundError,
        RenderBackendError,
        UnresolvedRequiredAssetError,
    )
    from app.media.repository import (
        create_render_job,
        get_render_manifest,
        mark_render_job_completed,
        mark_render_job_failed,
        mark_render_job_rendering,
    )
    from app.scenes.repository import get_scene_manifest_full_by_id

    if not check_ffmpeg_available():
        typer.echo("ffmpeg not found on PATH. Install FFmpeg to render videos.", err=True)
        raise typer.Exit(1)

    conn = _get_db()
    manifest = get_render_manifest(conn, render_manifest_id)
    if manifest is None:
        typer.echo(f"Render manifest {render_manifest_id} not found.", err=True)
        raise typer.Exit(1)

    if manifest.status != "draft":
        typer.echo(
            f"Render manifest {render_manifest_id} has status '{manifest.status}'"
            " (expected 'draft').",
            err=True,
        )
        raise typer.Exit(1)

    # Resolve output path
    if output:
        output_path = Path(output)
    else:
        artifacts = _artifacts_path()
        renders_dir = artifacts / "renders" / str(manifest.scene_manifest_id)
        renders_dir.mkdir(parents=True, exist_ok=True)
        output_path = renders_dir / f"render_{render_manifest_id}.mp4"

    # Create job record
    job = create_render_job(
        conn,
        render_manifest_id,
        backend="ffmpeg",
        backend_version=FFMPEG_BACKEND_VERSION,
        width=manifest.width,
        height=manifest.height,
        fps=manifest.fps,
        video_codec=video_codec,
        audio_codec=audio_codec,
        crf=crf,
        audio_bitrate=audio_bitrate,
        caption_burn_in=manifest.caption_burn_in,
    )
    conn.commit()

    typer.echo(f"Render job id={job.id} created. Fetching scene data...")

    approved = get_scene_manifest_full_by_id(conn, manifest.scene_manifest_id)
    if approved is None:
        typer.echo("Scene manifest not found or not approved.", err=True)
        raise typer.Exit(1)

    builder = SceneInputBuilder(conn)
    scene_inputs = builder.build(approved)

    from app.media.compositor import build_render_manifest as _build
    draft = _build(
        scene_manifest_id=approved.manifest_id,
        scene_manifest_input_hash=approved.input_hash,
        narration_run_id=approved.narration_run_id,
        narration_input_hash=builder.narration_input_hash,
        caption_run_id=approved.caption_run_id,
        topic_id=approved.topic_id,
        plan_id=approved.plan_id,
        script_id=approved.script_id,
        scenes=scene_inputs,
        width=manifest.width,
        height=manifest.height,
        fps=manifest.fps,
        caption_burn_in=manifest.caption_burn_in,
        experiment_id=manifest.experiment_id,
    )

    backend = FFmpegRenderBackend()
    mark_render_job_rendering(conn, job.id)
    conn.commit()
    typer.echo(f"Rendering to {output_path} ...")

    with tempfile.TemporaryDirectory(prefix="ace_render_") as tmp:
        try:
            result = backend.render(
                draft,
                output_path,
                Path(tmp),
                video_codec=video_codec,
                audio_codec=audio_codec,
                crf=crf,
                preset=preset,
                audio_bitrate=audio_bitrate,
                allow_placeholders=allow_placeholders,
            )
        except (RenderBackendError, FFmpegNotFoundError, UnresolvedRequiredAssetError) as exc:
            mark_render_job_failed(conn, job.id, error_message=str(exc))
            conn.commit()
            typer.echo(f"Render failed: {exc}", err=True)
            raise typer.Exit(1) from None

    mark_render_job_completed(
        conn,
        job.id,
        output_path=result.output_path,
        output_sha256=result.output_sha256,
        duration_s=result.duration_s,
        file_size_bytes=result.file_size_bytes,
        render_time_s=result.render_time_s,
        ffmpeg_cmd=result.ffmpeg_cmd,
    )
    conn.commit()

    typer.echo(f"Render complete: {result.output_path}")
    typer.echo(f"  sha256:        {result.output_sha256}")
    typer.echo(f"  duration:      {result.duration_s:.2f}s")
    typer.echo(f"  size:          {result.file_size_bytes:,} bytes")
    typer.echo(f"  render_time:   {result.render_time_s:.1f}s")
    typer.echo(f"  job_id:        {job.id}")


# ---------------------------------------------------------------------------
# ace render validate <render_job_id>
# ---------------------------------------------------------------------------


@render_app.command("validate")
def render_validate(
    render_job_id: Annotated[int, typer.Argument(help="Render job ID.")],
) -> None:
    """Run FFprobe validation on render job RENDER_JOB_ID."""
    from app.media.errors import FFprobeNotFoundError, RenderValidationError
    from app.media.repository import (
        get_render_job,
        get_render_manifest,
        mark_render_job_validated,
    )
    from app.media.validator import FFprobeValidator, check_ffprobe_available

    if not check_ffprobe_available():
        typer.echo("ffprobe not found on PATH. Install FFmpeg (includes ffprobe).", err=True)
        raise typer.Exit(1)

    conn = _get_db()
    job = get_render_job(conn, render_job_id)
    if job is None:
        typer.echo(f"Render job {render_job_id} not found.", err=True)
        raise typer.Exit(1)

    if job.status != "completed":
        typer.echo(
            f"Job {render_job_id} has status '{job.status}' (expected 'completed').",
            err=True,
        )
        raise typer.Exit(1)

    if not job.output_path:
        typer.echo("Job has no output path.", err=True)
        raise typer.Exit(1)

    manifest = get_render_manifest(conn, job.render_manifest_id)
    expected_s = manifest.total_duration_ms / 1000.0 if manifest else 0.0

    validator = FFprobeValidator()
    try:
        result = validator.validate(Path(job.output_path), expected_s)
    except (RenderValidationError, FFprobeNotFoundError) as exc:
        typer.echo(f"Validation failed: {exc}", err=True)
        raise typer.Exit(1) from None

    mark_render_job_validated(conn, render_job_id, validation_metadata=result.to_dict())
    conn.commit()

    typer.echo(f"Validation passed for job {render_job_id}:")
    typer.echo(f"  duration:      {result.duration_s:.2f}s")
    typer.echo(f"  resolution:    {result.width}x{result.height}")
    typer.echo(f"  fps:           {result.fps}")
    typer.echo(f"  video_codec:   {result.video_codec}")
    typer.echo(f"  audio_codec:   {result.audio_codec}")
    typer.echo(f"  size:          {result.file_size_bytes:,} bytes")


# ---------------------------------------------------------------------------
# ace render approve <render_manifest_id>
# ---------------------------------------------------------------------------


@render_app.command("approve")
def render_approve(
    render_manifest_id: Annotated[int, typer.Argument(help="Render manifest ID.")],
    actor: Annotated[str | None, typer.Option("--actor", help="Reviewer name.")] = None,
    notes: Annotated[str | None, typer.Option("--notes", help="Review notes.")] = None,
    render_job_id: Annotated[
        int | None, typer.Option("--job-id", help="Specific render job being approved.")
    ] = None,
) -> None:
    """Approve render manifest RENDER_MANIFEST_ID for publication."""
    from app.media.errors import IllegalRenderTransitionError, RenderManifestNotFoundError
    from app.media.repository import approve_render_manifest

    conn = _get_db()
    try:
        manifest = approve_render_manifest(
            conn,
            render_manifest_id,
            actor=actor,
            notes=notes,
            render_job_id=render_job_id,
        )
    except (IllegalRenderTransitionError, RenderManifestNotFoundError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None

    conn.commit()
    typer.echo(f"Render manifest {render_manifest_id} approved at {manifest.approved_at}.")


# ---------------------------------------------------------------------------
# ace render reject <render_manifest_id>
# ---------------------------------------------------------------------------


@render_app.command("reject")
def render_reject(
    render_manifest_id: Annotated[int, typer.Argument(help="Render manifest ID.")],
    reason: Annotated[
        str | None,
        typer.Option("--reason", help="Reason code (audio_sync/visual_quality/…)"),
    ] = None,
    severity: Annotated[
        int | None, typer.Option("--severity", help="Severity 1–5.")
    ] = None,
    correction: Annotated[
        str | None, typer.Option("--correction", help="Expected correction.")
    ] = None,
    actor: Annotated[str | None, typer.Option("--actor", help="Reviewer name.")] = None,
    notes: Annotated[str | None, typer.Option("--notes", help="Review notes.")] = None,
) -> None:
    """Reject render manifest RENDER_MANIFEST_ID."""
    from app.media.errors import IllegalRenderTransitionError, RenderManifestNotFoundError
    from app.media.repository import reject_render_manifest

    conn = _get_db()
    try:
        reject_render_manifest(
            conn,
            render_manifest_id,
            actor=actor,
            notes=notes,
            reason_code=reason,
            severity=severity,
            expected_correction=correction,
        )
    except (IllegalRenderTransitionError, RenderManifestNotFoundError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None

    conn.commit()
    typer.echo(f"Render manifest {render_manifest_id} rejected.")


# ---------------------------------------------------------------------------
# ace render list
# ---------------------------------------------------------------------------


@render_app.command("list")
def render_list(
    topic_id: Annotated[
        int | None, typer.Option("--topic-id", help="Filter by topic.")
    ] = None,
    scene_manifest_id: Annotated[
        int | None, typer.Option("--scene-manifest-id", help="Filter by scene manifest.")
    ] = None,
    status: Annotated[
        str | None, typer.Option("--status", help="Filter by status.")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max results.")] = 20,
) -> None:
    """List render manifests."""
    from app.media.repository import list_render_manifests

    conn = _get_db()
    manifests = list_render_manifests(
        conn,
        topic_id=topic_id,
        scene_manifest_id=scene_manifest_id,
        status=status,
        limit=limit,
    )
    if not manifests:
        typer.echo("No render manifests found.")
        return
    for m in manifests:
        typer.echo(
            f"id={m.id}  status={m.status:12s}  scenes={m.total_scene_count}"
            f"  {m.width}x{m.height}@{m.fps}fps  {m.total_duration_ms}ms"
            f"  scene_manifest={m.scene_manifest_id}"
        )


# ---------------------------------------------------------------------------
# ace render show <render_manifest_id>
# ---------------------------------------------------------------------------


@render_app.command("show")
def render_show(
    render_manifest_id: Annotated[int, typer.Argument(help="Render manifest ID.")],
) -> None:
    """Show detailed view of a render manifest and its jobs."""
    from app.media.repository import (
        get_render_manifest,
        list_render_jobs,
        list_render_review_events,
    )

    conn = _get_db()
    manifest = get_render_manifest(conn, render_manifest_id)
    if manifest is None:
        typer.echo(f"Render manifest {render_manifest_id} not found.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Render manifest id={manifest.id}")
    typer.echo(f"  status:             {manifest.status}")
    typer.echo(f"  scene_manifest_id:  {manifest.scene_manifest_id}")
    typer.echo(f"  narration_run_id:   {manifest.narration_run_id}")
    typer.echo(f"  caption_run_id:     {manifest.caption_run_id}")
    typer.echo(f"  resolution:         {manifest.width}x{manifest.height}@{manifest.fps}fps")
    typer.echo(f"  caption_burn_in:    {manifest.caption_burn_in}")
    typer.echo(f"  scenes:             {manifest.total_scene_count}")
    typer.echo(f"  duration_ms:        {manifest.total_duration_ms}")
    typer.echo(f"  input_hash:         {manifest.input_hash}")
    typer.echo(f"  compositor:         {manifest.compositor_version}")
    typer.echo(f"  schema:             {manifest.render_schema_version}")
    typer.echo(f"  approved_at:        {manifest.approved_at}")
    typer.echo(f"  created_at:         {manifest.created_at}")

    jobs = list_render_jobs(conn, render_manifest_id)
    typer.echo(f"\nRender jobs ({len(jobs)}):")
    for j in jobs:
        typer.echo(
            f"  job id={j.id}  status={j.status}  backend={j.backend}"
            + (f"  {j.duration_s:.1f}s  {j.file_size_bytes:,}B" if j.file_size_bytes else "")
            + (f"  validated={j.validated}" if j.status == "completed" else "")
        )
        if j.output_path:
            typer.echo(f"    output: {j.output_path}")

    events = list_render_review_events(conn, render_manifest_id)
    if events:
        typer.echo(f"\nReview events ({len(events)}):")
        for e in events:
            typer.echo(
                f"  [{e.created_at}] {e.event_type}"
                f"  actor={e.actor}  reason={e.reason_code}"
            )



# ---------------------------------------------------------------------------
# ace render events <render_manifest_id>
# ---------------------------------------------------------------------------


@render_app.command("events")
def render_events(
    render_manifest_id: Annotated[int, typer.Argument(help="Render manifest ID.")],
) -> None:
    """List review events for render manifest RENDER_MANIFEST_ID."""
    from app.media.repository import list_render_review_events

    conn = _get_db()
    events = list_render_review_events(conn, render_manifest_id)
    if not events:
        typer.echo("No review events.")
        return
    for e in events:
        typer.echo(
            f"[{e.created_at}] id={e.id}  {e.event_type}"
            f"  reason={e.reason_code}  severity={e.severity}"
            f"  actor={e.actor}"
        )
        if e.notes:
            typer.echo(f"  notes: {e.notes}")
        if e.expected_correction:
            typer.echo(f"  correction: {e.expected_correction}")
