"""Phase 6 M6.1 production plan repository.

All writes that span multiple tables use a SAVEPOINT so that any failure
rolls back the entire unit of work atomically.

SAVEPOINTs used:
  create_pp   — insert plan → segments → citations
  approve_pp  — supersede prior active → set approved → insert review event
  reject_pp   — set rejected → insert review event
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from app.production.constants import REJECTION_REASON_CODE_REQUIRING_NOTES, REJECTION_REASON_CODES
from app.production.errors import (
    DuplicateInputHashError,
    IllegalTransitionError,
    InvalidReasonCodeError,
    NoApprovedProductionPlanError,
    NoPlanError,
)
from app.production.models import (
    ApprovedProductionPlan,
    ProductionPlan,
    ProductionPlanDraft,
    ProductionPlanReviewEvent,
    ProductionSegment,
    ProductionSegmentCitation,
    ProductionSegmentWithCitations,
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# Read: single plan
# ---------------------------------------------------------------------------


def get_production_plan_by_id(conn: sqlite3.Connection, plan_id: int) -> ProductionPlan | None:
    row = conn.execute("SELECT * FROM production_plans WHERE id = ?", (plan_id,)).fetchone()
    return ProductionPlan.from_row(row) if row else None


def get_production_plan_by_input_hash(
    conn: sqlite3.Connection, *, script_id: int, input_hash: str
) -> ProductionPlan | None:
    row = conn.execute(
        "SELECT * FROM production_plans WHERE script_id = ? AND input_hash = ?",
        (script_id, input_hash),
    ).fetchone()
    return ProductionPlan.from_row(row) if row else None


def get_active_approved_production_plan(
    conn: sqlite3.Connection, topic_id: int
) -> ProductionPlan | None:
    row = conn.execute(
        """
        SELECT * FROM production_plans
         WHERE topic_id = ?
           AND status = 'approved'
           AND superseded_at IS NULL
           AND experiment_id IS NULL
        """,
        (topic_id,),
    ).fetchone()
    return ProductionPlan.from_row(row) if row else None


def require_active_approved_production_plan(
    conn: sqlite3.Connection, topic_id: int
) -> ProductionPlan:
    plan = get_active_approved_production_plan(conn, topic_id)
    if plan is None:
        raise NoApprovedProductionPlanError(
            f"No active approved production plan for topic_id={topic_id}"
        )
    return plan


# ---------------------------------------------------------------------------
# Read: segments and citations
# ---------------------------------------------------------------------------


def get_production_segments(conn: sqlite3.Connection, plan_id: int) -> list[ProductionSegment]:
    rows = conn.execute(
        "SELECT * FROM production_segments WHERE plan_id = ? ORDER BY segment_index",
        (plan_id,),
    ).fetchall()
    return [ProductionSegment.from_row(r) for r in rows]


def get_production_segment_citations(
    conn: sqlite3.Connection, segment_id: int
) -> list[ProductionSegmentCitation]:
    rows = conn.execute(
        """
        SELECT * FROM production_segment_citations
         WHERE segment_id = ?
         ORDER BY citation_order
        """,
        (segment_id,),
    ).fetchall()
    return [ProductionSegmentCitation.from_row(r) for r in rows]


def get_approved_production_plan_full(
    conn: sqlite3.Connection, topic_id: int
) -> ApprovedProductionPlan | None:
    plan = get_active_approved_production_plan(conn, topic_id)
    if plan is None:
        return None
    segments_raw = get_production_segments(conn, plan.id)
    segments_with_cits: list[ProductionSegmentWithCitations] = []
    for seg in segments_raw:
        citations = get_production_segment_citations(conn, seg.id)
        segments_with_cits.append(
            ProductionSegmentWithCitations(
                segment_id=seg.id,
                plan_id=seg.plan_id,
                segment_index=seg.segment_index,
                section_index=seg.section_index,
                section_type=seg.section_type,
                narration_text=seg.narration_text,
                estimated_duration_s=seg.estimated_duration_s,
                estimated_word_count=seg.estimated_word_count,
                citations=citations,
            )
        )
    return ApprovedProductionPlan(
        plan_id=plan.id,
        topic_id=plan.topic_id,
        script_id=plan.script_id,
        script_version=plan.script_version,
        input_hash=plan.input_hash,
        script_body_hash=plan.script_body_hash,
        plan_schema_version=plan.plan_schema_version,
        renderer_version=plan.renderer_version,
        duration_algorithm_version=plan.duration_algorithm_version,
        title=plan.title,
        format=plan.format,
        total_estimated_duration_s=plan.total_estimated_duration_s,
        total_word_count=plan.total_word_count,
        warnings=plan.warnings,
        requires_evidence_review=plan.requires_evidence_review,
        evidence_hash=plan.evidence_hash,
        generation_run_id=plan.generation_run_id,
        experiment_id=plan.experiment_id,
        approved_at=plan.approved_at or _now(),
        segments=segments_with_cits,
    )


# ---------------------------------------------------------------------------
# Read: list
# ---------------------------------------------------------------------------


def list_production_plans(conn: sqlite3.Connection, topic_id: int) -> list[ProductionPlan]:
    rows = conn.execute(
        "SELECT * FROM production_plans WHERE topic_id = ? ORDER BY created_at DESC",
        (topic_id,),
    ).fetchall()
    return [ProductionPlan.from_row(r) for r in rows]


def list_production_plan_review_events(
    conn: sqlite3.Connection, plan_id: int
) -> list[ProductionPlanReviewEvent]:
    rows = conn.execute(
        "SELECT * FROM production_plan_review_events WHERE plan_id = ? ORDER BY created_at",
        (plan_id,),
    ).fetchall()
    return [ProductionPlanReviewEvent.from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Write: create
# ---------------------------------------------------------------------------


def create_production_plan(conn: sqlite3.Connection, draft: ProductionPlanDraft) -> ProductionPlan:
    """Insert plan + segments + citations atomically.

    Idempotent: if (script_id, input_hash) already exists, raises DuplicateInputHashError.
    """
    now = _now()
    warnings_json = json.dumps(draft.warnings)

    conn.execute("SAVEPOINT create_pp")
    try:
        cur = conn.execute(
            """
            INSERT INTO production_plans (
                topic_id, script_id, script_version,
                input_hash, script_body_hash,
                plan_schema_version, renderer_version, duration_algorithm_version,
                title, format,
                total_estimated_duration_s, total_word_count,
                warnings_json,
                requires_evidence_review, evidence_hash,
                generation_run_id, experiment_id,
                status, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'draft',?,?)
            """,
            (
                draft.topic_id,
                draft.script_id,
                draft.script_version,
                draft.input_hash,
                draft.script_body_hash,
                draft.plan_schema_version,
                draft.renderer_version,
                draft.duration_algorithm_version,
                draft.title,
                draft.format,
                draft.total_estimated_duration_s,
                draft.total_word_count,
                warnings_json,
                int(draft.requires_evidence_review),
                draft.evidence_hash,
                draft.generation_run_id,
                draft.experiment_id,
                now,
                now,
            ),
        )
        plan_id = cur.lastrowid

        for seg in draft.segments:
            seg_cur = conn.execute(
                """
                INSERT INTO production_segments (
                    plan_id, segment_index, section_index, section_type,
                    narration_text, estimated_duration_s, estimated_word_count,
                    created_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    plan_id,
                    seg.segment_index,
                    seg.section_index,
                    seg.section_type,
                    seg.narration_text,
                    seg.estimated_duration_s,
                    seg.estimated_word_count,
                    now,
                ),
            )
            segment_id = seg_cur.lastrowid
            for order, claim_id in enumerate(seg.citation_claim_ids):
                conn.execute(
                    """
                    INSERT INTO production_segment_citations
                        (segment_id, claim_id, citation_order, created_at)
                    VALUES (?,?,?,?)
                    """,
                    (segment_id, claim_id, order, now),
                )

        conn.execute("RELEASE create_pp")
    except sqlite3.IntegrityError as exc:
        conn.execute("ROLLBACK TO create_pp")
        conn.execute("RELEASE create_pp")
        if "UNIQUE constraint failed" in str(exc) and "input_hash" in str(exc):
            raise DuplicateInputHashError(
                f"plan already exists for script_id={draft.script_id} input_hash={draft.input_hash}"
            ) from exc
        raise

    plan = get_production_plan_by_id(conn, plan_id)
    assert plan is not None
    return plan


