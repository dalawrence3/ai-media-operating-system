"""Evidence maturity / sufficiency evaluator for learning generators.

Maturity answers: "Do we have enough evidence to make an inference at all?"
This is distinct from confidence, which answers: "How strong is the evidence?"

A generator with immature analytics should be SKIPPED entirely.
Lowering confidence for immature data is not an adequate substitute —
a low-confidence recommendation still implies the inference was valid.

Design:
  - Each generator declares a MaturityRequirement at registration time.
  - The dispatch loop evaluates the requirement before calling the generator.
  - Insufficient maturity → GENERATOR_STATUS_SKIPPED with a diagnostic reason.
  - The learning run still completes successfully when generators are skipped.

Extensibility:
  MaturityRequirement is a frozen dataclass with additive optional fields.
  Future extensions (min_snapshots, min_days_since_publish, platform_filter,
  observation_state_required, etc.) add new fields without breaking callers
  that do not specify them.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from app.analytics.constants import METRIC_CTR, METRIC_VIEWS
from app.learning.constants import MIN_VIEWS_FOR_LEARNING


@dataclass(frozen=True)
class MaturityRequirement:
    """Declares what evidence a generator needs to make a legitimate inference.

    All specified criteria must pass (AND semantics).
    Omitting a field imposes no requirement on that dimension.

    Future fields are additive: existing requirements that omit them continue
    to pass unchanged, preserving backward compatibility.
    """

    min_lifetime_views: float | None = None
    """Minimum lifetime view count required.

    Generators that normalise by views or interpret audience behaviour require
    real audience exposure before their threshold comparisons are meaningful.
    """

    required_metrics: frozenset[str] = field(default_factory=frozenset)
    """Metric names that must have at least one non-seed lifetime aggregate row.

    If any named metric is absent, the generator is skipped with a diagnostic
    explaining which metrics are missing and how to obtain them.
    """


@dataclass(frozen=True)
class MaturityCheckResult:
    """Outcome of one evidence maturity evaluation."""

    sufficient: bool
    """True iff all criteria passed.  Only a True result allows generator execution."""

    reason: str
    """Human-readable diagnostic.  Logged to DB and shown in CLI output."""

    observed_views: float | None = None
    """Observed lifetime view count when min_lifetime_views check ran."""

    required_views: float | None = None
    """Required minimum when min_lifetime_views check ran."""

    missing_metrics: tuple[str, ...] = ()
    """Metric names absent from aggregates when required_metrics check ran."""

    source_snapshot_ids: tuple[int, ...] = ()
    """Snapshot IDs from the views aggregate row, for provenance tracing."""


# ── Internal DB helpers ───────────────────────────────────────────────────────


def _get_lifetime_views(
    conn: sqlite3.Connection,
    publication_id: int,
) -> tuple[float, list[int]]:
    """Return (views, snapshot_ids) from the most recent non-seed lifetime aggregate.

    Returns (0.0, []) when no qualifying aggregate exists — callers treat a
    missing views row identically to zero observed views.
    """
    row = conn.execute(
        """
        SELECT metric_value, source_snapshot_ids_json
          FROM analytics_aggregates
         WHERE publication_id = ?
           AND period_type    = 'lifetime'
           AND metric_name    = ?
           AND input_hash NOT LIKE 'seed-%'
         ORDER BY created_at DESC
         LIMIT 1
        """,
        (publication_id, METRIC_VIEWS),
    ).fetchone()
    if row is None:
        return 0.0, []
    try:
        snap_ids = json.loads(row["source_snapshot_ids_json"]) or []
    except (json.JSONDecodeError, TypeError):
        snap_ids = []
    return float(row["metric_value"]), snap_ids


def _aggregate_exists(
    conn: sqlite3.Connection,
    publication_id: int,
    metric_name: str,
) -> bool:
    """Return True iff a non-seed lifetime aggregate exists for metric_name."""
    row = conn.execute(
        """
        SELECT 1
          FROM analytics_aggregates
         WHERE publication_id = ?
           AND period_type    = 'lifetime'
           AND metric_name    = ?
           AND input_hash NOT LIKE 'seed-%'
         LIMIT 1
        """,
        (publication_id, metric_name),
    ).fetchone()
    return row is not None


# ── Evaluator ─────────────────────────────────────────────────────────────────


def evaluate_maturity(
    conn: sqlite3.Connection,
    publication_id: int,
    requirement: MaturityRequirement,
) -> MaturityCheckResult:
    """Evaluate whether persisted analytics meet the given maturity requirement.

    Criteria are evaluated in dependency order (most informative first):
      1. required_metrics — tells the operator exactly which data is missing.
      2. min_lifetime_views — gates view-dependent inference.

    Returns on the first failing criterion so the diagnostic is actionable.
    If no criteria are configured on the requirement, returns sufficient=True.
    """
    # ── 1. required metrics ──────────────────────────────────────────────────
    if requirement.required_metrics:
        missing = sorted(
            m
            for m in requirement.required_metrics
            if not _aggregate_exists(conn, publication_id, m)
        )
        if missing:
            return MaturityCheckResult(
                sufficient=False,
                reason=(
                    f"Required metric(s) not yet available: {', '.join(missing)}. "
                    "Ingest additional analytics snapshots before this generator "
                    "can evaluate."
                ),
                missing_metrics=tuple(missing),
            )

    # ── 2. minimum lifetime views ────────────────────────────────────────────
    if requirement.min_lifetime_views is not None:
        views, snap_ids = _get_lifetime_views(conn, publication_id)
        if views < requirement.min_lifetime_views:
            return MaturityCheckResult(
                sufficient=False,
                reason=(
                    f"Insufficient lifetime views for reliable inference: "
                    f"observed={views:.0f}, "
                    f"required>={requirement.min_lifetime_views:.0f}. "
                    "Re-run after more analytics have been collected."
                ),
                observed_views=views,
                required_views=requirement.min_lifetime_views,
                source_snapshot_ids=tuple(snap_ids),
            )

    return MaturityCheckResult(sufficient=True, reason="evidence sufficient")


# ── Standard requirement instances ───────────────────────────────────────────
# Import these into the generator dispatch table in recommendations.py.

REQUIRE_VIEWS = MaturityRequirement(min_lifetime_views=MIN_VIEWS_FOR_LEARNING)
"""Standard requirement for generators that normalise by or interpret views.

Used by: retention, engagement, watch_time, subscribers, shares.
"""

REQUIRE_CTR_DATA = MaturityRequirement(required_metrics=frozenset({METRIC_CTR}))
"""CTR generator: requires the CTR metric to be present in aggregates.

YouTube suppresses CTR below a privacy threshold regardless of view count.
The gating factor is metric availability, not views.
"""

REQUIRE_NONE = MaturityRequirement()
"""No maturity requirement.  Generator handles its own data-presence checks."""
