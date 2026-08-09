"""Phase 10 analytics repository — all SQLite persistence operations.

All writes are append-only.  Historical metrics are never updated or deleted.
Corrections create new rows.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from app.analytics.constants import ANALYTICS_ENGINE_VERSION, ANALYTICS_SCHEMA_VERSION
from app.analytics.errors import (
    DuplicateSnapshotError,
    MetricNotFoundError,
    ReviewEventNotFoundError,
    SnapshotNotFoundError,
)
from app.analytics.models import (
    AnalyticsAggregate,
    AnalyticsMetric,
    AnalyticsReviewEvent,
    AnalyticsSnapshot,
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


# ── Snapshots ─────────────────────────────────────────────────────────────────


def create_snapshot(
    conn: sqlite3.Connection,
    *,
    publication_id: int,
    publishing_plan_id: int,
    publishing_job_id: int,
    render_manifest_id: int,
    scene_manifest_id: int,
    production_plan_id: int,
    script_id: int,
    topic_id: int,
    narration_run_id: int,
    caption_run_id: int,
    experiment_id: str | None,
    provider: str,
    provider_version: str,
    adapter_version: str,
    raw_metrics: dict[str, object],
    input_hash: str,
    period_start: str | None = None,
    period_end: str | None = None,
    is_period_complete: bool = False,
    currency_code: str | None = None,
    db_schema_version: int = 16,
) -> AnalyticsSnapshot:
    """Insert a new analytics snapshot. Raises DuplicateSnapshotError on hash collision."""
    now = _now()
    try:
        cur = conn.execute(
            """
            INSERT INTO analytics_snapshots (
                publication_id, publishing_plan_id, publishing_job_id,
                render_manifest_id, scene_manifest_id, production_plan_id,
                script_id, topic_id, narration_run_id, caption_run_id,
                experiment_id, provider, provider_version, adapter_version,
                engine_version, analytics_schema_version, db_schema_version,
                input_hash, raw_metrics_json,
                period_start, period_end,
                is_period_complete, currency_code,
                ingested_at, created_at
            ) VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            (
                publication_id,
                publishing_plan_id,
                publishing_job_id,
                render_manifest_id,
                scene_manifest_id,
                production_plan_id,
                script_id,
                topic_id,
                narration_run_id,
                caption_run_id,
                experiment_id,
                provider,
                provider_version,
                adapter_version,
                ANALYTICS_ENGINE_VERSION,
                ANALYTICS_SCHEMA_VERSION,
                db_schema_version,
                input_hash,
                json.dumps(raw_metrics),
                period_start,
                period_end,
                1 if is_period_complete else 0,
                currency_code,
                now,
                now,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise DuplicateSnapshotError(input_hash) from exc
        raise
    return get_snapshot(conn, cur.lastrowid)  # type: ignore[arg-type]


def get_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> AnalyticsSnapshot:
    """Return an analytics snapshot by ID. Raises SnapshotNotFoundError if missing."""
    row = conn.execute("SELECT * FROM analytics_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    if row is None:
        raise SnapshotNotFoundError(snapshot_id)
    return AnalyticsSnapshot.from_row(row)


def get_snapshot_by_hash(conn: sqlite3.Connection, input_hash: str) -> AnalyticsSnapshot | None:
    """Return a snapshot by input_hash, or None if not found."""
    row = conn.execute(
        "SELECT * FROM analytics_snapshots WHERE input_hash = ?", (input_hash,)
    ).fetchone()
    return AnalyticsSnapshot.from_row(row) if row else None


def list_snapshots(
    conn: sqlite3.Connection,
    *,
    publication_id: int | None = None,
    topic_id: int | None = None,
    provider: str | None = None,
    limit: int = 50,
) -> list[AnalyticsSnapshot]:
    """Return analytics snapshots, newest first."""
    clauses = []
    params: list[object] = []
    if publication_id is not None:
        clauses.append("publication_id = ?")
        params.append(publication_id)
    if topic_id is not None:
        clauses.append("topic_id = ?")
        params.append(topic_id)
    if provider is not None:
        clauses.append("provider = ?")
        params.append(provider)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM analytics_snapshots {where} ORDER BY id DESC LIMIT ?",
        params,
    ).fetchall()
    return [AnalyticsSnapshot.from_row(r) for r in rows]


# ── Metrics ───────────────────────────────────────────────────────────────────


def create_metric(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    publication_id: int,
    topic_id: int,
    provider: str,
    metric_name: str,
    metric_value: float,
    period_start: str | None,
    period_end: str | None,
    input_hash: str,
) -> AnalyticsMetric:
    """Insert a normalized analytics metric (immutable)."""
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO analytics_metrics (
            snapshot_id, publication_id, topic_id, provider,
            metric_name, metric_value, period_start, period_end,
            input_hash, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            snapshot_id,
            publication_id,
            topic_id,
            provider,
            metric_name,
            metric_value,
            period_start,
            period_end,
            input_hash,
            now,
        ),
    )
    conn.commit()
    return get_metric(conn, cur.lastrowid)  # type: ignore[arg-type]


def get_metric(conn: sqlite3.Connection, metric_id: int) -> AnalyticsMetric:
    """Return a metric by ID. Raises MetricNotFoundError if missing."""
    row = conn.execute("SELECT * FROM analytics_metrics WHERE id = ?", (metric_id,)).fetchone()
    if row is None:
        raise MetricNotFoundError(metric_id)
    return AnalyticsMetric.from_row(row)


