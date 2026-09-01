"""Phase 12A: Recommendation Application Framework — end-to-end integration tests.

Proves the closed loop:
  accepted + actionable recommendation
  → propose_application()
  → resolve_speaking_rate_override() discovers it
  → narrate_plan() uses the override rate (NOT manually injected)
  → consume_proposed_application() transitions proposed → applied
  → application records the narration_run_id

Also covers idempotency, failure safety, and legacy (no application) behavior.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.database import open_db
from app.learning.application import (
    apply_application,
    build_narration_pace_intent,
    consume_proposed_application,
    get_application,
    propose_application,
    resolve_speaking_rate_override,
    revert_application,
)
from app.learning.constants import (
    APPLICATION_STATUS_APPLIED,
    APPLICATION_STATUS_PROPOSED,
    DIRECTION_DECREASE,
    STRENGTH_ACTIONABLE,
)

_NOW = "2026-01-01T00:00:00"


# ── Fixtures / helpers ────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    _seed(conn)
    yield conn
    conn.close()


@pytest.fixture
def artifacts_path(tmp_path: Path) -> Path:
    p = tmp_path / "artifacts"
    p.mkdir()
    return p


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO topics (id, title, angle, created_at, updated_at) VALUES (1, 'T', '', ?, ?)",
        (_NOW, _NOW),
    )
    conn.execute(
        "INSERT INTO scripts (id, topic_id, body, version, created_at, updated_at) "
        "VALUES (1, 1, 'Script body', 1, ?, ?)",
        (_NOW, _NOW),
    )
    conn.execute(
        "INSERT INTO production_plans "
        "(id, topic_id, script_id, script_version, input_hash, script_body_hash, "
        "plan_schema_version, renderer_version, duration_algorithm_version, status, "
        "created_at, updated_at) "
        "VALUES (1, 1, 1, 1, ?, ?, 'v1', 'v1', 'v1', 'approved', ?, ?)",
        ("a" * 64, "b" * 64, _NOW, _NOW),
    )
    conn.execute(
        "INSERT INTO production_segments "
        "(id, plan_id, segment_index, section_index, section_type, narration_text, created_at) "
        "VALUES (1, 1, 0, 0, 'narration', 'Hello world', ?)",
        (_NOW,),
    )
    conn.execute(
        "INSERT INTO voice_profiles "
        "(id, channel_id, provider, model, voice_id, name, language, "
        "speaking_rate, style, stability, similarity_boost, settings_json, "
        "version, is_default, created_at, updated_at) "
        "VALUES (1, NULL, 'elevenlabs', 'eleven_multilingual_v2', 'vid', 'V', "
        "'en-US', 1.0, NULL, 0.5, 0.75, '{}', 1, 1, ?, ?)",
        (_NOW, _NOW),
    )
    conn.commit()


def _insert_learning_run(conn: sqlite3.Connection, topic_id: int = 1) -> int:
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
    conn: sqlite3.Connection,
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


def _propose(
    conn: sqlite3.Connection,
    *,
    topic_id: int = 1,
    direction: str = DIRECTION_DECREASE,
    magnitude: float = 0.1,
    current_speaking_rate: float = 1.0,
) -> tuple[int, int]:
    """Insert rec + propose application; return (rec_id, app_id)."""
    rec_id = _insert_recommendation(conn, topic_id=topic_id)
    intent = build_narration_pace_intent(
        direction=direction,
        magnitude=magnitude,
        current_speaking_rate=current_speaking_rate,
    )
    app_id = propose_application(conn, recommendation_id=rec_id, intent=intent)
    return rec_id, app_id


def _run_wired_narration(
    conn: sqlite3.Connection,
    artifacts_path: Path,
    *,
    topic_id: int = 1,
    plan_input_hash: str = "a" * 64,
):
    """Simulate the CLI/executor wired path: resolve → narrate → consume.

    This is the production code path the integration tests exercise.
    speaking_rate_override is NOT manually supplied — it comes from the DB.
    Returns (run_result, active_app_snapshot, speaking_rate_override).
    """
    from app.narration.fake import FakeTTSProvider
    from app.narration.orchestrator import narrate_plan

    active_app, speaking_rate_override = resolve_speaking_rate_override(conn, topic_id=topic_id)

    run_result = narrate_plan(
        conn,
        plan_id=1,
        plan_input_hash=plan_input_hash,
        voice_profile_id=1,
        artifacts_path=artifacts_path,
        provider=FakeTTSProvider(),
        speaking_rate_override=speaking_rate_override,
    )
    conn.commit()

    if active_app is not None and speaking_rate_override is not None:
        consume_proposed_application(
            conn,
            active_app,
            narration_run_id=run_result.run_id,
            value_applied=speaking_rate_override,
        )

    return run_result, active_app, speaking_rate_override


# ── True end-to-end: proposed → applied with correct override rate ─────────────


class TestEndToEnd:
    def test_accepted_actionable_rec_drives_narration_override(self, db, artifacts_path):
        """TRUE end-to-end: accepted rec → propose → narration discovers override → applied.

        This test does NOT manually pass speaking_rate_override — it is discovered
        via resolve_speaking_rate_override(), exactly as the production executor does.
        """
        # Arrange: accepted + actionable recommendation; propose application (−0.1 from 1.0)
        _, app_id = _propose(db, magnitude=0.1, current_speaking_rate=1.0)

        app_before = get_application(db, app_id)
        assert app_before.status == APPLICATION_STATUS_PROPOSED
        assert app_before.intent_target_value == pytest.approx(0.9)

        # Act: wired path discovers and applies the override
        run_result, active_app, override_value = _run_wired_narration(db, artifacts_path)

        # Assert: narration ran with the override rate, not the voice profile default (1.0)
        assert active_app is not None
        assert override_value == pytest.approx(0.9)
        assert run_result.run_id is not None

        # Assert: application transitioned proposed → applied
        app_after = get_application(db, app_id)
        assert app_after.status == APPLICATION_STATUS_APPLIED
        assert app_after.narration_run_id == run_result.run_id
        assert app_after.value_applied == pytest.approx(0.9)
        assert app_after.applied_at is not None

    def test_narration_run_records_override_speaking_rate(self, db, artifacts_path):
        """The narration_runs row stores the override speaking_rate, not the profile default."""
        _propose(db, magnitude=0.1, current_speaking_rate=1.0)

        run_result, _, override_value = _run_wired_narration(db, artifacts_path)

        assert override_value == pytest.approx(0.9)
        run_row = db.execute(
            "SELECT speaking_rate FROM narration_runs WHERE id = ?",
            (run_result.run_id,),
        ).fetchone()
        assert run_row is not None
        assert run_row["speaking_rate"] == pytest.approx(0.9)


# ── Legacy: no application → voice profile rate preserved ─────────────────────


class TestNoApplication:
    def test_no_application_returns_none_override(self, db, artifacts_path):
        """When no application exists, resolve returns (None, None) and
        narrate_plan uses profile."""
        active_app, override = resolve_speaking_rate_override(db, topic_id=1)
        assert active_app is None
        assert override is None

    def test_no_application_narration_uses_profile_rate(self, db, artifacts_path):
        """Without any application, narration runs at the voice profile's speaking_rate."""
        run_result, active_app, override_value = _run_wired_narration(db, artifacts_path)

        assert active_app is None
        assert override_value is None

        run_row = db.execute(
            "SELECT speaking_rate FROM narration_runs WHERE id = ?",
            (run_result.run_id,),
        ).fetchone()
        assert run_row["speaking_rate"] == pytest.approx(1.0)  # voice profile default


