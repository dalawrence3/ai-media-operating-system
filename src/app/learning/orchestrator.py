"""Phase 11 — Learning & Optimization Engine orchestrator.

The orchestrator coordinates the full learning pipeline:
  1. Validate inputs and check for sufficient analytics data
  2. Create a learning_run record
  3. Query analytics aggregates for the target publication
  4. Build AnalyticsHandoff from existing analytics data
  5. Run all recommendation generators
  6. Persist recommendations
  7. Record review events (accept/reject)

The orchestrator reads from analytics tables but NEVER writes to them.
Only the learning_runs, optimization_recommendations, and
recommendation_review_events tables are written to.

Every recommendation remains in 'pending' status until a human explicitly
accepts or rejects it.  No recommendation is ever applied automatically.
"""

from __future__ import annotations

import sqlite3

from app.analytics.constants import (
    ANALYTICS_ENGINE_VERSION,
    ANALYTICS_SCHEMA_VERSION,
)
from app.analytics.models import (
    AnalyticsAggregate,
    AnalyticsHandoff,
    AnalyticsMetric,
    AnalyticsReviewEvent,
    AnalyticsSnapshot,
)
from app.learning.constants import (
    EVENT_ACCEPTED,
    EVENT_REJECTED,
    GENERATOR_STATUS_FAILED,
    GENERATOR_STATUS_SUCCEEDED,
    LEARNING_ALGORITHM_VERSION,
    LEARNING_ENGINE_VERSION,
    LEARNING_SCHEMA_VERSION,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_PARTIAL,
)
from app.learning.errors import InsufficientAnalyticsDataError
from app.learning.hashing import (
    LearningRunHashInput,
    ReviewEventHashInput,
    compute_learning_run_hash,
    compute_review_event_hash,
)
from app.learning.models import (
    OptimizationRecommendation,
    RecommendationReviewEvent,
    ReviewedOptimizationHandoff,
    ReviewedRecommendationItem,
    ReviewEventDraft,
)
from app.learning.recommendations import generate_all_recommendations
from app.learning.repository import (
    complete_learning_run,
    create_generator_result,
    create_learning_run,
    create_recommendation,
    create_review_event,
    fail_learning_run,
    find_active_recommendation_by_key,
    get_learning_run,
    get_recommendation,
    list_generator_results,
    list_learning_runs,
    list_recommendations,
    list_review_events,
    partial_learning_run,
    supersede_recommendation,
    update_recommendation_status,
)
from app.learning.validation import (
    validate_review_eligible,
    validate_review_event,
)


def _build_handoff_from_db(
    conn: sqlite3.Connection,
    publication_id: int,
    topic_id: int,
) -> AnalyticsHandoff:
    """Assemble an AnalyticsHandoff from persisted analytics rows.

    Reads analytics tables; does not call any provider.
    """
    from datetime import UTC, datetime

    # Fetch the most recent snapshot for this publication
    snapshots: list[AnalyticsSnapshot] = [
        AnalyticsSnapshot.from_row(r)
        for r in conn.execute(
            "SELECT * FROM analytics_snapshots WHERE publication_id = ? ORDER BY created_at ASC",
            (publication_id,),
        ).fetchall()
    ]
    if not snapshots:
        raise InsufficientAnalyticsDataError(
            f"No analytics snapshots found for publication_id={publication_id}. "
            "Ingest analytics data (ace analytics ingest) before running the optimizer."
        )

    latest = snapshots[-1]

    metrics: list[AnalyticsMetric] = [
        AnalyticsMetric.from_row(r)
        for r in conn.execute(
            "SELECT * FROM analytics_metrics WHERE publication_id = ? ORDER BY created_at ASC",
            (publication_id,),
        ).fetchall()
    ]

    aggregates: list[AnalyticsAggregate] = [
        AnalyticsAggregate.from_row(r)
        for r in conn.execute(
            "SELECT * FROM analytics_aggregates WHERE publication_id = ? ORDER BY created_at ASC",
            (publication_id,),
        ).fetchall()
    ]

    review_events: list[AnalyticsReviewEvent] = [
        AnalyticsReviewEvent.from_row(r)
        for r in conn.execute(
            "SELECT * FROM analytics_review_events WHERE snapshot_id IN "
            "(SELECT id FROM analytics_snapshots WHERE publication_id = ?) "
            "ORDER BY created_at ASC",
            (publication_id,),
        ).fetchall()
    ]

    # Resolve currency from aggregates
    currency_code: str | None = None
    for agg in aggregates:
        if agg.currency_code:
            currency_code = agg.currency_code
            break

    return AnalyticsHandoff(
        publication_id=publication_id,
        topic_id=topic_id,
        provider=latest.provider,
        publishing_plan_id=latest.publishing_plan_id,
        publishing_job_id=latest.publishing_job_id,
        render_manifest_id=latest.render_manifest_id,
        scene_manifest_id=latest.scene_manifest_id,
        production_plan_id=latest.production_plan_id,
        script_id=latest.script_id,
        narration_run_id=latest.narration_run_id,
        caption_run_id=latest.caption_run_id,
        experiment_id=latest.experiment_id,
        snapshots=snapshots,
        metrics=metrics,
        aggregates=aggregates,
        review_events=review_events,
        currency_code=currency_code,
        engine_version=ANALYTICS_ENGINE_VERSION,
        analytics_schema_version=ANALYTICS_SCHEMA_VERSION,
        handoff_created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
    )


