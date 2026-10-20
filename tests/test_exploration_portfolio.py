"""Phase 7A, §4/§5/§12/§18 — the exploration portfolio allocator."""

from __future__ import annotations

from app.intelligence.exploration.cell import ExplorationCell
from app.intelligence.exploration.diversity import DiversityPolicy
from app.intelligence.exploration.portfolio import (
    AllocationPolicy,
    choose_next_cell,
    exploit_ready_by_ratio,
    score_candidates,
)


def _cell(domain: str, fmt: str = "curiosity_explainer") -> ExplorationCell:
    return ExplorationCell(domain=domain, format=fmt, audience_value="curiosity")


_CANDIDATES = [
    _cell("science"),
    _cell("personal_finance"),
    _cell("language_learning"),
    _cell("psychology"),
]


class TestUnderExploredCellReceivesBonus:
    def test_zero_trial_cell_outranks_heavily_tried_cell_when_exploring(self):
        trial_counts = {("science", "curiosity_explainer"): 10}
        decision = choose_next_cell(
            _CANDIDATES, recent=[], trial_counts=trial_counts, exploit_ready=False
        )
        assert decision.cell.domain != "science"

    def test_information_value_decays_to_zero_past_the_floor(self):
        policy = AllocationPolicy(low_evidence_trial_floor=2)
        scored = score_candidates(
            _CANDIDATES,
            recent=[],
            trial_counts={("science", "curiosity_explainer"): 2},
            policy=policy,
        )
        science_score = next(s for s in scored if s.candidate.domain == "science")
        assert science_score.information_value == 0.0

    def test_never_tried_cell_gets_maximum_information_value(self):
        scored = score_candidates(_CANDIDATES, recent=[], trial_counts={})
        for s in scored:
            assert s.information_value == 1.0
            assert s.trial_count == 0


class TestFormatLevelFairness:
    """Phase 7B, §1 — a format with many domain seeds (e.g.
    interactive_recall_quiz, tried against every domain) must not
    monopolize exploration merely by always having a fresh domain×format
    combination available; domain-level fairness alone (Phase 7A) was not
    sufficient — see portfolio.py's _format_trial_total docstring."""

    def test_heavily_tried_format_deprioritized_across_different_domains(self):
        candidates = [
            ExplorationCell(
                domain="science", format="interactive_recall_quiz", audience_value="curiosity"
            ),
            ExplorationCell(
                domain="history", format="curiosity_explainer", audience_value="curiosity"
            ),
        ]
        # interactive_recall_quiz has been tried many times already, across
        # OTHER domains (not science specifically) — domain-level fairness
        # alone would not catch this, since 'science' itself has 0 trials.
        trial_counts = {
            ("personal_finance", "interactive_recall_quiz"): 3,
            ("psychology", "interactive_recall_quiz"): 3,
            ("business_career", "interactive_recall_quiz"): 3,
        }
        decision = choose_next_cell(
            candidates, recent=[], trial_counts=trial_counts, exploit_ready=False
        )
        assert decision.cell.format != "interactive_recall_quiz"

    def test_never_tried_format_wins_over_heavily_tried_one(self):
        candidates = [
            ExplorationCell(
                domain="science", format="practical_micro_lesson", audience_value="curiosity"
            ),
            ExplorationCell(
                domain="science", format="interactive_recall_quiz", audience_value="curiosity"
            ),
        ]
        trial_counts = {
            ("history", "interactive_recall_quiz"): 5,
            ("psychology", "interactive_recall_quiz"): 5,
        }
        decision = choose_next_cell(
            candidates, recent=[], trial_counts=trial_counts, exploit_ready=False
        )
        assert decision.cell.format == "practical_micro_lesson"

    def test_interactive_recall_quiz_remains_eligible_when_under_tested(self):
        """Deprioritized when over-tried, never banned outright."""
        candidates = [
            ExplorationCell(
                domain="science", format="interactive_recall_quiz", audience_value="curiosity"
            )
        ]
        decision = choose_next_cell(candidates, recent=[], exploit_ready=False)
        assert decision.cell.format == "interactive_recall_quiz"

    def test_one_format_cannot_dominate_via_many_fresh_domain_pairs(self):
        """The exact cold-start shape this section calibrates: quiz paired
        with 4 different NEVER-tried domains vs. one other format paired
        with ONE never-tried domain. Per-cell novelty alone (Phase 7A) would
        keep re-picking quiz across its many fresh domains; format-level
        fairness must eventually let the other format compete."""
        candidates = [
            ExplorationCell(domain=d, format="interactive_recall_quiz", audience_value="curiosity")
            for d in ("science", "history", "psychology", "business_career")
        ] + [
            ExplorationCell(domain="technology_ai", format="myth_bust", audience_value="curiosity")
        ]
        # Quiz has already been picked against 3 OTHER domains (not present
        # in this candidate round) — its format-level total is already high.
        trial_counts = {
            ("personal_finance", "interactive_recall_quiz"): 1,
            ("geography_culture", "interactive_recall_quiz"): 1,
            ("logic_math", "interactive_recall_quiz"): 1,
        }
        decision = choose_next_cell(
            candidates, recent=[], trial_counts=trial_counts, exploit_ready=False
        )
        assert decision.cell.format == "myth_bust"