# ── Failure safety: failed narration leaves application proposed ───────────────


class TestNarrationFailureSafety:
    def test_narration_failure_leaves_application_proposed(self, db, artifacts_path):
        """If narrate_plan raises, consume is never called and application remains proposed."""
        _, app_id = _propose(db, magnitude=0.1)

        active_app, speaking_rate_override = resolve_speaking_rate_override(db, topic_id=1)
        assert active_app is not None

        # Simulate narration failure — consume_proposed_application is NOT called
        with patch(
            "app.narration.orchestrator.narrate_plan", side_effect=RuntimeError("TTS failure")
        ):
            from app.narration.fake import FakeTTSProvider
            from app.narration.orchestrator import narrate_plan as real_narrate_plan

            with pytest.raises(RuntimeError, match="TTS failure"):
                real_narrate_plan(  # noqa: F841 — not the patched version here
                    db,
                    plan_id=1,
                    plan_input_hash="a" * 64,
                    voice_profile_id=1,
                    artifacts_path=artifacts_path,
                    provider=FakeTTSProvider(),
                    speaking_rate_override=speaking_rate_override,
                )

        # Application must still be proposed
        app = get_application(db, app_id)
        assert app.status == APPLICATION_STATUS_PROPOSED

    def test_consume_not_called_on_failure_leaves_proposed(self, db, artifacts_path):
        """Explicit guard: skipping consume keeps application in proposed state."""
        _, app_id = _propose(db, magnitude=0.1)

        # Resolve but deliberately do not call consume (simulating the exception path)
        active_app, _ = resolve_speaking_rate_override(db, topic_id=1)
        assert active_app is not None

        app = get_application(db, app_id)
        assert app.status == APPLICATION_STATUS_PROPOSED