def analyze_publication(
    conn: sqlite3.Connection,
    *,
    publication_id: int,
    topic_id: int,
) -> int:
    """Run the optimization engine for a single publication.

    Returns the learning_run_id.

    Steps:
    1. Build AnalyticsHandoff from persisted data.
    2. Compute a deterministic input_hash.
    3. Create learning_run row (status=running).
    4. Generate all recommendation drafts.
    5. Persist each draft.
    6. Complete the learning_run.

    On any failure the learning_run is marked failed and the error re-raised.
    """
    handoff = _build_handoff_from_db(conn, publication_id, topic_id)

    all_snapshot_ids = sorted({s.id for s in handoff.snapshots})
    run_hash_input = LearningRunHashInput(
        topic_id=topic_id,
        publication_id=publication_id,
        engine_version=LEARNING_ENGINE_VERSION,
        schema_version=LEARNING_SCHEMA_VERSION,
        algorithm_version=LEARNING_ALGORITHM_VERSION,
        snapshot_ids_sorted=all_snapshot_ids,
    )
    input_hash = compute_learning_run_hash(run_hash_input)

    run_id = create_learning_run(
        conn,
        topic_id=topic_id,
        publication_id=publication_id,
        input_hash=input_hash,
    )

    try:
        results = generate_all_recommendations(conn, handoff, run_id)
    except Exception as exc:
        fail_learning_run(conn, run_id, str(exc))
        raise

    # Persist generator outcome records.
    for gr in results.generator_results:
        create_generator_result(conn, run_id, gr)

    # Persist each draft with supersession check.
    persisted_count = 0
    for draft in results.drafts:
        existing = find_active_recommendation_by_key(
            conn,
            topic_id=draft.topic_id,
            publication_id=draft.publication_id,
            domain=draft.domain,
            subsystem=draft.subsystem,
            measure=draft.measure,
        )
        if existing is not None and existing.input_hash == draft.input_hash:
            # Identical recommendation already active — idempotent, skip.
            persisted_count += 1
            continue
        new_id = create_recommendation(conn, draft)
        if existing is not None:
            # Evidence changed — supersede the prior active recommendation.
            # Failed generators do not produce drafts, so only successfully
            # generated recommendations can trigger supersession.
            supersede_recommendation(conn, existing.id, new_id)
        persisted_count += 1

    # Determine final run status from generator outcomes.
    succeeded = sum(
        1 for gr in results.generator_results if gr.status == GENERATOR_STATUS_SUCCEEDED
    )
    failed = sum(1 for gr in results.generator_results if gr.status == GENERATOR_STATUS_FAILED)
    if failed == 0:
        final_status = RUN_STATUS_COMPLETED
    elif succeeded == 0:
        final_status = RUN_STATUS_FAILED
    else:
        final_status = RUN_STATUS_PARTIAL

    if final_status == RUN_STATUS_FAILED:
        fail_learning_run(conn, run_id, "All generators failed.")
    elif final_status == RUN_STATUS_PARTIAL:
        partial_learning_run(
            conn,
            run_id,
            publication_count=1,
            recommendation_count=persisted_count,
        )
    else:
        conn.commit()
        complete_learning_run(
            conn,
            run_id,
            publication_count=1,
            recommendation_count=persisted_count,
        )

    return run_id


def accept_recommendation(
    conn: sqlite3.Connection,
    recommendation_id: int,
    *,
    reviewer: str,
    notes: str = "",
    expected_outcome: str = "",
) -> RecommendationReviewEvent:
    """Record a human acceptance of a recommendation.

    The recommendation status transitions to 'accepted'.
    The review event is appended and the recommendation is never changed again
    by this function — a subsequent rejection would append another event.
    """
    rec = get_recommendation(conn, recommendation_id)
    validate_review_eligible(rec)
    validate_review_event(EVENT_ACCEPTED, reviewer, notes)

    event_hash = compute_review_event_hash(
        ReviewEventHashInput(
            recommendation_id=recommendation_id,
            topic_id=rec.topic_id,
            event_type=EVENT_ACCEPTED,
            reviewer=reviewer,
        )
    )
    draft = ReviewEventDraft(
        recommendation_id=recommendation_id,
        topic_id=rec.topic_id,
        event_type=EVENT_ACCEPTED,
        reviewer=reviewer,
        notes=notes,
        expected_outcome=expected_outcome,
        input_hash=event_hash,
    )
    event_id = create_review_event(conn, draft)
    update_recommendation_status(conn, recommendation_id, EVENT_ACCEPTED)
    conn.commit()

    rows = conn.execute(
        "SELECT * FROM recommendation_review_events WHERE id = ?", (event_id,)
    ).fetchone()
    return RecommendationReviewEvent.from_row(rows)


