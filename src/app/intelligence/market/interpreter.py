"""Phase 13E / 13E.1 — Market Interpretation service.

Converts stored external market evidence (observations + velocities + probes)
into structured, versioned market signals via hybrid clustering and scoring.

CLUSTERING APPROACH (Phase 13E.1 V1 Hybrid)
-------------------------------------------
Stage 1 — Jaccard lexical graph:
  Compute pairwise Jaccard similarity on normalized_query.
  Edges at or above JACCARD_THRESHOLD form strong candidate groups.
  Edges between SOFT_THRESHOLD and JACCARD_THRESHOLD are weak candidates.

Stage 2 — Semantic adjudication (optional, bounded LLM):
  Probes are enriched with VIDEO_TITLE observations from the DB.
  The LLM receives initial groups + video titles + weak-edge candidates.
  It proposes complete final group assignments:
    - Can MERGE groups (different vocab, same market theme)
    - Can SPLIT groups (similar vocab, different market intent)
  Fabricated probe IDs are rejected; missing probes become singletons.
  Falls back silently to Jaccard groups on any LLM error.

Stage 3 — Final grouping:
  Union-find applied to confirmed edges from Stage 1 (or Stage 2).

CANONICAL IDENTITY (Phase 13E.1)
---------------------------------
Each cluster snapshot is linked to a stable CanonicalMarketCluster record.
Matching order: exact fingerprint → exact normalized_label → word overlap.
New canonical cluster created when no match is found.
Phase 13F and signal history queries use canonical_cluster_id, not run-local cluster_id.

SAFETY
------
- No YouTube API calls
- No live analytics ingest
- LLM used ONLY for semantic cluster adjudication (optional, bounded)
- All numeric scores are deterministic functions of stored observations
- No Opportunity creation (Phase 13F only)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from app.intelligence.market.interpretation_models import (
    CLUSTERING_VERSION,
    SCORING_VERSION,
    SEMANTIC_CLUSTERING_VERSION,
    CanonicalMarketCluster,
    ClusterAdjudicationOutput,
    ExternalMarketOpportunityEvidence,
    MarketClusterSignal,
    MarketTopicCluster,
    build_interpretation_policy_snapshot,
    build_opportunity_evidence,
    compute_demand_score,
    compute_freshness_score,
    compute_momentum_score,
    compute_persistence_score,
    compute_saturation_score,
    compute_signal_confidence,
    compute_signal_maturity,
    compute_state_label,
    make_canonical_cluster_fingerprint,
    make_cluster_input_hash,
    make_interpretation_run_input_hash,
    make_signal_input_hash,
    validate_adjudication_output,
)
from app.intelligence.market.interpretation_repository import (
    find_canonical_cluster_by_fingerprint,
    find_canonical_cluster_by_label,
    get_cluster,
    get_cluster_signal_history,
    get_dispatched_probes_for_run,
    get_latest_signal_for_cluster,
    get_search_total_for_probe,
    get_titles_for_probe,
    get_velocity_for_video,
    get_videos_for_probe,
    insert_canonical_cluster,
    insert_cluster,
    insert_cluster_member_probe,
    insert_cluster_member_video,
    insert_cluster_signal,
    insert_interpretation_run,
    list_canonical_clusters,
    list_clusters_for_run,
    list_signals_for_run,
    update_interpretation_run_status,
)

try:
    from app.ai.provider import AIProvider, AIRequest
except ImportError:
    AIProvider = None  # type: ignore[assignment,misc]
    AIRequest = None  # type: ignore[assignment,misc]

from app.intelligence.dedup import jaccard_similarity, normalize_topic

# Jaccard similarity thresholds for hybrid clustering
_JACCARD_THRESHOLD: float = 0.35  # strong edge: probes definitely candidate-merge
_SOFT_THRESHOLD: float = 0.15  # weak edge: candidates for semantic review only


# ---------------------------------------------------------------------------
# Union-Find for confirmed-edge grouping
# ---------------------------------------------------------------------------


class _UnionFind:
    """Simple path-compressed union-find."""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x: int, y: int) -> None:
        px, py = self.find(x), self.find(y)
        if px == py:
            return
        if self.rank[px] < self.rank[py]:
            px, py = py, px
        self.parent[py] = px
        if self.rank[px] == self.rank[py]:
            self.rank[px] += 1


# ---------------------------------------------------------------------------
# Stage 1 — Jaccard graph (kept as-is for existing test compatibility)
# ---------------------------------------------------------------------------


def _cluster_probes_by_jaccard(
    probes: list[dict[str, Any]],
    *,
    threshold: float = 0.35,
) -> dict[int, list[int]]:
    """Group probe indices by Jaccard similarity on normalized_query.

    Returns {canonical_index: [member_indices]} where canonical is the
    lowest-index member of each cluster.

    Kept for backward compatibility (used in tests AP-AR).
    The actual interpretation run uses the graph-based approach below.
    """
    n = len(probes)
    if n == 0:
        return {}
    uf = _UnionFind(n)
    queries = [p.get("normalized_query", "") or "" for p in probes]

    for i in range(n):
        for j in range(i + 1, n):
            sim = jaccard_similarity(queries[i], queries[j])
            if sim >= threshold:
                uf.union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        root = uf.find(i)
        groups.setdefault(root, []).append(i)
    return groups


def _compute_jaccard_edges(
    probes: list[dict[str, Any]],
    *,
    threshold: float = _JACCARD_THRESHOLD,
    soft_threshold: float = _SOFT_THRESHOLD,
) -> tuple[list[tuple[int, int, float]], list[tuple[int, int, float]]]:
    """Compute pairwise Jaccard similarity edges.

    Returns:
        strong_edges: (i, j, sim) where sim >= threshold → definite candidates
        candidate_edges: (i, j, sim) where soft_threshold <= sim < threshold → review candidates
    """
    queries = [p.get("normalized_query", "") or "" for p in probes]
    strong: list[tuple[int, int, float]] = []
    weak: list[tuple[int, int, float]] = []
    n = len(probes)
    for i in range(n):
        for j in range(i + 1, n):
            sim = jaccard_similarity(queries[i], queries[j])
            if sim >= threshold:
                strong.append((i, j, sim))
            elif sim >= soft_threshold:
                weak.append((i, j, sim))
    return strong, weak


def _groups_from_edges(
    n: int,
    edges: list[tuple[int, int, float]],
) -> dict[int, list[int]]:
    """Build connected-component groups from a confirmed edge list via union-find."""
    uf = _UnionFind(n)
    for i, j, _ in edges:
        uf.union(i, j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        root = uf.find(i)
        groups.setdefault(root, []).append(i)
    return groups


# ---------------------------------------------------------------------------
# Cluster label derivation
# ---------------------------------------------------------------------------


def _pick_cluster_label(
    group_probes: list[dict[str, Any]],
) -> tuple[str, str]:
    """Pick a human-readable label and its normalized form.

    Heuristic V1: prefer the probe with the longest market_region_label,
    since that tends to be the most descriptive.
    """
    best = max(
        group_probes,
        key=lambda p: len(p.get("query_text") or p.get("normalized_query") or ""),
    )
    raw_label = best.get("query_text") or best.get("normalized_query") or "unknown"
    normalized = normalize_topic(raw_label)
    return raw_label, normalized


# ---------------------------------------------------------------------------
# Stage 2 — Semantic evidence enrichment + LLM adjudication
# ---------------------------------------------------------------------------


def _enrich_probes_with_titles(
    conn: sqlite3.Connection,
    probes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach VIDEO_TITLE observations to each probe for semantic evidence."""
    return [{**p, "video_titles": get_titles_for_probe(conn, p["id"])} for p in probes]


