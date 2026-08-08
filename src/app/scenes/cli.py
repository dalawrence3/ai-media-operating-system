"""Visual Intelligence CLI sub-app — scene manifest operator workflows."""

from __future__ import annotations

from typing import Annotated

import typer

scenes_app = typer.Typer(
    help="Visual Intelligence: plan, review, and inspect scene manifests.",
    no_args_is_help=True,
)


def _get_db():
    from app.core.config import get_config
    from app.core.database import open_db
    from app.core.logging import configure_logging

    cfg = get_config()
    configure_logging(cfg.log_level)
    return open_db(cfg.db_path)


@scenes_app.command("plan")
def scenes_plan(
    topic_id: Annotated[int, typer.Argument(help="Topic ID.")],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Build without persisting."),
    ] = False,
) -> None:
    """Build a scene manifest from the active approved caption run for TOPIC_ID."""
    from app.captions.repository import get_caption_run, list_caption_runs_by_plan
    from app.narration.repository import get_approved_narration_run_full
    from app.production.repository import get_approved_production_plan_full
    from app.scenes.errors import ManifestBuildError, NoApprovedCaptionRunError
    from app.scenes.planner import build_scene_manifest
    from app.scenes.repository import get_or_create_scene_manifest

    conn = _get_db()

    plan = get_approved_production_plan_full(conn, topic_id)
    if plan is None:
        typer.echo(f"No approved production plan for topic_id={topic_id}.", err=True)
        raise typer.Exit(1)

    narration_run = get_approved_narration_run_full(conn, plan.plan_id)
    if narration_run is None:
        typer.echo(f"No approved narration run for plan_id={plan.plan_id}.", err=True)
        raise typer.Exit(1)

    caption_runs = list_caption_runs_by_plan(conn, plan.plan_id)
    approved_captions = [
        r for r in caption_runs if r.status == "approved" and r.superseded_at is None
    ]
    if not approved_captions:
        raise NoApprovedCaptionRunError(f"No approved caption run for plan_id={plan.plan_id}.")
    caption_run = get_caption_run(conn, approved_captions[0].id)
    if caption_run is None:
        typer.echo(f"Caption run {approved_captions[0].id} not found.", err=True)
        raise typer.Exit(1)

    try:
        draft = build_scene_manifest(
            conn,
            caption_run=caption_run,
            narration_run=narration_run,
            production_plan=plan,
        )
    except Exception as exc:
        raise ManifestBuildError(str(exc)) from exc

    if dry_run:
        typer.echo(
            f"[dry-run] topic={topic_id}  plan={plan.plan_id}"
            f"  narration_run={narration_run.run_id}  caption_run={caption_run.id}"
        )
        typer.echo(
            f"  scenes={draft.total_scene_count}"
            f"  assets={draft.total_asset_count}"
            f"  duration_ms={draft.total_duration_ms}"
            f"  input_hash={draft.input_hash[:12]}..."
        )
        typer.echo("  Validation passed — no writes performed.")
        return

    manifest, created = get_or_create_scene_manifest(conn, draft)
    conn.commit()

    if created:
        typer.echo(
            f"Created scene manifest id={manifest.id}  topic={topic_id}  status={manifest.status}"
        )
    else:
        typer.echo(
            f"Scene manifest already exists id={manifest.id}"
            f"  topic={topic_id}  status={manifest.status}"
        )
    typer.echo(
        f"  scenes={manifest.total_scene_count}"
        f"  assets={manifest.total_asset_count}"
        f"  duration_ms={manifest.total_duration_ms}"
        f"  input_hash={manifest.input_hash[:12]}..."
    )


@scenes_app.command("list")
def scenes_list(
    topic_id: Annotated[int, typer.Argument(help="Topic ID.")],
) -> None:
    """List scene manifests for a topic."""
    from app.scenes.repository import list_scene_manifests

    conn = _get_db()
    manifests = list_scene_manifests(conn, topic_id)
    if not manifests:
        typer.echo("No scene manifests found.")
        return
    for m in manifests:
        active = " [active]" if m.status == "approved" and m.superseded_at is None else ""
        sup = "  [superseded]" if m.superseded_at else ""
        typer.echo(
            f"[{m.id}] status={m.status}{active}{sup}"
            f"  scenes={m.total_scene_count}  assets={m.total_asset_count}"
            f"  created={m.created_at}"
        )


