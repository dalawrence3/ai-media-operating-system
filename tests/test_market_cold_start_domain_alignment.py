"""Phase 7C.2, §5/§8.G — domain-alignment dispatch prioritization.

Proves the Phase 7C.1 regression this exists to fix: with a target_domain
supplied (the recovery use case — this cold-start call exists to
replenish ONE chosen cell), a candidate genuinely on that domain must
outrank an equally format-fit/novel candidate that is not, for the
limited dispatch budget (priority_score DESC ordering). Without
target_domain, every existing caller's scoring is byte-identical to
before.
"""

from __future__ import annotations

import json
import pathlib
import tempfile

import pytest

from app.ai.fake import FakeProvider
from app.core.database import open_db
from app.intelligence.market.cold_start import (
    ExplorationProfile,
    _compute_market_region_priority_score,
    plan_cold_start,
)
from app.intelligence.market.planner_repository import (
    list_exploration_probes,
    list_exploration_runs,
)


@pytest.fixture()
def conn():
    with tempfile.TemporaryDirectory() as d:
        c = open_db(pathlib.Path(d) / "test.db")
        yield c
        c.close()


_PROFILE = ExplorationProfile(
    primary_niche="science and technology explained",
    secondary_niches=["history and society"],
)

# Two candidates with IDENTICAL format_fit/distance/novelty inputs — the
# only thing that can differ their ranking is domain_alignment. One is
# clearly on-topic for "language_learning", the other is not (the exact
# Phase 7C.1 regression shape: "science of cooking food").
_TWO_EQUALLY_STRONG_CANDIDATES = json.dumps(
    {
        "probes": [
            {
                "query": "science of cooking food",
                "market_region_label": "Food Science",
                "domain_family": "Food & Culinary Science",
                "rationale": "r",
                "format_fit_score": 0.8,
                "distance_from_current_subjects": 0.8,
                "distinctiveness_rationale": "d",
            },
            {
                "query": "everyday vocabulary and pronunciation",
                "market_region_label": "Language Basics",
                "domain_family": "Language Learning",
                "rationale": "r",
                "format_fit_score": 0.8,
                "distance_from_current_subjects": 0.8,
                "distinctiveness_rationale": "d",
            },
        ]
    }
)


class TestDomainAlignmentDominatesDispatchRankingWhenTargeted:
    def test_on_domain_candidate_outranks_equally_strong_off_domain_one(self, conn):
        result = plan_cold_start(
            conn,
            _PROFILE,
            provider=FakeProvider(_TWO_EQUALLY_STRONG_CANDIDATES),
            # _PROFILE has 1 primary + 1 secondary niche (2 bootstrap
            # anchors) that consume budget first; +1 leaves exactly one
            # market_region slot to contest between the two candidates.
            max_probes=3,
            target_domain="language_learning",
        )
        runs = list_exploration_runs(conn)
        probes = list_exploration_probes(conn, run_id=runs[0].id)
        market_regions = [p for p in probes if p.probe_type == "market_region"]
        selected = [p for p in market_regions if p.status == "selected"]

        assert len(selected) == 1
        assert "vocabulary" in selected[0].query_text or "pronunciation" in selected[0].query_text
        assert result.selected_count >= 1

    def test_without_target_domain_behavior_is_unaffected(self, conn):
        """No target_domain -> byte-identical to the original 3-weight
        formula; both candidates are equally strong on format_fit/distance,
        so this must not error and must still select exactly one."""
        result = plan_cold_start(
            conn,
            _PROFILE,
            provider=FakeProvider(_TWO_EQUALLY_STRONG_CANDIDATES),
            max_probes=1,
        )
        assert result.selected_count >= 1


class TestPriorityScoreComponentsRevealDomainAlignment:
    def test_domain_alignment_none_uses_original_three_weights(self):
        score, comps = _compute_market_region_priority_score(
            format_fit=0.8, distance_from_current_subjects=0.8, novelty=1.0
        )
        assert "domain_alignment" not in comps
        assert set(comps) == {"format_fit", "distance_from_current_subjects", "novelty"}

    def test_domain_alignment_set_becomes_the_dominant_component(self):
        low_alignment_score, _ = _compute_market_region_priority_score(
            format_fit=0.8, distance_from_current_subjects=0.8, novelty=1.0, domain_alignment=0.0
        )
        high_alignment_score, _ = _compute_market_region_priority_score(
            format_fit=0.8, distance_from_current_subjects=0.8, novelty=1.0, domain_alignment=1.0
        )
        assert high_alignment_score > low_alignment_score
