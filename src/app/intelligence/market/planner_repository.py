"""Phase 13D-A — Repository for market exploration runs, probes, and evidence.

No LLM calls. No YouTube calls. Persistence layer only.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.intelligence.market.planner_models import (
    CollectionPolicy,
    ExplorationProbe,
    ExplorationRun,
    ProbeEvidence,
    make_probe_input_hash,
    make_run_input_hash,
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _row_to_run(row: sqlite3.Row) -> ExplorationRun:
    return ExplorationRun(**dict(row))


def _row_to_probe(row: sqlite3.Row) -> ExplorationProbe:
    return ExplorationProbe(**dict(row))


def _row_to_evidence(row: sqlite3.Row) -> ProbeEvidence:
    return ProbeEvidence(**dict(row))


# ---------------------------------------------------------------------------
# Exploration runs
# ---------------------------------------------------------------------------


def create_exploration_run(
    conn: sqlite3.Connection,
    *,
    channel_id: int | None = None,
    workspace_id: str | None = None,
    planner_version: str = "v1",
    max_depth: int = 3,
    max_probes: int = 10,
    search_budget: int = 20,
    policy_snapshot: CollectionPolicy | None = None,
) -> ExplorationRun:
    now = _now()
    # Use microsecond precision for hashing to avoid collisions when two runs
    # are created within the same second.
    now_precise = datetime.now(UTC).isoformat()
    policy_json = policy_snapshot.model_dump_json() if policy_snapshot else "{}"
    input_hash = make_run_input_hash(
        channel_id=channel_id,
        workspace_id=workspace_id,
        planner_version=planner_version,
        max_depth=max_depth,
        max_probes=max_probes,
        search_budget=search_budget,
        created_at=now_precise,
    )
    cursor = conn.execute(
        """
        INSERT INTO market_exploration_runs
            (workspace_id, channel_id, planner_version, max_depth, max_probes,
             search_budget, policy_json, input_hash, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            workspace_id,
            channel_id,
            planner_version,
            max_depth,
            max_probes,
            search_budget,
            policy_json,
            input_hash,
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM market_exploration_runs WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return _row_to_run(row)


def get_exploration_run(conn: sqlite3.Connection, run_id: int) -> ExplorationRun | None:
    row = conn.execute("SELECT * FROM market_exploration_runs WHERE id = ?", (run_id,)).fetchone()
    return _row_to_run(row) if row else None


def list_exploration_runs(
    conn: sqlite3.Connection,
    *,
    channel_id: int | None = None,
    workspace_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[ExplorationRun]:
    clauses = []
    params: list = []
    if channel_id is not None:
        clauses.append("channel_id = ?")
        params.append(channel_id)
    if workspace_id is not None:
        clauses.append("workspace_id = ?")
        params.append(workspace_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM market_exploration_runs {where} ORDER BY created_at DESC LIMIT ?",
        params,
    ).fetchall()
    return [_row_to_run(r) for r in rows]


def update_exploration_run_status(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    candidate_count: int | None = None,
    selected_count: int | None = None,
    deferred_count: int | None = None,
    rejected_count: int | None = None,
    dispatched_count: int | None = None,
    error_message: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
) -> ExplorationRun:
    sets = ["status = ?"]
    params: list = [status]
    if candidate_count is not None:
        sets.append("candidate_count = ?")
        params.append(candidate_count)
    if selected_count is not None:
        sets.append("selected_count = ?")
        params.append(selected_count)
    if deferred_count is not None:
        sets.append("deferred_count = ?")
        params.append(deferred_count)
    if rejected_count is not None:
        sets.append("rejected_count = ?")
        params.append(rejected_count)
    if dispatched_count is not None:
        sets.append("dispatched_count = ?")
        params.append(dispatched_count)
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
        f"UPDATE market_exploration_runs SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    conn.commit()
    row = conn.execute("SELECT * FROM market_exploration_runs WHERE id = ?", (run_id,)).fetchone()
    return _row_to_run(row)


# ---------------------------------------------------------------------------
# Exploration probes
# ---------------------------------------------------------------------------


def create_exploration_probe(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    query_text: str,
    normalized_query: str,
    probe_type: str,
    channel_id: int | None = None,
    workspace_id: str | None = None,
    parent_probe_id: int | None = None,
    parent_job_id: int | None = None,
    exploration_depth: int = 0,
    region_code: str | None = None,
    language_code: str | None = None,
    collection_policy: CollectionPolicy | None = None,
    planner_version: str = "v1",
    market_region_label: str | None = None,
) -> ExplorationProbe:
    policy = collection_policy or CollectionPolicy()
    policy_json = policy.model_dump_json()
    input_hash = make_probe_input_hash(
        normalized_query=normalized_query,
        probe_type=probe_type,
        region_code=region_code,
        language_code=language_code,
        max_pages=policy.max_pages,
        max_results=policy.max_results,
        order=policy.order,
        published_after=policy.published_after,
        planner_version=planner_version,
    )
    now = _now()
    cursor = conn.execute(
        """
        INSERT INTO market_exploration_probes
            (exploration_run_id, workspace_id, channel_id, query_text,
             normalized_query, probe_type, parent_probe_id, parent_job_id,
             exploration_depth, region_code, language_code,
             collection_policy_json, status, corroboration_count,
             planner_version, market_region_label, input_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', 0, ?, ?, ?, ?)
        """,
        (
            run_id,
            workspace_id,
            channel_id,
            query_text,
            normalized_query,
            probe_type,
            parent_probe_id,
            parent_job_id,
            exploration_depth,
            region_code,
            language_code,
            policy_json,
            planner_version,
            market_region_label,
            input_hash,
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM market_exploration_probes WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return _row_to_probe(row)


def get_exploration_probe(conn: sqlite3.Connection, probe_id: int) -> ExplorationProbe | None:
    row = conn.execute(
        "SELECT * FROM market_exploration_probes WHERE id = ?", (probe_id,)
    ).fetchone()
    return _row_to_probe(row) if row else None


def list_exploration_probes(
    conn: sqlite3.Connection,
    *,
    run_id: int | None = None,
    channel_id: int | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[ExplorationProbe]:
    clauses = []
    params: list = []
    if run_id is not None:
        clauses.append("exploration_run_id = ?")
        params.append(run_id)
    if channel_id is not None:
        clauses.append("channel_id = ?")
        params.append(channel_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM market_exploration_probes {where} ORDER BY created_at DESC LIMIT ?",
        params,
    ).fetchall()
    return [_row_to_probe(r) for r in rows]


def update_probe_status(
    conn: sqlite3.Connection,
    probe_id: int,
    *,
    status: str,
    decided_at: str | None = None,
    decision_reason: str | None = None,
    priority_score: float | None = None,
    priority_components_json: str | None = None,
    niche_fit_score: float | None = None,
    semantic_fit_status: str | None = None,
    corroboration_count: int | None = None,
) -> ExplorationProbe:
    sets = ["status = ?"]
    params: list = [status]
    if decided_at is not None:
        sets.append("decided_at = ?")
        params.append(decided_at)
    if decision_reason is not None:
        sets.append("decision_reason = ?")
        params.append(decision_reason)
    if priority_score is not None:
        sets.append("priority_score = ?")
        params.append(priority_score)
    if priority_components_json is not None:
        sets.append("priority_components_json = ?")
        params.append(priority_components_json)
    if niche_fit_score is not None:
        sets.append("niche_fit_score = ?")
        params.append(niche_fit_score)
    if semantic_fit_status is not None:
        sets.append("semantic_fit_status = ?")
        params.append(semantic_fit_status)
    if corroboration_count is not None:
        sets.append("corroboration_count = ?")
        params.append(corroboration_count)
    params.append(probe_id)
    conn.execute(
        f"UPDATE market_exploration_probes SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM market_exploration_probes WHERE id = ?", (probe_id,)
    ).fetchone()
    return _row_to_probe(row)


def update_probe_dispatch(
    conn: sqlite3.Connection,
    probe_id: int,
    *,
    dispatched_job_id: int,
    dispatched_at: str,
) -> ExplorationProbe:
    conn.execute(
        """
        UPDATE market_exploration_probes
           SET status = 'dispatched',
               dispatched_job_id = ?,
               dispatched_at = ?
         WHERE id = ?
        """,
        (dispatched_job_id, dispatched_at, probe_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM market_exploration_probes WHERE id = ?", (probe_id,)
    ).fetchone()
    return _row_to_probe(row)


# ---------------------------------------------------------------------------
# Probe evidence
# ---------------------------------------------------------------------------


def link_probe_evidence(
    conn: sqlite3.Connection,
    *,
    probe_id: int,
    evidence_type: str,
    observation_id: int | None = None,
    velocity_id: int | None = None,
    job_id: int | None = None,
    notes: str | None = None,
) -> ProbeEvidence:
    now = _now()
    cursor = conn.execute(
        """
        INSERT INTO market_probe_evidence
            (probe_id, evidence_type, observation_id, velocity_id, job_id,
             evidence_notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (probe_id, evidence_type, observation_id, velocity_id, job_id, notes, now),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM market_probe_evidence WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return _row_to_evidence(row)


def get_probe_evidence(conn: sqlite3.Connection, probe_id: int) -> list[ProbeEvidence]:
    rows = conn.execute(
        "SELECT * FROM market_probe_evidence WHERE probe_id = ? ORDER BY created_at",
        (probe_id,),
    ).fetchall()
    return [_row_to_evidence(r) for r in rows]


# ---------------------------------------------------------------------------
# Provenance update (populated after LLM call, not at run creation time)
# ---------------------------------------------------------------------------


def update_exploration_run_provenance(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    provider: str | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
) -> ExplorationRun:
    """Record which LLM provider/model/prompt produced this run's probes."""
    sets: list[str] = []
    params: list = []
    if provider is not None:
        sets.append("provider = ?")
        params.append(provider)
    if model is not None:
        sets.append("model = ?")
        params.append(model)
    if prompt_version is not None:
        sets.append("prompt_version = ?")
        params.append(prompt_version)
    if not sets:
        row = conn.execute(
            "SELECT * FROM market_exploration_runs WHERE id = ?", (run_id,)
        ).fetchone()
        return _row_to_run(row)
    params.append(run_id)
    conn.execute(
        f"UPDATE market_exploration_runs SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    conn.commit()
    row = conn.execute("SELECT * FROM market_exploration_runs WHERE id = ?", (run_id,)).fetchone()
    return _row_to_run(row)


# ---------------------------------------------------------------------------
# Prior-run coverage queries
# ---------------------------------------------------------------------------


def list_prior_probe_normalized_queries(
    conn: sqlite3.Connection,
    channel_id: int | None,
    *,
    statuses: tuple[str, ...] = ("selected", "dispatched"),
) -> set[str]:
    """Return normalized_queries already selected or dispatched for this channel.

    Used to compute novelty scores and avoid re-proposing covered regions.
    Cross-run: the same query is allowed but deprioritized.
    """
    if channel_id is None:
        rows = conn.execute(
            f"""
            SELECT DISTINCT p.normalized_query
              FROM market_exploration_probes p
             WHERE p.channel_id IS NULL
               AND p.status IN ({", ".join("?" for _ in statuses)})
            """,
            list(statuses),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT DISTINCT p.normalized_query
              FROM market_exploration_probes p
             WHERE p.channel_id = ?
               AND p.status IN ({", ".join("?" for _ in statuses)})
            """,
            [channel_id, *list(statuses)],
        ).fetchall()
    return {r[0] for r in rows}


def count_dispatched_probes(
    conn: sqlite3.Connection,
    channel_id: int | None,
) -> int:
    """Return total dispatched probe count for this channel (cold-start predicate input)."""
    if channel_id is None:
        row = conn.execute(
            "SELECT COUNT(*) FROM market_exploration_probes "
            "WHERE channel_id IS NULL AND status = 'dispatched'"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM market_exploration_probes "
            "WHERE channel_id = ? AND status = 'dispatched'",
            (channel_id,),
        ).fetchone()
    return row[0] if row else 0


def list_expandable_parent_probes(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    max_depth: int = 2,
    statuses: tuple[str, ...] = ("selected", "dispatched"),
) -> list[ExplorationProbe]:
    """Return probes from a run that are eligible to act as parents for adjacent expansion.

    A probe is expandable if it has been selected/dispatched AND its depth is below max_depth.
    """
    placeholders = ", ".join("?" for _ in statuses)
    rows = conn.execute(
        f"""
        SELECT * FROM market_exploration_probes
         WHERE exploration_run_id = ?
           AND status IN ({placeholders})
           AND exploration_depth < ?
         ORDER BY priority_score DESC, created_at ASC
        """,
        (run_id, *list(statuses), max_depth),
    ).fetchall()
    return [_row_to_probe(r) for r in rows]


def get_probe_supporting_videos(
    conn: sqlite3.Connection,
    probe_id: int,
) -> list[dict]:
    """Return aggregated per-video evidence for a dispatched probe's collection job.

    Joins market_exploration_probes → market_job_observations →
    market_intelligence_observations. Returns one row per distinct video.
    """
    row = conn.execute(
        "SELECT dispatched_job_id FROM market_exploration_probes WHERE id = ?",
        (probe_id,),
    ).fetchone()
    if not row or row[0] is None:
        return []
    job_id = row[0]
    rows = conn.execute(
        """
        SELECT
            o.external_video_id,
            o.category_id,
            o.external_channel_id,
            MAX(CASE WHEN o.signal_type = 'video_view_count'
                     THEN o.signal_value_numeric END) AS view_count,
            MAX(CASE WHEN o.signal_type = 'video_like_count'
                     THEN o.signal_value_numeric END) AS like_count,
            MAX(CASE WHEN o.signal_type = 'video_published_at'
                     THEN o.signal_value_text END)  AS published_at,
            MIN(o.id) AS min_obs_id
          FROM market_intelligence_observations o
          JOIN market_job_observations jo ON jo.observation_id = o.id
         WHERE jo.job_id = ?
           AND o.external_video_id IS NOT NULL
         GROUP BY o.external_video_id
        """,
        (job_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_probe_creator_diversity(
    conn: sqlite3.Connection,
    probe_id: int,
) -> int:
    """Return the number of distinct creator channel IDs seen in the probe's job."""
    row = conn.execute(
        "SELECT dispatched_job_id FROM market_exploration_probes WHERE id = ?",
        (probe_id,),
    ).fetchone()
    if not row or row[0] is None:
        return 0
    job_id = row[0]
    result = conn.execute(
        """
        SELECT COUNT(DISTINCT o.external_channel_id)
          FROM market_intelligence_observations o
          JOIN market_job_observations jo ON jo.observation_id = o.id
         WHERE jo.job_id = ?
           AND o.external_channel_id IS NOT NULL
        """,
        (job_id,),
    ).fetchone()
    return result[0] if result else 0


def get_latest_velocity_for_probe(
    conn: sqlite3.Connection,
    probe_id: int,
) -> dict | None:
    """Return the highest-velocity estimate for any video collected by this probe's job.

    Returns None if the probe has no dispatched job or no velocity estimates.
    """
    row = conn.execute(
        "SELECT dispatched_job_id FROM market_exploration_probes WHERE id = ?",
        (probe_id,),
    ).fetchone()
    if not row or row[0] is None:
        return None
    job_id = row[0]
    result = conn.execute(
        """
        SELECT mv.*
          FROM market_velocity_estimates mv
         WHERE mv.external_video_id IN (
               SELECT DISTINCT o.external_video_id
                 FROM market_intelligence_observations o
                 JOIN market_job_observations jo ON jo.observation_id = o.id
                WHERE jo.job_id = ?
                  AND o.external_video_id IS NOT NULL
               )
           AND mv.units_per_day IS NOT NULL
         ORDER BY mv.units_per_day DESC
         LIMIT 1
        """,
        (job_id,),
    ).fetchone()
    return dict(result) if result else None


def get_job_observation_ids(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    limit: int = 20,
) -> list[int]:
    """Return up to `limit` observation IDs linked to a job, for evidence reference building."""
    rows = conn.execute(
        """
        SELECT observation_id
          FROM market_job_observations
         WHERE job_id = ?
         ORDER BY observation_id ASC
         LIMIT ?
        """,
        (job_id, limit),
    ).fetchall()
    return [r[0] for r in rows]


def update_exploration_run_policy(
    conn: sqlite3.Connection,
    run_id: int,
    policy_json: str,
) -> ExplorationRun:
    """Overwrite the policy_json snapshot on an existing run.

    Called by the selector at the start of a selection pass to record the
    exact policy that governed this run's decisions (weights, thresholds,
    provenance versions). Written early so it survives partial failures.
    """
    conn.execute(
        "UPDATE market_exploration_runs SET policy_json = ? WHERE id = ?",
        (policy_json, run_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM market_exploration_runs WHERE id = ?", (run_id,)).fetchone()
    return _row_to_run(row)


def list_probes_for_selection(
    conn: sqlite3.Connection,
    run_id: int,
) -> list[ExplorationProbe]:
    """Return CANDIDATE and DEFERRED probes for a run, in creation order.

    These are the probes the selector evaluates. SELECTED, REJECTED, and
    DISPATCHED probes are excluded — their decisions are final.
    """
    rows = conn.execute(
        """
        SELECT * FROM market_exploration_probes
         WHERE exploration_run_id = ?
           AND status IN ('candidate', 'deferred')
         ORDER BY created_at ASC
        """,
        (run_id,),
    ).fetchall()
    return [_row_to_probe(r) for r in rows]


def list_selected_probes_for_dispatch(
    conn: sqlite3.Connection,
    run_id: int,
) -> list[ExplorationProbe]:
    """Return SELECTED probes for a run, ordered for deterministic dispatch.

    Ordering: priority_score DESC, then probe_id ASC as a tie-break.
    Already-dispatched probes are excluded — the dispatcher's idempotency
    guard handles re-dispatch of dispatched+failed probes via dispatch_probe.
    """
    rows = conn.execute(
        """
        SELECT * FROM market_exploration_probes
         WHERE exploration_run_id = ?
           AND status = 'selected'
         ORDER BY priority_score DESC, id ASC
        """,
        (run_id,),
    ).fetchall()
    return [_row_to_probe(r) for r in rows]


def record_probe_attempted_dispatch(
    conn: sqlite3.Connection,
    probe_id: int,
    *,
    job_id: int,
) -> ExplorationProbe:
    """Persist the attempted dispatched_job_id WITHOUT changing probe status.

    Called immediately after a dispatch job is created, before collection runs.
    If collection fails, the probe stays SELECTED (safe to retry) but the
    attempted job ID is stored for operator audit.
    """
    conn.execute(
        "UPDATE market_exploration_probes SET dispatched_job_id = ? WHERE id = ?",
        (job_id, probe_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM market_exploration_probes WHERE id = ?", (probe_id,)
    ).fetchone()
    return _row_to_probe(row)


def list_prior_market_region_labels(
    conn: sqlite3.Connection,
    channel_id: int | None,
) -> list[str]:
    """Return distinct market_region labels from prior run decisions for this channel.

    Stored in decision_reason as 'market_region:<label>' by the cold-start planner.
    Used to populate the prompt's prior_regions context.
    """
    if channel_id is None:
        rows = conn.execute(
            """
            SELECT DISTINCT normalized_query
              FROM market_exploration_probes
             WHERE channel_id IS NULL
               AND probe_type = 'market_region'
               AND status IN ('selected', 'dispatched', 'deferred')
            ORDER BY created_at DESC
            LIMIT 30
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT DISTINCT normalized_query
              FROM market_exploration_probes
             WHERE channel_id = ?
               AND probe_type = 'market_region'
               AND status IN ('selected', 'dispatched', 'deferred')
            ORDER BY created_at DESC
            LIMIT 30
            """,
            (channel_id,),
        ).fetchall()
    return [r[0] for r in rows]