@scenes_app.command("show")
def scenes_show(
    manifest_id: Annotated[int, typer.Argument(help="Scene manifest ID.")],
) -> None:
    """Show detailed information for a scene manifest."""
    from app.scenes.repository import (
        get_scene_manifest_assets,
        get_scene_manifest_by_id,
        get_scene_manifest_scenes,
        list_scene_manifest_review_events,
    )

    conn = _get_db()
    manifest = get_scene_manifest_by_id(conn, manifest_id)
    if manifest is None:
        typer.echo(f"Scene manifest {manifest_id} not found.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Scene Manifest [{manifest.id}]")
    typer.echo(f"  status             : {manifest.status}")
    typer.echo(f"  topic              : {manifest.topic_id}")
    typer.echo(f"  plan               : {manifest.plan_id}")
    typer.echo(f"  script             : {manifest.script_id}")
    typer.echo(f"  narration_run      : {manifest.narration_run_id}")
    typer.echo(f"  caption_run        : {manifest.caption_run_id}")
    typer.echo(f"  experiment         : {manifest.experiment_id or '(none)'}")
    typer.echo(f"  total_scenes       : {manifest.total_scene_count}")
    typer.echo(f"  total_assets       : {manifest.total_asset_count}")
    typer.echo(f"  total_duration_ms  : {manifest.total_duration_ms}")
    typer.echo(f"  manifest_schema    : {manifest.manifest_schema_version}")
    typer.echo(f"  planner_version    : {manifest.planner_version}")
    typer.echo(f"  input_hash         : {manifest.input_hash}")
    typer.echo(f"  created_at         : {manifest.created_at}")
    if manifest.approved_at:
        typer.echo(f"  approved_at        : {manifest.approved_at}")
    if manifest.rejected_at:
        typer.echo(f"  rejected_at        : {manifest.rejected_at}")
    if manifest.superseded_at:
        typer.echo(f"  superseded_at      : {manifest.superseded_at}")
        typer.echo(f"  superseded_by      : {manifest.superseded_by_manifest_id}")

    events = list_scene_manifest_review_events(conn, manifest_id)
    if events:
        typer.echo(f"  --- review events ({len(events)}) ---")
        for ev in events:
            code = f"  reason={ev.reason_code}" if ev.reason_code else ""
            typer.echo(f"    [{ev.id}] {ev.event_type}{code}  {ev.created_at}")

    scenes = get_scene_manifest_scenes(conn, manifest_id)
    typer.echo(f"  --- scenes ({len(scenes)}) ---")
    for scene in scenes:
        typer.echo(
            f"  [{scene.scene_index:>3}]"
            f"  seg={scene.segment_id}"
            f"  {scene.start_ms}–{scene.end_ms}ms"
            f"  shot={scene.shot_type}"
            f"  cam={scene.camera_movement}"
            f"  t_in={scene.transition_in}"
            f"  t_out={scene.transition_out}"
            f"  conf={scene.confidence:.2f}"
        )
        typer.echo(f"         objective: {scene.visual_objective[:80]}")
        assets = get_scene_manifest_assets(conn, scene.id)
        for asset in assets:
            ai_flag = " [AI]" if asset.ai_generation_requested else ""
            typer.echo(
                f"         asset[{asset.asset_index}] {asset.category}"
                f"  priority={asset.priority}"
                f"  license={asset.license_status}{ai_flag}"
            )


@scenes_app.command("approve")
def scenes_approve(
    manifest_id: Annotated[int, typer.Argument(help="Scene manifest ID.")],
    actor: Annotated[str | None, typer.Option("--actor", help="Reviewer name.")] = None,
    notes: Annotated[str | None, typer.Option("--notes", help="Approval notes.")] = None,
) -> None:
    """Approve a draft scene manifest."""
    from app.scenes.errors import IllegalManifestTransitionError, ManifestNotFoundError
    from app.scenes.repository import approve_scene_manifest

    conn = _get_db()
    try:
        manifest = approve_scene_manifest(conn, manifest_id, actor=actor, notes=notes)
        conn.commit()
    except ManifestNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    except IllegalManifestTransitionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"Approved scene manifest id={manifest.id}  approved_at={manifest.approved_at}")


@scenes_app.command("reject")
def scenes_reject(
    manifest_id: Annotated[int, typer.Argument(help="Scene manifest ID.")],
    reason_code: Annotated[
        str | None, typer.Option("--reason-code", "-r", help="Rejection reason code.")
    ] = None,
    notes: Annotated[str | None, typer.Option("--notes", help="Rejection notes.")] = None,
    severity: Annotated[int | None, typer.Option("--severity", help="Severity 1–5.")] = None,
    actor: Annotated[str | None, typer.Option("--actor", help="Reviewer name.")] = None,
) -> None:
    """Reject a draft or approved scene manifest."""
    from app.scenes.errors import IllegalManifestTransitionError, ManifestNotFoundError
    from app.scenes.repository import reject_scene_manifest

    conn = _get_db()
    try:
        manifest = reject_scene_manifest(
            conn,
            manifest_id,
            reason_code=reason_code,
            notes=notes,
            severity=severity,
            actor=actor,
        )
        conn.commit()
    except ManifestNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    except IllegalManifestTransitionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"Rejected scene manifest id={manifest.id}  rejected_at={manifest.rejected_at}")


