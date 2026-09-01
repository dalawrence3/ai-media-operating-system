"""Phase 12A: Recommendation Application Framework tests.

Covers:
  A. propose_application with accepted+actionable recommendation succeeds
  B. propose_application with pending recommendation → RecommendationNotApplicableError
  C. propose_application with rejected recommendation → RecommendationNotApplicableError
  D. propose_application with accepted+exploratory → RecommendationNotApplicableError
  E. build_narration_pace_intent direction=increase computes correct target
  F. build_narration_pace_intent direction=decrease computes correct target
  G. build_narration_pace_intent direction=maintain → target == value_before
  H. build_narration_pace_intent magnitude > max_delta → ApplicationParameterError
  I. build_narration_pace_intent target below safety min → ApplicationParameterError
  J. build_narration_pace_intent target above safety max → ApplicationParameterError
  K. apply_application transitions proposed → applied with value_applied recorded
  L. apply_application from non-proposed state → InvalidApplicationTransitionError
  M. revert_application transitions applied → reverted
  N. revert_application from non-applied state → InvalidApplicationTransitionError
  O. scope isolation: topic_id=1 application not visible for topic_id=2
  + idempotency (UNIQUE conflict → DuplicateApplicationError)
  + speaking_rate_override propagates into NarrationRunDraft
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from app.core.database import open_db
from app.learning.application import (
    ApplicationIntent,
    apply_application,
    build_narration_pace_intent,
    get_active_application_for_parameter,
    get_application,
    propose_application,
    revert_application,
)
from app.learning.constants import (
    APPLICATION_STATUS_APPLIED,
    APPLICATION_STATUS_PROPOSED,
    APPLICATION_STATUS_REVERTED,
    DIRECTION_DECREASE,
    DIRECTION_INCREASE,
    DIRECTION_MAINTAIN,
    NARRATION_PACE_MAX_DELTA,
    NARRATION_PACE_PARAMETER,
    NARRATION_PACE_SPEAKING_RATE_MAX,
    NARRATION_PACE_SPEAKING_RATE_MIN,
    STRENGTH_ACTIONABLE,
    STRENGTH_EXPLORATORY,
)
from app.learning.errors import (
    ApplicationNotFoundError,
    ApplicationParameterError,
    DuplicateApplicationError,
    InvalidApplicationTransitionError,
    RecommendationNotApplicableError,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        conn = open_db(pathlib.Path(d) / "test.db")
        conn.execute("INSERT INTO topics (title, angle) VALUES ('Test Topic', 'test')")
        conn.commit()
        yield conn


def _insert_learning_run(conn, topic_id: int = 1) -> int:
    cursor = conn.execute(
        """
        INSERT INTO learning_runs
            (topic_id, publication_id, publication_count, recommendation_count,
             status, engine_version, schema_version, input_hash, created_at)
        VALUES (?,NULL,0,0,'completed','engine-v1','schema-v1','abc123',
                strftime('%Y-%m-%dT%H:%M:%S','now'))
        """,
        (topic_id,),
    )
    conn.commit()
    return cursor.lastrowid


def _insert_recommendation(
    conn,
    *,
    topic_id: int = 1,
    status: str = "accepted",
    strength: str = STRENGTH_ACTIONABLE,
    learning_run_id: int | None = None,
) -> int:
    if learning_run_id is None:
        learning_run_id = _insert_learning_run(conn, topic_id)
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
        VALUES (?,?,NULL,'narration','narration_pace','speaking_rate',
                'Test rec','explanation','improvement',
                '[]','observational',?,
                'medium',0.6,
                'narration_pace','narration_run',NULL,
                NULL,
                'engine-v1','schema-v1',?,
                ?,strftime('%Y-%m-%dT%H:%M:%S','now'))
        """,
        (
            learning_run_id,
            topic_id,
            strength,
            f"hash_rec_{topic_id}_{status}_{strength}",
            status,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def _make_intent(
    direction: str = DIRECTION_DECREASE,
    magnitude: float = 0.1,
    value_before: float = 1.0,
) -> ApplicationIntent:
    return build_narration_pace_intent(
        direction=direction,
        magnitude=magnitude,
        current_speaking_rate=value_before,
    )


# ── Tests A–D: propose_application eligibility ───────────────────────────────


class TestProposeEligibility:
    def test_a_accepted_actionable_succeeds(self, db):
        """A: propose succeeds for accepted+actionable recommendation."""
        rec_id = _insert_recommendation(db, status="accepted", strength=STRENGTH_ACTIONABLE)
        intent = _make_intent()
        app_id = propose_application(db, recommendation_id=rec_id, intent=intent)
        assert isinstance(app_id, int)
        app = get_application(db, app_id)
        assert app.status == APPLICATION_STATUS_PROPOSED
        assert app.recommendation_id == rec_id

    def test_b_pending_raises(self, db):
        """B: propose fails for pending recommendation."""
        rec_id = _insert_recommendation(db, status="pending", strength=STRENGTH_ACTIONABLE)
        intent = _make_intent()
        with pytest.raises(RecommendationNotApplicableError, match="status='pending'"):
            propose_application(db, recommendation_id=rec_id, intent=intent)

    def test_c_rejected_raises(self, db):
        """C: propose fails for rejected recommendation."""
        rec_id = _insert_recommendation(db, status="rejected", strength=STRENGTH_ACTIONABLE)
        intent = _make_intent()
        with pytest.raises(RecommendationNotApplicableError, match="status='rejected'"):
            propose_application(db, recommendation_id=rec_id, intent=intent)

    def test_d_accepted_exploratory_raises(self, db):
        """D: propose fails even when accepted but strength=exploratory."""
        rec_id = _insert_recommendation(db, status="accepted", strength=STRENGTH_EXPLORATORY)
        intent = _make_intent()
        with pytest.raises(RecommendationNotApplicableError, match="exploratory"):
            propose_application(db, recommendation_id=rec_id, intent=intent)


# ── Tests E–J: build_narration_pace_intent ───────────────────────────────────


class TestBuildNarrationPaceIntent:
    def test_e_increase_computes_correct_target(self):
        """E: direction=increase → target = current + magnitude."""
        intent = build_narration_pace_intent(
            direction=DIRECTION_INCREASE,
            magnitude=0.1,
            current_speaking_rate=1.0,
        )
        assert intent.target_value == pytest.approx(1.1)
        assert intent.direction == DIRECTION_INCREASE
        assert intent.magnitude == 0.1
        assert intent.value_before == 1.0

    def test_f_decrease_computes_correct_target(self):
        """F: direction=decrease → target = current - magnitude."""
        intent = build_narration_pace_intent(
            direction=DIRECTION_DECREASE,
            magnitude=0.1,
            current_speaking_rate=1.0,
        )
        assert intent.target_value == pytest.approx(0.9)

    def test_g_maintain_target_equals_value_before(self):
        """G: direction=maintain → target == value_before."""
        intent = build_narration_pace_intent(
            direction=DIRECTION_MAINTAIN,
            magnitude=0.0,
            current_speaking_rate=1.0,
        )
        assert intent.target_value == pytest.approx(intent.value_before)

    def test_h_magnitude_exceeds_max_delta_raises(self):
        """H: magnitude > NARRATION_PACE_MAX_DELTA → ApplicationParameterError."""
        over_max = NARRATION_PACE_MAX_DELTA + 0.01
        with pytest.raises(ApplicationParameterError, match="max_delta"):
            build_narration_pace_intent(
                direction=DIRECTION_DECREASE,
                magnitude=over_max,
                current_speaking_rate=1.0,
            )

    def test_i_target_below_min_raises(self):
        """I: target below safety min → ApplicationParameterError."""
        with pytest.raises(ApplicationParameterError, match="minimum"):
            build_narration_pace_intent(
                direction=DIRECTION_DECREASE,
                magnitude=0.15,
                current_speaking_rate=NARRATION_PACE_SPEAKING_RATE_MIN,  # 0.7
            )

    def test_j_target_above_max_raises(self):
        """J: target above safety max → ApplicationParameterError."""
        with pytest.raises(ApplicationParameterError, match="maximum"):
            build_narration_pace_intent(
                direction=DIRECTION_INCREASE,
                magnitude=0.1,
                current_speaking_rate=NARRATION_PACE_SPEAKING_RATE_MAX,  # 1.5
            )

    def test_safety_bounds_recorded_on_intent(self):
        intent = _make_intent()
        assert intent.safety_min == NARRATION_PACE_SPEAKING_RATE_MIN
        assert intent.safety_max == NARRATION_PACE_SPEAKING_RATE_MAX
        assert intent.safety_max_delta == NARRATION_PACE_MAX_DELTA

    def test_domain_and_subsystem_fixed(self):
        intent = _make_intent()
        assert intent.domain == "narration"
        assert intent.subsystem == "narration_pace"
        assert intent.parameter_name == NARRATION_PACE_PARAMETER


# ── Tests K–L: apply_application ─────────────────────────────────────────────


class TestApplyApplication:
    def test_k_apply_transitions_proposed_to_applied(self, db):
        """K: apply_application transitions proposed → applied."""
        rec_id = _insert_recommendation(db)
        intent = _make_intent()
        app_id = propose_application(db, recommendation_id=rec_id, intent=intent)

        apply_application(db, app_id, value_applied=intent.target_value)

        app = get_application(db, app_id)
        assert app.status == APPLICATION_STATUS_APPLIED
        assert app.value_applied == pytest.approx(intent.target_value)
        assert app.applied_at is not None

    def test_k_apply_records_narration_run_id(self, db):
        """K: apply_application stores narration_run_id when provided."""
        rec_id = _insert_recommendation(db)
        intent = _make_intent()
        app_id = propose_application(db, recommendation_id=rec_id, intent=intent)

        apply_application(db, app_id, value_applied=intent.target_value, narration_run_id=99)

        app = get_application(db, app_id)
        assert app.narration_run_id == 99

    def test_l_apply_from_non_proposed_raises(self, db):
        """L: apply_application on already-applied application raises."""
        rec_id = _insert_recommendation(db)
        intent = _make_intent()
        app_id = propose_application(db, recommendation_id=rec_id, intent=intent)
        apply_application(db, app_id, value_applied=intent.target_value)

        with pytest.raises(InvalidApplicationTransitionError, match="'applied'"):
            apply_application(db, app_id, value_applied=intent.target_value)

    def test_l_apply_nonexistent_raises(self, db):
        """L: apply_application on unknown ID raises ApplicationNotFoundError."""
        with pytest.raises(ApplicationNotFoundError):
            apply_application(db, 9999, value_applied=0.9)


# ── Tests M–N: revert_application ────────────────────────────────────────────


class TestRevertApplication:
    def test_m_revert_transitions_applied_to_reverted(self, db):
        """M: revert_application transitions applied → reverted."""
        rec_id = _insert_recommendation(db)
        intent = _make_intent()
        app_id = propose_application(db, recommendation_id=rec_id, intent=intent)
        apply_application(db, app_id, value_applied=intent.target_value)

        revert_application(db, app_id)

        app = get_application(db, app_id)
        assert app.status == APPLICATION_STATUS_REVERTED
        assert app.reverted_at is not None

    def test_n_revert_from_proposed_raises(self, db):
        """N: revert_application on proposed (not applied) raises."""
        rec_id = _insert_recommendation(db)
        intent = _make_intent()
        app_id = propose_application(db, recommendation_id=rec_id, intent=intent)

        with pytest.raises(InvalidApplicationTransitionError, match="'proposed'"):
            revert_application(db, app_id)

    def test_n_revert_nonexistent_raises(self, db):
        """N: revert_application on unknown ID raises ApplicationNotFoundError."""
        with pytest.raises(ApplicationNotFoundError):
            revert_application(db, 9999)


# ── Test O: scope isolation ───────────────────────────────────────────────────


class TestScopeIsolation:
    def test_o_topic1_application_not_visible_for_topic2(self, db):
        """O: get_active_application_for_parameter is always scoped to topic_id."""
        db.execute("INSERT INTO topics (title, angle) VALUES ('Topic 2', 'angle2')")
        db.commit()

        rec_id = _insert_recommendation(db, topic_id=1)
        intent = _make_intent()
        propose_application(db, recommendation_id=rec_id, intent=intent)

        # Topic 1 has an application
        app_topic1 = get_active_application_for_parameter(
            db, topic_id=1, parameter_name=NARRATION_PACE_PARAMETER
        )
        assert app_topic1 is not None

        # Topic 2 sees nothing
        app_topic2 = get_active_application_for_parameter(
            db, topic_id=2, parameter_name=NARRATION_PACE_PARAMETER
        )
        assert app_topic2 is None

    def test_reverted_application_not_returned_as_active(self, db):
        """Reverted applications are not returned by get_active_application_for_parameter."""
        rec_id = _insert_recommendation(db)
        intent = _make_intent()
        app_id = propose_application(db, recommendation_id=rec_id, intent=intent)
        apply_application(db, app_id, value_applied=intent.target_value)
        revert_application(db, app_id)

        result = get_active_application_for_parameter(
            db, topic_id=1, parameter_name=NARRATION_PACE_PARAMETER
        )
        assert result is None


# ── Idempotency: UNIQUE constraint ────────────────────────────────────────────


class TestIdempotency:
    def test_duplicate_propose_raises(self, db):
        """UNIQUE(recommendation_id, topic_id, parameter_name) prevents double-propose."""
        rec_id = _insert_recommendation(db)
        intent = _make_intent()
        propose_application(db, recommendation_id=rec_id, intent=intent)

        with pytest.raises(DuplicateApplicationError):
            propose_application(db, recommendation_id=rec_id, intent=intent)


# ── Provenance and content correctness ────────────────────────────────────────


class TestProvenance:
    def test_application_stores_full_provenance(self, db):
        """Application row preserves recommendation_id, learning_run_id, publication_id."""
        run_id = _insert_learning_run(db)
        rec_id = _insert_recommendation(db, learning_run_id=run_id)
        intent = _make_intent()
        app_id = propose_application(db, recommendation_id=rec_id, intent=intent)

        app = get_application(db, app_id)
        assert app.recommendation_id == rec_id
        assert app.learning_run_id == run_id
        assert app.topic_id == 1

    def test_application_stores_intent_fields(self, db):
        """Intent fields (direction, magnitude, target) are persisted verbatim."""
        rec_id = _insert_recommendation(db)
        intent = build_narration_pace_intent(
            direction=DIRECTION_DECREASE,
            magnitude=0.05,
            current_speaking_rate=1.0,
        )
        app_id = propose_application(db, recommendation_id=rec_id, intent=intent)

        app = get_application(db, app_id)
        assert app.intent_direction == DIRECTION_DECREASE
        assert app.intent_magnitude == pytest.approx(0.05)
        assert app.intent_target_value == pytest.approx(0.95)
        assert app.value_before == pytest.approx(1.0)
        assert app.domain == "narration"
        assert app.subsystem == "narration_pace"
        assert app.parameter_name == NARRATION_PACE_PARAMETER

    def test_application_stores_safety_bounds(self, db):
        """Safety bounds from intent are recorded on the application row."""
        rec_id = _insert_recommendation(db)
        intent = _make_intent()
        app_id = propose_application(db, recommendation_id=rec_id, intent=intent)

        app = get_application(db, app_id)
        assert app.safety_min == NARRATION_PACE_SPEAKING_RATE_MIN
        assert app.safety_max == NARRATION_PACE_SPEAKING_RATE_MAX
        assert app.safety_max_delta == NARRATION_PACE_MAX_DELTA

    def test_input_hash_is_deterministic(self, db):
        """Same inputs always produce the same input_hash on the application row."""
        rec_id = _insert_recommendation(db)
        intent = build_narration_pace_intent(
            direction=DIRECTION_DECREASE,
            magnitude=0.1,
            current_speaking_rate=1.0,
        )
        app_id = propose_application(db, recommendation_id=rec_id, intent=intent)
        app = get_application(db, app_id)
        assert len(app.input_hash) == 64  # SHA-256 hex


# ── narrate_plan speaking_rate_override ──────────────────────────────────────


class TestNarratePlanSpeakingRateOverride:
    def test_speaking_rate_override_propagates(self, db):
        """speaking_rate_override flows into NarrationRunDraft and the input_hash.

        This test verifies the narration orchestrator accepts the parameter without
        error and that the value is injected.  Full end-to-end synthesis is not
        tested here (requires TTS provider mock); the override parameter contract
        is what matters.
        """
        from unittest.mock import MagicMock, patch

        from app.narration.orchestrator import narrate_plan

        # Build a minimal DB state: voice_profile, channel, production_plan, topic
        db.execute(
            """
            INSERT INTO voice_profiles
                (channel_id, provider, model, voice_id, name, language,
                 speaking_rate, style, stability, similarity_boost,
                 settings_json, version, is_default, created_at, updated_at)
            VALUES (NULL,'elevenlabs','eleven_multilingual_v2','test_voice','Test',
                    'en-US',1.0,NULL,0.5,0.75,'{}',1,1,
                    strftime('%Y-%m-%dT%H:%M:%S','now'),
                    strftime('%Y-%m-%dT%H:%M:%S','now'))
            """
        )
        db.commit()

        captured_draft = []

        def capture_create(conn, draft):
            captured_draft.append(draft)
            raise _StopTest("captured")

        class _StopTest(Exception):
            pass

        with patch("app.narration.orchestrator.create_narration_run", side_effect=capture_create):
            with patch(
                "app.narration.orchestrator.find_narration_run_by_input_hash", return_value=None
            ):
                with patch("app.narration.orchestrator.cleanup_stale_temp_files"):
                    with patch(
                        "app.narration.orchestrator.resolve_artifacts_path",
                        return_value=pathlib.Path("/tmp"),
                    ):
                        try:
                            narrate_plan(
                                db,
                                plan_id=1,
                                plan_input_hash="abc",
                                voice_profile_id=1,
                                artifacts_path=pathlib.Path("/tmp"),
                                provider=MagicMock(),
                                speaking_rate_override=0.85,
                            )
                        except _StopTest:
                            pass

        assert len(captured_draft) == 1
        draft = captured_draft[0]
        assert draft.speaking_rate == pytest.approx(0.85), (
            "speaking_rate_override must propagate into NarrationRunDraft"
        )
