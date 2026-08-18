"""Publications routes — workspace-scoped list, detail, media stream, analytics, and release."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.deps import get_config, get_db, require_workspace_permission
from app.api.jwt_auth import CurrentUser, get_current_user
from app.core.config import Config
from app.oauth.flow import has_release_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces/{workspace_id}/publications", tags=["publications"])


def _get_release_youtube_client() -> Any:
    """Injectable for tests. Returns None → production resolves client from credential."""
    return None


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
    cfg: Config = Depends(get_config),
) -> dict[str, Any]:
    require_workspace_permission(current_user, workspace_id, "publish:view")
    _assert_publication_in_workspace(conn, publication_id, workspace_id)
    row = conn.execute(
        "SELECT p.id, p.provider, p.provider_video_id, p.provider_url, p.visibility, "
        "p.status, p.published_at, p.created_at, p.platform_account_id, p.channel_id, "
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

    # release_eligible: structural preconditions met (not about feature flags or scope)
    platform_account_id: str | None = d.get("platform_account_id") or None
    d["release_eligible"] = (
        d.get("provider") == "youtube"
        and d.get("status") == "published"
        and d.get("visibility") == "private"
        and bool(d.get("provider_video_id"))
        and bool(platform_account_id)
    )
    # release_enabled: operator has enabled the feature flag AND live publishing gate is set
    d["release_enabled"] = cfg.release_public_enabled and cfg.publishing_live_enabled

    # release_scope_granted: stored token contains youtube.force-ssl (no network, no refresh)
    # Also needs channel_id for the account lookup; both are set together or both null.
    pub_channel_id: str | None = d.pop("channel_id", None) or None
    if platform_account_id and pub_channel_id:
        d["release_scope_granted"] = has_release_scope(
            conn,
            account_id=platform_account_id,
            workspace_id=workspace_id,
            channel_id=pub_channel_id,
        )
    else:
        d["release_scope_granted"] = False

    d.pop("platform_account_id", None)  # internal FK — not exposed to frontend
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


# ---------------------------------------------------------------------------
# Release endpoint
# ---------------------------------------------------------------------------

# Read-only fields returned by YouTube videos.list that must not be sent back
# in a videos.update call — YouTube returns 400 if they are included.
_STATUS_READ_ONLY_FIELDS = frozenset(
    {"uploadStatus", "failureReason", "rejectionReason", "madeForKids"}
)


@router.post("/{publication_id}/release-public")
def release_publication_public(
    workspace_id: str,
    publication_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    conn: Any = Depends(get_db),
    cfg: Config = Depends(get_config),
    _yt_client_override: Any = Depends(_get_release_youtube_client),
) -> dict[str, Any]:
    """Change an uploaded-private YouTube video to public.

    Gate sequence (fail-closed):
      1. ACE_RELEASE_PUBLIC_ENABLED=true
      2. ACE_PUBLISHING_LIVE_ENABLED=true
      3. RBAC: publish:approve
      4. workspace_id match (404)
      5. provider == "youtube" (422)
      6. status == "published" (409)
      7. visibility == "private" — already public returns 409
      8. provider_video_id non-empty (500)
      9. platform_account_id non-null (422)
      10. Token resolve + refresh (502)
      11. check_release_scope (403)
      12. videos.list read-before-write (502)
      13. Ground-truth reconcile if YouTube already public (200, no write)
      14. videos.update (502 on failure, DB unchanged)
      15. UPDATE publications SET visibility='public' (CRITICAL log on failure)
      16. create_event cp_events "publication.released_public"
    """
    from datetime import UTC, datetime

    # 1–2: feature flags
    if not cfg.release_public_enabled:
        raise HTTPException(
            status_code=403,
            detail="ACE_RELEASE_PUBLIC_ENABLED is not set to true. "
            "Set this flag explicitly to enable public release.",
        )
    if not cfg.publishing_live_enabled:
        raise HTTPException(
            status_code=403,
            detail="ACE_PUBLISHING_LIVE_ENABLED is not set to true. "
            "Both flags must be true to release a video publicly.",
        )

    # 3: RBAC
    require_workspace_permission(current_user, workspace_id, "publish:approve")

    # 4: workspace ownership
    _assert_publication_in_workspace(conn, publication_id, workspace_id)

    # Fetch full publication row
    pub_row = conn.execute(
        "SELECT id, provider, provider_video_id, visibility, status, "
        "platform_account_id, channel_id "
        "FROM publications WHERE id = ?",
        (publication_id,),
    ).fetchone()
    if pub_row is None:
        raise HTTPException(status_code=404, detail="Publication not found")
    pub = dict(pub_row)

    # 5: provider
    if pub["provider"] != "youtube":
        raise HTTPException(
            status_code=422,
            detail=f"Provider '{pub['provider']}' does not support public release.",
        )

    # 6: status
    if pub["status"] != "published":
        raise HTTPException(
            status_code=409,
            detail=f"Publication is not in 'published' state (current: {pub['status']}).",
        )

    # 7: visibility
    if pub["visibility"] == "public":
        raise HTTPException(status_code=409, detail="Publication is already public.")

    # 8: provider_video_id
    video_id: str = pub["provider_video_id"] or ""
    if not video_id:
        raise HTTPException(
            status_code=500,
            detail="Publication has no provider_video_id; cannot release.",
        )

    # 9: platform_account_id
    platform_account_id: str = pub["platform_account_id"] or ""
    if not platform_account_id:
        raise HTTPException(
            status_code=422,
            detail="Publication has no platform_account_id; cannot resolve credentials.",
        )
    channel_id: str = pub["channel_id"] or ""

    # 10: resolve (and refresh if expired) the account OAuth token
    if _yt_client_override is None:
        from app.oauth.errors import OAuthRefreshError, OAuthTokenStoreError
        from app.publishing.upload_gate import resolve_upload_token

        try:
            from app.api.routes.oauth import get_oauth_client as _get_oauth_client_dep

            _oauth_client = _get_oauth_client_dep()
        except Exception:
            _oauth_client = None

        try:
            stored_token = resolve_upload_token(
                conn,
                account_id=platform_account_id,
                workspace_id=workspace_id,
                channel_id=channel_id,
                oauth_client=_oauth_client,
            )
        except (OAuthRefreshError, OAuthTokenStoreError) as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to resolve OAuth token: {exc}",
            ) from exc

        # 11: scope check
        from app.publishing.errors import ReleaseScopeNotGrantedError
        from app.publishing.upload_gate import check_release_scope

        try:
            check_release_scope(stored_token)
        except ReleaseScopeNotGrantedError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        # Build real YouTube client
        from app.publishing.providers.youtube import RealYouTubeAPIClient
        from app.publishing.upload_gate import _load_client_secrets

        client_secrets = _load_client_secrets(_oauth_client)
        yt_client = RealYouTubeAPIClient(
            access_token=stored_token.access_token,
            refresh_token=stored_token.refresh_token,
            token_uri=client_secrets.get("token_uri"),
            client_id=client_secrets.get("client_id"),
            client_secret=client_secrets.get("client_secret"),
        )
    else:
        yt_client = _yt_client_override

    # 12: read-before-write — get current YouTube status
    try:
        list_resp = yt_client.get_video(video_id, ["status"])
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to read current video status from YouTube: {exc}",
        ) from exc

    items = list_resp.get("items", [])
    if not items:
        raise HTTPException(
            status_code=502,
            detail=f"Video '{video_id}' not found on YouTube.",
        )
    current_yt_status: dict = items[0].get("status", {})

    # 13: ground-truth reconcile — YouTube is already public
    if current_yt_status.get("privacyStatus") == "public":
        if pub["visibility"] != "public":
            import uuid as _uuid_r

            from app.control_plane.models import ControlEventDraft
            from app.control_plane.repository import create_event

            previous_visibility = pub["visibility"]
            conn.execute(
                "UPDATE publications SET visibility='public', updated_at=? WHERE id=?",
                (datetime.now(UTC).isoformat(), publication_id),
            )
            conn.commit()
            logger.info(
                "release-public: reconciled local visibility for publication %d "
                "(YouTube ground truth was already public)",
                publication_id,
            )
            try:
                create_event(
                    conn,
                    ControlEventDraft(
                        id=str(_uuid_r.uuid4()),
                        event_type="publication.visibility_reconciled_public",
                        workspace_id=workspace_id,
                        actor=current_user.actor,
                        platform_account_id=platform_account_id,
                        source_entity_id=str(publication_id),
                        payload={
                            "publication_id": publication_id,
                            "provider_video_id": video_id,
                            "previous_local_visibility": previous_visibility,
                            "observed_provider_visibility": "public",
                        },
                    ),
                )
                conn.commit()
            except Exception as exc:
                logger.warning(
                    "release-public: reconciliation audit event write failed (non-fatal): %s",
                    exc,
                )
        return {"visibility": "public", "reconciled": True}

    # 14: build the update status body
    update_status: dict = {
        k: v for k, v in current_yt_status.items() if k not in _STATUS_READ_ONLY_FIELDS
    }
    update_status["privacyStatus"] = "public"
    update_status.pop("publishAt", None)  # clear scheduled release for immediate publish

    # 15: call YouTube videos.update — DB is NOT changed until this succeeds
    try:
        yt_client.update_video(video_id, snippet={}, status=update_status)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"YouTube videos.update failed: {exc}",
        ) from exc

    # 16: update local DB — only after YouTube confirms success
    now_str = datetime.now(UTC).isoformat()
    try:
        conn.execute(
            "UPDATE publications SET visibility='public', updated_at=? WHERE id=?",
            (now_str, publication_id),
        )
        conn.commit()
    except Exception as exc:
        logger.critical(
            "CRITICAL: YouTube reports publication %d (%s) is now public but local DB "
            "update failed: %s. Manual reconciliation required.",
            publication_id,
            video_id,
            exc,
        )
        raise HTTPException(
            status_code=500,
            detail="YouTube update succeeded but local DB update failed. "
            "Check server logs — manual reconciliation required.",
        ) from exc

    # 17: audit event in cp_events
    import uuid as _uuid

    from app.control_plane.models import ControlEventDraft
    from app.control_plane.repository import create_event

    try:
        create_event(
            conn,
            ControlEventDraft(
                id=str(_uuid.uuid4()),
                event_type="publication.released_public",
                workspace_id=workspace_id,
                actor=current_user.actor,
                platform_account_id=platform_account_id,
                source_entity_id=str(publication_id),
                payload={
                    "publication_id": publication_id,
                    "provider_video_id": video_id,
                    "visibility_before": "private",
                    "visibility_after": "public",
                },
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("release-public: audit event write failed (non-fatal): %s", exc)

    return {"visibility": "public", "reconciled": False}
