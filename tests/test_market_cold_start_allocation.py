"""Phase 13D-B.1 — Portfolio allocation correction tests.

Tests A–M (13 tests).  Test N is the full suite run (executed separately).

Verifies the V1 portfolio allocation policy:
- COLD_START_V1_ANCHOR_CAP caps the number of selected CHANNEL_BOOTSTRAP probes
- Secondary niches beyond the cap are DEFERRED (portfolio_allocation), not selected
- Semantic MARKET_REGION capacity is preserved even with large secondary-niche lists
- Small-budget behaviour is deterministic and correct
- All deferred probes are persisted with an explanatory decision_reason
"""

from __future__ import annotations

import json
import pathlib
import tempfile

import pytest

from app.ai.fake import FakeProvider
from app.ai.provider import AIRequest, AIResponse
from app.core.database import open_db
from app.intelligence.market.cold_start import (
    ExplorationProfile,
    plan_cold_start,
)
from app.intelligence.market.planner_prompts import COLD_START_V1_ANCHOR_CAP
from app.intelligence.market.planner_repository import list_exploration_probes

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    with tempfile.TemporaryDirectory() as d:
        c = open_db(pathlib.Path(d) / "test.db")
        yield c
        c.close()


_LLM_TWO = json.dumps(
    {
        "probes": [
            {
                "query": "retirement savings planning",
                "market_region_label": "Retirement Planning",
                "rationale": "High-relevance adjacent region",
                "semantic_fit_score_estimate": 0.88,
                "distinctiveness_rationale": "Long-term vs day-to-day finance",
            },
            {
                "query": "stock market beginner guide",
                "market_region_label": "Stock Investing",
                "rationale": "Natural next step after budgeting",
                "semantic_fit_score_estimate": 0.75,
                "distinctiveness_rationale": "Wealth-building vs expense reduction",
            },
        ]
    }
)

_LLM_EMPTY = json.dumps({"probes": []})


class _FailProvider:
    name = "fail"

    def complete(self, request: AIRequest) -> AIResponse:
        raise RuntimeError("simulated LLM failure")


# Profile with 1 primary + 1 secondary niche — the minimal "2 bootstrap" scenario.
_DUAL_PROFILE = ExplorationProfile(
    primary_niche="personal finance tips",
    secondary_niches=["budgeting basics"],
    excluded_topics=["gambling"],
    audience_description="adults managing money",
)

# Profile with 6 secondary niches (stress test for anchor starvation prevention).
_MANY_SECONDARY_PROFILE = ExplorationProfile(
    primary_niche="personal finance tips",
    secondary_niches=[
        "budgeting basics",
        "frugal living",
        "tax planning guide",
        "real estate investment",
        "retirement accounts",
        "credit score improvement",
    ],
    excluded_topics=[],
)


# ---------------------------------------------------------------------------
# A: 2 bootstrap niches + max_probes=2 → secondary deferred, semantic expansion happens
# ---------------------------------------------------------------------------


def test_a_two_bootstrap_two_probes_semantic_expansion_preserved(conn):
    """A: max_probes=2 with 2-niche profile: secondary deferred, 1 LLM probe selected.

    This is the canonical production scenario that motivated the allocation fix:
    before the correction, both bootstrap probes consumed max_probes=2 leaving
    zero capacity for semantic expansion.
    """
    result = plan_cold_start(
        conn,
        _DUAL_PROFILE,
        provider=FakeProvider(_LLM_TWO),
        max_probes=2,
        search_budget=2,
    )
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)

    bootstrap = [p for p in probes if p.probe_type == "channel_bootstrap"]
    market_region = [p for p in probes if p.probe_type == "market_region"]
    selected = [p for p in probes if p.status == "selected"]

    # Exactly 1 anchor selected (primary niche), secondary deferred
    assert sum(1 for p in bootstrap if p.status == "selected") == 1
    assert sum(1 for p in bootstrap if p.status == "deferred") == 1

    # At least 1 market_region probe was created AND selected
    assert len(market_region) >= 1
    assert any(p.status == "selected" for p in market_region)

    # Total selected respects max_probes
    assert len(selected) <= 2

    # Portfolio includes both probe classes
    selected_types = {p.probe_type for p in selected}
    assert "channel_bootstrap" in selected_types
    assert "market_region" in selected_types


# ---------------------------------------------------------------------------
# B: max_probes=1 → only primary niche selected, no semantic probes
# ---------------------------------------------------------------------------


def test_b_max_probes_1_primary_only(conn):
    """B: max_probes=1 → only primary niche anchor, no LLM call."""
    result = plan_cold_start(
        conn,
        _DUAL_PROFILE,
        provider=FakeProvider(_LLM_TWO),
        max_probes=1,
        search_budget=1,
    )
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)

    selected = [p for p in probes if p.status == "selected"]
    market_region = [p for p in probes if p.probe_type == "market_region"]

    # Exactly 1 probe selected: the primary niche anchor
    assert len(selected) == 1
    assert selected[0].probe_type == "channel_bootstrap"

    # No market_region probes created (no budget for LLM)
    assert len(market_region) == 0

    # result counts match
    assert result.selected_count == 1
    assert result.llm_used is False