def _apply_llm_semantic_adjudication(
    *,
    enriched_probes: list[dict[str, Any]],
    initial_groups: dict[int, list[int]],
    candidate_edges: list[tuple[int, int, float]],
    ai_provider: Any,
    max_groups: int,
) -> tuple[dict[int, list[int]], bool, str | None]:
    """Bounded LLM semantic cluster adjudication using video title evidence.

    The LLM receives:
    - Initial Jaccard groups (with video titles) — can split them
    - Weak-edge candidate pairs — can merge them
    - All supplied probe IDs — cannot fabricate

    Returns (updated_groups, llm_used, model_name).
    Falls back to initial_groups on any error.
    """
    if AIRequest is None or ai_provider is None:
        return initial_groups, False, None

    valid_probe_ids = {enriched_probes[i]["id"] for i in range(len(enriched_probes))}
    all_probe_ids = [p["id"] for p in enriched_probes]

    # Build initial group summaries with video titles
    group_summaries = []
    for _root_idx, member_indices in sorted(initial_groups.items()):
        probes_in_group = [enriched_probes[i] for i in member_indices]
        group_probe_ids = [p["id"] for p in probes_in_group]
        probe_queries = [
            p.get("query_text") or p.get("normalized_query", "") for p in probes_in_group
        ]
        titles_flat: list[str] = []
        for p in probes_in_group:
            titles_flat.extend(p.get("video_titles", [])[:5])
        group_summaries.append(
            {
                "probe_ids": group_probe_ids,
                "queries": probe_queries[:5],
                "sample_titles": titles_flat[:10],
            }
        )

    # Build candidate pairs for potential merging
    candidate_pairs = [
        {
            "probe_i": enriched_probes[i]["id"],
            "probe_j": enriched_probes[j]["id"],
            "jaccard": round(sim, 3),
        }
        for i, j, sim in candidate_edges[:20]  # bounded
    ]

    prompt_text = (
        f"You are a market analyst performing semantic cluster adjudication.\n\n"
        f"Supplied probe IDs: {sorted(valid_probe_ids)}\n\n"
        f"Initial lexical clusters (assign probe IDs from these groups):\n"
        + json.dumps(group_summaries[: max_groups * 2], ensure_ascii=False)
        + (
            f"\n\nWeak-overlap pairs to consider merging:\n{json.dumps(candidate_pairs)}"
            if candidate_pairs
            else ""
        )
        + f"\n\nRules:\n"
        f"1. Assign EVERY supplied probe ID to exactly one group\n"
        f"2. Use ONLY probe IDs from the supplied list above\n"
        f"3. Groups representing the same viewer market should be merged even "
        f"with different vocabulary\n"
        f"4. Groups with similar queries but different viewer intent should remain separate\n"
        f"5. Produce at most {max_groups} groups\n\n"
        f"Return final_groups: each group has member_probe_ids (subset of supplied IDs), "
        f"rationale (why grouped), evidence_basis (which titles/queries support this)."
    )

    try:
        request = AIRequest(
            system="You are a market intelligence analyst. Respond with valid JSON only.",
            user=prompt_text,
            model="claude-haiku-4-5-20251001",
            response_schema=ClusterAdjudicationOutput,
            prompt_name="market-cluster-adjudication",
            prompt_version=SEMANTIC_CLUSTERING_VERSION,
        )
        response = ai_provider.complete(request)
        llm_model = getattr(response, "model", None)

        if response.parsed is None:
            return initial_groups, False, None

        adjudication: ClusterAdjudicationOutput = response.parsed
        id_to_idx = {enriched_probes[i]["id"]: i for i in range(len(enriched_probes))}

        validated = validate_adjudication_output(adjudication, valid_probe_ids, all_probe_ids)

        # Convert from probe-id-keyed to probe-index-keyed
        final_groups: dict[int, list[int]] = {}
        for _probe_id_key, probe_ids in validated.items():
            idxs = [id_to_idx[pid] for pid in probe_ids if pid in id_to_idx]
            if not idxs:
                continue
            canonical_idx = min(idxs)
            final_groups[canonical_idx] = idxs

        # Add any probe indices not in final_groups (safety net)
        assigned_idxs = {idx for group in final_groups.values() for idx in group}
        for idx in range(len(enriched_probes)):
            if idx not in assigned_idxs:
                final_groups[idx] = [idx]

        return final_groups, True, llm_model

    except Exception:
        return initial_groups, False, None


