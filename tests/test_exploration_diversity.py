"""Phase 7A, §6/§18 — rolling diversity guards."""

from __future__ import annotations

from app.intelligence.exploration.cell import ExplorationCell
from app.intelligence.exploration.diversity import (
    DiversityPolicy,
    assess_candidate,
    distinct_domain_count,
    max_consecutive_same_domain,
)


def _cell(domain: str, fmt: str = "curiosity_explainer") -> ExplorationCell:
    return ExplorationCell(domain=domain, format=fmt, audience_value="curiosity")


class TestDomainRepetitionGuard:
    def test_within_repeat_budget_is_ok(self):
        recent = [_cell("science"), _cell("history")]
        assessment = assess_candidate(_cell("science"), recent)
        assert assessment.domain_repetition_ok  # 1 prior science < default max 2

    def test_exceeding_repeat_budget_is_not_ok(self):
        recent = [_cell("science"), _cell("science")]
        assessment = assess_candidate(_cell("science"), recent)
        assert not assessment.domain_repetition_ok

    def test_science_explainer_science_explainer_science_explainer_flagged(self):
        """§6's explicit example — three same-domain-same-format picks in a
        row must be flagged (format diversity does not rescue it, since the
        format is identical here too, but the guard fires on domain alone)."""
        recent = [_cell("science"), _cell("science")]
        assessment = assess_candidate(_cell("science"), recent)
        assert not assessment.ok


class TestFormatDoesNotMaskDomainRepetition:
    def test_varied_subtopics_same_domain_still_counts_as_repetition(self):
        """CRISPR, cosmology, string theory are distinct topics but the
        SAME high-level domain — the guard must count them as repeats."""
        recent = [_cell("science"), _cell("science")]
        # A third science pick, even with a different format, still trips
        # the domain guard (format diversity is a SEPARATE axis).
        assessment = assess_candidate(_cell("science", fmt="myth_bust"), recent)
        assert not assessment.domain_repetition_ok


class TestDistinctDomainFloor:
    def test_floor_met_with_enough_distinct_domains(self):
        recent = [_cell("science"), _cell("history"), _cell("psychology")]
        assessment = assess_candidate(_cell("personal_finance"), recent)
        assert assessment.distinct_domain_floor_met  # 4 distinct incl. candidate

    def test_floor_not_met_with_too_few_distinct_domains(self):
        recent = [_cell("science"), _cell("science"), _cell("science")]
        assessment = assess_candidate(_cell("science"), recent)
        assert not assessment.distinct_domain_floor_met  # only 1 distinct


class TestFormatRepetitionGuard:
    def test_format_diversity_checked_independently_of_domain(self):
        recent = [
            _cell("science", "curiosity_explainer"),
            _cell("history", "curiosity_explainer"),
            _cell("psychology", "curiosity_explainer"),
        ]
        assessment = assess_candidate(_cell("business_career", "curiosity_explainer"), recent)
        # domain is fine (all distinct), but format repeats 3x already
        assert assessment.domain_repetition_ok
        assert not assessment.format_repetition_ok


class TestDistinctDomainCountAndConsecutiveRuns:
    def test_distinct_domain_count_over_window(self):
        cells = [_cell("science"), _cell("science"), _cell("history"), _cell("psychology")]
        assert distinct_domain_count(cells, window=4) == 3

    def test_max_consecutive_same_domain(self):
        cells = [_cell("science"), _cell("science"), _cell("science"), _cell("history")]
        assert max_consecutive_same_domain(cells) == 3

    def test_max_consecutive_with_no_repeats(self):
        cells = [_cell("science"), _cell("history"), _cell("psychology")]
        assert max_consecutive_same_domain(cells) == 1

    def test_empty_history_has_zero_consecutive_run(self):
        assert max_consecutive_same_domain([]) == 0


class TestCustomPolicy:
    def test_custom_window_and_thresholds_respected(self):
        policy = DiversityPolicy(domain_window=2, domain_max_repeats=1)
        recent = [_cell("science")]
        assessment = assess_candidate(_cell("science"), recent, policy=policy)
        assert not assessment.domain_repetition_ok  # 1 prior >= max_repeats(1)
