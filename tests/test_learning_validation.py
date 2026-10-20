"""Tests for the Phase 11 validation module, plus Phase 18F's
compute_evidence_validity() — recommendation evidence-validity re-check.

compute_evidence_validity() is a read-only, deterministic replay of the
CURRENT generator sufficiency/threshold rules against a recommendation's own
persisted evidence — it never mutates the row, never re-ingests analytics,
and never spends a provider call.

Tests:
  Phase 11 validators (TestValidate*): unchanged from before Phase 18F.

  Phase 18F compute_evidence_validity / _lifetime_views_as_of:
  A  current, valid subscriber-gain recommendation (net < 0, sufficient views
     on record) -> evidence_valid=True
  B  the net=0 subscriber-loss historical artifact -> evidence_valid=False,
     reason cites the threshold
  C  a retention recommendation whose views-as-of-created_at were below
     MIN_VIEWS_FOR_LEARNING -> evidence_valid=False, reason cites views
     (also exercises required case 3 "multiple snapshots in one lifetime
     series" and required case 4 "recommendation created between snapshots")
  D  a retention/watch-time recommendation with sufficient views-as-of and a
     legitimately-zero observed_value -> evidence_valid=True (0.0 is not
     auto-invalid)
  E  a CTR recommendation -> evidence_valid=True unconditionally (documents
     the explicit REQUIRE_CTR_DATA scope limitation this phase)
  F  a superseded row is unaffected (status alone distinguishes it; validity
     still computed but immaterial to display)
  G  computing validity never mutates the recommendation row
  H  a seed-only views reading does not satisfy MIN_VIEWS_FOR_LEARNING
  I  a seed reading alongside a real reading uses the real reading
  J  multiple period_start groups (a production-unreachable state — see
     _lifetime_views_as_of's docstring) fail closed to None/insufficient
     rather than guessing at cross-group aggregation
  K  no qualifying historical views metric at all -> insufficient
  L  a views metric created strictly after the recommendation's created_at
     is never used, even when it would otherwise satisfy sufficiency
  M  an ordinary single-lifetime-series, sufficient-views case is valid
     (required case 1 "ordinary single lifetime series", explicit form)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.database import open_db
from app.learning import repository as learning_repo
from app.learning.constants import (
    LEARNING_ENGINE_VERSION,
    LEARNING_SCHEMA_VERSION,
    MIN_VIEWS_FOR_LEARNING,
    STATUS_PENDING,
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
    InvalidRecommendationSubsystemError,
    InvalidReviewEventTypeError,
    NotesRequiredError,
    RecommendationNotReviewableError,
    ReviewerRequiredError,
)
from app.learning.validation import (
    _lifetime_views_as_of,
    compute_evidence_validity,
    validate_confidence_level,
    validate_confidence_score,
    validate_domain,
    validate_evidence_classification,
    validate_recommendation_status,
    validate_review_eligible,
    validate_review_event,
    validate_review_event_type,
    validate_subsystem,
)

NOW = "2026-08-19T22:15:45"


# ═══════════════════════════════════════════════════════════════════════════
# Phase 11 — validation.py's pure validators (restored, unchanged from HEAD)
# ═══════════════════════════════════════════════════════════════════════════


class TestValidateEvidenceClassification:
    def test_all_valid_classifications_pass(self):
        for c in (
            "observational",
            "controlled_experiment",
            "human_preference",
            "operational_failure",
            "mixed",
        ):
            validate_evidence_classification(c)  # should not raise

    def test_invalid_classification_raises(self):
        with pytest.raises(InvalidEvidenceClassificationError):
            validate_evidence_classification("unknown")

    def test_empty_string_raises(self):
        with pytest.raises(InvalidEvidenceClassificationError):
            validate_evidence_classification("")

    def test_causal_label_is_rejected(self):
        with pytest.raises(InvalidEvidenceClassificationError):
            validate_evidence_classification("causal")


class TestValidateDomain:
    def test_valid_domains_pass(self):
        for d in ("topics", "research", "scripts", "narration", "publishing"):
            validate_domain(d)  # should not raise

    def test_invalid_domain_raises(self):
        with pytest.raises(InvalidRecommendationDomainError):
            validate_domain("invalid_domain")

    def test_empty_string_raises(self):
        with pytest.raises(InvalidRecommendationDomainError):
            validate_domain("")


class TestValidateSubsystem:
    def test_valid_subsystem_passes(self):
        validate_subsystem("hook_effectiveness")
        validate_subsystem("title_effectiveness")

    def test_invalid_subsystem_raises(self):
        with pytest.raises(InvalidRecommendationSubsystemError):
            validate_subsystem("magic_feature")


class TestValidateConfidenceLevel:
    def test_valid_levels_pass(self):
        for level in ("low", "medium", "high"):
            validate_confidence_level(level)

    def test_invalid_level_raises(self):
        with pytest.raises(InvalidConfidenceLevelError):
            validate_confidence_level("very_high")


class TestValidateConfidenceScore:
    def test_zero_is_valid(self):
        validate_confidence_score(0.0)

    def test_one_is_valid(self):
        validate_confidence_score(1.0)

    def test_midpoint_is_valid(self):
        validate_confidence_score(0.5)

    def test_negative_raises(self):
        with pytest.raises(InvalidConfidenceScoreError):
            validate_confidence_score(-0.001)

    def test_over_one_raises(self):
        with pytest.raises(InvalidConfidenceScoreError):
            validate_confidence_score(1.001)


class TestValidateRecommendationStatus:
    def test_valid_statuses_pass(self):
        for s in ("pending", "accepted", "rejected", "superseded"):
            validate_recommendation_status(s)

    def test_invalid_status_raises(self):
        with pytest.raises(InvalidRecommendationStatusError):
            validate_recommendation_status("unknown")


class TestValidateReviewEventType:
    def test_valid_types_pass(self):
        for t in ("accepted", "rejected", "noted"):
            validate_review_event_type(t)

    def test_invalid_type_raises(self):
        with pytest.raises(InvalidReviewEventTypeError):
            validate_review_event_type("dismissed")


class TestValidateReviewEligible:
    def _make_rec(self, status: str):
        from unittest.mock import MagicMock

        rec = MagicMock()
        rec.id = 1
        rec.status = status
        return rec

    def test_pending_is_eligible(self):
        validate_review_eligible(self._make_rec("pending"))  # should not raise

    def test_accepted_is_eligible(self):
        validate_review_eligible(self._make_rec("accepted"))

    def test_rejected_is_eligible(self):
        validate_review_eligible(self._make_rec("rejected"))

    def test_superseded_raises(self):
        with pytest.raises(RecommendationNotReviewableError):
            validate_review_eligible(self._make_rec("superseded"))


class TestValidateReviewEvent:
    def test_noted_without_reviewer_is_ok(self):
        validate_review_event("noted", "", "")  # should not raise

    def test_accepted_requires_reviewer(self):
        with pytest.raises(ReviewerRequiredError):
            validate_review_event("accepted", "", "")

    def test_accepted_with_reviewer_passes(self):
        validate_review_event("accepted", "operator", "")

    def test_rejected_requires_reviewer_and_notes(self):
        with pytest.raises(ReviewerRequiredError):
            validate_review_event("rejected", "", "some notes")

    def test_rejected_requires_notes(self):
        with pytest.raises(NotesRequiredError):
            validate_review_event("rejected", "operator", "")

    def test_rejected_with_reviewer_and_notes_passes(self):
        validate_review_event("rejected", "operator", "not applicable")

    def test_invalid_event_type_raises(self):
        with pytest.raises(InvalidReviewEventTypeError):
            validate_review_event("unknown", "operator", "notes")


# ═══════════════════════════════════════════════════════════════════════════
# Phase 18F — compute_evidence_validity / _lifetime_views_as_of
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def db_conn(tmp_path: Path):
    conn = open_db(tmp_path / "validation_test.db")
    conn.execute("PRAGMA foreign_keys = OFF")
    yield conn
    conn.close()


def _insert_topic(conn) -> int:
    cur = conn.execute(
        "INSERT INTO topics (title, angle, status, created_at, updated_at) VALUES (?,?,?,?,?)",
        ("Test Topic", "", "active", NOW, NOW),
    )
    return cur.lastrowid


def _insert_publication(conn, topic_id: int) -> int:
    """Minimal publications row — foreign_keys off, so publishing_plan_id/
    publishing_job_id need not reference real rows; only NOT NULL/CHECK
    constraints on this table itself matter for the validity re-check,
    which reads analytics_metrics by publication_id, not this row's
    content."""
    conn.execute("PRAGMA foreign_keys = OFF")
    cur = conn.execute(
        """INSERT INTO publications
               (publishing_plan_id, publishing_job_id, provider, provider_version,
                status, visibility, publishing_engine_version, input_hash,
                output_sha256, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            topic_id,
            topic_id,
            "youtube",
            "v1",
            "published",
            "public",
            "v1",
            f"hash-{topic_id}",
            f"sha-{topic_id}",
            NOW,
            NOW,
        ),
    )
    return cur.lastrowid