# ---------------------------------------------------------------------------
# C: max_probes=2 → 1 anchor + 1 semantic
# ---------------------------------------------------------------------------


def test_c_max_probes_2_one_anchor_one_semantic(conn):
    """C: max_probes=2 → exactly 1 anchor + 1 semantic region selected."""
    result = plan_cold_start(
        conn,
        _DUAL_PROFILE,
        provider=FakeProvider(_LLM_TWO),
        max_probes=2,
        search_budget=10,
    )
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    selected = [p for p in probes if p.status == "selected"]

    selected_bootstrap = [p for p in selected if p.probe_type == "channel_bootstrap"]
    selected_market = [p for p in selected if p.probe_type == "market_region"]

    assert len(selected_bootstrap) == 1
    assert len(selected_market) == 1
    assert result.selected_count == 2
    assert result.diagnostics["anchor_slots_allocated"] == 1


# ---------------------------------------------------------------------------
# D: max_probes=3 → 2 anchors + 1 semantic
# ---------------------------------------------------------------------------


def test_d_max_probes_3_two_anchors_one_semantic(conn):
    """D: max_probes=3 → 2 anchor slots (primary + one secondary) + 1 semantic."""
    result = plan_cold_start(
        conn,
        _DUAL_PROFILE,
        provider=FakeProvider(_LLM_TWO),
        max_probes=3,
        search_budget=10,
    )
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    selected = [p for p in probes if p.status == "selected"]

    selected_bootstrap = [p for p in selected if p.probe_type == "channel_bootstrap"]
    selected_market = [p for p in selected if p.probe_type == "market_region"]

    assert len(selected_bootstrap) == 2  # primary + secondary both selected
    assert len(selected_market) >= 1  # at least 1 semantic
    assert result.diagnostics["anchor_slots_allocated"] == 2


# ---------------------------------------------------------------------------
# E: many secondary niches → only anchor_cap selected, rest deferred; semantic preserved
# ---------------------------------------------------------------------------


def test_e_many_secondary_niches_do_not_crowd_out_semantic(conn):
    """E: 6 secondary niches → only COLD_START_V1_ANCHOR_CAP anchors selected; semantic survives."""
    result = plan_cold_start(
        conn,
        _MANY_SECONDARY_PROFILE,
        provider=FakeProvider(_LLM_TWO),
        max_probes=10,
        search_budget=20,
    )
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)

    selected_bootstrap = [
        p for p in probes if p.probe_type == "channel_bootstrap" and p.status == "selected"
    ]
    deferred_bootstrap = [
        p for p in probes if p.probe_type == "channel_bootstrap" and p.status == "deferred"
    ]
    selected_market = [
        p for p in probes if p.probe_type == "market_region" and p.status == "selected"
    ]

    # Anchor selection bounded by cap
    assert len(selected_bootstrap) <= COLD_START_V1_ANCHOR_CAP

    # Excess secondary niches are DEFERRED, not lost
    assert len(deferred_bootstrap) >= 1

    # Semantic expansion still happened
    assert len(selected_market) >= 1


# ---------------------------------------------------------------------------
# F: direct anchor (primary niche) always present in selected probes
# ---------------------------------------------------------------------------


def test_f_primary_niche_always_selected(conn):
    """F: primary niche is always selected as a channel_bootstrap probe."""
    result = plan_cold_start(
        conn,
        _MANY_SECONDARY_PROFILE,
        provider=FakeProvider(_LLM_TWO),
        max_probes=6,
        search_budget=15,
    )
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    selected_bootstrap = [
        p for p in probes if p.probe_type == "channel_bootstrap" and p.status == "selected"
    ]
    primary_nq = "personal finance tips"
    assert any(p.normalized_query == primary_nq for p in selected_bootstrap)


# ---------------------------------------------------------------------------
# G: semantic market-region present when LLM succeeds and budget allows
# ---------------------------------------------------------------------------


def test_g_semantic_market_region_present_when_feasible(conn):
    """G: when LLM succeeds and budget > anchor_slots, at least 1 market_region selected."""
    result = plan_cold_start(
        conn,
        _DUAL_PROFILE,
        provider=FakeProvider(_LLM_TWO),
        max_probes=4,
        search_budget=10,
    )
    assert result.llm_used is True
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    selected_market = [
        p for p in probes if p.probe_type == "market_region" and p.status == "selected"
    ]
    assert len(selected_market) >= 1


# ---------------------------------------------------------------------------
# H: search budget never exceeded regardless of profile size
# ---------------------------------------------------------------------------


