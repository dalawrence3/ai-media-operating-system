"""Persistence for the semantic visual engine."""

from __future__ import annotations

import json
import sqlite3

from app.visuals.memory import clear_manifest_usage, record_asset_usage
from app.visuals.models import VisualBeatRecord, VisualPlan


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
    )


def save_visual_plan(
    conn: sqlite3.Connection,
    plan: VisualPlan,
    *,
    workspace_id: str | None = None,
    render_manifest_id: int | None = None,
) -> int:
    """Persist a resolved plan and its asset-usage lineage.

    Replaces any previous plan for the same scene manifest: a manifest has one
    current visual plan, and superseded beats would otherwise inflate the
    manifest's own reuse penalties on the next render.
    """
    if not _table_exists(conn, "visual_beats"):
        return 0

    conn.execute("DELETE FROM visual_beats WHERE scene_manifest_id = ?", (plan.scene_manifest_id,))
    clear_manifest_usage(conn, plan.scene_manifest_id)

    by_index = {r.beat.beat_index: r for r in plan.resolutions}
    written = 0

    for beat in plan.beats:
        resolution = by_index.get(beat.beat_index)
        conn.execute(
            """
            INSERT INTO visual_beats (
                scene_manifest_id, scene_id, beat_index, scene_index, segment_id,
                start_ms, end_ms, duration_ms, narration_text,
                keywords_json, entities_json, visual_intent,
                media_type_preferences_json, search_queries_json, avoid_terms_json,
                claim_ids_json, preferred_motion, importance, confidence,
                resolved_media_type, resolved_provider, resolved_asset_key,
                resolved_local_path, resolved_score, resolved_motion,
                license_status, attribution_text, fallback_reason,
                engine_version, planner_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan.scene_manifest_id,
                beat.scene_id,
                beat.beat_index,
                beat.scene_index,
                beat.segment_id,
                beat.start_ms,
                beat.end_ms,
                beat.duration_ms,
                beat.narration_text,
                json.dumps(beat.keywords),
                json.dumps(beat.entities),
                beat.visual_intent,
                json.dumps(beat.media_type_preferences),
                json.dumps(beat.search_queries),
                json.dumps(beat.avoid_terms),
                json.dumps(beat.claim_ids),
                beat.preferred_motion,
                beat.importance,
                beat.confidence,
                resolution.media_type if resolution else None,
                resolution.provider if resolution else None,
                resolution.asset_key if resolution else None,
                resolution.local_path if resolution else None,
                resolution.score if resolution else None,
                resolution.motion if resolution else None,
                resolution.license_status if resolution else None,
                resolution.attribution_text if resolution else None,
                resolution.fallback_reason if resolution else None,
                plan.engine_version,
                plan.planner_version,
            ),
        )
        written += 1

        if resolution and resolution.asset_key and resolution.provider != "programmatic":
            provider, _, provider_asset_id = resolution.asset_key.partition(":")
            record_asset_usage(
                conn,
                asset_key=resolution.asset_key,
                provider=provider,
                provider_asset_id=provider_asset_id,
                media_type=resolution.media_type,
                duration_ms=beat.duration_ms,
                channel_key=plan.channel_key,
                workspace_id=workspace_id,
                topic_id=plan.topic_id or None,
                experiment_id=plan.experiment_id,
                scene_manifest_id=plan.scene_manifest_id,
                render_manifest_id=render_manifest_id,
                beat_index=beat.beat_index,
                scene_index=beat.scene_index,
            )

    return written


def list_visual_beats(conn: sqlite3.Connection, scene_manifest_id: int) -> list[VisualBeatRecord]:
    if not _table_exists(conn, "visual_beats"):
        return []
    rows = conn.execute(
        "SELECT * FROM visual_beats WHERE scene_manifest_id = ? ORDER BY beat_index",
        (scene_manifest_id,),
    ).fetchall()
    return [VisualBeatRecord.from_row(row) for row in rows]


def attach_render_manifest(
    conn: sqlite3.Connection, scene_manifest_id: int, render_manifest_id: int
) -> None:
    """Backfill the render manifest id on this manifest's usage rows."""
    if not _table_exists(conn, "visual_asset_usage"):
        return
    conn.execute(
        "UPDATE visual_asset_usage SET render_manifest_id = ? WHERE scene_manifest_id = ?",
        (render_manifest_id, scene_manifest_id),
    )