def _insert_views_metric(
    conn,
    publication_id: int,
    views: float,
    created_at: str,
    *,
    period_start: str | None = None,
    is_seed: bool = False,
) -> None:
    """A views reading as it would genuinely exist in analytics_metrics —
    immutable, append-only per snapshot ingestion (see validation.py's
    _lifetime_views_as_of for why this table, not analytics_aggregates,
    which is upserted in place and cannot answer "what did we know then").

    `period_start` defaults to None, matching every publication's single
    real lifetime series in ordinary use; pass a distinct value only to
    simulate the production-unreachable multiple-period-start case.
    `is_seed=True` gives the row the canonical `seed-%` input_hash prefix
    that _lifetime_views_as_of excludes, matching maturity.py/
    cross_publication.py's convention.
    """
    prefix = "seed" if is_seed else "metric"
    conn.execute(
        """INSERT INTO analytics_metrics
               (snapshot_id, publication_id, topic_id, provider, metric_name,
                metric_value, period_start, period_end, input_hash, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            publication_id * 1000 + int(views),
            publication_id,
            1,
            "youtube",
            "views",
            views,
            period_start,
            None,
            f"{prefix}-views-{publication_id}-{created_at}",
            created_at,
        ),
    )


def _insert_recommendation(
    conn,
    *,
    topic_id: int,
    publication_id: int | None,
    subsystem: str,
    measure: str,
    title: str,
    evidence: list[dict],
    status: str = STATUS_PENDING,
    created_at: str = NOW,
) -> int:
    run_cur = conn.execute(
        """INSERT INTO learning_runs
               (topic_id, publication_id, publication_count, recommendation_count,
                status, engine_version, schema_version, input_hash, created_at, completed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            topic_id,
            publication_id,
            1,
            1,
            "completed",
            LEARNING_ENGINE_VERSION,
            LEARNING_SCHEMA_VERSION,
            f"hash-run-{title}",
            created_at,
            created_at,
        ),
    )
    run_id = run_cur.lastrowid
    rec_cur = conn.execute(
        """INSERT INTO optimization_recommendations
               (learning_run_id, topic_id, publication_id, domain, subsystem, measure,
                title, explanation, expected_improvement,
                evidence_json, evidence_classification, recommendation_strength,
                confidence, confidence_score,
                affected_subsystem, subsystem_entity_type, subsystem_entity_id,
                experiment_id, engine_version, schema_version, input_hash,
                status, superseded_at, superseded_by_id, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            topic_id,
            publication_id,
            "topics",
            subsystem,
            measure,
            title,
            "test explanation",
            "test expected improvement",
            json.dumps(evidence),
            "observational",
            "exploratory",
            "medium",
            0.5,
            "",
            "",
            None,
            None,
            LEARNING_ENGINE_VERSION,
            LEARNING_SCHEMA_VERSION,
            f"hash-rec-{title}",
            status,
            None,
            None,
            created_at,
        ),
    )
    conn.commit()
    return rec_cur.lastrowid


def test_A_valid_subscriber_gain_recommendation_is_valid(db_conn):
    topic_id = _insert_topic(db_conn)
    pub_id = _insert_publication(db_conn, topic_id)
    _insert_views_metric(db_conn, pub_id, views=50.0, created_at=NOW)
    rec_id = _insert_recommendation(
        db_conn,
        topic_id=topic_id,
        publication_id=pub_id,
        subsystem=SUBSYSTEM_TOPIC_SELECTION,
        measure="subscribers_gained",
        title="Strong subscriber growth — replicate topic and content approach",
        evidence=[
            {
                "metric_name": "subscribers_gained",
                "observed_value": 12.0,
                "comparison_value": 0.0,
                "period_type": "lifetime",
                "period_key": "lifetime",
                "snapshot_ids": [],
                "interpretation": "net +12",
            }
        ],
    )
    rec = learning_repo.get_recommendation(db_conn, rec_id)
    is_valid, reason = compute_evidence_validity(db_conn, rec)
    assert is_valid is True
    assert reason is None


def test_B_net_zero_subscriber_loss_is_invalid_under_current_threshold(db_conn):
    """Reproduces the confirmed historical artifact: generated under the old
    `<=` comparison (net == 0 counted as "loss"), now stale under the fixed
    `<` operator."""
    topic_id = _insert_topic(db_conn)
    pub_id = _insert_publication(db_conn, topic_id)
    _insert_views_metric(db_conn, pub_id, views=200.0, created_at=NOW)
    rec_id = _insert_recommendation(
        db_conn,
        topic_id=topic_id,
        publication_id=pub_id,
        subsystem=SUBSYSTEM_TOPIC_SELECTION,
        measure="subscribers_gained",
        title="Net subscriber loss — review topic alignment with channel audience",
        evidence=[
            {
                "metric_name": "subscribers_gained",
                "observed_value": 0.0,
                "comparison_value": 0.0,
                "period_type": "lifetime",
                "period_key": "lifetime",
                "snapshot_ids": [],
                "interpretation": "net +0",
            }
        ],
        status="accepted",
    )
    rec = learning_repo.get_recommendation(db_conn, rec_id)
    is_valid, reason = compute_evidence_validity(db_conn, rec)
    assert is_valid is False
    assert "threshold" in reason.lower()
    assert f"{SUBSCRIBER_LOSS_THRESHOLD:.0f}" in reason


def test_C_retention_recommendation_with_insufficient_views_at_generation_is_invalid(db_conn):
    """Reproduces the confirmed historical artifact: avg_view_duration=0.0s
    generated when lifetime views on record were below the sufficiency bar
    (the id 4/5 scenario) — replayed against TODAY's MIN_VIEWS_FOR_LEARNING,
    using the views value actually on record at generation time, not live
    current views. Also exercises required case 3 (multiple snapshots in one
    lifetime series — two _insert_views_metric calls, same period_start) and
    required case 4 (recommendation created between the two snapshots)."""
    topic_id = _insert_topic(db_conn)
    pub_id = _insert_publication(db_conn, topic_id)
    _insert_views_metric(db_conn, pub_id, views=0.0, created_at="2026-08-19T21:00:00")
    # A later, mature aggregate exists (today's live views) — must NOT be
    # used to launder the historical row as valid.
    _insert_views_metric(db_conn, pub_id, views=500.0, created_at="2026-09-01T00:00:00")
    rec_id = _insert_recommendation(
        db_conn,
        topic_id=topic_id,
        publication_id=pub_id,
        subsystem="script_pacing",
        measure="average_view_duration",
        title="Low retention — review script pacing and segment structure",
        evidence=[
            {
                "metric_name": "average_view_duration",
                "observed_value": 0.0,
                "comparison_value": 20.0,
                "period_type": "lifetime",
                "period_key": "lifetime",
                "snapshot_ids": [],
                "interpretation": "avd=0.0s",
            }
        ],
        status="accepted",
        created_at="2026-08-19T22:15:45",
    )
    rec = learning_repo.get_recommendation(db_conn, rec_id)
    is_valid, reason = compute_evidence_validity(db_conn, rec)
    assert is_valid is False
    assert "views" in reason.lower()
    assert str(MIN_VIEWS_FOR_LEARNING) in reason or f"{MIN_VIEWS_FOR_LEARNING:.0f}" in reason


def test_D_legitimate_zero_with_sufficient_views_remains_valid(db_conn):
    """0.0 is not auto-invalid — only insufficient views or a stale
    threshold comparison make a row invalid."""
    topic_id = _insert_topic(db_conn)
    pub_id = _insert_publication(db_conn, topic_id)
    _insert_views_metric(db_conn, pub_id, views=50.0, created_at=NOW)
    rec_id = _insert_recommendation(
        db_conn,
        topic_id=topic_id,
        publication_id=pub_id,
        subsystem="watch_time_trends",
        measure="watch_time_seconds",
        title="Low total watch time relative to view count",
        evidence=[
            {
                "metric_name": "watch_time_seconds",
                "observed_value": 0.0,
                "comparison_value": 1000.0,
                "period_type": "lifetime",
                "period_key": "lifetime",
                "snapshot_ids": [],
                "interpretation": "genuinely zero watch time",
            }
        ],
    )
    rec = learning_repo.get_recommendation(db_conn, rec_id)
    is_valid, reason = compute_evidence_validity(db_conn, rec)
    assert is_valid is True
    assert reason is None


def test_E_ctr_recommendation_is_unconditionally_valid_this_phase(db_conn):
    """REQUIRE_CTR_DATA is explicitly out of scope this phase — no known
    historical artifact implicates it; documented limitation, not a silent
    mismatch against the REQUIRE_VIEWS rule."""
    topic_id = _insert_topic(db_conn)
    pub_id = _insert_publication(db_conn, topic_id)
    # Deliberately no views aggregate at all — would be "insufficient" under
    # REQUIRE_VIEWS, but CTR recommendations are not gated by that rule.
    rec_id = _insert_recommendation(
        db_conn,
        topic_id=topic_id,
        publication_id=pub_id,
        subsystem="ctr_trends",
        measure="ctr",
        title="Low CTR — review hook and thumbnail",
        evidence=[
            {
                "metric_name": "ctr",
                "observed_value": 0.01,
                "comparison_value": 0.04,
                "period_type": "lifetime",
                "period_key": "lifetime",
                "snapshot_ids": [],
                "interpretation": "ctr low",
            }
        ],
    )
    rec = learning_repo.get_recommendation(db_conn, rec_id)
    is_valid, reason = compute_evidence_validity(db_conn, rec)
    assert is_valid is True
    assert reason is None


def test_F_superseded_row_validity_still_computed_but_immaterial_to_status(db_conn):
    topic_id = _insert_topic(db_conn)
    pub_id = _insert_publication(db_conn, topic_id)
    _insert_views_metric(db_conn, pub_id, views=0.0, created_at=NOW)
    rec_id = _insert_recommendation(
        db_conn,
        topic_id=topic_id,
        publication_id=pub_id,
        subsystem="script_pacing",
        measure="average_view_duration",
        title="Low retention — review script pacing and segment structure",
        evidence=[
            {
                "metric_name": "average_view_duration",
                "observed_value": 0.0,
                "comparison_value": 20.0,
                "period_type": "lifetime",
                "period_key": "lifetime",
                "snapshot_ids": [],
                "interpretation": "avd=0.0s",
            }
        ],
        status=STATUS_SUPERSEDED,
    )
    rec = learning_repo.get_recommendation(db_conn, rec_id)
    assert rec.status == STATUS_SUPERSEDED
    is_valid, reason = compute_evidence_validity(db_conn, rec)
    assert is_valid is False  # validity is a property of the evidence, independent of status


def test_G_computing_validity_does_not_mutate_the_row(db_conn):
    topic_id = _insert_topic(db_conn)
    pub_id = _insert_publication(db_conn, topic_id)
    _insert_views_metric(db_conn, pub_id, views=0.0, created_at=NOW)
    rec_id = _insert_recommendation(
        db_conn,
        topic_id=topic_id,
        publication_id=pub_id,
        subsystem="script_pacing",
        measure="average_view_duration",
        title="Low retention — review script pacing and segment structure",
        evidence=[
            {
                "metric_name": "average_view_duration",
                "observed_value": 0.0,
                "comparison_value": 20.0,
                "period_type": "lifetime",
                "period_key": "lifetime",
                "snapshot_ids": [],
                "interpretation": "avd=0.0s",
            }
        ],
        status="accepted",
    )
    before = dict(
        db_conn.execute(
            "SELECT status, evidence_json, created_at FROM optimization_recommendations WHERE id=?",
            (rec_id,),
        ).fetchone()
    )
    rec = learning_repo.get_recommendation(db_conn, rec_id)
    compute_evidence_validity(db_conn, rec)
    after = dict(
        db_conn.execute(
            "SELECT status, evidence_json, created_at FROM optimization_recommendations WHERE id=?",
            (rec_id,),
        ).fetchone()
    )
    assert before == after


def test_H_seed_only_views_do_not_satisfy_sufficiency(db_conn):
    """Part 2: a seed-marked views row must not count toward
    MIN_VIEWS_FOR_LEARNING, even though its raw value alone would qualify."""
    topic_id = _insert_topic(db_conn)
    pub_id = _insert_publication(db_conn, topic_id)
    _insert_views_metric(db_conn, pub_id, views=500.0, created_at=NOW, is_seed=True)
    assert _lifetime_views_as_of(db_conn, pub_id, NOW) is None
    rec_id = _insert_recommendation(
        db_conn,
        topic_id=topic_id,
        publication_id=pub_id,
        subsystem="script_pacing",
        measure="average_view_duration",
        title="Low retention — review script pacing and segment structure",
        evidence=[
            {
                "metric_name": "average_view_duration",
                "observed_value": 5.0,
                "comparison_value": 20.0,
                "period_type": "lifetime",
                "period_key": "lifetime",
                "snapshot_ids": [],
                "interpretation": "avd=5.0s",
            }
        ],
    )
    rec = learning_repo.get_recommendation(db_conn, rec_id)
    is_valid, reason = compute_evidence_validity(db_conn, rec)
    assert is_valid is False
    assert "views" in reason.lower()


def test_I_seed_plus_real_metric_uses_the_real_reading(db_conn):
    """Part 2: a seed row must not shadow a genuine, sufficient real
    reading — the real value is what gets used."""
    topic_id = _insert_topic(db_conn)
    pub_id = _insert_publication(db_conn, topic_id)
    _insert_views_metric(db_conn, pub_id, views=0.0, created_at="2026-08-19T20:00:00", is_seed=True)
    _insert_views_metric(db_conn, pub_id, views=50.0, created_at="2026-08-19T21:00:00")
    assert _lifetime_views_as_of(db_conn, pub_id, NOW) == 50.0
    rec_id = _insert_recommendation(
        db_conn,
        topic_id=topic_id,
        publication_id=pub_id,
        subsystem="watch_time_trends",
        measure="watch_time_seconds",
        title="Low total watch time relative to view count",
        evidence=[
            {
                "metric_name": "watch_time_seconds",
                "observed_value": 10.0,
                "comparison_value": 1000.0,
                "period_type": "lifetime",
                "period_key": "lifetime",
                "snapshot_ids": [],
                "interpretation": "low watch time",
            }
        ],
    )
    rec = learning_repo.get_recommendation(db_conn, rec_id)
    is_valid, reason = compute_evidence_validity(db_conn, rec)
    assert is_valid is True
    assert reason is None


def test_J_multiple_period_start_groups_fail_closed(db_conn):
    """Required case 5 (republish scenario). Proven production-unreachable
    today (PUB_TRANSITIONS makes 'published' terminal except to 'deleted';
    no republish path exists) — but if it ever occurred, _lifetime_views_as_of
    must fail closed (None) rather than silently pick one group or invent
    cross-group summation semantics it was never asked to implement."""
    topic_id = _insert_topic(db_conn)
    pub_id = _insert_publication(db_conn, topic_id)
    _insert_views_metric(
        db_conn, pub_id, views=50.0, created_at="2026-08-01T00:00:00", period_start="2026-08-01"
    )
    _insert_views_metric(
        db_conn, pub_id, views=50.0, created_at="2026-08-15T00:00:00", period_start="2026-08-14"
    )
    assert _lifetime_views_as_of(db_conn, pub_id, NOW) is None


def test_K_no_qualifying_historical_metric_is_insufficient(db_conn):
    """Required case 6: no views metric exists at all for this publication."""
    topic_id = _insert_topic(db_conn)
    pub_id = _insert_publication(db_conn, topic_id)
    assert _lifetime_views_as_of(db_conn, pub_id, NOW) is None
    rec_id = _insert_recommendation(
        db_conn,
        topic_id=topic_id,
        publication_id=pub_id,
        subsystem="script_pacing",
        measure="average_view_duration",
        title="Low retention — review script pacing and segment structure",
        evidence=[
            {
                "metric_name": "average_view_duration",
                "observed_value": 5.0,
                "comparison_value": 20.0,
                "period_type": "lifetime",
                "period_key": "lifetime",
                "snapshot_ids": [],
                "interpretation": "avd=5.0s",
            }
        ],
    )
    rec = learning_repo.get_recommendation(db_conn, rec_id)
    is_valid, reason = compute_evidence_validity(db_conn, rec)
    assert is_valid is False


def test_L_metrics_created_after_recommendation_are_never_used(db_conn):
    """Required case 8: a views row ingested strictly after the
    recommendation's created_at must never be used to justify it, even
    though its value alone would satisfy sufficiency."""
    topic_id = _insert_topic(db_conn)
    pub_id = _insert_publication(db_conn, topic_id)
    _insert_views_metric(db_conn, pub_id, views=1000.0, created_at="2026-08-20T00:00:00")
    assert _lifetime_views_as_of(db_conn, pub_id, NOW) is None
    assert _lifetime_views_as_of(db_conn, pub_id, "2026-08-20T00:00:00") == 1000.0


def test_M_ordinary_single_lifetime_series_with_sufficient_views_is_valid(db_conn):
    """Required case 1, explicit form: one lifetime series, one real
    sufficient reading, straightforward valid result."""
    topic_id = _insert_topic(db_conn)
    pub_id = _insert_publication(db_conn, topic_id)
    _insert_views_metric(db_conn, pub_id, views=1000.0, created_at=NOW)
    rec_id = _insert_recommendation(
        db_conn,
        topic_id=topic_id,
        publication_id=pub_id,
        subsystem="script_pacing",
        measure="average_view_duration",
        title="Strong retention — replicate script and narration approach",
        evidence=[
            {
                "metric_name": "average_view_duration",
                "observed_value": 56.0,
                "comparison_value": 45.0,
                "period_type": "lifetime",
                "period_key": "lifetime",
                "snapshot_ids": [],
                "interpretation": "avd=56.0s",
            }
        ],
    )
    rec = learning_repo.get_recommendation(db_conn, rec_id)
    is_valid, reason = compute_evidence_validity(db_conn, rec)
    assert is_valid is True
    assert reason is None