# ---------------------------------------------------------------------------
# Stage 3 — Canonical cluster identity matching
# ---------------------------------------------------------------------------


def _find_or_create_canonical_cluster(
    conn: sqlite3.Connection,
    *,
    platform: str,
    provider: str,
    region_code: str | None,
    language_code: str | None,
    canonical_label: str,
    normalized_label: str,
    probe_normalized_queries: list[str],
) -> CanonicalMarketCluster:
    """Match this cluster to an existing canonical identity or create a new one.

    Matching order:
    1. Exact semantic fingerprint (same probe set, same scope)
    2. Exact normalized_label match within same scope
    3. Word-overlap match (Jaccard ≥ 0.5 on label word sets) within scope
    4. Create new canonical cluster
    """
    fingerprint = make_canonical_cluster_fingerprint(
        normalized_queries=probe_normalized_queries,
        platform=platform,
        provider=provider,
        region_code=region_code,
        language_code=language_code,
    )

    # 1. Exact fingerprint
    existing = find_canonical_cluster_by_fingerprint(
        conn,
        semantic_fingerprint=fingerprint,
        platform=platform,
        provider=provider,
        region_code=region_code,
        language_code=language_code,
    )
    if existing:
        return existing

    # 2. Exact normalized_label
    existing = find_canonical_cluster_by_label(
        conn,
        normalized_label=normalized_label,
        platform=platform,
        provider=provider,
        region_code=region_code,
        language_code=language_code,
    )
    if existing:
        return existing

    # 3. High word-overlap on label
    candidates = list_canonical_clusters(
        conn,
        platform=platform,
        provider=provider,
        region_code=region_code,
        language_code=language_code,
        limit=200,
    )
    for c in candidates:
        if jaccard_similarity(c.normalized_label, normalized_label) >= 0.5:
            return c

    # 4. Create new canonical cluster
    return insert_canonical_cluster(
        conn,
        platform=platform,
        provider=provider,
        region_code=region_code,
        language_code=language_code,
        canonical_label=canonical_label,
        normalized_label=normalized_label,
        semantic_fingerprint=fingerprint,
    )


