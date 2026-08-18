"""Publications routes — workspace-scoped list, detail, media stream, and analytics."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.deps import get_config, get_db, require_workspace_permission
from app.api.jwt_auth import CurrentUser, get_current_user
from app.core.config import Config

router = APIRouter(prefix="/workspaces/{workspace_id}/publications", tags=["publications"])


def _assert_publication_in_workspace(conn: Any, publication_id: int, workspace_id: str) -> None:
    row = conn.execute(
        "SELECT id FROM publications WHERE id = ? AND workspace_id = ?",
        (publication_id, workspace_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Publication not found")


@router.get("")
def list_publications(
    workspace_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    conn: Any = Depends(get_db),
) -> list[dict[str, Any]]:
    require_workspace_permission(current_user, workspace_id, "publish:view")
    rows = conn.execute(
        "SELECT p.id, p.provider, p.provider_video_id, p.provider_url, p.visibility, "
        "p.status, p.published_at, p.created_at, pp.title, pp.render_manifest_id "
        "FROM publications p "
        "JOIN publishing_plans pp ON pp.id = p.publishing_plan_id "
        "WHERE p.workspace_id = ? ORDER BY p.id DESC",
        (workspace_id,),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/{publication_id}")
def get_publication(
    workspace_id: str,
    publication_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    conn: Any = Depends(get_db),
) -> dict[str, Any]:
    require_workspace_permission(current_user, workspace_id, "publish:view")
    _assert_publication_in_workspace(conn, publication_id, workspace_id)
    row = conn.execute(
        "SELECT p.id, p.provider, p.provider_video_id, p.provider_url, p.visibility, "
        "p.status, p.published_at, p.created_at, "
        "pp.title, pp.description, pp.tags_json, pp.render_manifest_id, "
        "rm.total_duration_ms AS render_duration_ms, "
        "rm.width AS render_width, rm.height AS render_height, "
        "rm.fps AS render_fps, rm.status AS render_status, rm.approved_at AS render_approved_at "
        "FROM publications p "
        "JOIN publishing_plans pp ON pp.id = p.publishing_plan_id "
        "LEFT JOIN render_manifests rm ON rm.id = pp.render_manifest_id "
        "WHERE p.id = ?",
        (publication_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Publication not found")
    d = dict(row)
    try:
        d["tags"] = json.loads(d.pop("tags_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["tags"] = []
    return d


@router.get("/{publication_id}/stream")
def stream_render(
    workspace_id: str,
    publication_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    conn: Any = Depends(get_db),
    cfg: Config = Depends(get_config),
) -> FileResponse:
    require_workspace_permission(current_user, workspace_id, "publish:view")
    _assert_publication_in_workspace(conn, publication_id, workspace_id)

    rj_row = conn.execute(
        "SELECT rj.output_path FROM publications p "
        "JOIN publishing_plans pp ON pp.id = p.publishing_plan_id "
        "JOIN render_manifests rm ON rm.id = pp.render_manifest_id "
        "JOIN render_jobs rj ON rj.render_manifest_id = rm.id "
        "WHERE p.id = ? AND rj.output_path IS NOT NULL AND rj.status = 'completed' "
        "ORDER BY rj.id DESC LIMIT 1",
        (publication_id,),
    ).fetchone()
    if rj_row is None or not rj_row["output_path"]:
        raise HTTPException(status_code=404, detail="Render not available for this publication")

    output_path: str = rj_row["output_path"]
    artifacts_root = Path(cfg.artifacts_path).resolve()
    candidate = Path(output_path).resolve()
    root_str = str(artifacts_root)
    candidate_str = str(candidate)
    if not (candidate_str == root_str or candidate_str.startswith(root_str + os.sep)):
        raise HTTPException(status_code=403, detail="Access denied")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Render file not found on disk")

    return FileResponse(
        path=candidate_str,
        media_type="video/mp4",
        filename=f"publication_{publication_id}.mp4",
        headers={"Accept-Ranges": "bytes"},
    )


@router.get("/{publication_id}/analytics")
def get_publication_analytics(
    workspace_id: str,
    publication_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    conn: Any = Depends(get_db),
) -> dict[str, Any]:
    require_workspace_permission(current_user, workspace_id, "publish:view")
    _assert_publication_in_workspace(conn, publication_id, workspace_id)

    snap = conn.execute(
        "SELECT id, ingested_at, period_start, period_end FROM analytics_snapshots "
        "WHERE publication_id = ? ORDER BY id DESC LIMIT 1",
        (publication_id,),
    ).fetchone()
    snapshot_id: int | None = snap["id"] if snap else None

    metrics: dict[str, float] = {}
    if snapshot_id is not None:
        for mr in conn.execute(
            "SELECT metric_name, metric_value FROM analytics_metrics WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchall():
            metrics[mr["metric_name"]] = mr["metric_value"]

    retention_count: int = conn.execute(
        "SELECT COUNT(*) FROM analytics_retention_points WHERE publication_id = ?",
        (publication_id,),
    ).fetchone()[0]

    return {
        "snapshot_id": snapshot_id,
        "snapshot_ingested_at": snap["ingested_at"] if snap else None,
        "period_start": snap["period_start"] if snap else None,
        "period_end": snap["period_end"] if snap else None,
        "metrics": metrics,
        "retention_point_count": retention_count,
    }