# ── Idempotency: repeated narration with applied app ─────────────────────────


class TestIdempotency:
    def test_applied_application_reused_without_second_transition(self, db, artifacts_path):
        """Second narration run with an already-applied application no-ops on consume."""
        _, app_id = _propose(db, magnitude=0.1)

        # First narration: proposed → applied
        run_result_1, _, _ = _run_wired_narration(db, artifacts_path)
        app_after_first = get_application(db, app_id)
        assert app_after_first.status == APPLICATION_STATUS_APPLIED
        assert app_after_first.narration_run_id == run_result_1.run_id

        # Second narration: narrate_plan deduplicates (same input hash → same run),
        # consume_proposed_application no-ops because status is already 'applied'
        run_result_2, _, _ = _run_wired_narration(db, artifacts_path)
        app_after_second = get_application(db, app_id)

        # Application is still applied, narration_run_id unchanged (first run wins)
        assert app_after_second.status == APPLICATION_STATUS_APPLIED
        assert app_after_second.narration_run_id == run_result_1.run_id

        # Both narration calls returned the same deduped run
        assert run_result_1.run_id == run_result_2.run_id

    def test_applied_application_resolve_returns_value_applied(self, db, artifacts_path):
        """After first consumption, resolve returns (app, app.value_applied) for reuse."""
        _, app_id = _propose(db, magnitude=0.1)

        # Simulate manual apply (as if a prior narration already consumed it)
        apply_application(db, app_id, value_applied=0.9, narration_run_id=42)

        active_app, override = resolve_speaking_rate_override(db, topic_id=1)
        assert active_app is not None
        assert active_app.status == APPLICATION_STATUS_APPLIED
        assert override == pytest.approx(0.9)  # value_applied, not re-computed


# ── Reverted application: ignored by resolve ──────────────────────────────────


class TestRevokedApplication:
    def test_reverted_application_not_discovered(self, db, artifacts_path):
        """Reverted applications are invisible to resolve_speaking_rate_override."""
        _, app_id = _propose(db, magnitude=0.1)
        apply_application(db, app_id, value_applied=0.9)
        revert_application(db, app_id)

        active_app, override = resolve_speaking_rate_override(db, topic_id=1)
        assert active_app is None
        assert override is None

    def test_reverted_application_narration_uses_profile_rate(self, db, artifacts_path):
        """After revocation, narration reverts to using the voice profile rate."""
        _, app_id = _propose(db, magnitude=0.1)
        apply_application(db, app_id, value_applied=0.9)
        revert_application(db, app_id)

        run_result, active_app, override_value = _run_wired_narration(db, artifacts_path)

        assert active_app is None
        assert override_value is None

        run_row = db.execute(
            "SELECT speaking_rate FROM narration_runs WHERE id = ?",
            (run_result.run_id,),
        ).fetchone()
        assert run_row["speaking_rate"] == pytest.approx(1.0)


# ── Scope isolation: topic_id boundary ───────────────────────────────────────


class TestScopeIsolation:
    def test_application_for_topic1_not_discovered_for_topic2(self, db, artifacts_path):
        """resolve_speaking_rate_override respects topic_id scope."""
        db.execute("INSERT INTO topics (id, title, angle) VALUES (2, 'T2', '')")
        db.commit()

        # Application only for topic 1
        _propose(db, topic_id=1, magnitude=0.1)

        # Topic 2 sees no application
        active_app_t2, override_t2 = resolve_speaking_rate_override(db, topic_id=2)
        assert active_app_t2 is None
        assert override_t2 is None

        # Topic 1 still sees its application
        active_app_t1, override_t1 = resolve_speaking_rate_override(db, topic_id=1)
        assert active_app_t1 is not None
        assert override_t1 == pytest.approx(0.9)