# ---------------------------------------------------------------------------
# Evidence aggregation
# ---------------------------------------------------------------------------


def _aggregate_cluster_evidence(
    conn: sqlite3.Connection,
    probe_ids: list[int],
) -> dict[str, Any]:
    """Aggregate all evidence for a cluster's probe members."""
    view_counts: list[float] = []
    age_days_list: list[float] = []
    view_by_age_index: list[float | None] = []
    creator_ids: set[str] = set()
    search_totals: list[float] = []
    vpd_positive: list[float] = []
    velocity_count = 0
    all_video_ids: list[str] = []
    observation_ids: list[int] = []
    seen_video_ids: set[str] = set()

    for probe_id in probe_ids:
        videos = get_videos_for_probe(conn, probe_id)
        for v in videos:
            vid = v.get("external_video_id")
            if not vid or vid in seen_video_ids:
                continue
            seen_video_ids.add(vid)
            all_video_ids.append(vid)

            if v.get("external_channel_id"):
                creator_ids.add(v["external_channel_id"])

            vc = v.get("view_count")
            age = v.get("content_age_days")

            if vc is not None:
                view_counts.append(float(vc))
            if age is not None:
                age_days_list.append(float(age))
                view_by_age_index.append(float(vc) if vc is not None else None)

            obs_ids = v.get("observation_ids") or []
            observation_ids.extend(obs_ids)

            vel = get_velocity_for_video(conn, vid)
            if vel is not None:
                velocity_count += 1
                is_neg = int(vel.get("is_negative_delta") or 0)
                vpd = vel.get("units_per_day")
                if not is_neg and vpd is not None and float(vpd) > 0:
                    vpd_positive.append(float(vpd))

        st = get_search_total_for_probe(conn, probe_id)
        if st is not None:
            search_totals.append(st)

    return {
        "view_counts": view_counts,
        "age_days_list": age_days_list,
        "view_by_age_index": view_by_age_index,
        "creator_ids": creator_ids,
        "search_total": max(search_totals) if search_totals else None,
        "vpd_positive": vpd_positive,
        "velocity_count": velocity_count,
        "all_video_ids": all_video_ids,
        "observation_ids": list(set(observation_ids)),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_market_interpretation(
    conn: sqlite3.Connection,
    *,
    platform: str = "youtube",
    provider: str = "youtube_data_api",
    region_code: str | None = None,
    language_code: str | None = None,
    source_run_ids: list[int] | None = None,
    jaccard_threshold: float = 0.35,
    ai_provider: Any | None = None,
    max_llm_clusters: int = 0,
    policy_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one full market interpretation pass.

    Parameters
    ----------
    source_run_ids : optional list of exploration_run_ids to restrict probe scope.
                     None → all dispatched probes for (platform, provider).
    max_llm_clusters : 0 (default) → skip LLM semantic adjudication entirely.
    ai_provider      : only consulted when max_llm_clusters > 0.
    jaccard_threshold: probes with Jaccard ≥ this are strong-edge candidates.
    policy_snapshot  : merged with auto-built policy dict before persisting.
                       Auto-built constants are always included; caller dict
                       can add extra keys but cannot erase defaults.

    Returns
    -------
    {
      "run_id": int,
      "cluster_count": int,
      "clusters": list[MarketTopicCluster],
      "signals": list[MarketClusterSignal],
      "opportunities": list[ExternalMarketOpportunityEvidence],
      "llm_used": bool,
      "input_hash": str,
      "status": str,
    }
    """
    now = datetime.now(UTC).isoformat()
    src_ids: list[int] = sorted(source_run_ids or [])
    evidence_cutoff = now

    # Auto-build complete policy snapshot (caller overrides merged in)
    full_policy = build_interpretation_policy_snapshot(
        jaccard_threshold=jaccard_threshold,
        semantic_clustering_version=SEMANTIC_CLUSTERING_VERSION,
        caller_overrides=policy_snapshot or {},
    )

    # --- idempotency hash ---
    input_hash = make_interpretation_run_input_hash(
        platform=platform,
        provider=provider,
        region_code=region_code,
        language_code=language_code,
        evidence_cutoff=evidence_cutoff,
        source_run_ids=src_ids,
        clustering_version=CLUSTERING_VERSION,
        scoring_version=SCORING_VERSION,
    )

    run = insert_interpretation_run(
        conn,
        platform=platform,
        provider=provider,
        region_code=region_code,
        language_code=language_code,
        clustering_version=CLUSTERING_VERSION,
        scoring_version=SCORING_VERSION,
        evidence_cutoff=evidence_cutoff,
        source_run_ids=src_ids,
        policy_snapshot=full_policy,
        input_hash=input_hash,
    )
    run_id = run.id

    # --- check idempotent hit ---
    if run.status == "completed":
        clusters = list_clusters_for_run(conn, run_id)
        signals = list_signals_for_run(conn, run_id)
        # clusters and signals are independent queries (not length-guaranteed to
        # match); strict=False preserves the existing truncate-to-shorter behavior.
        opps = [
            build_opportunity_evidence(c, s)
            for c, s in zip(clusters, signals, strict=False)
            if s is not None
        ]
        return {
            "run_id": run_id,
            "cluster_count": run.cluster_count,
            "clusters": clusters,
            "signals": signals,
            "opportunities": opps,
            "llm_used": bool(run.source_run_ids()),
            "input_hash": input_hash,
            "status": "completed",
            "idempotent_hit": True,
        }

    update_interpretation_run_status(conn, run_id, status="running", started_at=now)

    try:
        probes = get_dispatched_probes_for_run(
            conn,
            platform=platform,
            provider=provider,
            exploration_run_id=src_ids[0] if len(src_ids) == 1 else None,
            region_code=region_code,
            language_code=language_code,
        )

        if not probes:
            update_interpretation_run_status(
                conn,
                run_id,
                status="completed",
                cluster_count=0,
                completed_at=datetime.now(UTC).isoformat(),
            )
            conn.commit()
            return {
                "run_id": run_id,
                "cluster_count": 0,
                "clusters": [],
                "signals": [],
                "opportunities": [],
                "llm_used": False,
                "input_hash": input_hash,
                "status": "completed",
            }

        # --- Stage 1: Jaccard graph ---
        strong_edges, candidate_edges = _compute_jaccard_edges(
            probes,
            threshold=jaccard_threshold,
            soft_threshold=_SOFT_THRESHOLD,
        )
        initial_groups = _groups_from_edges(len(probes), strong_edges)

        llm_used = False
        llm_model: str | None = None

        # --- Stage 2: optional LLM semantic adjudication ---
        if max_llm_clusters > 0 and ai_provider is not None:
            enriched_probes = _enrich_probes_with_titles(conn, probes)
            initial_groups, llm_used, llm_model = _apply_llm_semantic_adjudication(
                enriched_probes=enriched_probes,
                initial_groups=initial_groups,
                candidate_edges=candidate_edges,
                ai_provider=ai_provider,
                max_groups=max_llm_clusters,
            )

        # --- build clusters and signals ---
        clusters: list[MarketTopicCluster] = []
        signals: list[MarketClusterSignal] = []
        opportunities: list[ExternalMarketOpportunityEvidence] = []

        for _canonical_idx, member_indices in sorted(initial_groups.items()):
            group_probes = [probes[i] for i in member_indices]
            probe_ids = [p["id"] for p in group_probes]
            probe_normalized_queries = [p.get("normalized_query", "") or "" for p in group_probes]

            raw_label, normalized_label = _pick_cluster_label(group_probes)

            # --- Stage 3: canonical cluster identity ---
            canonical = _find_or_create_canonical_cluster(
                conn,
                platform=platform,
                provider=provider,
                region_code=region_code,
                language_code=language_code,
                canonical_label=raw_label,
                normalized_label=normalized_label,
                probe_normalized_queries=probe_normalized_queries,
            )

            cluster_hash = make_cluster_input_hash(
                normalized_label=normalized_label,
                region_code=region_code,
                language_code=language_code,
                platform=platform,
                provider=provider,
                cluster_version=CLUSTERING_VERSION,
                member_probe_ids=probe_ids,
            )

            evidence = _aggregate_cluster_evidence(conn, probe_ids)

            member_probe_count = len(probe_ids)
            member_video_count = len(evidence["all_video_ids"])

            cluster = insert_cluster(
                conn,
                interpretation_run_id=run_id,
                platform=platform,
                provider=provider,
                region_code=region_code,
                language_code=language_code,
                cluster_label=raw_label,
                normalized_label=normalized_label,
                cluster_type="market_region",
                description="",
                clustering_rationale=(
                    f"{'Hybrid semantic+Jaccard' if llm_used else 'Jaccard'} "
                    f"Δ≥{jaccard_threshold:.2f} on normalized_query "
                    f"({member_probe_count} probes)"
                ),
                cluster_version=CLUSTERING_VERSION,
                llm_used=1 if llm_used else 0,
                llm_model=llm_model,
                llm_prompt_version=SEMANTIC_CLUSTERING_VERSION if llm_used else None,
                member_probe_count=member_probe_count,
                member_video_count=member_video_count,
                canonical_cluster_id=canonical.id,
                input_hash=cluster_hash,
            )

            for probe_id in probe_ids:
                insert_cluster_member_probe(conn, cluster_id=cluster.id, probe_id=probe_id)

            for vid in evidence["all_video_ids"]:
                insert_cluster_member_video(conn, cluster_id=cluster.id, external_video_id=vid)

            # --- scoring ---
            creator_count = len(evidence["creator_ids"])
            view_counts: list[float] = evidence["view_counts"]
            age_days_list: list[float] = evidence["age_days_list"]
            view_by_age: list[float | None] = evidence["view_by_age_index"]
            vpd_positive: list[float] = evidence["vpd_positive"]
            velocity_count: int = evidence["velocity_count"]
            video_ids: list[str] = evidence["all_video_ids"]
            observation_ids: list[int] = evidence["observation_ids"]

            demand_score, demand_components = compute_demand_score(
                view_counts=view_counts,
                creator_count=creator_count,
            )
            saturation_score, saturation_components = compute_saturation_score(
                creator_count=creator_count,
                search_total_estimate=evidence["search_total"],
            )
            freshness_score, freshness_components = compute_freshness_score(
                age_days_list=age_days_list,
            )
            momentum_score, momentum_components = compute_momentum_score(
                positive_vpd_list=vpd_positive,
                total_velocity_count=velocity_count,
            )
            persistence_score, persistence_components = compute_persistence_score(
                age_days_list=age_days_list,
                view_counts_by_index=view_by_age,
            )

            completeness = (
                sum(
                    1
                    for s in [
                        demand_score,
                        saturation_score,
                        freshness_score,
                        momentum_score,
                        persistence_score,
                    ]
                    if s is not None
                )
                / 5.0
            )

            maturity = compute_signal_maturity(len(video_ids), creator_count)
            confidence = compute_signal_confidence(
                video_count=len(video_ids),
                creator_count=creator_count,
                velocity_count=velocity_count,
                completeness=completeness,
            )
            state_label = compute_state_label(
                demand=demand_score,
                saturation=saturation_score,
                freshness=freshness_score,
                momentum=momentum_score,
                persistence=persistence_score,
            )

            signal_hash = make_signal_input_hash(
                cluster_id=cluster.id,
                member_video_ids=video_ids,
                scoring_version=SCORING_VERSION,
            )

            signal = insert_cluster_signal(
                conn,
                cluster_id=cluster.id,
                interpretation_run_id=run_id,
                demand_score=demand_score,
                saturation_score=saturation_score,
                freshness_score=freshness_score,
                momentum_score=momentum_score,
                persistence_score=persistence_score,
                confidence=confidence,
                signal_maturity=maturity,
                state_label=state_label,
                supporting_video_count=len(video_ids),
                supporting_creator_count=creator_count,
                velocity_tracked_video_count=velocity_count,
                demand_components=demand_components,
                saturation_components=saturation_components,
                freshness_components=freshness_components,
                momentum_components=momentum_components,
                persistence_components=persistence_components,
                scoring_version=SCORING_VERSION,
                supporting_observation_ids=observation_ids,
                input_hash=signal_hash,
                scored_at=datetime.now(UTC).isoformat(),
            )

            clusters.append(cluster)
            signals.append(signal)
            opportunities.append(build_opportunity_evidence(cluster, signal))

        cluster_count = len(clusters)
        update_interpretation_run_status(
            conn,
            run_id,
            status="completed",
            cluster_count=cluster_count,
            completed_at=datetime.now(UTC).isoformat(),
        )
        conn.commit()

        return {
            "run_id": run_id,
            "cluster_count": cluster_count,
            "clusters": clusters,
            "signals": signals,
            "opportunities": opportunities,
            "llm_used": llm_used,
            "input_hash": input_hash,
            "status": "completed",
        }

    except Exception as exc:
        update_interpretation_run_status(
            conn,
            run_id,
            status="failed",
            error_message=str(exc),
            completed_at=datetime.now(UTC).isoformat(),
        )
        raise


# ---------------------------------------------------------------------------
# Query helpers for CLI / display
# ---------------------------------------------------------------------------


def get_cluster_summary(
    conn: sqlite3.Connection,
    cluster_id: int,
) -> dict[str, Any] | None:
    """Return a combined cluster + latest signal summary for display."""
    try:
        cluster = get_cluster(conn, cluster_id)
    except LookupError:
        return None

    signal = get_latest_signal_for_cluster(conn, cluster_id)

    result: dict[str, Any] = {
        "cluster_id": cluster.id,
        "canonical_cluster_id": cluster.canonical_cluster_id,
        "cluster_label": cluster.cluster_label,
        "normalized_label": cluster.normalized_label,
        "cluster_type": cluster.cluster_type,
        "platform": cluster.platform,
        "region_code": cluster.region_code,
        "language_code": cluster.language_code,
        "member_probe_count": cluster.member_probe_count,
        "member_video_count": cluster.member_video_count,
        "clustering_rationale": cluster.clustering_rationale,
        "llm_used": bool(cluster.llm_used),
        "created_at": cluster.created_at,
    }

    if signal:
        result.update(
            {
                "demand_score": signal.demand_score,
                "saturation_score": signal.saturation_score,
                "freshness_score": signal.freshness_score,
                "momentum_score": signal.momentum_score,
                "persistence_score": signal.persistence_score,
                "confidence": signal.confidence,
                "signal_maturity": signal.signal_maturity,
                "state_label": signal.state_label,
                "supporting_video_count": signal.supporting_video_count,
                "supporting_creator_count": signal.supporting_creator_count,
                "velocity_tracked_video_count": signal.velocity_tracked_video_count,
                "scored_at": signal.scored_at,
            }
        )
    else:
        result["signal"] = None

    return result


def list_signals_for_cluster_display(
    conn: sqlite3.Connection,
    cluster_id: int,
) -> list[dict[str, Any]]:
    """Return all signal snapshots for a run-local cluster, newest first."""
    rows = conn.execute(
        "SELECT * FROM market_cluster_signals WHERE cluster_id = ? ORDER BY id DESC",
        (cluster_id,),
    ).fetchall()
    results = []
    for row in rows:
        d = dict(row)
        results.append(
            {
                "signal_id": d["id"],
                "interpretation_run_id": d["interpretation_run_id"],
                "demand_score": d.get("demand_score"),
                "saturation_score": d.get("saturation_score"),
                "freshness_score": d.get("freshness_score"),
                "momentum_score": d.get("momentum_score"),
                "persistence_score": d.get("persistence_score"),
                "confidence": d.get("confidence"),
                "signal_maturity": d.get("signal_maturity"),
                "state_label": d.get("state_label"),
                "supporting_video_count": d.get("supporting_video_count", 0),
                "scored_at": d.get("scored_at"),
            }
        )
    return results


def get_canonical_signal_history(
    conn: sqlite3.Connection,
    canonical_cluster_id: int,
) -> list[dict[str, Any]]:
    """Return all signal snapshots for a canonical cluster, oldest first.

    Enables demand/momentum/freshness trend queries across interpretation runs
    without manual label matching.
    """
    return get_cluster_signal_history(conn, canonical_cluster_id)