class TestExploitationCanReviseAProvenCell:
    def test_high_evidence_cell_wins_when_exploiting_with_mature_evidence(self):
        """Phase 7B, §2 — exploitation only activates once evidence is
        mature (>= MIN_CELLS_WITH_EVIDENCE_FOR_EXPLOITATION cells carry a
        real recorded score); three cells' worth here clears that floor."""
        evidence = {
            ("science", "curiosity_explainer"): 0.9,
            ("personal_finance", "curiosity_explainer"): 0.2,
            ("language_learning", "curiosity_explainer"): 0.1,
        }
        decision = choose_next_cell(
            _CANDIDATES,
            recent=[],
            evidence_scores=evidence,
            exploit_ready=True,
        )
        assert decision.cell.domain == "science"
        assert decision.designation == "exploitation"

    def test_exploration_ignores_evidence_in_favor_of_novelty(self):
        evidence = {("science", "curiosity_explainer"): 0.9}
        decision = choose_next_cell(
            _CANDIDATES,
            recent=[],
            evidence_scores=evidence,
            exploit_ready=False,
        )
        # All candidates are equally novel (0 trials); science's high
        # evidence only matters as a tiebreaker, so it CAN still win, but
        # the designation must correctly say "exploration".
        assert decision.designation == "exploration"


class TestFormatSuitabilityInfluencesButNeverOverridesExploration:
    """Phase 7C.1, Part B, §6/§8 — format_suitability is a tiebreak among
    equally-novel candidates, never a way for a well-fitting format to
    beat a more-novel one."""

    def test_among_equally_novel_candidates_the_better_fit_format_wins(self):
        candidates = [
            ExplorationCell(
                domain="woodworking", format="interactive_recall_quiz", audience_value="curiosity"
            ),
            ExplorationCell(
                domain="woodworking",
                format="curiosity_explainer",
                audience_value="curiosity",
            ),
        ]
        # Both 0 trials — equally novel. curiosity_explainer fits "curiosity"
        # much better than interactive_recall_quiz does.
        decision = choose_next_cell(candidates, recent=[], trial_counts={}, exploit_ready=False)
        assert decision.cell.format == "curiosity_explainer"

    def test_underexplored_format_still_beats_a_better_fitting_tried_format(self):
        candidates = [
            ExplorationCell(
                domain="cooking", format="curiosity_explainer", audience_value="curiosity"
            ),
            ExplorationCell(
                domain="cooking", format="practical_micro_lesson", audience_value="curiosity"
            ),
        ]
        # curiosity_explainer fits "curiosity" better, but it's been tried
        # 5 times; practical_micro_lesson has never been tried at all.
        # Novelty must win — a worse-fitting-but-fresh format still gets
        # its trial.
        trial_counts = {("cooking", "curiosity_explainer"): 5}
        decision = choose_next_cell(
            candidates, recent=[], trial_counts=trial_counts, exploit_ready=False
        )
        assert decision.cell.format == "practical_micro_lesson"

    def test_no_domain_is_permanently_bound_to_one_format(self):
        """The same audience_value drives the same format preference
        regardless of which domain it's paired with — proving suitability
        is not secretly keyed on domain."""
        for domain in ("woodworking", "astronomy", "personal_finance", "language_learning"):
            candidates = [
                ExplorationCell(
                    domain=domain, format="interactive_recall_quiz", audience_value="curiosity"
                ),
                ExplorationCell(
                    domain=domain, format="curiosity_explainer", audience_value="curiosity"
                ),
            ]
            decision = choose_next_cell(candidates, recent=[], trial_counts={}, exploit_ready=False)
            assert decision.cell.format == "curiosity_explainer"


class TestScienceNotBanned:
    def test_science_can_still_be_chosen_when_under_tested(self):
        decision = choose_next_cell([_cell("science")], recent=[], exploit_ready=False)
        assert decision.cell.domain == "science"

    def test_science_deprioritized_not_forbidden_when_over_tested(self):
        recent = [_cell("science"), _cell("science")]
        decision = choose_next_cell(_CANDIDATES, recent=recent, exploit_ready=False)
        assert decision.cell.domain != "science"
        # But it remains a legal candidate — no exception, no empty result.


class TestDiversityGuardNeverDeadlocks:
    def test_all_candidates_failing_guard_still_returns_a_choice(self):
        policy = AllocationPolicy(diversity=DiversityPolicy(domain_window=1, domain_max_repeats=0))
        recent = [_cell("science")]
        decision = choose_next_cell([_cell("science")], recent=recent, policy=policy)
        assert decision.cell.domain == "science"  # only candidate, guard fails, still chosen


class TestExploitReadyByRatio:
    def test_default_ratio_produces_roughly_one_in_four_exploitation(self):
        policy = AllocationPolicy(exploration_ratio=0.75)
        ticks = [exploit_ready_by_ratio(i, policy=policy) for i in range(12)]
        assert ticks.count(True) == 3  # 12/4

    def test_full_exploration_ratio_never_exploits(self):
        policy = AllocationPolicy(exploration_ratio=1.0)
        assert all(not exploit_ready_by_ratio(i, policy=policy) for i in range(10))


class TestAllocationPolicyValidation:
    def test_out_of_range_ratio_rejected(self):
        import pytest

        with pytest.raises(ValueError):
            AllocationPolicy(exploration_ratio=1.5)


class TestChooseNextCellRequiresCandidates:
    def test_empty_candidate_list_raises(self):
        import pytest

        with pytest.raises(ValueError):
            choose_next_cell([], recent=[])
