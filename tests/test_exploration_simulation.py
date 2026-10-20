"""Phase 7A, §16/§18 — exploratory-horizon simulation.

The most important validation output per the phase spec: would this policy
actually produce a diverse experimental channel? Simulated entirely in
memory, deterministic given its inputs.
"""

from __future__ import annotations

from app.intelligence.exploration.cell import ExplorationCell
from app.intelligence.exploration.portfolio import AllocationPolicy
from app.intelligence.exploration.simulation import (
    default_candidate_pool,
    simulate_next_n_cells,
)


def _science_heavy_seed(n: int = 6) -> list[ExplorationCell]:
    return [
        ExplorationCell(domain="science", format="curiosity_explainer", audience_value="curiosity")
        for _ in range(n)
    ]


class TestSimulationBasics:
    def test_produces_exactly_n_selections(self):
        report = simulate_next_n_cells(n=20)
        assert len(report.selections) == 20

    def test_is_deterministic_given_same_inputs(self):
        a = simulate_next_n_cells(n=10, seed_history=_science_heavy_seed())
        b = simulate_next_n_cells(n=10, seed_history=_science_heavy_seed())
        assert [c.label() for c in a.selections] == [c.label() for c in b.selections]

    def test_default_candidate_pool_is_non_empty_and_includes_seed_dimensions(self):
        pool = default_candidate_pool()
        assert len(pool) > 0
        domains = {c.domain for c in pool}
        assert "language_learning" in domains
        assert "personal_finance" in domains
        assert "science" in domains


class TestScienceDoesNotDominateAfterCorrection:
    def test_starting_from_science_heavy_history_still_diversifies(self):
        """Reproduces the observed live shape: a channel whose recent
        history is all-science. The policy must steer toward other domains
        over the next 20 picks, not perpetuate the concentration."""
        report = simulate_next_n_cells(n=20, seed_history=_science_heavy_seed())
        assert report.domain_distribution.get("science", 0) < 10  # well under half of 20

    def test_science_still_appears_not_banned(self):
        """Diversity, not prohibition — science must still be a legal,
        occasionally-selected domain even starting from a heavy seed, once
        the OTHER 11 seed domains have caught up to its head start (fair
        domain-level rotation deliberately makes a pre-seeded-ahead domain
        wait its turn — that is the correct behavior, not a bug; this test
        just needs enough ticks for every domain to reach parity)."""
        report = simulate_next_n_cells(n=70, seed_history=_science_heavy_seed(n=3))
        assert report.domain_distribution.get("science", 0) > 0

    def test_max_consecutive_same_domain_stays_bounded(self):
        report = simulate_next_n_cells(n=20, seed_history=_science_heavy_seed())
        assert report.max_consecutive_same_domain <= 3

    def test_distinct_domain_floor_generally_satisfied_over_rolling_window(self):
        report = simulate_next_n_cells(n=20)
        assert report.distinct_domain_counts_by_window[8] >= 3


class TestExplorationExploitationSplit:
    def test_no_evidence_never_produces_exploitation(self):
        """Phase 7B, §2 — cold-start rule: with no evidence_scores at all
        (the real shape before any publication evidence exists), every tick
        must read as exploration regardless of the configured ratio."""
        policy = AllocationPolicy(exploration_ratio=0.75)
        report = simulate_next_n_cells(n=20, policy=policy)
        assert report.exploitation_count == 0
        assert report.exploration_count == 20

    def test_matures_evidence_activates_the_configured_ratio(self):
        policy = AllocationPolicy(exploration_ratio=0.75)
        evidence = {
            ("science", "curiosity_explainer"): 0.8,
            ("personal_finance", "practical_micro_lesson"): 0.3,
            ("language_learning", "interactive_recall_quiz"): 0.5,
            ("history", "story_reveal"): 0.2,
        }
        report = simulate_next_n_cells(n=20, policy=policy, evidence_scores=evidence)
        assert report.exploration_count + report.exploitation_count == 20
        # 75% exploration over 20 ticks with a 1-in-4 exploit schedule -> 5 exploit.
        assert report.exploitation_count == 5
        assert report.exploration_count == 15
        # Exploitation must never fully replace exploration — some
        # exploration is always preserved even once evidence is mature.
        assert report.exploration_count > 0


class TestFormatDistribution:
    def test_format_distribution_is_reported_and_non_trivial(self):
        report = simulate_next_n_cells(n=20)
        assert len(report.format_distribution) > 1  # not collapsed onto one format


class TestUnderexploredCellsReceiveTrials:
    def test_every_domain_in_the_default_pool_gets_at_least_one_trial_eventually(self):
        """Over a horizon at least as long as the candidate pool's domain
        count, every domain should be sampled at least once — the
        information-value bonus must actually surface under-tested cells,
        not just avoid over-tested ones."""
        from app.intelligence.exploration.taxonomy import DOMAIN_SEEDS

        report = simulate_next_n_cells(n=len(DOMAIN_SEEDS) * 3)
        tried_domains = set(report.domain_distribution.keys())
        assert set(DOMAIN_SEEDS) <= tried_domains


class TestReportSerialization:
    def test_as_dict_is_json_serializable(self):
        import json

        report = simulate_next_n_cells(n=5)
        json.dumps(report.as_dict())  # must not raise