def get_or_create_production_plan(
    conn: sqlite3.Connection, draft: ProductionPlanDraft
) -> tuple[ProductionPlan, bool]:
    """Return (plan, created). If a plan with this (script_id, input_hash) already exists,
    return it without inserting. created=True means a new row was inserted.
    """
    existing = get_production_plan_by_input_hash(
        conn, script_id=draft.script_id, input_hash=draft.input_hash
    )
    if existing is not None:
        return existing, False
    plan = create_production_plan(conn, draft)
    return plan, True


# ---------------------------------------------------------------------------
# Write: approve
# ---------------------------------------------------------------------------


def approve_production_plan(
    conn: sqlite3.Connection,
    plan_id: int,
    *,
    actor: str | None = None,
    model: str | None = None,
    prompt_hash: str | None = None,
) -> ProductionPlan:
    """Approve a draft plan.

    SAVEPOINT sequence:
      1. Fetch target plan — must exist and be draft.
      2. Supersede any currently active normal approved plan for this topic.
      3. Set target to approved.
      4. Insert approved review event.
      5. RELEASE.
    """
    now = _now()

    conn.execute("SAVEPOINT approve_pp")
    try:
        row = conn.execute("SELECT * FROM production_plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            raise NoPlanError(f"production plan id={plan_id} not found")
        plan = ProductionPlan.from_row(row)

        if plan.status != "draft":
            raise IllegalTransitionError(
                f"plan id={plan_id} is {plan.status!r}; only draft plans can be approved"
            )

        # Supersede any active normal approved plan for this topic
        conn.execute(
            """
            UPDATE production_plans
               SET superseded_at = ?, updated_at = ?
             WHERE topic_id = ?
               AND status = 'approved'
               AND superseded_at IS NULL
               AND experiment_id IS NULL
            """,
            (now, now, plan.topic_id),
        )

        conn.execute(
            """
            UPDATE production_plans
               SET status = 'approved', approved_at = ?, updated_at = ?
             WHERE id = ?
            """,
            (now, now, plan_id),
        )

        conn.execute(
            """
            INSERT INTO production_plan_review_events
                (plan_id, topic_id, script_id, evidence_hash,
                 model, prompt_hash, experiment_id,
                 decision, reason_code, notes, actor, created_at)
            VALUES (?,?,?,?,?,?,?,'approved',NULL,NULL,?,?)
            """,
            (
                plan_id,
                plan.topic_id,
                plan.script_id,
                plan.evidence_hash,
                model,
                prompt_hash,
                plan.experiment_id,
                actor,
                now,
            ),
        )

        conn.execute("RELEASE approve_pp")
    except Exception:
        conn.execute("ROLLBACK TO approve_pp")
        conn.execute("RELEASE approve_pp")
        raise

    approved = get_production_plan_by_id(conn, plan_id)
    assert approved is not None
    return approved


# ---------------------------------------------------------------------------
# Write: reject
# ---------------------------------------------------------------------------


def reject_production_plan(
    conn: sqlite3.Connection,
    plan_id: int,
    *,
    reason_code: str,
    notes: str | None = None,
    actor: str | None = None,
    model: str | None = None,
    prompt_hash: str | None = None,
) -> ProductionPlan:
    """Reject a draft plan (terminal transition).

    SAVEPOINT sequence:
      1. Validate reason_code.
      2. Fetch target — must be draft.
      3. Set rejected.
      4. Insert rejected review event.
      5. RELEASE.

    The active approved plan (if any) is NOT touched.
    """
    if reason_code not in REJECTION_REASON_CODES:
        raise InvalidReasonCodeError(
            f"unknown reason_code={reason_code!r}; valid: {sorted(REJECTION_REASON_CODES)}"
        )
    if reason_code == REJECTION_REASON_CODE_REQUIRING_NOTES and not notes:
        raise InvalidReasonCodeError(f"reason_code={reason_code!r} requires non-empty notes")

    now = _now()

    conn.execute("SAVEPOINT reject_pp")
    try:
        row = conn.execute("SELECT * FROM production_plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            raise NoPlanError(f"production plan id={plan_id} not found")
        plan = ProductionPlan.from_row(row)

        if plan.status != "draft":
            raise IllegalTransitionError(
                f"plan id={plan_id} is {plan.status!r}; only draft plans can be rejected"
            )

        conn.execute(
            """
            UPDATE production_plans
               SET status = 'rejected', rejected_at = ?, updated_at = ?
             WHERE id = ?
            """,
            (now, now, plan_id),
        )

        conn.execute(
            """
            INSERT INTO production_plan_review_events
                (plan_id, topic_id, script_id, evidence_hash,
                 model, prompt_hash, experiment_id,
                 decision, reason_code, notes, actor, created_at)
            VALUES (?,?,?,?,?,?,?,'rejected',?,?,?,?)
            """,
            (
                plan_id,
                plan.topic_id,
                plan.script_id,
                plan.evidence_hash,
                model,
                prompt_hash,
                plan.experiment_id,
                reason_code,
                notes,
                actor,
                now,
            ),
        )

        conn.execute("RELEASE reject_pp")
    except Exception:
        conn.execute("ROLLBACK TO reject_pp")
        conn.execute("RELEASE reject_pp")
        raise

    rejected = get_production_plan_by_id(conn, plan_id)
    assert rejected is not None
    return rejected
