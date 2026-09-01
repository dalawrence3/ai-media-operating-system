"""Phase 18E closure — slot retirement, queue deadlock, and topic specificity.

Three defects, one causal chain:

  a topic that was a category ("history and society")
    → a script with nothing concrete to show
      → a render that was 84% typeset narration
        → a visual-quality block
          → a slot that could never leave the queue

The tests are grouped by that chain rather than by module, because fixing any
one link alone leaves the system broken in a different place.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.ai.fake import FakeProvider
from app.core.database import open_db
from app.intelligence.autonomy.models import (
    DETERMINISTIC_ARTIFACT_FAILURES,
    RETRYABLE_PUBLISH_FAILURES,
    TERMINAL_PUBLISH_STATUSES,
    PublishFailureCategory,
    PublishOutcome,
)
from app.intelligence.autonomy.repository import (
    _NOT_TERMINAL_SQL,
    _slot_key_is_spent,
    find_slot_ready_to_publish,
    get_slot,
    list_active_slots,
    retire_slot,
)
from app.intelligence.experiments.eligibility import EligibilityPolicy
from app.intelligence.experiments.eligibility_service import (
    TopicSpecificityResult,
    assess_semantic_fit,
    evaluate_topic_specificity,
)

CH = "ch-closure"
WS = "ws-closure"


@pytest.fixture()
def sdb(tmp_path: Path) -> sqlite3.Connection:
    """Schema-complete DB with FK enforcement relaxed for slot fixtures."""
    conn = open_db(tmp_path / "closure.db")
    conn.execute("PRAGMA foreign_keys = OFF")
    return conn


def _slot(
    conn: sqlite3.Connection,
    slot_id: int,
    *,
    slot_key: str,
    due: str = "2026-09-01T13:00:00",
    state: str = "filled",
    production_status: str | None = "ready",
    publish_status: str | None = None,
    plan_id: int | None = 5,
    retries: int = 0,
) -> None:
    now = "2026-08-30T00:00:00"
    conn.execute(
        """INSERT INTO publishing_slots
           (id, channel_id, workspace_id, slot_key, scheduled_for_local, timezone,
            scheduled_for_utc, state, reserved_at, created_at, updated_at,
            production_status, production_publishing_plan_id, publish_status,
            publish_retry_count)
           VALUES (?,?,?,?,?, 'America/New_York', ?,?,?,?,?,?,?,?,?)""",
        (
            slot_id,
            CH,
            WS,
            slot_key,
            due,
            due,
            state,
            now,
            now,
            now,
            production_status,
            plan_id,
            publish_status,
            retries,
        ),
    )
    conn.commit()


# ── 1. Retirement is terminal, honest, and cheap ─────────────────────────────


class TestSlotRetirement:
    def test_retirement_releases_queue_occupancy(self, sdb):
        _slot(sdb, 1, slot_key="2026-08-31")
        assert [s.id for s in list_active_slots(sdb, CH)] == [1]

        retire_slot(
            sdb,
            1,
            category=PublishFailureCategory.ARTIFACT_QUALITY_BLOCKED.value,
            reason="visual_quality_blocked: 16% meaningful runtime",
        )

        assert list_active_slots(sdb, CH) == []

    def test_retired_slot_is_never_selected_for_publishing(self, sdb):
        _slot(sdb, 1, slot_key="2026-08-31")
        assert find_slot_ready_to_publish(sdb, CH).id == 1

        retire_slot(
            sdb, 1, category=PublishFailureCategory.ARTIFACT_QUALITY_BLOCKED.value, reason="blocked"
        )

        assert find_slot_ready_to_publish(sdb, CH) is None

    def test_retirement_consumes_no_retry_budget(self, sdb):
        """The whole point: retrying cannot change a verdict about the artifact."""
        _slot(sdb, 1, slot_key="2026-08-31")
        retire_slot(
            sdb, 1, category=PublishFailureCategory.ARTIFACT_QUALITY_BLOCKED.value, reason="blocked"
        )
        assert get_slot(sdb, 1).publish_retry_count == 0

    def test_retirement_preserves_history(self, sdb):
        _slot(sdb, 1, slot_key="2026-08-31", due="2026-08-31T13:00:00")
        retire_slot(
            sdb, 1, category=PublishFailureCategory.ARTIFACT_QUALITY_BLOCKED.value, reason="blocked"
        )
        slot = get_slot(sdb, 1)

        # The row must still say what was attempted and when.
        assert slot.slot_key == "2026-08-31"
        assert slot.scheduled_for_utc == "2026-08-31T13:00:00"
        assert slot.state == "filled"
        assert slot.production_publishing_plan_id == 5
        assert slot.production_status == "ready"

    def test_retirement_is_idempotent(self, sdb):
        """A restart mid-cycle must not rewrite when or why a slot was retired."""
        _slot(sdb, 1, slot_key="2026-08-31")
        retire_slot(
            sdb,
            1,
            category=PublishFailureCategory.ARTIFACT_QUALITY_BLOCKED.value,
            reason="the original reason",
        )
        first = get_slot(sdb, 1)

        retire_slot(sdb, 1, category="SOMETHING_ELSE", reason="a later, different reason")
        second = get_slot(sdb, 1)

        assert second.retired_at == first.retired_at
        assert second.retirement_reason == "the original reason"
        assert second.publish_failure_category == first.publish_failure_category
        assert second.publish_retry_count == 0

    def test_retirement_records_a_truthful_reason(self, sdb):
        _slot(sdb, 1, slot_key="2026-08-31")
        retire_slot(
            sdb,
            1,
            category=PublishFailureCategory.ARTIFACT_QUALITY_BLOCKED.value,
            reason="visual_quality_blocked: only 16% meaningful runtime",
        )
        slot = get_slot(sdb, 1)

        assert slot.retired is True
        assert "visual_quality_blocked" in slot.retirement_reason
        assert slot.publish_failure_category == "ARTIFACT_QUALITY_BLOCKED"

    def test_channel_isolation(self, sdb):
        _slot(sdb, 1, slot_key="2026-08-31")
        now = "2026-08-30T00:00:00"
        sdb.execute(
            """INSERT INTO publishing_slots
               (id, channel_id, workspace_id, slot_key, scheduled_for_local, timezone,
                scheduled_for_utc, state, reserved_at, created_at, updated_at,
                production_status, publish_retry_count)
               VALUES (2,'other-channel',?, '2026-08-31',?,'UTC','2026-08-31T13:00:00',
                       'filled',?,?,?,'ready',0)""",
            (WS, now, now, now, now),
        )
        sdb.commit()

        retire_slot(
            sdb, 1, category=PublishFailureCategory.ARTIFACT_QUALITY_BLOCKED.value, reason="blocked"
        )

        assert list_active_slots(sdb, CH) == []
        assert [s.id for s in list_active_slots(sdb, "other-channel")] == [2]


# ── 2. Terminality is defined once ───────────────────────────────────────────


class TestTerminalityIsCentralised:
    def test_the_shared_predicate_covers_both_ways_out(self):
        """The deadlock came from a query honouring one exit and missing another."""
        assert "retired_at IS NULL" in _NOT_TERMINAL_SQL
        assert "publish_status NOT IN" in _NOT_TERMINAL_SQL

    def test_failed_is_still_not_a_terminal_status(self):
        """Transient failures must stay retryable; retirement is the terminal path."""
        assert "failed" not in TERMINAL_PUBLISH_STATUSES

    def test_deterministic_failures_are_not_retryable(self):
        assert not (DETERMINISTIC_ARTIFACT_FAILURES & RETRYABLE_PUBLISH_FAILURES)
        assert PublishFailureCategory.ARTIFACT_QUALITY_BLOCKED in DETERMINISTIC_ARTIFACT_FAILURES
        # A provider outage might succeed next tick and must keep its retries.
        assert PublishFailureCategory.UPLOAD_FAILED_RETRYABLE in RETRYABLE_PUBLISH_FAILURES

    def test_a_spent_cadence_key_is_recognised(self, sdb):
        _slot(sdb, 1, slot_key="2026-08-31")
        assert _slot_key_is_spent(sdb, CH, "2026-08-31") is False

        retire_slot(
            sdb, 1, category=PublishFailureCategory.ARTIFACT_QUALITY_BLOCKED.value, reason="blocked"
        )
        assert _slot_key_is_spent(sdb, CH, "2026-08-31") is True

    def test_an_active_slot_key_is_not_spent(self, sdb):
        """Resuming an in-flight slot is the case reserve_slot exists to serve."""
        _slot(sdb, 1, slot_key="2026-08-31", state="reserved", production_status=None)
        assert _slot_key_is_spent(sdb, CH, "2026-08-31") is False


# ── 3. The publishing cycle retires rather than retrying ─────────────────────


def _blocked_assessment(conn: sqlite3.Connection, render_manifest_id: int) -> None:
    conn.execute(
        """INSERT INTO render_visual_assessments
           (render_manifest_id, scene_manifest_id, assessment_version,
            composition_version, policy_version, status, total_beat_count,
            total_duration_ms, findings_json, input_hash, created_at, updated_at)
           VALUES (?, 7, 'v', 'v', 'v', 'blocked', 18, 69474, ?, 'h', 'now', 'now')""",
        (
            render_manifest_id,
            json.dumps(
                [
                    {
                        "code": "visual_meaningful_runtime_below_floor",
                        "severity": "blocking",
                        "message": "Only 16% of runtime carries a meaningful visual (floor 25%).",
                        "evidence": {},
                    },
                ]
            ),
        ),
    )
    conn.commit()


class TestDeterministicQualityBlock:
    def test_a_blocked_render_is_detected_from_its_stored_assessment(self, sdb):
        from app.intelligence.autonomy.publishing_cycle import _deterministic_quality_block

        _blocked_assessment(sdb, 9)
        plan = type("P", (), {"render_manifest_id": 9, "id": 5, "status": "draft"})()

        reason = _deterministic_quality_block(sdb, plan)
        assert reason is not None
        assert "visual_quality_blocked" in reason
        assert "16%" in reason

    def test_a_missing_assessment_is_not_a_block(self, sdb):
        """Unmeasured is not the same as bad — pre-18E renders must still publish."""
        from app.intelligence.autonomy.publishing_cycle import _deterministic_quality_block

        plan = type("P", (), {"render_manifest_id": 999, "id": 5, "status": "draft"})()
        assert _deterministic_quality_block(sdb, plan) is None

    def test_a_passing_assessment_is_not_a_block(self, sdb):
        from app.intelligence.autonomy.publishing_cycle import _deterministic_quality_block

        sdb.execute(
            """INSERT INTO render_visual_assessments
               (render_manifest_id, scene_manifest_id, assessment_version,
                composition_version, policy_version, status, input_hash,
                created_at, updated_at)
               VALUES (11, 7, 'v','v','v','pass','h','now','now')"""
        )
        sdb.commit()
        plan = type("P", (), {"render_manifest_id": 11, "id": 5, "status": "draft"})()
        assert _deterministic_quality_block(sdb, plan) is None

    def test_retire_helper_sets_the_outcome_without_spending_retries(self, sdb):
        from app.intelligence.autonomy.models import PublishingCycleResult
        from app.intelligence.autonomy.publishing_cycle import _retire

        _slot(sdb, 1, slot_key="2026-08-31")
        result = PublishingCycleResult(channel_id=CH, workspace_id=WS, slot_id=1, started_at="now")
        _retire(
            sdb,
            1,
            result,
            category=PublishFailureCategory.ARTIFACT_QUALITY_BLOCKED,
            reason="visual_quality_blocked: 16% meaningful",
        )

        assert result.outcome == PublishOutcome.RETIRED
        assert result.retired is True
        assert result.failure_category == "ARTIFACT_QUALITY_BLOCKED"
        assert get_slot(sdb, 1).publish_retry_count == 0

    def test_repeated_ticks_do_nothing_to_a_retired_slot(self, sdb):
        """Restart-safety: the retired artifact must not be re-engaged."""
        _slot(sdb, 1, slot_key="2026-08-31")
        retire_slot(
            sdb, 1, category=PublishFailureCategory.ARTIFACT_QUALITY_BLOCKED.value, reason="blocked"
        )
        before = get_slot(sdb, 1)

        for _ in range(5):
            assert find_slot_ready_to_publish(sdb, CH) is None

        after = get_slot(sdb, 1)
        assert after.retired_at == before.retired_at
        assert after.publish_retry_count == 0


# ── 4. Topic specificity ─────────────────────────────────────────────────────


def _fit_payload(**overrides) -> str:
    base = {
        "score": 0.9,
        "fit_label": "strong_fit",
        "rationale": "On-niche.",
        "topic_specificity": 0.9,
        "specificity_label": "concrete_topic",
        "visual_groundability": 0.9,
        "concrete_subjects": ["a specific thing"],
        "viewer_promise": "You will learn one specific thing.",
        "refined_topic": None,
    }
    base.update(overrides)
    return json.dumps(base)


def _assess(payload: str, policy: EligibilityPolicy | None = None):
    return assess_semantic_fit(
        opportunity_normalized_topic="t",
        opportunity_title="t",
        opportunity_topic_summary="",
        primary_niche="science and technology explained",
        audience_description="Curious adults",
        excluded_topics=[],
        ai_provider=FakeProvider(output=payload),
        policy=policy or EligibilityPolicy(),
    )


class TestTopicSpecificity:
    def test_a_broad_category_is_blocked_despite_a_strong_fit(self):
        """The exact defect: 'history and society' scored 0.8 strong_fit.

        The fit judgement was CORRECT — that topic genuinely suits a curious
        general audience. It was answering a different question from "is this
        a topic at all?", which is why tightening the fit threshold would have
        been the wrong fix.
        """
        score, _, findings, spec = _assess(
            _fit_payload(
                score=0.8,
                topic_specificity=0.15,
                specificity_label="broad_category",
                visual_groundability=0.2,
                concrete_subjects=[],
                refined_topic="Why Roman concrete outlasted modern concrete",
            )
        )
        blocks = [f for f in findings if f.severity == "block"]

        assert score == 0.8, "fit is not the thing that failed"
        assert [f.code for f in blocks] == ["topic_not_concrete"]
        assert spec.is_broad_category
        assert spec.refined_topic == "Why Roman concrete outlasted modern concrete"

    def test_a_concrete_topic_with_no_named_entity_is_accepted(self):
        """Named-entity count must never be the criterion.

        "Why airplane windows are rounded" names nothing and is an excellent
        visual topic; "innovation in the modern era" names nothing and is
        unusable. What separates them is whether there is something to show.
        """
        _, _, findings, spec = _assess(
            _fit_payload(
                topic_specificity=0.95,
                specificity_label="concrete_topic",
                visual_groundability=0.9,
                concrete_subjects=["aircraft window", "stress concentration at corners"],
            )
        )
        assert [f for f in findings if f.severity == "block"] == []
        assert spec.specificity_label == "concrete_topic"

    def test_an_ungroundable_topic_is_blocked(self):
        _, _, findings, _ = _assess(
            _fit_payload(
                topic_specificity=0.8,
                visual_groundability=0.1,
                concrete_subjects=[],
            )
        )
        assert [f.code for f in findings if f.severity == "block"] == [
            "topic_not_visually_groundable"
        ]

    def test_specificity_and_fit_are_independent_axes(self):
        # Perfect fit, zero specificity — the channel's own niche string.
        _, _, findings, _ = _assess(
            _fit_payload(
                score=1.0,
                fit_label="strong_fit",
                topic_specificity=0.05,
                specificity_label="broad_category",
                visual_groundability=0.1,
            )
        )
        codes = {f.code for f in findings}
        assert "semantic_fit_passed" in codes
        assert "topic_not_concrete" in codes

    def test_an_unevaluated_topic_warns_rather_than_blocks(self):
        """Fail-open on unknown, exactly like a missing visual assessment."""
        findings = evaluate_topic_specificity(TopicSpecificityResult(), policy=EligibilityPolicy())
        assert [f.severity for f in findings] == ["warn"]
        assert findings[0].code == "topic_specificity_not_evaluated"

    def test_a_prompt_v1_response_does_not_block(self):
        """Old cached answers have no specificity field; that is not a failure."""
        _, _, findings, spec = _assess(
            json.dumps(
                {
                    "score": 0.9,
                    "fit_label": "strong_fit",
                    "rationale": "ok",
                }
            )
        )
        assert spec.evaluated is False
        assert [f for f in findings if f.severity == "block"] == []

    def test_thresholds_are_policy_not_hardcoded(self):
        payload = _fit_payload(topic_specificity=0.6, specificity_label="narrow_theme")
        lenient = EligibilityPolicy(min_topic_specificity=0.5)
        strict = EligibilityPolicy(min_topic_specificity=0.8)

        assert [f for f in _assess(payload, lenient)[2] if f.severity == "block"] == []
        assert [f.code for f in _assess(payload, strict)[2] if f.severity == "block"] == [
            "topic_not_concrete"
        ]

    def test_no_allowed_topic_list_exists_anywhere(self):
        """The gate must judge shape, never membership of a curated list.

        Checks executable code only. A naive text scan fails on this module's
        own docstrings, which necessarily name the topic that motivated the
        gate — explaining a bug is not the same as hardcoding it.
        """
        import ast

        import app.intelligence.experiments.eligibility_service as svc

        tree = ast.parse(Path(svc.__file__).read_text())
        # Identify docstring nodes by identity. ast.get_docstring() returns a
        # cleaned (dedented, stripped) string that no longer equals the raw
        # Constant value, so comparing by value silently matches nothing.
        docstring_nodes = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef):
                continue
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_nodes.add(id(body[0].value))

        literals = [
            node.value.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_nodes
        ]
        for literal in literals:
            assert "history and society" not in literal
            assert "science and technology" not in literal

        names = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        assert not {n for n in names if "WHITELIST" in n or "ALLOWED_TOPIC" in n}

    def test_the_policy_defaults_to_the_specificity_aware_prompt(self):
        assert EligibilityPolicy().semantic_fit_prompt_version == "2"


class TestSpecificityCaching:
    def test_a_cached_row_replays_through_the_same_gate(self, sdb):
        from app.intelligence.experiments.eligibility_service import (
            _specificity_from_cache_row,
        )

        row = {
            "topic_specificity": 0.15,
            "specificity_label": "broad_category",
            "visual_groundability": 0.2,
            "concrete_subjects_json": json.dumps([]),
            "viewer_promise": "Learn about history.",
            "refined_topic": "Why Roman concrete outlasted modern concrete",
        }
        spec = _specificity_from_cache_row(row)
        assert spec.evaluated is True
        assert spec.is_broad_category

        findings = evaluate_topic_specificity(spec, policy=EligibilityPolicy())
        assert [f.code for f in findings if f.severity == "block"] == ["topic_not_concrete"]

    def test_a_row_cached_before_the_columns_existed_reads_as_unevaluated(self):
        from app.intelligence.experiments.eligibility_service import (
            _specificity_from_cache_row,
        )

        spec = _specificity_from_cache_row({"score": 0.8, "fit_label": "strong_fit"})
        assert spec.evaluated is False
        assert spec.topic_specificity is None, "NULL must not read as 0.0"

    def test_specificity_survives_a_save_and_reload(self, sdb):
        from app.intelligence.experiments.eligibility_service import (
            _specificity_from_cache_row,
            get_cached_semantic_fit,
            save_semantic_fit_result,
        )

        save_semantic_fit_result(
            sdb,
            opportunity_id=1,
            channel_id=1,
            channel_profile_version_id=None,
            prompt_version="2",
            input_hash="hash-1",
            score=0.8,
            fit_label="strong_fit",
            rationale="r",
            provider_name="anthropic",
            model="m",
            specificity=TopicSpecificityResult(
                evaluated=True,
                topic_specificity=0.15,
                specificity_label="broad_category",
                visual_groundability=0.2,
                concrete_subjects=[],
                viewer_promise="p",
                refined_topic="Why Roman concrete outlasted modern concrete",
            ),
        )
        cached = get_cached_semantic_fit(sdb, opportunity_id=1, input_hash="hash-1")
        spec = _specificity_from_cache_row(cached)

        assert spec.evaluated is True
        assert spec.specificity_label == "broad_category"
        assert spec.refined_topic == "Why Roman concrete outlasted modern concrete"


# ── 5. Migration ─────────────────────────────────────────────────────────────


class TestMigrationV51:
    def test_fresh_install_has_the_retirement_and_specificity_columns(self, tmp_path):
        conn = open_db(tmp_path / "fresh51.db")
        slot_cols = {r[1] for r in conn.execute("PRAGMA table_info('publishing_slots')")}
        assert {"retired_at", "retirement_reason"} <= slot_cols

        fit_cols = {
            r[1] for r in conn.execute("PRAGMA table_info('opportunity_semantic_fit_results')")
        }
        assert {
            "topic_specificity",
            "specificity_label",
            "visual_groundability",
            "concrete_subjects_json",
            "viewer_promise",
            "refined_topic",
        } <= fit_cols

    def test_schema_version_is_51(self, tmp_path):
        from app.core.database import SCHEMA_VERSION

        conn = open_db(tmp_path / "v51.db")
        assert SCHEMA_VERSION == 51
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 51
