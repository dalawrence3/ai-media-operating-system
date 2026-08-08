"""Phase 11 — Learning & Optimization Engine repository layer.

All writes are append-only.  Recommendations are superseded, not deleted.
Review events are never overwritten.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from app.learning.constants import (
    LEARNING_ENGINE_VERSION,
    LEARNING_SCHEMA_VERSION,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_PARTIAL,
    RUN_STATUS_RUNNING,
    STATUS_PENDING,
    STATUS_SUPERSEDED,
)
from app.learning.errors import (
    LearningRunNotFoundError,
    RecommendationNotFoundError,
)
from app.learning.models import GeneratorResult as GeneratorResultDraft
from app.learning.models import (
    LearningRun,
    LearningRunGeneratorResult,
    OptimizationRecommendation,
    RecommendationDraft,
    RecommendationReviewEvent,
    ReviewEventDraft,
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


# ── Learning run operations ───────────────────────────────────────────────────


def create_learning_run(
    conn: sqlite3.Connection,
    *,
    topic_id: int,
    publication_id: int | None,
    input_hash: str,
) -> int:
    """Insert a new learning_run row in 'running' state.  Returns the run ID."""
    cursor = conn.execute(
        """
        INSERT INTO learning_runs
            (topic_id, publication_id, publication_count, recommendation_count,
             status, engine_version, schema_version, input_hash, created_at)
        VALUES (?, ?, 0, 0, ?, ?, ?, ?, ?)
        """,
        (
            topic_id,
            publication_id,
            RUN_STATUS_RUNNING,
            LEARNING_ENGINE_VERSION,
            LEARNING_SCHEMA_VERSION,
            input_hash,
            _now(),
        ),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore[return-value]


def complete_learning_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    publication_count: int,
    recommendation_count: int,
) -> None:
    """Mark a learning run as completed with final counts."""
    conn.execute(
        """
        UPDATE learning_runs
           SET status               = ?,
               publication_count    = ?,
               recommendation_count = ?,
               completed_at         = ?
         WHERE id = ?
        """,
        (
            RUN_STATUS_COMPLETED,
            publication_count,
            recommendation_count,
            _now(),
            run_id,
        ),
    )
    conn.commit()


def partial_learning_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    publication_count: int,
    recommendation_count: int,
) -> None:
    """Mark a learning run as partial — some generators failed, some succeeded."""
    conn.execute(
        """
        UPDATE learning_runs
           SET status               = ?,
               publication_count    = ?,
               recommendation_count = ?,
               completed_at         = ?
         WHERE id = ?
        """,
        (
            RUN_STATUS_PARTIAL,
            publication_count,
            recommendation_count,
            _now(),
            run_id,
        ),
    )
    conn.commit()


def fail_learning_run(
    conn: sqlite3.Connection,
    run_id: int,
    error: str,
) -> None:
    """Mark a learning run as failed with an error message."""
    conn.execute(
        """
        UPDATE learning_runs
           SET status       = ?,
               error        = ?,
               completed_at = ?
         WHERE id = ?
        """,
        (RUN_STATUS_FAILED, error, _now(), run_id),
    )
    conn.commit()


def get_learning_run(conn: sqlite3.Connection, run_id: int) -> LearningRun:
    row = conn.execute("SELECT * FROM learning_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise LearningRunNotFoundError(f"No learning run with id={run_id}")
    return LearningRun.from_row(row)


def list_learning_runs(
    conn: sqlite3.Connection,
    topic_id: int | None = None,
    limit: int = 50,
) -> list[LearningRun]:
    if topic_id is not None:
        rows = conn.execute(
            "SELECT * FROM learning_runs WHERE topic_id = ? ORDER BY created_at DESC LIMIT ?",
            (topic_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM learning_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [LearningRun.from_row(r) for r in rows]


# ── Recommendation operations ─────────────────────────────────────────────────


def create_recommendation(
    conn: sqlite3.Connection,
    draft: RecommendationDraft,
) -> int:
    """Insert a recommendation row.  Returns the row ID."""
    evidence_json = json.dumps(
        [ev.to_dict() for ev in draft.evidence],
        sort_keys=True,
        separators=(",", ":"),
    )
    cursor = conn.execute(
        """
        INSERT INTO optimization_recommendations
            (learning_run_id, topic_id, publication_id,
             domain, subsystem, measure,
             title, explanation, expected_improvement,
             evidence_json, evidence_classification, recommendation_strength,
             confidence, confidence_score,
             affected_subsystem, subsystem_entity_type, subsystem_entity_id,
             experiment_id,
             engine_version, schema_version, input_hash,
             status, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            draft.learning_run_id,
            draft.topic_id,
            draft.publication_id,
            draft.domain,
            draft.subsystem,
            draft.measure,
            draft.title,
            draft.explanation,
            draft.expected_improvement,
            evidence_json,
            draft.evidence_classification,
            draft.recommendation_strength,
            draft.confidence,
            draft.confidence_score,
            draft.affected_subsystem,
            draft.subsystem_entity_type,
            draft.subsystem_entity_id,
            draft.experiment_id,
            draft.engine_version,
            draft.schema_version,
            draft.input_hash,
            STATUS_PENDING,
            _now(),
        ),
    )
    return cursor.lastrowid  # type: ignore[return-value]


def get_recommendation(
    conn: sqlite3.Connection, recommendation_id: int
) -> OptimizationRecommendation:
    row = conn.execute(
        "SELECT * FROM optimization_recommendations WHERE id = ?",
        (recommendation_id,),
    ).fetchone()
    if row is None:
        raise RecommendationNotFoundError(f"No recommendation with id={recommendation_id}")
    return OptimizationRecommendation.from_row(row)