def list_metrics_for_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> list[AnalyticsMetric]:
    """Return all metrics for a snapshot, ordered by metric_name."""
    rows = conn.execute(
        "SELECT * FROM analytics_metrics WHERE snapshot_id = ? ORDER BY metric_name",
        (snapshot_id,),
    ).fetchall()
    return [AnalyticsMetric.from_row(r) for r in rows]


def list_metrics_for_publication(
    conn: sqlite3.Connection,
    publication_id: int,
    *,
    metric_name: str | None = None,
) -> list[AnalyticsMetric]:
    """Return metrics for a publication in insertion order (oldest first).

    Aggregation relies on list ordering: rows[-1] is the most recently inserted
    metric, which is used by AGG_LAST and by base-period deduplication.
    """
    if metric_name is not None:
        rows = conn.execute(
            "SELECT * FROM analytics_metrics WHERE publication_id = ? AND metric_name = ?"
            " ORDER BY id ASC",
            (publication_id, metric_name),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM analytics_metrics WHERE publication_id = ? ORDER BY id ASC",
            (publication_id,),
        ).fetchall()
    return [AnalyticsMetric.from_row(r) for r in rows]


# ── Aggregates ────────────────────────────────────────────────────────────────


def upsert_aggregate(
    conn: sqlite3.Connection,
    *,
    publication_id: int,
    topic_id: int,
    provider: str,
    period_type: str,
    period_key: str,
    metric_name: str,
    metric_value: float,
    snapshot_count: int,
    calculation_method: str,
    source_snapshot_ids: list[int],
    input_hash: str,
    currency_code: str | None = None,
) -> AnalyticsAggregate:
    """Insert or replace an aggregate row (deterministic rollup result)."""
    import json as _json

    now = _now()
    source_json = _json.dumps(sorted(source_snapshot_ids))
    conn.execute(
        """
        INSERT INTO analytics_aggregates (
            publication_id, topic_id, provider,
            period_type, period_key, metric_name, metric_value,
            snapshot_count, calculation_method, currency_code,
            source_snapshot_ids_json, input_hash, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(publication_id, provider, period_type, period_key, metric_name)
        DO UPDATE SET
            metric_value             = excluded.metric_value,
            snapshot_count           = excluded.snapshot_count,
            calculation_method       = excluded.calculation_method,
            currency_code            = excluded.currency_code,
            source_snapshot_ids_json = excluded.source_snapshot_ids_json,
            input_hash               = excluded.input_hash
        """,
        (
            publication_id,
            topic_id,
            provider,
            period_type,
            period_key,
            metric_name,
            metric_value,
            snapshot_count,
            calculation_method,
            currency_code,
            source_json,
            input_hash,
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        """
        SELECT * FROM analytics_aggregates
        WHERE publication_id = ? AND provider = ?
          AND period_type = ? AND period_key = ? AND metric_name = ?
        """,
        (publication_id, provider, period_type, period_key, metric_name),
    ).fetchone()
    return AnalyticsAggregate.from_row(row)  # type: ignore[arg-type]


def list_aggregates(
    conn: sqlite3.Connection,
    *,
    publication_id: int | None = None,
    topic_id: int | None = None,
    provider: str | None = None,
    period_type: str | None = None,
    metric_name: str | None = None,
) -> list[AnalyticsAggregate]:
    """Return aggregates matching the given filters."""
    clauses = []
    params: list[object] = []
    for col, val in [
        ("publication_id", publication_id),
        ("topic_id", topic_id),
        ("provider", provider),
        ("period_type", period_type),
        ("metric_name", metric_name),
    ]:
        if val is not None:
            clauses.append(f"{col} = ?")
            params.append(val)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM analytics_aggregates {where} ORDER BY period_key DESC, metric_name",
        params,
    ).fetchall()
    return [AnalyticsAggregate.from_row(r) for r in rows]


# ── Review events ─────────────────────────────────────────────────────────────


def create_review_event(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    severity: str,
    notes: str,
    reviewer: str,
    input_hash: str,
) -> AnalyticsReviewEvent:
    """Append a review event (never updated or deleted)."""
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO analytics_review_events (
            snapshot_id, severity, notes, reviewer, input_hash, created_at
        ) VALUES (?,?,?,?,?,?)
        """,
        (snapshot_id, severity, notes, reviewer, input_hash, now),
    )
    conn.commit()
    return get_review_event(conn, cur.lastrowid)  # type: ignore[arg-type]


def get_review_event(conn: sqlite3.Connection, event_id: int) -> AnalyticsReviewEvent:
    """Return a review event by ID. Raises ReviewEventNotFoundError if missing."""
    row = conn.execute("SELECT * FROM analytics_review_events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        raise ReviewEventNotFoundError(event_id)
    return AnalyticsReviewEvent.from_row(row)


def list_review_events(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int | None = None,
    severity: str | None = None,
) -> list[AnalyticsReviewEvent]:
    """Return review events, newest first."""
    clauses = []
    params: list[object] = []
    if snapshot_id is not None:
        clauses.append("snapshot_id = ?")
        params.append(snapshot_id)
    if severity is not None:
        clauses.append("severity = ?")
        params.append(severity)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM analytics_review_events {where} ORDER BY id DESC",
        params,
    ).fetchall()
    return [AnalyticsReviewEvent.from_row(r) for r in rows]
