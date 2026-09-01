"""Market intelligence routes — read-only views of canonical clusters, opportunities,
and experiment planning state. No live market scans are triggered here."""

from __future__ import annotations

import dataclasses
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_db, require_workspace_permission
from app.api.jwt_auth import CurrentUser, get_current_user

router = APIRouter(prefix="/workspaces/{workspace_id}/market", tags=["market"])


def _resolve_channel_id(conn: Any, cp_channel_id: str | None) -> int | None:
    """Resolve a control-plane channel UUID to its intelligence-domain integer
    id, or None if not provided or not bootstrapped for market intelligence.

    `opportunities`/`experiments`/`experiment_strategy_briefs` are keyed by
    the integer intelligence channel_id, a distinct identity space from
    cp_channels.id — see app.intelligence.channel_bridge. A channel with no
    bootstrapped intelligence identity (e.g. a dev/fixture channel) simply
    has no market data; that is a real, honest empty result, not an error.
    """
    if not cp_channel_id:
        return None
    from app.intelligence.channel_bridge import get_intelligence_channel_id

    return get_intelligence_channel_id(conn, cp_channel_id)


@router.get("/clusters")
def list_canonical_clusters(
    workspace_id: str,
    limit: int = 100,
    current_user: CurrentUser = Depends(get_current_user),
    conn: Any = Depends(get_db),
) -> list[dict[str, Any]]:
    require_workspace_permission(current_user, workspace_id, "analytics:view")
    rows = conn.execute(
        "SELECT id, platform, provider, region_code, language_code, "
        "canonical_label, normalized_label, semantic_fingerprint, "
        "identity_version, created_at, updated_at "
        "FROM market_canonical_clusters "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/opportunities")
def list_opportunities(
    workspace_id: str,
    limit: int = 100,
    channel_id: int | None = None,
    cp_channel_id: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    conn: Any = Depends(get_db),
) -> list[dict[str, Any]]:
    """List opportunities with their latest score, canonical cluster label,
    and source-evidence count.

    `channel_id` (intelligence-domain int) and `cp_channel_id` (control-plane
    UUID, resolved via the identity bridge) are alternative ways to scope to
    one channel; if neither is given, results span every channel — callers
    that want workspace/channel isolation must pass one. `cp_channel_id` is
    the one the frontend actually has on hand (see useCurrentChannel).
    """
    require_workspace_permission(current_user, workspace_id, "analytics:view")
    resolved_channel_id = (
        channel_id if channel_id is not None else _resolve_channel_id(conn, cp_channel_id)
    )
    if cp_channel_id is not None and resolved_channel_id is None:
        # A cp_channel_id was given but has no bootstrapped intelligence
        # identity — there is no market data for it, by definition.
        return []

    _score_join = (
        "LEFT JOIN opportunity_scores os "
        "  ON os.opportunity_id = o.id "
        "  AND os.id = (SELECT MAX(id) FROM opportunity_scores WHERE opportunity_id = o.id) "
    )
    _cluster_join = "LEFT JOIN market_canonical_clusters mcc ON mcc.id = o.canonical_cluster_id "
    _evidence_join = (
        "LEFT JOIN ("
        "  SELECT opportunity_id, COUNT(id) AS evidence_count"
        "  FROM opportunity_source_evidence"
        "  GROUP BY opportunity_id"
        ") eo ON eo.opportunity_id = o.id "
    )
    _cols = (
        "o.id, o.channel_id, o.normalized_topic, o.raw_topic, o.title, "
        "o.topic_summary, o.format_recommendation, o.strategic_role, "
        "o.current_lifecycle_state, o.canonical_cluster_id, o.created_at, "
        "mcc.canonical_label, "
        "COALESCE(eo.evidence_count, 0) AS evidence_count, "
        "os.composite_score, os.confidence, "
        "os.score_trend_strength, os.score_audience_demand, os.score_competition, "
        "os.score_evergreen_value, os.score_audience_fit, os.score_content_novelty, "
        "os.status_trend_strength, os.status_audience_demand, os.status_competition, "
        "os.status_evergreen_value, os.status_audience_fit, os.status_content_novelty"
    )
    query = f"SELECT {_cols} FROM opportunities o {_score_join}{_cluster_join}{_evidence_join}"
    if resolved_channel_id is not None:
        rows = conn.execute(
            f"{query}WHERE o.channel_id = ? ORDER BY o.created_at DESC LIMIT ?",
            (resolved_channel_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            f"{query}ORDER BY o.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/signals")
def list_market_signals(
    workspace_id: str,
    limit: int = 50,
    current_user: CurrentUser = Depends(get_current_user),
    conn: Any = Depends(get_db),
) -> list[dict[str, Any]]:
    require_workspace_permission(current_user, workspace_id, "analytics:view")
    rows = conn.execute(
        "SELECT id, interpretation_run_id, cluster_label, normalized_label, "
        "cluster_type, member_probe_count, member_video_count, "
        "canonical_cluster_id, created_at "
        "FROM market_topic_clusters "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/experiments")
def list_market_experiments(
    workspace_id: str,
    limit: int = 100,
    channel_id: int | None = None,
    cp_channel_id: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    conn: Any = Depends(get_db),
) -> list[dict[str, Any]]:
    require_workspace_permission(current_user, workspace_id, "analytics:view")
    resolved_channel_id = (
        channel_id if channel_id is not None else _resolve_channel_id(conn, cp_channel_id)
    )
    if cp_channel_id is not None and resolved_channel_id is None:
        return []
    if resolved_channel_id is not None:
        rows = conn.execute(
            "SELECT id, channel_id, opportunity_id, experiment_type, "
            "hypothesis, status, created_at "
            "FROM experiments WHERE channel_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (resolved_channel_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, channel_id, opportunity_id, experiment_type, "
            "hypothesis, status, created_at "
            "FROM experiments "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/strategy-briefs")
def list_strategy_briefs(
    workspace_id: str,
    cp_channel_id: str,
    status: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    conn: Any = Depends(get_db),
) -> list[dict[str, Any]]:
    """List experiment strategy briefs for a channel — the "what to try next"
    handoff artifact from the experiment planner (Phase 14E).

    Each brief already carries real, deterministic narrative fields
    (`strategic_reason`, `information_gain_reason`) generated from its
    scoring components, not free-text LLM copy — see
    app.intelligence.experiments.brief_service.create_strategy_brief.
    `status='pending_approval'` by default: briefs describe a proposed next
    experiment for a human to review, never an auto-applied action.

    Read-only; requires a bootstrapped intelligence channel for
    cp_channel_id, same as the other /market/* endpoints.
    """
    require_workspace_permission(current_user, workspace_id, "analytics:view")
    resolved_channel_id = _resolve_channel_id(conn, cp_channel_id)
    if resolved_channel_id is None:
        return []

    from app.intelligence.experiments.brief_service import list_briefs_for_channel

    briefs = list_briefs_for_channel(conn, resolved_channel_id, status=status)

    results: list[dict[str, Any]] = []
    for brief in briefs:
        payload = dataclasses.asdict(brief)
        # Best-effort link to a real experiments row sharing the same
        # opportunity — experiments has no direct FK back to a brief/decision.
        exp_row = conn.execute(
            "SELECT id, status, experiment_type, created_at FROM experiments "
            "WHERE opportunity_id = ? ORDER BY created_at DESC LIMIT 1",
            (brief.opportunity_id,),
        ).fetchone()
        payload["linked_experiment"] = dict(exp_row) if exp_row else None
        results.append(payload)
    return results


_EVIDENCE_TYPE_LABEL = {
    "market_demand_score": "Audience demand",
    "market_saturation_score": "Competition / saturation",
    "market_freshness_score": "Freshness",
    "market_persistence_score": "Evergreen persistence",
    "market_confidence": "Evidence confidence",
    "market_maturity": "Evidence maturity",
    "market_state_label": "Market state",
    "market_canonical_cluster_id": "Canonical cluster",
    "market_cluster_snapshot_id": "Cluster snapshot",
    "market_signal_snapshot_id": "Signal snapshot",
    "market_interpretation_run_id": "Interpretation run",
}


@router.get("/opportunities/{opportunity_id}/evidence")
def get_opportunity_evidence(
    workspace_id: str,
    opportunity_id: int,
    cp_channel_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    conn: Any = Depends(get_db),
) -> dict[str, Any]:
    """EXTERNAL MARKET EVIDENCE drill-down for one opportunity — why the
    system considers it interesting, grouped by the sync snapshot that
    produced each batch of evidence (source_label encodes
    'market_intelligence:canonical=<cluster>:snap=<snapshot_id>' — one
    snapshot's rows always arrive together, so grouping by it gives an
    honest history of observations over time rather than a flat, meaningless
    list). Read-only; no raw provider payloads are ever returned.

    Channel-scoped: the opportunity must belong to the channel resolved
    from cp_channel_id, or this 404s — an operator cannot probe another
    channel's evidence by guessing opportunity_id.
    """
    require_workspace_permission(current_user, workspace_id, "analytics:view")
    resolved_channel_id = _resolve_channel_id(conn, cp_channel_id)
    if resolved_channel_id is None:
        raise HTTPException(status_code=404, detail="Channel has no market intelligence data")

    owner = conn.execute(
        "SELECT channel_id FROM opportunities WHERE id = ?", (opportunity_id,)
    ).fetchone()
    if owner is None or owner["channel_id"] != resolved_channel_id:
        raise HTTPException(status_code=404, detail="Opportunity not found for this channel")

    rows = conn.execute(
        "SELECT id, observation_id, evidence_type, evidence_value, evidence_text, "
        "evidence_unit, source_label, collected_at FROM opportunity_source_evidence "
        "WHERE opportunity_id = ? ORDER BY collected_at DESC, id DESC",
        (opportunity_id,),
    ).fetchall()

    snapshots: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for r in rows:
        label = r["source_label"]
        if label not in snapshots:
            snapshots[label] = {
                "source_label": label,
                "collected_at": r["collected_at"],
                "items": [],
            }
            order.append(label)
        snapshots[label]["items"].append(
            {
                "evidence_type": r["evidence_type"],
                "label": _EVIDENCE_TYPE_LABEL.get(r["evidence_type"], r["evidence_type"]),
                "value": r["evidence_value"],
                "text": r["evidence_text"],
                "unit": r["evidence_unit"] or None,
            }
        )

    return {
        "opportunity_id": opportunity_id,
        "evidence_count": len(rows),
        "snapshots": [snapshots[label] for label in order],
    }
