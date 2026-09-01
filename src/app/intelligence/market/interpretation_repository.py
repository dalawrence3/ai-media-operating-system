"""Phase 13E — Market Interpretation repository (DB read/write layer).

All write operations use INSERT OR IGNORE + UNIQUE constraint on input_hash
so the full API is idempotent: calling with the same evidence snapshot
returns the existing row rather than creating a duplicate.

All reads return typed Pydantic models.  Raw SQL stays in this file.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.intelligence.market.interpretation_models import (
    CanonicalMarketCluster,
    MarketClusterMember,
    MarketClusterSignal,
    MarketInterpretationRun,
    MarketTopicCluster,
)

# ---------------------------------------------------------------------------
# MarketInterpretationRun CRUD
# ---------------------------------------------------------------------------


def insert_interpretation_run(
    conn: sqlite3.Connection,
    *,
    platform: str,
    provider: str,
    region_code: str | None,
    language_code: str | None,
    clustering_version: str,
    scoring_version: str,
    evidence_cutoff: str,
    source_run_ids: list[int],
    policy_snapshot: dict[str, Any],
    input_hash: str,
) -> MarketInterpretationRun:
    """Insert a new interpretation run (idempotent via input_hash)."""
    source_json = json.dumps(sorted(source_run_ids))
    policy_json = json.dumps(policy_snapshot, sort_keys=True)
    conn.execute(
        """
        INSERT OR IGNORE INTO market_interpretation_runs
            (platform, provider, region_code, language_code,
             clustering_version, scoring_version, evidence_cutoff,
             source_run_ids_json, policy_snapshot_json, status,
             cluster_count, input_hash)
        VALUES (?,?,?,?,?,?,?,?,?,'pending',0,?)
        """,
        (
            platform,
            provider,
            region_code,
            language_code,
            clustering_version,
            scoring_version,
            evidence_cutoff,
            source_json,
            policy_json,
            input_hash,
        ),
    )
    return get_interpretation_run_by_hash(conn, input_hash)


def get_interpretation_run_by_hash(
    conn: sqlite3.Connection,
    input_hash: str,
) -> MarketInterpretationRun:
    row = conn.execute(
        "SELECT * FROM market_interpretation_runs WHERE input_hash = ?",
        (input_hash,),
    ).fetchone()
    if row is None:
        raise LookupError(f"No interpretation run with input_hash={input_hash!r}")
    return _row_to_run(row)


def get_interpretation_run(conn: sqlite3.Connection, run_id: int) -> MarketInterpretationRun:
    row = conn.execute(
        "SELECT * FROM market_interpretation_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"No interpretation run id={run_id}")
    return _row_to_run(row)


def update_interpretation_run_status(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    cluster_count: int | None = None,
    error_message: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
) -> None:
    sets = ["status = ?"]
    params: list[Any] = [status]
    if cluster_count is not None:
        sets.append("cluster_count = ?")
        params.append(cluster_count)
    if error_message is not None:
        sets.append("error_message = ?")
        params.append(error_message)
    if started_at is not None:
        sets.append("started_at = ?")
        params.append(started_at)
    if completed_at is not None:
        sets.append("completed_at = ?")
        params.append(completed_at)
    params.append(run_id)
    conn.execute(
        f"UPDATE market_interpretation_runs SET {', '.join(sets)} WHERE id = ?",
        params,
    )


def list_interpretation_runs(
    conn: sqlite3.Connection,
    *,
    platform: str = "youtube",
    provider: str = "youtube_data_api",
    limit: int = 25,
    status: str | None = None,
) -> list[MarketInterpretationRun]:
    params: list[Any] = [platform, provider]
    sql = "SELECT * FROM market_interpretation_runs WHERE platform = ? AND provider = ?"
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [_row_to_run(r) for r in conn.execute(sql, params).fetchall()]


def _row_to_run(row: sqlite3.Row) -> MarketInterpretationRun:
    d = dict(row)
    return MarketInterpretationRun(**d)


# ---------------------------------------------------------------------------
# CanonicalMarketCluster CRUD (Phase 13E.1)
# ---------------------------------------------------------------------------


def insert_canonical_cluster(
    conn: sqlite3.Connection,
    *,
    platform: str,
    provider: str,
    region_code: str | None,
    language_code: str | None,
    canonical_label: str,
    normalized_label: str,
    semantic_fingerprint: str,
    identity_version: str = "v1",
) -> CanonicalMarketCluster:
    """Insert or return existing canonical cluster (idempotent via fingerprint UNIQUE)."""
    conn.execute(
        """
        INSERT OR IGNORE INTO market_canonical_clusters
            (platform, provider, region_code, language_code,
             canonical_label, normalized_label, semantic_fingerprint, identity_version)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            platform,
            provider,
            region_code,
            language_code,
            canonical_label,
            normalized_label,
            semantic_fingerprint,
            identity_version,
        ),
    )
    row = conn.execute(
        "SELECT * FROM market_canonical_clusters "
        "WHERE platform = ? AND provider = ? "
        "AND region_code IS ? AND language_code IS ? "
        "AND semantic_fingerprint = ?",
        (platform, provider, region_code, language_code, semantic_fingerprint),
    ).fetchone()
    return _row_to_canonical(row)


