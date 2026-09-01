"""Phase 18E.1 — activating the topic refinement the evaluator already produces.

Phase 18E generated a concrete framing for every narrow_theme candidate,
persisted it on the semantic-fit row, and then materialized the opportunity's
own title anyway. The live example:

    source      "universe edge boundaries cosmology"       (narrow_theme, 0.65)
    generated   "Does the universe have an edge, and what would it mean if it did?"
    produced    "universe edge boundaries cosmology"        ← the refinement was ignored

The three concepts these tests keep apart throughout:

    A  source market opportunity   — never rewritten, owns market attribution
    B  evaluated refinement        — the production-facing interpretation
    C  materialized content topic  — what script generation actually receives
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.core.database import open_db
from app.intelligence.experiments.eligibility import EligibilityPolicy
from app.intelligence.experiments.eligibility_service import (
    ProductionTopicDecision,
    select_production_topic,
)

# The live example, used verbatim as the regression case.
LIVE_SOURCE = "universe edge boundaries cosmology"
LIVE_REFINEMENT = "Does the universe have an edge, and what would it mean if it did?"
LIVE_PROMISE = (
    "Learn what lies at the edge of the universe and why the universe may not "
    "have a boundary at all."
)
LIVE_SUBJECTS = [
    "The observable universe boundary",
    "Cosmic horizon and light travel distance",
    "Cosmic microwave background radiation visualization",
]


@pytest.fixture()
def tdb(tmp_path: Path) -> sqlite3.Connection:
    conn = open_db(tmp_path / "refine.db")
    conn.execute("PRAGMA foreign_keys = OFF")
    return conn


def _evaluation(
    conn: sqlite3.Connection,
    opportunity_id: int,
    *,
    specificity: float | None = 0.65,
    label: str | None = "narrow_theme",
    groundability: float | None = 0.75,
    refined: str | None = LIVE_REFINEMENT,
    promise: str | None = LIVE_PROMISE,
    subjects: list[str] | None = None,
    channel_id: int = 1,
    evaluated_at: str = "2026-08-30T18:00:00",
    input_hash: str = "h1",
) -> None:
    conn.execute(
        """INSERT INTO opportunity_semantic_fit_results
           (opportunity_id, channel_id, prompt_version, input_hash, score, fit_label,
            rationale, provider_name, model, evaluated_at, topic_specificity,
            specificity_label, visual_groundability, concrete_subjects_json,
            viewer_promise, refined_topic)
           VALUES (?, ?, '2', ?, 0.9, 'strong_fit', 'r', 'anthropic', 'm', ?,
                   ?, ?, ?, ?, ?, ?)""",
        (
            opportunity_id,
            channel_id,
            input_hash,
            evaluated_at,
            specificity,
            label,
            groundability,
            json.dumps(LIVE_SUBJECTS if subjects is None else subjects),
            promise,
            refined,
        ),
    )
    conn.commit()


def _decide(
    conn: sqlite3.Connection, opportunity_id: int = 1, source: str = LIVE_SOURCE
) -> ProductionTopicDecision:
    return select_production_topic(conn, opportunity_id=opportunity_id, source_topic=source)


# ── Activation policy, by specificity label ──────────────────────────────────


class TestActivationPolicy:
    def test_narrow_theme_with_a_valid_refinement_is_promoted(self, tdb):
        """The case this phase exists for — the live example, verbatim."""
        _evaluation(tdb, 1)
        d = _decide(tdb)

        assert d.used_refinement is True
        assert d.production_topic == LIVE_REFINEMENT
        assert d.source_topic == LIVE_SOURCE, "A must survive B becoming C"
        assert d.changed is True

    def test_concrete_topic_keeps_its_original_title(self, tdb):
        """A topic that already reads as one subject is not churned for a paraphrase."""
        _evaluation(
            tdb,
            1,
            specificity=0.85,
            label="concrete_topic",
            refined="Some alternative phrasing of the same thing",
        )
        d = _decide(tdb, source="CRISPR gene editing technology")

        assert d.used_refinement is False
        assert d.production_topic == "CRISPR gene editing technology"
        assert "already concrete" in d.reason

    def test_broad_category_cannot_be_rescued_by_a_refinement(self, tdb):
        """A refinement must never be a backdoor around the eligibility block.

        Such candidates are blocked upstream (topic_not_concrete, severity
        block → INELIGIBLE) and never reach materialization. This refuses them
        again, so the property survives an upstream caller changing.
        """
        _evaluation(
            tdb,
            1,
            specificity=0.35,
            label="broad_category",
            groundability=0.5,
            refined="Why physicists think the universe is made of vibrating strings",
        )
        d = _decide(tdb, source="string theory quantum physics")

        assert d.used_refinement is False
        assert d.production_topic == "string theory quantum physics"
        assert "broad category" in d.reason

    def test_an_unevaluated_opportunity_keeps_its_original_title(self, tdb):
        """Fail-open, matching Phase 18E: 'we did not ask' is not 'we decided'."""
        d = _decide(tdb, source="history and society")

        assert d.used_refinement is False
        assert d.production_topic == "history and society"
        assert d.specificity_label is None

    def test_a_prompt_v1_row_without_specificity_keeps_the_original(self, tdb):
        _evaluation(tdb, 1, specificity=None, label=None, groundability=None, refined=None)
        d = _decide(tdb)

        assert d.used_refinement is False
        assert "never evaluated" in d.reason


# ── Refinement validation (no extra LLM call) ────────────────────────────────


class TestRefinementValidation:
    @pytest.mark.parametrize(
        ("refined", "expected_reason"),
        [
            (None, "no refinement"),
            ("", "blank"),
            ("   ", "blank"),
            ("too short", "fewer than"),
            ("word " * 40, "more than"),
            ("x" * 250, "exceeds"),
        ],
    )
    def test_malformed_refinements_do_not_replace_the_original(self, tdb, refined, expected_reason):
        _evaluation(tdb, 1, refined=refined)
        d = _decide(tdb)

        assert d.used_refinement is False
        assert d.production_topic == LIVE_SOURCE
        assert expected_reason in d.reason

    def test_a_refinement_identical_to_the_source_is_not_a_change(self, tdb):
        """Swapping a string for itself is churn dressed as a decision."""
        _evaluation(tdb, 1, refined="  Universe Edge   Boundaries Cosmology  ")
        d = _decide(tdb, source="universe edge boundaries cosmology")

        assert d.used_refinement is False
        assert "identical to the source" in d.reason

    def test_a_refinement_below_the_specificity_floor_is_not_promoted(self, tdb):
        """The refinement inherits the evaluation's authority, so that
        evaluation must have passed on its own terms first."""
        _evaluation(tdb, 1, specificity=0.45, label="narrow_theme")
        d = _decide(tdb)

        assert d.used_refinement is False
        assert "did not clear" in d.reason

    def test_a_refinement_below_the_groundability_floor_is_not_promoted(self, tdb):
        _evaluation(tdb, 1, specificity=0.65, label="narrow_theme", groundability=0.1)
        d = _decide(tdb)

        assert d.used_refinement is False
        assert "did not clear" in d.reason

    def test_thresholds_come_from_policy_not_from_constants(self, tdb):
        _evaluation(tdb, 1, specificity=0.55, label="narrow_theme")

        lenient = select_production_topic(
            tdb,
            opportunity_id=1,
            source_topic=LIVE_SOURCE,
            policy=EligibilityPolicy(min_topic_specificity=0.5),
        )
        strict = select_production_topic(
            tdb,
            opportunity_id=1,
            source_topic=LIVE_SOURCE,
            policy=EligibilityPolicy(min_topic_specificity=0.8),
        )
        assert lenient.used_refinement is True
        assert strict.used_refinement is False

    def test_no_llm_provider_is_ever_constructed(self, tdb, monkeypatch):
        """The refinement was produced by an evaluation already paid for.

        Validation here is deterministic by design; a second opinion would cost
        money to re-ask a question already answered with more context.
        """
        import app.ai.claude as claude_mod

        def _boom(*a, **k):
            raise AssertionError("an LLM provider was constructed during topic selection")

        monkeypatch.setattr(claude_mod, "ClaudeProvider", _boom)
        _evaluation(tdb, 1)
        assert _decide(tdb).used_refinement is True


# ── Lineage ──────────────────────────────────────────────────────────────────


def _opportunity(conn: sqlite3.Connection, opp_id: int, title: str) -> None:
    conn.execute(
        """INSERT INTO opportunities
           (id, channel_id, discovery_run_id, normalized_topic, raw_topic, title,
            current_lifecycle_state, created_at, updated_at)
           VALUES (?, 1, 1, ?, ?, ?, 'new', '2026-08-30', '2026-08-30')""",
        (opp_id, title.lower(), title, title),
    )
    conn.commit()


class TestLineage:
    def test_source_opportunity_is_never_rewritten(self, tdb):
        from app.intelligence.repository import get_opportunity, promote_opportunity

        _opportunity(tdb, 1, LIVE_SOURCE)
        _evaluation(tdb, 1)
        d = _decide(tdb)

        promote_opportunity(
            tdb, 1, title_override=d.production_topic, operator="test", allow_unscored=True
        )

        assert get_opportunity(tdb, 1).title == LIVE_SOURCE, (
            "market attribution depends on the opportunity keeping its own words"
        )

    def test_both_lineage_questions_are_answerable(self, tdb):
        """'What opportunity produced this?' and 'what topic did it use?'"""
        from app.intelligence.repository import promote_opportunity

        _opportunity(tdb, 1, LIVE_SOURCE)
        _evaluation(tdb, 1)
        d = _decide(tdb)
        topic, _ = promote_opportunity(
            tdb, 1, title_override=d.production_topic, operator="test", allow_unscored=True
        )

        row = tdb.execute(
            "SELECT t.title AS production, o.title AS source "
            "FROM topics t JOIN opportunities o ON o.id = t.promoted_opportunity_id "
            "WHERE t.id = ?",
            (topic.id,),
        ).fetchone()

        assert row["source"] == LIVE_SOURCE
        assert row["production"] == LIVE_REFINEMENT
        assert row["source"] != row["production"]

    def test_no_duplicate_topic_rows_on_re_materialization(self, tdb):
        from app.intelligence.repository import promote_opportunity

        _opportunity(tdb, 1, LIVE_SOURCE)
        _evaluation(tdb, 1)
        d = _decide(tdb)

        first, _ = promote_opportunity(
            tdb, 1, title_override=d.production_topic, operator="test", allow_unscored=True
        )
        second, _ = promote_opportunity(
            tdb, 1, title_override=d.production_topic, operator="test", allow_unscored=True
        )

        assert first.id == second.id
        assert (
            tdb.execute("SELECT COUNT(*) FROM topics WHERE promoted_opportunity_id = 1").fetchone()[
                0
            ]
            == 1
        )

    def test_an_already_materialized_topic_is_never_retitled(self, tdb):
        """The slot-4 protection, asserted structurally.

        promote_opportunity returns any existing topic before it looks at the
        override, so a produced artifact cannot be retroactively renamed even
        if a caller passes one.
        """
        from app.intelligence.repository import promote_opportunity

        _opportunity(tdb, 1, LIVE_SOURCE)
        original, _ = promote_opportunity(tdb, 1, operator="test", allow_unscored=True)
        assert original.title == LIVE_SOURCE

        again, _ = promote_opportunity(
            tdb,
            1,
            title_override="A COMPLETELY DIFFERENT TITLE",
            angle_override="A DIFFERENT ANGLE",
            operator="test",
            allow_unscored=True,
        )

        assert again.id == original.id
        assert again.title == LIVE_SOURCE
        assert again.angle == original.angle

    def test_no_override_preserves_pre_18e1_behaviour(self, tdb):
        from app.intelligence.repository import promote_opportunity

        _opportunity(tdb, 1, LIVE_SOURCE)
        topic, _ = promote_opportunity(tdb, 1, operator="test", allow_unscored=True)
        assert topic.title == LIVE_SOURCE

    def test_channel_isolation(self, tdb):
        """One channel's evaluation must never decide another's topic."""
        _evaluation(tdb, 1, channel_id=1)
        _evaluation(
            tdb,
            2,
            channel_id=2,
            refined="A totally different refined question here",
            input_hash="h2",
        )

        assert _decide(tdb, opportunity_id=1).production_topic == LIVE_REFINEMENT
        assert _decide(tdb, opportunity_id=2).production_topic == (
            "A totally different refined question here"
        )
        assert _decide(tdb, opportunity_id=3).used_refinement is False

    def test_the_newest_evaluation_wins(self, tdb):
        """A newer evaluation supersedes an older one by construction."""
        _evaluation(
            tdb,
            1,
            evaluated_at="2026-01-01T00:00:00",
            input_hash="old",
            refined="An older refined question about the universe edge",
        )
        _evaluation(tdb, 1, evaluated_at="2026-08-30T18:00:00", input_hash="new")

        assert _decide(tdb).production_topic == LIVE_REFINEMENT


# ── Semantic context forwarded downstream ────────────────────────────────────


class TestContextForwarding:
    def test_the_angle_carries_the_viewer_promise_and_subjects(self, tdb):
        from app.intelligence.autonomy.production_cycle import _build_topic_angle

        _evaluation(tdb, 1)
        d = _decide(tdb)

        class _Brief:
            canonical_topic = LIVE_SOURCE
            market_theme = "cosmology"

        angle = _build_topic_angle(brief=_Brief(), decision=d)

        assert LIVE_PROMISE in angle
        for subject in LIVE_SUBJECTS:
            assert subject in angle

    def test_the_angle_falls_back_to_the_brief_when_context_is_absent(self, tdb):
        """Pre-18E.1 behaviour is preserved when the evaluation offers nothing."""
        from app.intelligence.autonomy.production_cycle import _build_topic_angle

        d = _decide(tdb)  # no evaluation at all

        class _Brief:
            canonical_topic = "canonical topic text"
            market_theme = "theme"

        assert _build_topic_angle(brief=_Brief(), decision=d) == "canonical topic text"

    def test_the_angle_reaches_script_generation_unchanged_in_shape(self, tdb):
        """topic_angle is an EXISTING prompt variable — no contract widening."""
        from app.ai.registry import PromptRegistry

        prompt = PromptRegistry().get("script-generation", "1")
        assert "{topic_angle}" in prompt.user_template
        assert "{topic_title}" in prompt.user_template


# ── The live regression case, end to end ─────────────────────────────────────


class TestLiveShapeRegression:
    def test_the_universe_edge_case_materializes_the_refined_question(self, tdb):
        """Exactly the shape observed live on 2026-08-30."""
        from app.intelligence.autonomy.production_cycle import _build_topic_angle
        from app.intelligence.repository import get_opportunity, promote_opportunity

        _opportunity(tdb, 1, LIVE_SOURCE)
        _evaluation(tdb, 1, specificity=0.65, label="narrow_theme", groundability=0.75)

        d = _decide(tdb)

        class _Brief:
            canonical_topic = LIVE_SOURCE
            market_theme = "cosmology"

        topic, _ = promote_opportunity(
            tdb,
            1,
            angle_override=_build_topic_angle(brief=_Brief(), decision=d),
            title_override=d.production_topic if d.changed else None,
            operator="system:autonomy-production",
            allow_unscored=True,
        )

        # C — what production actually uses
        assert topic.title == LIVE_REFINEMENT
        # A — unchanged
        assert get_opportunity(tdb, 1).title == LIVE_SOURCE
        # lineage joins the two
        assert topic.promoted_opportunity_id == 1
        # context forwarded
        assert LIVE_PROMISE in topic.angle
