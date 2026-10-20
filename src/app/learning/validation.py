"""Phase 11 — input validation for the Learning & Optimization Engine."""

from __future__ import annotations

import json
import sqlite3

from app.analytics.constants import (
    METRIC_AVERAGE_VIEW_DURATION,
    METRIC_LIKES,
    METRIC_SHARES,
    METRIC_SUBSCRIBERS_GAINED,
    METRIC_VIEWS,
    METRIC_WATCH_TIME_SECONDS,
)
from app.learning.constants import (
    CONFIDENCE_LEVELS,
    EVIDENCE_CLASSIFICATIONS,
    MIN_VIEWS_FOR_LEARNING,
    RECOMMENDATION_DOMAINS,
    RECOMMENDATION_STATUSES,
    RECOMMENDATION_STRENGTHS,
    RECOMMENDATION_SUBSYSTEMS,
    REVIEW_ELIGIBLE_STATUSES,
    REVIEW_EVENT_TYPES,
    STATUS_SUPERSEDED,
    SUBSCRIBER_LOSS_THRESHOLD,
    SUBSYSTEM_TOPIC_SELECTION,
)
from app.learning.errors import (
    InvalidConfidenceLevelError,
    InvalidConfidenceScoreError,
    InvalidEvidenceClassificationError,
    InvalidRecommendationDomainError,
    InvalidRecommendationStatusError,
    InvalidRecommendationStrengthError,
    InvalidRecommendationSubsystemError,
    InvalidReviewEventTypeError,
    NotesRequiredError,
    RecommendationNotReviewableError,
    ReviewerRequiredError,
)
from app.learning.models import OptimizationRecommendation

# Every measure produced by a REQUIRE_VIEWS-gated generator
# (recommendations.py:785-792): retention, engagement, watch_time,
# subscribers, shares. METRIC_CTR (REQUIRE_CTR_DATA) is deliberately not
# replayed here — no known historical artifact implicates it (Phase 18F).
_VIEWS_GATED_MEASURES = frozenset(
    {
        METRIC_AVERAGE_VIEW_DURATION,
        METRIC_LIKES,
        METRIC_WATCH_TIME_SECONDS,
        METRIC_SUBSCRIBERS_GAINED,
        METRIC_SHARES,
    }
)


def validate_evidence_classification(classification: str) -> None:
    if classification not in EVIDENCE_CLASSIFICATIONS:
        raise InvalidEvidenceClassificationError(
            f"Invalid evidence_classification {classification!r}. "
            f"Valid: {sorted(EVIDENCE_CLASSIFICATIONS)}"
        )


def validate_recommendation_strength(strength: str) -> None:
    if strength not in RECOMMENDATION_STRENGTHS:
        raise InvalidRecommendationStrengthError(
            f"Invalid recommendation_strength {strength!r}. "
            f"Valid: {sorted(RECOMMENDATION_STRENGTHS)}"
        )


def validate_domain(domain: str) -> None:
    if domain not in RECOMMENDATION_DOMAINS:
        raise InvalidRecommendationDomainError(
            f"Invalid domain {domain!r}. Valid: {sorted(RECOMMENDATION_DOMAINS)}"
        )


def validate_subsystem(subsystem: str) -> None:
    if subsystem not in RECOMMENDATION_SUBSYSTEMS:
        raise InvalidRecommendationSubsystemError(
            f"Invalid subsystem {subsystem!r}. Valid: {sorted(RECOMMENDATION_SUBSYSTEMS)}"
        )


def validate_confidence_level(confidence: str) -> None:
    if confidence not in CONFIDENCE_LEVELS:
        raise InvalidConfidenceLevelError(
            f"Invalid confidence {confidence!r}. Valid: {sorted(CONFIDENCE_LEVELS)}"
        )


def validate_confidence_score(score: float) -> None:
    if not (0.0 <= score <= 1.0):
        raise InvalidConfidenceScoreError(f"confidence_score must be in [0.0, 1.0]; got {score}")


def validate_recommendation_status(status: str) -> None:
    if status not in RECOMMENDATION_STATUSES:
        raise InvalidRecommendationStatusError(
            f"Invalid status {status!r}. Valid: {sorted(RECOMMENDATION_STATUSES)}"
        )


def validate_review_event_type(event_type: str) -> None:
    if event_type not in REVIEW_EVENT_TYPES:
        raise InvalidReviewEventTypeError(
            f"Invalid event_type {event_type!r}. Valid: {sorted(REVIEW_EVENT_TYPES)}"
        )


def validate_review_eligible(
    recommendation: OptimizationRecommendation,
) -> None:
    if recommendation.status == STATUS_SUPERSEDED:
        raise RecommendationNotReviewableError(
            f"Recommendation {recommendation.id} is superseded and cannot be reviewed."
        )
    if recommendation.status not in REVIEW_ELIGIBLE_STATUSES:
        raise RecommendationNotReviewableError(
            f"Recommendation {recommendation.id} has status {recommendation.status!r} "
            f"which does not accept review events."
        )