def get_canonical_cluster(
    conn: sqlite3.Connection,
    canonical_cluster_id: int,
) -> CanonicalMarketCluster:
    row = conn.execute(
        "SELECT * FROM market_canonical_clusters WHERE id = ?",
        (canonical_cluster_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"No canonical cluster id={canonical_cluster_id}")
    return _row_to_canonical(row)


def find_canonical_cluster_by_fingerprint(
    conn: sqlite3.Connection,
    *,
    semantic_fingerprint: str,
    platform: str,
    provider: str,
    region_code: str | None,
    language_code: str | None,
) -> CanonicalMarketCluster | None:
    """Return the canonical cluster matching this fingerprint in scope, if any."""
    row = conn.execute(
        "SELECT * FROM market_canonical_clusters "
        "WHERE platform = ? AND provider = ? "
        "AND region_code IS ? AND language_code IS ? "
        "AND semantic_fingerprint = ?",
        (platform, provider, region_code, language_code, semantic_fingerprint),
    ).fetchone()
    return _row_to_canonical(row) if row else None


def find_canonical_cluster_by_label(
    conn: sqlite3.Connection,
    *,
    normalized_label: str,
    platform: str,
    provider: str,
    region_code: str | None,
    language_code: str | None,
) -> CanonicalMarketCluster | None:
    """Return the canonical cluster with the exact normalized_label in scope, if any."""
    row = conn.execute(
        "SELECT * FROM market_canonical_clusters "
        "WHERE platform = ? AND provider = ? "
        "AND region_code IS ? AND language_code IS ? "
        "AND normalized_label = ? "
        "ORDER BY id LIMIT 1",
        (platform, provider, region_code, language_code, normalized_label),
    ).fetchone()
    return _row_to_canonical(row) if row else None


def list_canonical_clusters(
    conn: sqlite3.Connection,
    *,
    platform: str = "youtube",
    provider: str = "youtube_data_api",
    region_code: str | None = None,
    language_code: str | None = None,
    limit: int = 50,
) -> list[CanonicalMarketCluster]:
    rows = conn.execute(
        "SELECT * FROM market_canonical_clusters "
        "WHERE platform = ? AND provider = ? "
        "AND region_code IS ? AND language_code IS ? "
        "ORDER BY id DESC LIMIT ?",
        (platform, provider, region_code, language_code, limit),
    ).fetchall()
    return [_row_to_canonical(r) for r in rows]


def _row_to_canonical(row: sqlite3.Row) -> CanonicalMarketCluster:
    return CanonicalMarketCluster(**dict(row))


# ---------------------------------------------------------------------------
# Cross-run signal history (Phase 13E.1)
# ---------------------------------------------------------------------------


def get_cluster_signal_history(
    conn: sqlite3.Connection,
    canonical_cluster_id: int,
) -> list[dict[str, Any]]:
    """Return all signal snapshots for a canonical cluster, oldest first.

    Joins market_topic_clusters → market_cluster_signals to collect signal
    history across interpretation runs without manual label matching.
    """
    rows = conn.execute(
        """
        SELECT
            s.id AS signal_id,
            s.interpretation_run_id,
            s.cluster_id,
            c.cluster_label,
            c.normalized_label,
            s.demand_score,
            s.saturation_score,
            s.freshness_score,
            s.momentum_score,
            s.persistence_score,
            s.confidence,
            s.signal_maturity,
            s.state_label,
            s.supporting_video_count,
            s.supporting_creator_count,
            s.velocity_tracked_video_count,
            s.scored_at
        FROM market_cluster_signals s
        JOIN market_topic_clusters c ON c.id = s.cluster_id
        WHERE c.canonical_cluster_id = ?
        ORDER BY s.scored_at ASC, s.id ASC
        """,
        (canonical_cluster_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# MarketTopicCluster CRUD
# ---------------------------------------------------------------------------


def insert_cluster(
    conn: sqlite3.Connection,
    *,
    interpretation_run_id: int,
    platform: str,
    provider: str,
    region_code: str | None,
    language_code: str | None,
    cluster_label: str,
    normalized_label: str,
    cluster_type: str,
    description: str,
    clustering_rationale: str,
    cluster_version: str,
    llm_used: int,
    llm_model: str | None,
    llm_prompt_version: str | None,
    member_probe_count: int,
    member_video_count: int,
    input_hash: str,
    canonical_cluster_id: int | None = None,
) -> MarketTopicCluster:
    """Insert cluster; idempotent via (interpretation_run_id, input_hash) UNIQUE."""
    conn.execute(
        """
        INSERT OR IGNORE INTO market_topic_clusters
            (interpretation_run_id, platform, provider, region_code, language_code,
             cluster_label, normalized_label, cluster_type, description,
             clustering_rationale, cluster_version, llm_used, llm_model,
             llm_prompt_version, member_probe_count, member_video_count,
             canonical_cluster_id, input_hash)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            interpretation_run_id,
            platform,
            provider,
            region_code,
            language_code,
            cluster_label,
            normalized_label,
            cluster_type,
            description,
            clustering_rationale,
            cluster_version,
            llm_used,
            llm_model,
            llm_prompt_version,
            member_probe_count,
            member_video_count,
            canonical_cluster_id,
            input_hash,
        ),
    )
    row = conn.execute(
        "SELECT * FROM market_topic_clusters WHERE interpretation_run_id = ? AND input_hash = ?",
        (interpretation_run_id, input_hash),
    ).fetchone()
    return _row_to_cluster(row)


def get_cluster(conn: sqlite3.Connection, cluster_id: int) -> MarketTopicCluster:
    row = conn.execute(
        "SELECT * FROM market_topic_clusters WHERE id = ?",
        (cluster_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"No cluster id={cluster_id}")
    return _row_to_cluster(row)


def list_clusters_for_run(
    conn: sqlite3.Connection,
    run_id: int,
) -> list[MarketTopicCluster]:
    rows = conn.execute(
        "SELECT * FROM market_topic_clusters WHERE interpretation_run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    return [_row_to_cluster(r) for r in rows]


def _row_to_cluster(row: sqlite3.Row) -> MarketTopicCluster:
    return MarketTopicCluster(**dict(row))


# ---------------------------------------------------------------------------
# MarketClusterMember CRUD
# ---------------------------------------------------------------------------


def insert_cluster_member_probe(
    conn: sqlite3.Connection,
    *,
    cluster_id: int,
    probe_id: int,
    platform: str = "youtube",
    provider: str = "youtube_data_api",
) -> MarketClusterMember:
    conn.execute(
        """
        INSERT OR IGNORE INTO market_cluster_members
            (cluster_id, member_type, probe_id, platform, provider)
        VALUES (?, 'probe_origin', ?, ?, ?)
        """,
        (cluster_id, probe_id, platform, provider),
    )
    row = conn.execute(
        "SELECT * FROM market_cluster_members "
        "WHERE cluster_id = ? AND member_type = 'probe_origin' AND probe_id = ?",
        (cluster_id, probe_id),
    ).fetchone()
    return _row_to_member(row)


def insert_cluster_member_video(
    conn: sqlite3.Connection,
    *,
    cluster_id: int,
    external_video_id: str,
    platform: str = "youtube",
    provider: str = "youtube_data_api",
    supporting_job_id: int | None = None,
) -> MarketClusterMember:
    conn.execute(
        """
        INSERT OR IGNORE INTO market_cluster_members
            (cluster_id, member_type, external_video_id, platform, provider,
             supporting_job_id)
        VALUES (?, 'evidence_video', ?, ?, ?, ?)
        """,
        (cluster_id, external_video_id, platform, provider, supporting_job_id),
    )
    row = conn.execute(
        "SELECT * FROM market_cluster_members "
        "WHERE cluster_id = ? AND member_type = 'evidence_video' "
        "AND external_video_id = ?",
        (cluster_id, external_video_id),
    ).fetchone()
    return _row_to_member(row)


def list_members_for_cluster(
    conn: sqlite3.Connection,
    cluster_id: int,
    *,
    member_type: str | None = None,
) -> list[MarketClusterMember]:
    if member_type is not None:
        rows = conn.execute(
            "SELECT * FROM market_cluster_members "
            "WHERE cluster_id = ? AND member_type = ? ORDER BY id",
            (cluster_id, member_type),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM market_cluster_members WHERE cluster_id = ? ORDER BY id",
            (cluster_id,),
        ).fetchall()
    return [_row_to_member(r) for r in rows]


def _row_to_member(row: sqlite3.Row) -> MarketClusterMember:
    return MarketClusterMember(**dict(row))


# ---------------------------------------------------------------------------
# MarketClusterSignal CRUD
# ---------------------------------------------------------------------------


def insert_cluster_signal(
    conn: sqlite3.Connection,
    *,
    cluster_id: int,
    interpretation_run_id: int,
    demand_score: float | None,
    saturation_score: float | None,
    freshness_score: float | None,
    momentum_score: float | None,
    persistence_score: float | None,
    confidence: float,
    signal_maturity: str,
    state_label: str | None,
    supporting_video_count: int,
    supporting_creator_count: int,
    velocity_tracked_video_count: int,
    demand_components: dict[str, Any],
    saturation_components: dict[str, Any],
    freshness_components: dict[str, Any],
    momentum_components: dict[str, Any],
    persistence_components: dict[str, Any],
    scoring_version: str,
    supporting_observation_ids: list[int],
    input_hash: str,
    scored_at: str,
) -> MarketClusterSignal:
    """Insert signal; idempotent via (cluster_id, interpretation_run_id) UNIQUE."""
    conn.execute(
        """
        INSERT OR IGNORE INTO market_cluster_signals
            (cluster_id, interpretation_run_id,
             demand_score, saturation_score, freshness_score,
             momentum_score, persistence_score, confidence,
             signal_maturity, state_label,
             supporting_video_count, supporting_creator_count,
             velocity_tracked_video_count,
             demand_components_json, saturation_components_json,
             freshness_components_json, momentum_components_json,
             persistence_components_json,
             scoring_version, supporting_observation_ids_json,
             input_hash, scored_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            cluster_id,
            interpretation_run_id,
            demand_score,
            saturation_score,
            freshness_score,
            momentum_score,
            persistence_score,
            confidence,
            signal_maturity,
            state_label,
            supporting_video_count,
            supporting_creator_count,
            velocity_tracked_video_count,
            json.dumps(demand_components, sort_keys=True),
            json.dumps(saturation_components, sort_keys=True),
            json.dumps(freshness_components, sort_keys=True),
            json.dumps(momentum_components, sort_keys=True),
            json.dumps(persistence_components, sort_keys=True),
            scoring_version,
            json.dumps(sorted(supporting_observation_ids)),
            input_hash,
            scored_at,
        ),
    )
    row = conn.execute(
        "SELECT * FROM market_cluster_signals WHERE cluster_id = ? AND interpretation_run_id = ?",
        (cluster_id, interpretation_run_id),
    ).fetchone()
    return _row_to_signal(row)


def get_cluster_signal(
    conn: sqlite3.Connection,
    cluster_id: int,
    interpretation_run_id: int,
) -> MarketClusterSignal | None:
    row = conn.execute(
        "SELECT * FROM market_cluster_signals WHERE cluster_id = ? AND interpretation_run_id = ?",
        (cluster_id, interpretation_run_id),
    ).fetchone()
    return _row_to_signal(row) if row else None


def get_latest_signal_for_cluster(
    conn: sqlite3.Connection,
    cluster_id: int,
) -> MarketClusterSignal | None:
    row = conn.execute(
        "SELECT * FROM market_cluster_signals WHERE cluster_id = ? ORDER BY id DESC LIMIT 1",
        (cluster_id,),
    ).fetchone()
    return _row_to_signal(row) if row else None


def list_signals_for_run(
    conn: sqlite3.Connection,
    run_id: int,
) -> list[MarketClusterSignal]:
    rows = conn.execute(
        "SELECT * FROM market_cluster_signals WHERE interpretation_run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    return [_row_to_signal(r) for r in rows]


def _row_to_signal(row: sqlite3.Row) -> MarketClusterSignal:
    return MarketClusterSignal(**dict(row))


# ---------------------------------------------------------------------------
# Evidence queries — reads against existing market tables
# ---------------------------------------------------------------------------


def get_dispatched_probes_for_run(
    conn: sqlite3.Connection,
    *,
    platform: str = "youtube",
    provider: str = "youtube_data_api",
    exploration_run_id: int | None = None,
    region_code: str | None = None,
    language_code: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch dispatched probes eligible for clustering.

    Only returns probes with status='dispatched' or 'completed' (not archived/excluded).
    Optionally filtered by exploration_run_id via the collection jobs linkage.
    """
    params: list[Any] = []
    sql = (
        "SELECT DISTINCT p.id, p.normalized_query, p.query_text, p.probe_type, p.status, "
        "p.exploration_depth, p.dispatched_job_id "
        "FROM market_exploration_probes p "
        "WHERE p.status IN ('dispatched', 'completed')"
    )

    if exploration_run_id is not None:
        sql += " AND p.exploration_run_id = ?"
        params.append(exploration_run_id)

    sql += " ORDER BY p.id"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_videos_for_probe(
    conn: sqlite3.Connection,
    probe_id: int,
) -> list[dict[str, Any]]:
    """Return all evidence videos for a probe with their key observation values.

    Pulls view_count, published_at, external_channel_id from observations
    via the market_job_observations join.
    """
    sql = """
        SELECT
            o.external_video_id,
            MAX(CASE WHEN o.signal_type = 'video_view_count'
                     THEN CAST(o.signal_value_numeric AS REAL) END) AS view_count,
            MAX(CASE WHEN o.signal_type = 'video_published_at'
                     THEN o.signal_value_text END) AS published_at,
            MAX(CASE WHEN o.signal_type = 'video_published_at'
                     THEN CAST(o.content_age_days AS REAL) END) AS content_age_days,
            MAX(o.external_channel_id) AS external_channel_id,
            GROUP_CONCAT(DISTINCT o.id) AS observation_ids
        FROM market_intelligence_observations o
        JOIN market_job_observations mjo ON mjo.observation_id = o.id
        JOIN market_exploration_probes p ON p.dispatched_job_id = mjo.job_id
        WHERE p.id = ?
          AND o.external_video_id IS NOT NULL
        GROUP BY o.external_video_id
    """
    rows = conn.execute(sql, (probe_id,)).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        raw_ids = d.get("observation_ids") or ""
        d["observation_ids"] = [int(x) for x in raw_ids.split(",") if x.strip().isdigit()]
        results.append(d)
    return results


def get_search_total_for_probe(
    conn: sqlite3.Connection,
    probe_id: int,
) -> float | None:
    """Return the most recent SEARCH_RESULT_TOTAL_ESTIMATE for a probe, if any."""
    row = conn.execute(
        """
        SELECT MAX(CAST(o.signal_value_numeric AS REAL)) AS search_total
        FROM market_intelligence_observations o
        JOIN market_job_observations mjo ON mjo.observation_id = o.id
        JOIN market_exploration_probes p ON p.dispatched_job_id = mjo.job_id
        WHERE p.id = ?
          AND o.signal_type = 'search_result_total_estimate'
        """,
        (probe_id,),
    ).fetchone()
    if row and row["search_total"] is not None:
        return float(row["search_total"])
    return None


def get_velocity_for_video(
    conn: sqlite3.Connection,
    external_video_id: str,
) -> dict[str, Any] | None:
    """Return the most recent velocity estimate for a video, if any."""
    row = conn.execute(
        """
        SELECT units_per_day, is_negative_delta, velocity_maturity
        FROM market_velocity_estimates
        WHERE external_video_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (external_video_id,),
    ).fetchone()
    return dict(row) if row else None


def get_titles_for_probe(
    conn: sqlite3.Connection,
    probe_id: int,
    *,
    limit: int = 10,
) -> list[str]:
    """Return VIDEO_TITLE observation values for a probe's collection job.

    These titles are the primary semantic evidence for cluster adjudication.
    Bounded by limit to keep LLM prompts manageable.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT o.signal_value_text
        FROM market_intelligence_observations o
        JOIN market_job_observations mjo ON mjo.observation_id = o.id
        JOIN market_exploration_probes p ON p.dispatched_job_id = mjo.job_id
        WHERE p.id = ?
          AND o.signal_type = 'video_title'
          AND o.signal_value_text IS NOT NULL
        LIMIT ?
        """,
        (probe_id, limit),
    ).fetchall()
    return [r[0] for r in rows if r[0]]


def update_cluster_canonical_id(
    conn: sqlite3.Connection,
    cluster_id: int,
    canonical_cluster_id: int,
) -> None:
    """Set or update the canonical_cluster_id for an existing cluster row."""
    conn.execute(
        "UPDATE market_topic_clusters SET canonical_cluster_id = ? WHERE id = ?",
        (canonical_cluster_id, cluster_id),
    )