def test_h_search_budget_never_exceeded(conn):
    """H: total search calls used never exceeds search_budget."""
    for budget in (1, 2, 3, 5, 10):
        with tempfile.TemporaryDirectory() as d:
            c = open_db(pathlib.Path(d) / "test.db")
            result = plan_cold_start(
                c,
                _MANY_SECONDARY_PROFILE,
                provider=FakeProvider(_LLM_TWO),
                max_probes=budget + 2,
                search_budget=budget,
            )
            assert result.diagnostics["search_calls_used"] <= budget, (
                f"search_calls_used={result.diagnostics['search_calls_used']} "
                f"exceeded budget={budget}"
            )
            c.close()


# ---------------------------------------------------------------------------
# I: deferred bootstrap probes persisted with portfolio_allocation reason
# ---------------------------------------------------------------------------


def test_i_deferred_bootstrap_probes_persisted(conn):
    """I: valid bootstrap probes beyond anchor cap are DEFERRED and persisted."""
    result = plan_cold_start(
        conn,
        _MANY_SECONDARY_PROFILE,
        provider=FakeProvider(_LLM_EMPTY),
        max_probes=6,
        search_budget=10,
    )
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    deferred_bootstrap = [
        p for p in probes if p.probe_type == "channel_bootstrap" and p.status == "deferred"
    ]
    assert len(deferred_bootstrap) >= 1

    for p in deferred_bootstrap:
        assert "portfolio_allocation" in p.decision_reason


# ---------------------------------------------------------------------------
# J: deferred semantic candidates persisted with budget_exhausted reason
# ---------------------------------------------------------------------------


def test_j_deferred_semantic_candidates_persisted(conn):
    """J: LLM candidates beyond budget persisted as DEFERRED with budget_exhausted reason."""
    # max_probes=2, anchor_slots=1 → 1 slot for LLM, second LLM probe deferred
    result = plan_cold_start(
        conn,
        _DUAL_PROFILE,
        provider=FakeProvider(_LLM_TWO),
        max_probes=2,
        search_budget=2,
    )
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    deferred_market = [
        p for p in probes if p.probe_type == "market_region" and p.status == "deferred"
    ]
    assert len(deferred_market) >= 1
    for p in deferred_market:
        assert "budget_exhausted" in p.decision_reason or "near_duplicate" in p.decision_reason


# ---------------------------------------------------------------------------
# K: decision reasons identify portfolio allocation for deferred bootstraps
# ---------------------------------------------------------------------------


def test_k_portfolio_allocation_in_decision_reason(conn):
    """K: bootstrap probes deferred due to anchor cap contain 'portfolio_allocation'."""
    result = plan_cold_start(
        conn,
        _MANY_SECONDARY_PROFILE,
        provider=FakeProvider(_LLM_EMPTY),
        max_probes=10,
        search_budget=20,
    )
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    deferred_bootstrap = [
        p for p in probes if p.probe_type == "channel_bootstrap" and p.status == "deferred"
    ]
    assert len(deferred_bootstrap) >= 1
    for p in deferred_bootstrap:
        assert "portfolio_allocation" in p.decision_reason, (
            f"expected 'portfolio_allocation' in decision_reason, got: {p.decision_reason!r}"
        )


# ---------------------------------------------------------------------------
# L: allocation is deterministic for same inputs
# ---------------------------------------------------------------------------


def test_l_allocation_is_deterministic():
    """L: same profile + FakeProvider output in isolated DBs → identical probe dispositions.

    Uses two separate databases to ensure prior-query state from one run cannot
    affect the other.  Determinism is the property: given the same inputs and
    no prior exploration history, the planner always produces the same allocation.
    """

    def _run_isolated():
        with tempfile.TemporaryDirectory() as d:
            c = open_db(pathlib.Path(d) / "test.db")
            result = plan_cold_start(
                c,
                _DUAL_PROFILE,
                provider=FakeProvider(_LLM_TWO),
                max_probes=3,
                search_budget=5,
            )
            probes = list_exploration_probes(c, run_id=result.exploration_run_id)
            disposition = {p.normalized_query: p.status for p in probes}
            c.close()
        return disposition

    disposition1 = _run_isolated()
    disposition2 = _run_isolated()

    assert disposition1 == disposition2, (
        f"Allocation differed between isolated runs:\n  run1={disposition1}\n  run2={disposition2}"
    )


# ---------------------------------------------------------------------------
# M: LLM failure falls back to deterministic profile seeds (no market_region probes)
# ---------------------------------------------------------------------------


def test_m_llm_failure_fallback_preserves_bootstrap_probes(conn):
    """M: LLM exception → bootstrap probes still selected, run status='partial'."""
    result = plan_cold_start(
        conn,
        _DUAL_PROFILE,
        provider=_FailProvider(),
        max_probes=6,
        search_budget=10,
    )
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)

    selected = [p for p in probes if p.status == "selected"]
    market_region = [p for p in probes if p.probe_type == "market_region"]

    # Bootstrap anchors persisted
    assert result.selected_count >= 1
    assert any(p.probe_type == "channel_bootstrap" for p in selected)

    # No market_region probes created (LLM never responded)
    assert len(market_region) == 0

    # Run marked partial
    assert result.diagnostics["final_status"] == "partial"
    assert result.llm_used is False
    assert "llm_error" in result.diagnostics