def list_recommendations(
    conn: sqlite3.Connection,
    topic_id: int | None = None,
    publication_id: int | None = None,
    domain: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[OptimizationRecommendation]:
    clauses = []
    params: list[object] = []

    if topic_id is not None:
        clauses.append("topic_id = ?")
        params.append(topic_id)
    if publication_id is not None:
        clauses.append("publication_id = ?")
        params.append(publication_id)
    if domain is not None:
        clauses.append("domain = ?")
        params.append(domain)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)

    rows = conn.execute(
        f"SELECT * FROM optimization_recommendations {where} ORDER BY created_at DESC LIMIT ?",
        params,
    ).fetchall()
    return [OptimizationRecommendation.from_row(r) for r in rows]


def find_active_recommendation_by_key(
    conn: sqlite3.Connection,
    *,
    topic_id: int,
    publication_id: int | None,
    domain: str,
    subsystem: str,
    measure: str,
) -> OptimizationRecommendation | None:
    """Return the most recent non-superseded recommendation for the given key.

    Key = (topic_id, publication_id, domain, subsystem, measure).
    Returns None when no active recommendation exists.
    """
    row = conn.execute(
        """
        SELECT * FROM optimization_recommendations
         WHERE topic_id = ?
           AND (
               (? IS NULL AND publication_id IS NULL) OR
               (? IS NOT NULL AND publication_id = ?)
           )
           AND domain    = ?
           AND subsystem = ?
           AND measure   = ?
           AND status   != ?
         ORDER BY created_at DESC
         LIMIT 1
        """,
        (
            topic_id,
            publication_id,
            publication_id,
            publication_id,
            domain,
            subsystem,
            measure,
            STATUS_SUPERSEDED,
        ),
    ).fetchone()
    if row is None:
        return None
    return OptimizationRecommendation.from_row(row)


def supersede_recommendation(
    conn: sqlite3.Connection,
    old_id: int,
    new_id: int,
) -> None:
    """Mark old_id as superseded by new_id (append-only lifecycle).

    Supersession policy: the orchestrator supersedes an existing recommendation
    when a new learning run produces a recommendation with the same (topic_id,
    publication_id, domain, subsystem, measure) key but a different input_hash.
    The old row is preserved with status='superseded'; it is never deleted.
    Only the system triggers supersession; humans may only accept or reject.
    """
    conn.execute(
        """
        UPDATE optimization_recommendations
           SET status         = ?,
               superseded_at  = ?,
               superseded_by_id = ?
         WHERE id = ?
        """,
        (STATUS_SUPERSEDED, _now(), new_id, old_id),
    )


def update_recommendation_status(
    conn: sqlite3.Connection,
    recommendation_id: int,
    status: str,
) -> None:
    """Transition a recommendation status (used by review flow)."""
    conn.execute(
        "UPDATE optimization_recommendations SET status = ? WHERE id = ?",
        (status, recommendation_id),
    )


# ── Review event operations ───────────────────────────────────────────────────


def create_review_event(
    conn: sqlite3.Connection,
    draft: ReviewEventDraft,
) -> int:
    """Insert a review event row.  Returns the row ID."""
    cursor = conn.execute(
        """
        INSERT INTO recommendation_review_events
            (recommendation_id, topic_id, event_type,
             reviewer, notes, expected_outcome,
             input_hash, created_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            draft.recommendation_id,
            draft.topic_id,
            draft.event_type,
            draft.reviewer,
            draft.notes,
            draft.expected_outcome,
            draft.input_hash,
            _now(),
        ),
    )
    return cursor.lastrowid  # type: ignore[return-value]


def list_review_events(
    conn: sqlite3.Connection,
    recommendation_id: int | None = None,
    topic_id: int | None = None,
    limit: int = 100,
) -> list[RecommendationReviewEvent]:
    clauses = []
    params: list[object] = []

    if recommendation_id is not None:
        clauses.append("recommendation_id = ?")
        params.append(recommendation_id)
    if topic_id is not None:
        clauses.append("topic_id = ?")
        params.append(topic_id)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)

    rows = conn.execute(
        f"SELECT * FROM recommendation_review_events {where} ORDER BY created_at DESC LIMIT ?",
        params,
    ).fetchall()
    return [RecommendationReviewEvent.from_row(r) for r in rows]


# ── Generator result operations ───────────────────────────────────────────────


def create_generator_result(
    conn: sqlite3.Connection,
    run_id: int,
    result: GeneratorResultDraft,
) -> int:
    """Persist a single generator outcome.  Returns the row ID."""
    cursor = conn.execute(
        """
        INSERT INTO learning_run_generator_results
            (learning_run_id, generator_name, status,
             recommendation_count, error_message, created_at)
        VALUES (?,?,?,?,?,?)
        """,
        (
            run_id,
            result.generator_name,
            result.status,
            result.recommendation_count,
            result.error_message,
            _now(),
        ),
    )
    return cursor.lastrowid  # type: ignore[return-value]


def list_generator_results(
    conn: sqlite3.Connection,
    run_id: int,
) -> list[LearningRunGeneratorResult]:
    """Return all generator results for a learning run."""
    rows = conn.execute(
        "SELECT * FROM learning_run_generator_results WHERE learning_run_id = ? "
        "ORDER BY created_at ASC",
        (run_id,),
    ).fetchall()
    return [LearningRunGeneratorResult.from_row(r) for r in rows]