@scenes_app.command("reject-scene")
def scenes_reject_scene(
    manifest_id: Annotated[int, typer.Argument(help="Scene manifest ID.")],
    scene_id: Annotated[int, typer.Argument(help="Scene ID to reject.")],
    reason_code: Annotated[
        str | None, typer.Option("--reason-code", "-r", help="Rejection reason code.")
    ] = None,
    notes: Annotated[str | None, typer.Option("--notes", help="Rejection notes.")] = None,
    severity: Annotated[int | None, typer.Option("--severity", help="Severity 1–5.")] = None,
    expected_correction: Annotated[
        str | None, typer.Option("--correction", help="Expected correction.")
    ] = None,
    actor: Annotated[str | None, typer.Option("--actor", help="Reviewer name.")] = None,
) -> None:
    """Flag a specific scene for rejection (training signal; does not block approval)."""
    from app.scenes.errors import ManifestNotFoundError
    from app.scenes.repository import record_scene_rejection

    conn = _get_db()
    try:
        event = record_scene_rejection(
            conn,
            manifest_id,
            scene_id,
            reason_code=reason_code,
            notes=notes,
            severity=severity,
            expected_correction=expected_correction,
            actor=actor,
        )
        conn.commit()
    except ManifestNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    typer.echo(
        f"Scene rejection recorded: event id={event.id}  manifest={manifest_id}  scene={scene_id}"
    )


@scenes_app.command("events")
def scenes_events(
    manifest_id: Annotated[int, typer.Argument(help="Scene manifest ID.")],
) -> None:
    """List review events for a scene manifest."""
    from app.scenes.repository import list_scene_manifest_review_events

    conn = _get_db()
    events = list_scene_manifest_review_events(conn, manifest_id)
    if not events:
        typer.echo(f"No review events for manifest id={manifest_id}.")
        return
    for ev in events:
        scene_str = f"  scene={ev.scene_id}" if ev.scene_id else ""
        code_str = f"  reason={ev.reason_code}" if ev.reason_code else ""
        actor_str = f"  actor={ev.actor}" if ev.actor else ""
        typer.echo(f"[{ev.id}] {ev.event_type}{scene_str}{code_str}{actor_str}  {ev.created_at}")


@scenes_app.command("manifest")
def scenes_manifest(
    topic_id: Annotated[int, typer.Argument(help="Topic ID.")],
) -> None:
    """Show the approved scene manifest handoff for TOPIC_ID."""

    from app.scenes.repository import get_approved_scene_manifest_full

    conn = _get_db()
    manifest = get_approved_scene_manifest_full(conn, topic_id)
    if manifest is None:
        typer.echo(f"No approved scene manifest for topic_id={topic_id}.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Approved Scene Manifest [{manifest.manifest_id}]")
    typer.echo(f"  topic              : {manifest.topic_id}")
    typer.echo(f"  plan               : {manifest.plan_id}")
    typer.echo(f"  schema_version     : {manifest.manifest_schema_version}")
    typer.echo(f"  planner_version    : {manifest.planner_version}")
    typer.echo(f"  total_scenes       : {manifest.total_scene_count}")
    typer.echo(f"  total_duration_ms  : {manifest.total_duration_ms}")
    typer.echo(f"  approved_at        : {manifest.approved_at}")
    typer.echo("")
    for scene in manifest.scenes:
        typer.echo(
            f"  [{scene.scene_index:>3}]  {scene.start_ms}–{scene.end_ms}ms"
            f"  shot={scene.shot_type}  cam={scene.camera_movement}"
            f"  conf={scene.confidence:.2f}"
        )
        if scene.claim_ids:
            typer.echo(f"         claims: {scene.claim_ids}")
        typer.echo(f"         {scene.visual_objective[:80]}")
        for asset in scene.assets:
            ai_flag = " [AI]" if asset.ai_generation_requested else ""
            typer.echo(
                f"         asset[{asset.asset_index}] {asset.category}"
                f"  {asset.priority}  {asset.license_status}{ai_flag}"
            )
