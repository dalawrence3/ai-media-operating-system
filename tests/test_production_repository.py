"""Tests for Phase 6 M6.1 production plan repository."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core.database import open_db
from app.core.models import Script, ScriptStatus, Topic
from app.core.repository import create_script, create_topic
from app.production.constants import (
    PRODUCTION_DURATION_VERSION,
    PRODUCTION_PLAN_RENDERER_VERSION,
    PRODUCTION_PLAN_SCHEMA_VERSION,
)
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
    ProductionSegmentDraft,
)
from app.production.repository import (
    approve_production_plan,
    create_production_plan,
    get_active_approved_production_plan,
    get_approved_production_plan_full,
    get_or_create_production_plan,
    get_production_plan_by_id,
    get_production_plan_by_input_hash,
    get_production_segment_citations,
    get_production_segments,
    list_production_plan_review_events,
    list_production_plans,
    reject_production_plan,
    require_active_approved_production_plan,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "test.db")


def _topic(db: sqlite3.Connection, title: str = "Test Topic") -> Topic:
    return create_topic(db, Topic(title=title))


def _script(db: sqlite3.Connection, topic_id: int, version: int = 1) -> Script:
    s = create_script(
        db,
        Script(
            topic_id=topic_id,
            version=version,
            body="Script body.",
            status=ScriptStatus.draft,
        ),
    )
    db.commit()
    return s


def _seed_claim(db: sqlite3.Connection, topic_id: int) -> int:
    """Create the full FK chain (source → source_content → cer → claim) and return claim_id."""
    now = "2024-01-01T00:00:00"
    src_id = db.execute(
        "INSERT INTO sources (topic_id, kind, reference) VALUES (?, 'url', 'http://example.com')",
        (topic_id,),
    ).lastrowid
    sc_id = db.execute(
        """INSERT INTO source_contents
           (source_id, fetch_status, extraction_status, fetched_at)
           VALUES (?, 'ok', 'ok', ?)""",
        (src_id, now),
    ).lastrowid
    cer_id = db.execute(
        """INSERT INTO claim_extraction_runs
           (source_content_id, status, input_hash, total_chunk_count,
            prompt_name, prompt_version, model, provider, extraction_algo_version, started_at)
           VALUES (?, 'completed', ?, 1, '', '', 'fake', 'fake', 'v1', ?)""",
        (sc_id, "a" * 64, now),
    ).lastrowid
    claim_id = db.execute(
        """INSERT INTO claims
           (extraction_run_id, chunk_index, claim_text, claim_type,
            quote_support_status)
           VALUES (?, 0, 'Claim text.', 'factual', 'no_quote')""",
        (cer_id,),
    ).lastrowid
    db.commit()
    return claim_id


def _make_draft(
    topic_id: int,
    script_id: int,
    *,
    input_hash: str | None = None,
    segments: list[ProductionSegmentDraft] | None = None,
    warnings: list[str] | None = None,
) -> ProductionPlanDraft:
    if segments is None:
        segments = [
            ProductionSegmentDraft(
                segment_index=0,
                section_index=0,
                section_type="hook",
                narration_text="Hook narration.",
                estimated_duration_s=4,
                estimated_word_count=2,
            ),
            ProductionSegmentDraft(
                segment_index=1,
                section_index=1,
                section_type="cta",
                narration_text="CTA text.",
                estimated_duration_s=2,
                estimated_word_count=2,
            ),
        ]
    return ProductionPlanDraft(
        topic_id=topic_id,
        script_id=script_id,
        script_version=1,
        input_hash=input_hash or "a" * 64,
        script_body_hash="b" * 64,
        plan_schema_version=PRODUCTION_PLAN_SCHEMA_VERSION,
        renderer_version=PRODUCTION_PLAN_RENDERER_VERSION,
        duration_algorithm_version=PRODUCTION_DURATION_VERSION,
        title="Test Script",
        format="short",
        total_estimated_duration_s=6,
        total_word_count=4,
        warnings=warnings or [],
        requires_evidence_review=False,
        evidence_hash="e" * 64,
        generation_run_id=None,
        experiment_id=None,
        segments=segments,
    )


# ---------------------------------------------------------------------------
# create_production_plan
# ---------------------------------------------------------------------------


def test_create_production_plan_returns_plan(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    draft = _make_draft(topic.id, script.id)
    plan = create_production_plan(db, draft)
    assert isinstance(plan, ProductionPlan)
    assert plan.id > 0
    assert plan.status == "draft"
    assert plan.topic_id == topic.id
    assert plan.script_id == script.id


def test_create_production_plan_persists_segments(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    draft = _make_draft(topic.id, script.id)
    plan = create_production_plan(db, draft)
    segments = get_production_segments(db, plan.id)
    assert len(segments) == 2
    assert segments[0].segment_index == 0
    assert segments[0].section_type == "hook"
    assert segments[0].narration_text == "Hook narration."
    assert segments[1].segment_index == 1
    assert segments[1].section_type == "cta"


def test_create_production_plan_preserves_warnings(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    draft = _make_draft(topic.id, script.id, warnings=["stale evidence"])
    plan = create_production_plan(db, draft)
    assert plan.warnings == ["stale evidence"]


def test_create_production_plan_duplicate_input_hash_raises(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    draft = _make_draft(topic.id, script.id, input_hash="c" * 64)
    create_production_plan(db, draft)
    with pytest.raises(DuplicateInputHashError):
        create_production_plan(db, draft)


def test_create_production_plan_with_citations(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    claim_id = _seed_claim(db, topic.id)
    seg_with_cit = ProductionSegmentDraft(
        segment_index=0,
        section_index=0,
        section_type="body",
        narration_text="Body with claim.",
        estimated_duration_s=3,
        estimated_word_count=3,
        citation_claim_ids=[claim_id],
    )
    draft = _make_draft(topic.id, script.id, segments=[seg_with_cit])
    plan = create_production_plan(db, draft)
    segs = get_production_segments(db, plan.id)
    cits = get_production_segment_citations(db, segs[0].id)
    assert len(cits) == 1
    assert cits[0].claim_id == claim_id
    assert cits[0].citation_order == 0


def test_create_production_plan_citation_order_preserved(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    claim_a = _seed_claim(db, topic.id)
    claim_b = _seed_claim(db, topic.id)
    claim_c = _seed_claim(db, topic.id)
    seg = ProductionSegmentDraft(
        segment_index=0,
        section_index=0,
        section_type="body",
        narration_text="Three claims.",
        estimated_duration_s=3,
        estimated_word_count=2,
        citation_claim_ids=[claim_a, claim_b, claim_c],
    )
    draft = _make_draft(topic.id, script.id, segments=[seg])
    plan = create_production_plan(db, draft)
    segs = get_production_segments(db, plan.id)
    cits = get_production_segment_citations(db, segs[0].id)
    assert [c.claim_id for c in cits] == [claim_a, claim_b, claim_c]
    assert [c.citation_order for c in cits] == [0, 1, 2]


# ---------------------------------------------------------------------------
# get_or_create_production_plan
# ---------------------------------------------------------------------------


def test_get_or_create_creates_new(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    draft = _make_draft(topic.id, script.id)
    plan, created = get_or_create_production_plan(db, draft)
    assert created is True
    assert plan.id > 0


def test_get_or_create_returns_existing(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    draft = _make_draft(topic.id, script.id)
    plan1, created1 = get_or_create_production_plan(db, draft)
    plan2, created2 = get_or_create_production_plan(db, draft)
    assert created1 is True
    assert created2 is False
    assert plan1.id == plan2.id


# ---------------------------------------------------------------------------
# get_production_plan_by_id and by_input_hash
# ---------------------------------------------------------------------------


def test_get_plan_by_id_found(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    plan = create_production_plan(db, _make_draft(topic.id, script.id))
    fetched = get_production_plan_by_id(db, plan.id)
    assert fetched is not None
    assert fetched.id == plan.id


def test_get_plan_by_id_not_found(db: sqlite3.Connection) -> None:
    result = get_production_plan_by_id(db, 99999)
    assert result is None


def test_get_plan_by_input_hash_found(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    draft = _make_draft(topic.id, script.id, input_hash="d" * 64)
    plan = create_production_plan(db, draft)
    fetched = get_production_plan_by_input_hash(db, script_id=script.id, input_hash="d" * 64)
    assert fetched is not None
    assert fetched.id == plan.id


def test_get_plan_by_input_hash_not_found(db: sqlite3.Connection) -> None:
    result = get_production_plan_by_input_hash(db, script_id=1, input_hash="z" * 64)
    assert result is None


# ---------------------------------------------------------------------------
# list_production_plans
# ---------------------------------------------------------------------------


def test_list_production_plans_empty(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    assert list_production_plans(db, topic.id) == []


def test_list_production_plans_multiple(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script1 = _script(db, topic.id)
    script2 = _script(db, topic.id, version=2)
    create_production_plan(db, _make_draft(topic.id, script1.id, input_hash="1" * 64))
    create_production_plan(db, _make_draft(topic.id, script2.id, input_hash="2" * 64))
    plans = list_production_plans(db, topic.id)
    assert len(plans) == 2


def test_list_production_plans_isolated_by_topic(db: sqlite3.Connection) -> None:
    topic1 = _topic(db, "Topic One")
    topic2 = _topic(db, "Topic Two")
    s1 = _script(db, topic1.id)
    s2 = _script(db, topic2.id)
    create_production_plan(db, _make_draft(topic1.id, s1.id))
    plans = list_production_plans(db, topic2.id)
    assert len(plans) == 0
    _ = s2


# ---------------------------------------------------------------------------
# approve_production_plan
# ---------------------------------------------------------------------------


def test_approve_sets_status(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    plan = create_production_plan(db, _make_draft(topic.id, script.id))
    db.commit()
    approved = approve_production_plan(db, plan.id, actor="dom")
    db.commit()
    assert approved.status == "approved"
    assert approved.approved_at is not None


def test_approve_inserts_review_event(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    plan = create_production_plan(db, _make_draft(topic.id, script.id))
    db.commit()
    approve_production_plan(db, plan.id, actor="dom")
    db.commit()
    events = list_production_plan_review_events(db, plan.id)
    assert len(events) == 1
    assert events[0].decision == "approved"
    assert events[0].actor == "dom"


def test_approve_supersedes_prior_active_plan(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    plan1 = create_production_plan(db, _make_draft(topic.id, script.id, input_hash="1" * 64))
    db.commit()
    approve_production_plan(db, plan1.id, actor="dom")
    db.commit()

    plan2 = create_production_plan(db, _make_draft(topic.id, script.id, input_hash="2" * 64))
    db.commit()
    approve_production_plan(db, plan2.id, actor="dom")
    db.commit()

    superseded = get_production_plan_by_id(db, plan1.id)
    active = get_active_approved_production_plan(db, topic.id)
    assert superseded is not None
    assert superseded.superseded_at is not None
    assert active is not None
    assert active.id == plan2.id


def test_approve_non_draft_raises_illegal_transition(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    plan = create_production_plan(db, _make_draft(topic.id, script.id))
    db.commit()
    approve_production_plan(db, plan.id)
    db.commit()
    with pytest.raises(IllegalTransitionError):
        approve_production_plan(db, plan.id)


def test_approve_missing_plan_raises_no_plan_error(db: sqlite3.Connection) -> None:
    with pytest.raises(NoPlanError):
        approve_production_plan(db, 99999)


# ---------------------------------------------------------------------------
# reject_production_plan
# ---------------------------------------------------------------------------


def test_reject_sets_status(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    plan = create_production_plan(db, _make_draft(topic.id, script.id))
    db.commit()
    rejected = reject_production_plan(db, plan.id, reason_code="pacing")
    db.commit()
    assert rejected.status == "rejected"
    assert rejected.rejected_at is not None


def test_reject_inserts_review_event(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    plan = create_production_plan(db, _make_draft(topic.id, script.id))
    db.commit()
    reject_production_plan(db, plan.id, reason_code="pacing", notes=None, actor="dom")
    db.commit()
    events = list_production_plan_review_events(db, plan.id)
    assert len(events) == 1
    assert events[0].decision == "rejected"
    assert events[0].reason_code == "pacing"
    assert events[0].actor == "dom"


def test_reject_does_not_touch_approved_plan(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    plan_approved = create_production_plan(
        db, _make_draft(topic.id, script.id, input_hash="1" * 64)
    )
    db.commit()
    approve_production_plan(db, plan_approved.id)
    db.commit()

    plan_draft = create_production_plan(db, _make_draft(topic.id, script.id, input_hash="2" * 64))
    db.commit()
    reject_production_plan(db, plan_draft.id, reason_code="pacing")
    db.commit()

    active = get_active_approved_production_plan(db, topic.id)
    assert active is not None
    assert active.id == plan_approved.id


def test_reject_non_draft_raises_illegal_transition(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    plan = create_production_plan(db, _make_draft(topic.id, script.id))
    db.commit()
    reject_production_plan(db, plan.id, reason_code="pacing")
    db.commit()
    with pytest.raises(IllegalTransitionError):
        reject_production_plan(db, plan.id, reason_code="pacing")


def test_reject_invalid_reason_code_raises(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    plan = create_production_plan(db, _make_draft(topic.id, script.id))
    db.commit()
    with pytest.raises(InvalidReasonCodeError):
        reject_production_plan(db, plan.id, reason_code="not_a_real_code")


def test_reject_other_without_notes_raises(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    plan = create_production_plan(db, _make_draft(topic.id, script.id))
    db.commit()
    with pytest.raises(InvalidReasonCodeError):
        reject_production_plan(db, plan.id, reason_code="other", notes=None)


def test_reject_other_with_notes_succeeds(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    plan = create_production_plan(db, _make_draft(topic.id, script.id))
    db.commit()
    rejected = reject_production_plan(db, plan.id, reason_code="other", notes="Other reason text.")
    db.commit()
    assert rejected.status == "rejected"
    events = list_production_plan_review_events(db, plan.id)
    assert events[0].notes == "Other reason text."


def test_reject_missing_plan_raises_no_plan_error(db: sqlite3.Connection) -> None:
    with pytest.raises(NoPlanError):
        reject_production_plan(db, 99999, reason_code="pacing")


# ---------------------------------------------------------------------------
# get_active_approved_production_plan / require_active_approved_production_plan
# ---------------------------------------------------------------------------


def test_get_active_approved_none_when_no_plans(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    result = get_active_approved_production_plan(db, topic.id)
    assert result is None


def test_get_active_approved_none_when_only_draft(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    create_production_plan(db, _make_draft(topic.id, script.id))
    db.commit()
    result = get_active_approved_production_plan(db, topic.id)
    assert result is None


def test_get_active_approved_returns_approved_plan(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    plan = create_production_plan(db, _make_draft(topic.id, script.id))
    db.commit()
    approve_production_plan(db, plan.id)
    db.commit()
    result = get_active_approved_production_plan(db, topic.id)
    assert result is not None
    assert result.id == plan.id
    assert result.status == "approved"


def test_require_active_approved_raises_when_none(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    with pytest.raises(NoApprovedProductionPlanError):
        require_active_approved_production_plan(db, topic.id)


def test_require_active_approved_returns_plan(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    plan = create_production_plan(db, _make_draft(topic.id, script.id))
    db.commit()
    approve_production_plan(db, plan.id)
    db.commit()
    result = require_active_approved_production_plan(db, topic.id)
    assert result.id == plan.id


# ---------------------------------------------------------------------------
# get_approved_production_plan_full
# ---------------------------------------------------------------------------


def test_get_approved_full_none_when_no_approved(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    assert get_approved_production_plan_full(db, topic.id) is None


def test_get_approved_full_returns_hydrated_plan(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    plan = create_production_plan(db, _make_draft(topic.id, script.id))
    db.commit()
    approve_production_plan(db, plan.id)
    db.commit()
    full = get_approved_production_plan_full(db, topic.id)
    assert isinstance(full, ApprovedProductionPlan)
    assert full.plan_id == plan.id
    assert len(full.segments) == 2
    assert full.segments[0].section_type == "hook"
    assert full.segments[1].section_type == "cta"


def test_get_approved_full_includes_citations(db: sqlite3.Connection) -> None:
    topic = _topic(db)
    script = _script(db, topic.id)
    claim_id = _seed_claim(db, topic.id)
    seg = ProductionSegmentDraft(
        segment_index=0,
        section_index=0,
        section_type="body",
        narration_text="Body.",
        estimated_duration_s=2,
        estimated_word_count=1,
        citation_claim_ids=[claim_id],
    )
    draft = _make_draft(topic.id, script.id, segments=[seg])
    plan = create_production_plan(db, draft)
    db.commit()
    approve_production_plan(db, plan.id)
    db.commit()
    full = get_approved_production_plan_full(db, topic.id)
    assert full is not None
    assert full.segments[0].citation_claim_ids == [claim_id]
