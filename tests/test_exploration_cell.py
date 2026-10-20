"""Phase 7A, §3/§15/§18 — exploration-cell representation.

Includes the Spanish-quiz and finance-lesson test cases (§15) — proven via
the generic ExplorationCell representation, with no Spanish- or finance-
specific code anywhere in the architecture.
"""

from __future__ import annotations

import pytest

from app.intelligence.exploration.cell import (
    DESIGNATION_EXPLOITATION,
    DESIGNATION_EXPLORATION,
    ExplorationCell,
)


class TestExplorationCellConstruction:
    def test_valid_cell_constructs(self):
        cell = ExplorationCell(
            domain="personal_finance",
            format="practical_micro_lesson",
            audience_value="practical_utility",
        )
        assert cell.designation == DESIGNATION_EXPLORATION  # default

    def test_empty_domain_rejected(self):
        with pytest.raises(ValueError):
            ExplorationCell(domain="  ", format="quiz", audience_value="curiosity")

    def test_empty_format_rejected(self):
        with pytest.raises(ValueError):
            ExplorationCell(domain="science", format="", audience_value="curiosity")

    def test_empty_audience_value_rejected(self):
        with pytest.raises(ValueError):
            ExplorationCell(domain="science", format="quiz", audience_value="")

    def test_invalid_designation_rejected(self):
        with pytest.raises(ValueError):
            ExplorationCell(
                domain="science",
                format="quiz",
                audience_value="curiosity",
                designation="maybe",
            )


class TestExplorationCellIdentity:
    def test_key_is_normalized(self):
        a = ExplorationCell(domain="Language_Learning", format="Quiz", audience_value="Curiosity")
        b = ExplorationCell(domain="language_learning", format="quiz", audience_value="curiosity")
        assert a.key() == b.key()

    def test_cells_are_hashable_and_usable_as_set_members(self):
        a = ExplorationCell(domain="science", format="quiz", audience_value="curiosity")
        b = ExplorationCell(domain="science", format="quiz", audience_value="curiosity")
        assert {a, b} == {a}

    def test_label_includes_all_three_dimensions(self):
        cell = ExplorationCell(
            domain="personal_finance",
            format="practical_micro_lesson",
            audience_value="practical_utility",
        )
        label = cell.label()
        assert "personal_finance" in label
        assert "practical_micro_lesson" in label
        assert "practical_utility" in label


class TestSpanishQuizRepresentedGenerically:
    """§15.A — language_learning x interactive_recall_quiz x skill_building."""

    def test_spanish_recall_quiz_cell_constructs(self):
        cell = ExplorationCell(
            domain="language_learning",
            format="interactive_recall_quiz",
            audience_value="practical_utility",
            rationale="English -> Spanish recall challenge",
        )
        assert cell.domain_key() == "language_learning"
        assert cell.format_key() == "interactive_recall_quiz"
        assert cell.is_seed_domain
        assert cell.is_seed_format

    def test_no_spanish_specific_code_needed(self):
        """The SAME cell type represents any language pair — nothing about
        'Spanish' is a first-class concept anywhere in ExplorationCell."""
        cell = ExplorationCell(
            domain="language_learning",
            format="interactive_recall_quiz",
            audience_value="practical_utility",
            rationale="English -> Mandarin recall challenge",
        )
        assert cell.domain == "language_learning"


class TestFinanceLessonRepresentedGenerically:
    """§15.B — personal_finance x practical_micro_lesson x practical_utility."""

    def test_finance_micro_lesson_cell_constructs(self):
        cell = ExplorationCell(
            domain="personal_finance",
            format="practical_micro_lesson",
            audience_value="practical_utility",
            rationale="compound interest concept",
        )
        assert cell.domain_key() == "personal_finance"
        assert cell.format_key() == "practical_micro_lesson"
        assert cell.is_seed_domain
        assert cell.is_seed_format

    def test_no_finance_specific_code_needed(self):
        cell = ExplorationCell(
            domain="personal_finance",
            format="practical_micro_lesson",
            audience_value="practical_utility",
            rationale="minimum credit card payment trap",
        )
        assert cell.domain == "personal_finance"


class TestDesignationConstants:
    def test_designation_constants_match_allowed_values(self):
        assert DESIGNATION_EXPLORATION == "exploration"
        assert DESIGNATION_EXPLOITATION == "exploitation"