def validate_review_event(
    event_type: str,
    reviewer: str,
    notes: str,
) -> None:
    validate_review_event_type(event_type)
    if event_type in {"accepted", "rejected"} and not reviewer.strip():
        raise ReviewerRequiredError(f"A reviewer name is required for {event_type!r} events.")
    if event_type == "rejected" and not notes.strip():
        raise NotesRequiredError("Notes are required for 'rejected' events.")


def _lifetime_views_as_of(
    conn: sqlite3.Connection, publication_id: int, as_of: str
) -> float | None:
    """The views figure genuinely on record at `as_of`.

    Deliberately reads analytics_metrics, not analytics_aggregates:
    analytics_aggregates is UPSERTed in place per (publication_id, provider,
    period_type, period_key, metric_name) — app.analytics.repository's
    upsert_aggregate updates metric_value on conflict but never touches
    created_at, so a single lifetime-views row's `created_at` reflects only
    when it was first computed, not when it was last updated. Filtering that
    table by created_at <= as_of would therefore silently return today's
    live value, not the value genuinely available at generation time —
    exactly the failure mode this check exists to avoid.

    analytics_metrics is immutable and append-only (one row per snapshot
    ingestion, see app.analytics.repository.create_metric's docstring), so
    it is the one table that actually preserves a point-in-time trail.

    Seed rows are excluded via the same `input_hash NOT LIKE 'seed-%'`
    convention used everywhere else in the learning subsystem (see
    maturity.py, cross_publication.py) — no separate seed-marker convention
    exists for this table, and none should be invented.

    A single ingested reading equals the true lifetime total only while the
    publication has exactly one period_start group — which is a mathematical
    consequence of app.publishing.constants.PUB_TRANSITIONS: PUB_STATUS_
    PUBLISHED transitions only to PUB_STATUS_DELETED, never back to
    UPLOADED/SCHEDULED, and no "republish" code path exists anywhere in this
    codebase (grepped) to re-set publications.published_at once set. Since
    ingestion seeds analytics_metrics.period_start from published_at
    (app.analytics.auto_observer), published_at's one-way-terminal state
    machine guarantees a single period_start group per publication today.
    This is checked, not assumed: if it is ever violated (a future republish
    feature, a data anomaly), a naive single-row read could silently pick
    from the wrong window and either under- or over-count — so this function
    fails closed (returns None, treated as insufficient evidence) rather
    than guess at cross-group aggregation semantics it hasn't been asked to
    implement (Phase 18F closure).
    """
    rows = conn.execute(
        """SELECT metric_value, period_start FROM analytics_metrics
           WHERE publication_id = ? AND metric_name = ?
             AND input_hash NOT LIKE 'seed-%' AND created_at <= ?
           ORDER BY created_at DESC""",
        (publication_id, METRIC_VIEWS, as_of),
    ).fetchall()
    if not rows:
        return None
    if len({r["period_start"] for r in rows}) > 1:
        return None
    return float(rows[0]["metric_value"])


def compute_evidence_validity(
    conn: sqlite3.Connection, rec: OptimizationRecommendation
) -> tuple[bool, str | None]:
    """Read-only, deterministic re-check: would this recommendation's own
    persisted evidence still satisfy the CURRENT generator's sufficiency and
    threshold rules?

    Does not mutate rec.status — an accepted recommendation whose evidence
    no longer validates stays accepted (the human decision is preserved) but
    is flagged so it is not presented as current channel knowledge. See
    Phase 18F.
    """
    try:
        evidence = json.loads(rec.evidence_json)
    except (json.JSONDecodeError, TypeError):
        return True, None  # cannot evaluate — do not invent a false negative

    # Check 1 — sufficiency currency: replay today's REQUIRE_VIEWS threshold
    # against the views value actually on record when this was generated.
    if rec.measure in _VIEWS_GATED_MEASURES and rec.publication_id is not None:
        views = _lifetime_views_as_of(conn, rec.publication_id, rec.created_at)
        if views is None or views < MIN_VIEWS_FOR_LEARNING:
            return False, (
                f"Lifetime views on record when this was generated "
                f"({views if views is not None else 0:.0f}) were below the current "
                f"minimum sufficiency threshold ({MIN_VIEWS_FOR_LEARNING:.0f}) "
                "required for this recommendation type"
            )

    # Check 2 — subscriber-loss threshold currency: SUBSCRIBER_LOSS_THRESHOLD
    # comparison was fixed from `<=` to `<` (net == 0 is no longer "loss").
    # Re-evaluate the persisted observed_value against the current operator.
    if (
        rec.subsystem == SUBSYSTEM_TOPIC_SELECTION
        and rec.measure == METRIC_SUBSCRIBERS_GAINED
        and "loss" in rec.title.lower()
        and evidence
    ):
        observed = evidence[0].get("observed_value")
        if observed is not None and not (observed < SUBSCRIBER_LOSS_THRESHOLD):
            return False, (
                f"Net subscriber change ({observed:+.0f}) no longer meets the current "
                f"loss threshold (< {SUBSCRIBER_LOSS_THRESHOLD:.0f})"
            )

    return True, None
