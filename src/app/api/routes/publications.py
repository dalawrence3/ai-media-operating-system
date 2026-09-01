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
        "p.status, p.published_at, p.created_at, pp.title, pp.render_manifest_id, "
        "rm.total_duration_ms AS render_duration_ms, t.title AS topic_title "
        "FROM publications p "
        "JOIN publishing_plans pp ON pp.id = p.publishing_plan_id "
        "LEFT JOIN render_manifests rm ON rm.id = pp.render_manifest_id "
        "LEFT JOIN topics t ON t.id = pp.topic_id "
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
        "rm.fps AS render_fps, rm.status AS render_status, rm.approved_at AS render_approved_at, "
        "t.title AS topic_title "
        "FROM publications p "
        "JOIN publishing_plans pp ON pp.id = p.publishing_plan_id "
        "LEFT JOIN render_manifests rm ON rm.id = pp.render_manifest_id "
        "LEFT JOIN topics t ON t.id = pp.topic_id "
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


@router.get("/{publication_id}/visual-quality")
def get_publication_visual_quality(
    workspace_id: str,
    publication_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    conn: Any = Depends(get_db),
) -> dict[str, Any]:
    """The render's measured visual composition and quality verdict (Phase 18E).

    Returns `{"assessed": false}` rather than 404 for a publication produced
    before this phase: "we never measured this" is a real, displayable answer,
    and a 404 would read in the UI as a broken endpoint.
    """
    require_workspace_permission(current_user, workspace_id, "publish:view")
    _assert_publication_in_workspace(conn, publication_id, workspace_id)

    from app.visuals.assessment_repository import get_assessment_for_publication

    assessment = get_assessment_for_publication(conn, publication_id)
    if assessment is None:
        return {"assessed": False}

    total_ms = assessment.total_duration_ms or 0

    def _pct(ms: int) -> float:
        return round(ms / total_ms, 4) if total_ms else 0.0

    return {
        "assessed": True,
        "status": assessment.status,
        "assessment_version": assessment.assessment_version,
        "policy_version": assessment.policy_version,
        "visual_style": assessment.visual_style,
        "total_beat_count": assessment.total_beat_count,
        "total_duration_ms": total_ms,
        "scene_count": assessment.scene_count,
        "meaningful_runtime_pct": round(assessment.meaningful_runtime_pct, 4),
        "text_card_runtime_pct": round(assessment.text_card_runtime_pct, 4),
        "meaningful_beat_count": assessment.meaningful_beat_count,
        "visual_changes_per_minute": round(assessment.visual_changes_per_minute, 2),
        "distinct_asset_count": assessment.distinct_asset_count,
        "asset_reuse_ratio": round(assessment.asset_reuse_ratio, 4),
        "max_meaningful_gap_ms": assessment.max_meaningful_gap_ms,
        "avg_meaningful_gap_ms": round(assessment.avg_meaningful_gap_ms, 1),
        "opening_meaningful_visual": assessment.opening_meaningful_visual,
        "dominant_family": assessment.dominant_family,
        "dominant_family_share": round(assessment.dominant_family_share, 4),
        "family_diversity": round(assessment.family_diversity, 4),
        "family_distribution": [
            {
                "family": family,
                "beat_count": assessment.family_beat_count.get(family, 0),
                "runtime_ms": ms,
                "runtime_pct": _pct(ms),
            }
            for family, ms in sorted(assessment.family_runtime.items(), key=lambda kv: -kv[1])
        ],
        "fallback_beat_count": assessment.fallback_beat_count,
        "provider_fallback_beats": assessment.provider_fallback_beats,
        "creative_fallback_beats": assessment.creative_fallback_beats,
        "provider_fallback_rate": round(assessment.provider_fallback_rate, 4),
        "fallback_reasons": assessment.fallback_reasons,
        "planned_meaningful_beats": assessment.planned_meaningful_beats,
        "remediation_attempts": assessment.remediation_attempts,
        "remediated": assessment.remediated,
        "findings": assessment.findings,
        "scene_diagnostics": assessment.scene_diagnostics,
        "assessed_at": assessment.updated_at,
    }


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
        "SELECT id, ingested_at, period_start, period_end, experiment_id FROM analytics_snapshots "
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

    if snapshot_id is not None:
        retention_count: int = conn.execute(
            "SELECT COUNT(*) FROM analytics_retention_points WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()[0]
    else:
        retention_count = 0

    return {
        "snapshot_id": snapshot_id,
        "snapshot_ingested_at": snap["ingested_at"] if snap else None,
        "period_start": snap["period_start"] if snap else None,
        "period_end": snap["period_end"] if snap else None,
        "metrics": metrics,
        "retention_point_count": retention_count,
        "experiment_id": snap["experiment_id"] if snap else None,
    }


@router.get("/{publication_id}/analytics/history")
def get_publication_analytics_history(
    workspace_id: str,
    publication_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    conn: Any = Depends(get_db),
) -> list[dict[str, Any]]:
    """Full observation history for a publication — one entry per analytics
    snapshot, oldest first.

    Phase 17C addition. Additive and read-only; publication-scoped like
    /analytics above, so it inherits the same protection against the two
    known data-correctness gaps documented in the Phase 17B/17C reports:
    it does not use workspace_topic_ids (which returns [] for topics with a
    NULL workspace_id, e.g. topic_id=4) and it does not read
    analytics_aggregates (which carries a contaminated 'youtube_dev_seed'
    seed row for publication 1). Powers the per-video performance-over-time
    chart — there is no channel-level equivalent because snapshot ingestion
    dates do not align across videos, which is too sparse to plot honestly
    as a single trend.

    `observation_state` ('data' | 'no_data' | null) is passed through as
    recorded by the ingestion pipeline: a provider can be polled and report
    nothing yet (immature video, reporting latency), which is why `metrics`
    can legitimately be `{}` for an entry even though the snapshot exists.
    """
    require_workspace_permission(current_user, workspace_id, "publish:view")
    _assert_publication_in_workspace(conn, publication_id, workspace_id)

    snapshots = conn.execute(
        "SELECT id, ingested_at, observed_at, period_start, period_end, observation_state, "
        "experiment_id FROM analytics_snapshots WHERE publication_id = ? ORDER BY id ASC",
        (publication_id,),
    ).fetchall()

    history: list[dict[str, Any]] = []
    for snap in snapshots:
        metrics: dict[str, float] = {}
        for mr in conn.execute(
            "SELECT metric_name, metric_value FROM analytics_metrics WHERE snapshot_id = ?",
            (snap["id"],),
        ).fetchall():
            metrics[mr["metric_name"]] = mr["metric_value"]
        history.append(
            {
                "snapshot_id": snap["id"],
                "ingested_at": snap["ingested_at"],
                "observed_at": snap["observed_at"],
                "period_start": snap["period_start"],
                "period_end": snap["period_end"],
                "observation_state": snap["observation_state"],
                "experiment_id": snap["experiment_id"],
                "metrics": metrics,
            }
        )
    return history


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

    # 0: Phase 18E — a public release is a real, irreversible provider effect,
    # so it is refused in a test runtime regardless of which database is open.
    # Database isolation cannot help here: the side effect leaves the database
    # entirely.
    from app.core.runtime_mode import RuntimeIsolationError, assert_live_effect_allowed

    try:
        assert_live_effect_allowed("provider_release_public")
    except RuntimeIsolationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

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

    # 12–17: delegate to the shared release service.
    #
    # Phase 18C extracted this sequence (read-before-write, ground-truth
    # reconcile, videos.update, local persistence, audit) into
    # app.publishing.release_service so the autonomous publishing cycle can
    # execute exactly the same logic from a background worker. This route
    # keeps its original behaviour and simply maps the typed outcome back to
    # HTTP status codes — one implementation, two callers, so the safety
    # properties cannot drift apart.
    from app.publishing.release_service import ReleaseOutcome, release_publication_to_public

    release = release_publication_to_public(
        conn,
        publication_id=publication_id,
        provider_video_id=video_id,
        workspace_id=workspace_id,
        platform_account_id=platform_account_id,
        actor=current_user.actor,
        yt_client=yt_client,
    )

    if release.outcome is ReleaseOutcome.already_public_reconciled:
        return {"visibility": "public", "reconciled": True}
    if release.outcome is ReleaseOutcome.released:
        return {"visibility": "public", "reconciled": False}
    if release.outcome is ReleaseOutcome.video_not_found:
        raise HTTPException(status_code=502, detail=release.detail)
    if release.outcome is ReleaseOutcome.local_persist_failed:
        raise HTTPException(status_code=500, detail=release.detail)
    # provider_read_failed / provider_update_failed
    raise HTTPException(status_code=502, detail=release.detail)