def reject_recommendation(
    conn: sqlite3.Connection,
    recommendation_id: int,
    *,
    reviewer: str,
    notes: str,
    expected_outcome: str = "",
) -> RecommendationReviewEvent:
    """Record a human rejection of a recommendation.

    The recommendation status transitions to 'rejected'.
    Notes are required — the rejection reason is a training signal.
    """
    rec = get_recommendation(conn, recommendation_id)
    validate_review_eligible(rec)
    validate_review_event(EVENT_REJECTED, reviewer, notes)

    event_hash = compute_review_event_hash(
        ReviewEventHashInput(
            recommendation_id=recommendation_id,
            topic_id=rec.topic_id,
            event_type=EVENT_REJECTED,
            reviewer=reviewer,
        )
    )
    draft = ReviewEventDraft(
        recommendation_id=recommendation_id,
        topic_id=rec.topic_id,
        event_type=EVENT_REJECTED,
        reviewer=reviewer,
        notes=notes,
        expected_outcome=expected_outcome,
        input_hash=event_hash,
    )
    event_id = create_review_event(conn, draft)
    update_recommendation_status(conn, recommendation_id, EVENT_REJECTED)
    conn.commit()

    rows = conn.execute(
        "SELECT * FROM recommendation_review_events WHERE id = ?", (event_id,)
    ).fetchone()
    return RecommendationReviewEvent.from_row(rows)


def get_run(conn: sqlite3.Connection, run_id: int):
    return get_learning_run(conn, run_id)


def list_runs(conn: sqlite3.Connection, topic_id: int | None = None, limit: int = 50):
    return list_learning_runs(conn, topic_id=topic_id, limit=limit)


def list_recs(
    conn: sqlite3.Connection,
    topic_id: int | None = None,
    publication_id: int | None = None,
    domain: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[OptimizationRecommendation]:
    return list_recommendations(
        conn,
        topic_id=topic_id,
        publication_id=publication_id,
        domain=domain,
        status=status,
        limit=limit,
    )


def list_events(
    conn: sqlite3.Connection,
    recommendation_id: int | None = None,
    topic_id: int | None = None,
    limit: int = 100,
) -> list[RecommendationReviewEvent]:
    return list_review_events(
        conn,
        recommendation_id=recommendation_id,
        topic_id=topic_id,
        limit=limit,
    )


def build_reviewed_handoff(
    conn: sqlite3.Connection,
    run_id: int,
) -> ReviewedOptimizationHandoff:
    """Build the frozen Phase 12 handoff from a completed learning run.

    This is the ONLY way Phase 12 should consume learning run output.
    It surfaces human-reviewed decisions without requiring Phase 12 to
    query raw learning tables.

    The handoff includes all recommendations (accepted, rejected, pending)
    along with their full review-event history and generator outcome records.
    """
    from datetime import UTC, datetime

    run = get_learning_run(conn, run_id)
    recs = list_recommendations(conn, topic_id=run.topic_id, limit=10000)
    # Filter to this run's recommendations
    run_recs = [r for r in recs if r.learning_run_id == run_id]

    gen_results = list_generator_results(conn, run_id)

    def _build_item(rec: OptimizationRecommendation) -> ReviewedRecommendationItem:
        events = list_review_events(conn, recommendation_id=rec.id)
        return ReviewedRecommendationItem.from_recommendation(rec, events)

    accepted = [_build_item(r) for r in run_recs if r.status == "accepted"]
    rejected = [_build_item(r) for r in run_recs if r.status == "rejected"]
    pending = [_build_item(r) for r in run_recs if r.status == "pending"]

    return ReviewedOptimizationHandoff(
        learning_run_id=run_id,
        topic_id=run.topic_id,
        publication_id=run.publication_id,
        run_status=run.status,
        accepted=accepted,
        rejected=rejected,
        pending=pending,
        generator_results=gen_results,
        engine_version=LEARNING_ENGINE_VERSION,
        schema_version=LEARNING_SCHEMA_VERSION,
        algorithm_version=LEARNING_ALGORITHM_VERSION,
        handoff_created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
    )
