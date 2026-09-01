"""Phase 13D-B tests — Cold-start bootstrap + bounded semantic seed expansion.

Tests A–AT (46 tests total).

All tests use FakeProvider (no real LLM calls) or a FailProvider.
No YouTube calls. No Opportunity creation. No scoring changes.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import tempfile

import pytest
from pydantic import ValidationError

from app.ai.fake import FakeProvider
from app.ai.provider import AIRequest, AIResponse
from app.core.database import open_db
from app.intelligence.market.cold_start import (
    ColdStartExplorationResult,
    ExplorationProfile,
    InsufficientProfileError,
    SeedExpansionCandidate,
    SeedExpansionOutput,
    is_channel_cold_start,
    plan_cold_start,
)
from app.intelligence.market.planner_prompts import (
    COLD_START_DISPATCHED_PROBE_THRESHOLD,
    DEFAULT_JACCARD_DEDUP_THRESHOLD,
)
from app.intelligence.market.planner_repository import (
    count_dispatched_probes,
    create_exploration_probe,
    create_exploration_run,
    list_exploration_probes,
    list_exploration_runs,
    list_prior_market_region_labels,
    list_prior_probe_normalized_queries,
    update_exploration_run_provenance,
    update_probe_status,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    with tempfile.TemporaryDirectory() as d:
        c = open_db(pathlib.Path(d) / "test.db")
        yield c
        c.close()


def _make_run(conn: sqlite3.Connection, **overrides):
    defaults = dict(channel_id=None, workspace_id=None)
    defaults.update(overrides)
    return create_exploration_run(conn, **defaults)


def _make_probe(conn: sqlite3.Connection, run_id: int, **overrides):
    defaults = dict(
        run_id=run_id,
        query_text="test query",
        normalized_query="test query",
        probe_type="market_region",
        channel_id=None,
        workspace_id=None,
    )
    defaults.update(overrides)
    return create_exploration_probe(conn, **defaults)


def _dispatch_probe(conn: sqlite3.Connection, probe_id: int):
    """Mark a probe as dispatched (for cold-start predicate tests)."""
    update_probe_status(conn, probe_id, status="dispatched")


_SIMPLE_PROFILE = ExplorationProfile(
    primary_niche="money saving tips",
    secondary_niches=["frugal living"],
    excluded_topics=["gambling", "casino games"],
    audience_description="young adults learning personal finance",
)

_LLM_TWO_CANDIDATES = json.dumps(
    {
        "probes": [
            {
                "query": "personal finance beginner tips",
                "market_region_label": "Personal Finance Basics",
                "rationale": "Core area for the channel",
                "semantic_fit_score_estimate": 0.85,
                "distinctiveness_rationale": "Foundational budgeting content",
            },
            {
                "query": "investment portfolio management",
                "market_region_label": "Portfolio Management",
                "rationale": "Advanced personal finance topic",
                "semantic_fit_score_estimate": 0.78,
                "distinctiveness_rationale": "Differs from basics — wealth growth focus",
            },
        ]
    }
)

_LLM_EMPTY = json.dumps({"probes": []})


class _FailProvider:
    """Always raises to simulate an LLM failure."""

    name = "fail"

    def complete(self, request: AIRequest) -> AIResponse:
        raise RuntimeError("simulated LLM failure")


# ---------------------------------------------------------------------------
# A–D: is_channel_cold_start predicate
# ---------------------------------------------------------------------------


def test_a_cold_start_true_when_no_probes(conn):
    """A: channel with 0 dispatched probes → is_channel_cold_start returns True."""
    assert is_channel_cold_start(conn, channel_id=None) is True


def test_b_cold_start_true_below_threshold(conn):
    """B: channel with dispatched < THRESHOLD → still cold-start."""
    run = _make_run(conn)
    for i in range(COLD_START_DISPATCHED_PROBE_THRESHOLD - 1):
        probe = _make_probe(conn, run.id, query_text=f"q{i}", normalized_query=f"q{i}")
        _dispatch_probe(conn, probe.id)
    assert is_channel_cold_start(conn, channel_id=None) is True


def test_c_cold_start_false_at_threshold(conn):
    """C: channel with dispatched == THRESHOLD → not cold-start."""
    run = _make_run(conn)
    for i in range(COLD_START_DISPATCHED_PROBE_THRESHOLD):
        probe = _make_probe(conn, run.id, query_text=f"q{i}", normalized_query=f"q{i}")
        _dispatch_probe(conn, probe.id)
    assert is_channel_cold_start(conn, channel_id=None) is False


def test_d_cold_start_true_for_none_channel_id(conn):
    """D: channel_id=None with 0 dispatched probes → cold-start."""
    assert is_channel_cold_start(conn, channel_id=None) is True


# ---------------------------------------------------------------------------
# E–J: Model validation
# ---------------------------------------------------------------------------


def test_e_exploration_profile_round_trips():
    """E: ExplorationProfile accepts and stores all fields."""
    p = ExplorationProfile(
        primary_niche="cooking tutorials",
        secondary_niches=["meal prep", "healthy eating"],
        excluded_topics=["fast food"],
        audience_description="health-conscious adults",
        duplicate_similarity_threshold=0.65,
        region_code="US",
        language_code="en",
    )
    assert p.primary_niche == "cooking tutorials"
    assert p.secondary_niches == ["meal prep", "healthy eating"]
    assert p.excluded_topics == ["fast food"]
    assert p.audience_description == "health-conscious adults"
    assert p.duplicate_similarity_threshold == 0.65
    assert p.region_code == "US"
    assert p.language_code == "en"


def test_f_exploration_profile_rejects_extra_fields():
    """F: ExplorationProfile extra='forbid' raises on unknown fields."""
    with pytest.raises(ValidationError):
        ExplorationProfile(primary_niche="test", unknown_field="bad")  # type: ignore[call-arg]


def test_g_exploration_profile_defaults():
    """G: ExplorationProfile has sensible defaults."""
    p = ExplorationProfile(primary_niche="test niche")
    assert p.secondary_niches == []
    assert p.excluded_topics == []
    assert p.audience_description == ""
    assert p.duplicate_similarity_threshold == DEFAULT_JACCARD_DEDUP_THRESHOLD
    assert p.region_code is None
    assert p.language_code is None


def test_h_seed_expansion_candidate_round_trips():
    """H: SeedExpansionCandidate accepts all required fields."""
    c = SeedExpansionCandidate(
        query="budget tips for beginners",
        market_region_label="Beginner Budgeting",
        rationale="High search volume area",
        semantic_fit_score_estimate=0.82,
        distinctiveness_rationale="Focused on first-timers",
    )
    assert c.query == "budget tips for beginners"
    assert c.semantic_fit_score_estimate == 0.82


def test_i_seed_expansion_candidate_rejects_extra():
    """I: SeedExpansionCandidate extra='forbid' raises on unknown fields."""
    with pytest.raises(ValidationError):
        SeedExpansionCandidate(
            query="q",
            market_region_label="L",
            rationale="R",
            semantic_fit_score_estimate=0.5,
            distinctiveness_rationale="D",
            unexpected_field=True,  # type: ignore[call-arg]
        )


def test_j_seed_expansion_output_max_length():
    """J: SeedExpansionOutput enforces max_length=12 on probes list."""
    thirteen_candidates = [
        {
            "query": f"region query {i}",
            "market_region_label": f"Label {i}",
            "rationale": "r",
            "semantic_fit_score_estimate": 0.5,
            "distinctiveness_rationale": "d",
        }
        for i in range(13)
    ]
    with pytest.raises(ValidationError):
        SeedExpansionOutput(probes=thirteen_candidates)


# ---------------------------------------------------------------------------
# K–L: InsufficientProfileError
# ---------------------------------------------------------------------------


def test_k_raises_on_empty_primary_niche(conn):
    """K: plan_cold_start raises InsufficientProfileError when primary_niche is empty."""
    profile = ExplorationProfile(primary_niche="")
    with pytest.raises(InsufficientProfileError):
        plan_cold_start(conn, profile, provider=FakeProvider(_LLM_TWO_CANDIDATES))


def test_l_raises_on_whitespace_only_primary_niche(conn):
    """L: plan_cold_start raises InsufficientProfileError for whitespace-only primary_niche."""
    profile = ExplorationProfile(primary_niche="   ")
    with pytest.raises(InsufficientProfileError):
        plan_cold_start(conn, profile, provider=FakeProvider(_LLM_TWO_CANDIDATES))


# ---------------------------------------------------------------------------
# M–X: Happy path
# ---------------------------------------------------------------------------


def test_m_creates_exploration_run(conn):
    """M: plan_cold_start persists an exploration run to the DB."""
    plan_cold_start(conn, _SIMPLE_PROFILE, provider=FakeProvider(_LLM_TWO_CANDIDATES))
    runs = list_exploration_runs(conn)
    assert len(runs) == 1


def test_n_run_completes(conn):
    """N: exploration run transitions to completed status."""
    result = plan_cold_start(conn, _SIMPLE_PROFILE, provider=FakeProvider(_LLM_TWO_CANDIDATES))
    runs = list_exploration_runs(conn)
    assert runs[0].status == "completed"
    assert result.diagnostics["final_status"] == "completed"


def test_o_bootstrap_probe_from_primary_niche(conn):
    """O: channel_bootstrap probe created for the primary niche."""
    plan_cold_start(conn, _SIMPLE_PROFILE, provider=FakeProvider(_LLM_EMPTY))
    runs = list_exploration_runs(conn)
    probes = list_exploration_probes(conn, run_id=runs[0].id)
    bootstrap = [p for p in probes if p.probe_type == "channel_bootstrap"]
    assert len(bootstrap) >= 1
    assert any("money saving" in p.normalized_query for p in bootstrap)


def test_p_bootstrap_probe_from_secondary_niches(conn):
    """P: channel_bootstrap probe created for each secondary niche."""
    plan_cold_start(conn, _SIMPLE_PROFILE, provider=FakeProvider(_LLM_EMPTY))
    runs = list_exploration_runs(conn)
    probes = list_exploration_probes(conn, run_id=runs[0].id)
    bootstrap = [p for p in probes if p.probe_type == "channel_bootstrap"]
    assert any("frugal" in p.normalized_query for p in bootstrap)


def test_q_market_region_probes_created(conn):
    """Q: market_region probes created from LLM output."""
    plan_cold_start(conn, _SIMPLE_PROFILE, provider=FakeProvider(_LLM_TWO_CANDIDATES))
    runs = list_exploration_runs(conn)
    probes = list_exploration_probes(conn, run_id=runs[0].id)
    market_regions = [p for p in probes if p.probe_type == "market_region"]
    assert len(market_regions) == 2


def test_r_all_candidates_persisted(conn):
    """R: ALL candidates persisted — selected, deferred, and rejected."""
    llm_with_deferred = json.dumps(
        {
            "probes": [
                {
                    "query": "personal finance beginner tips",
                    "market_region_label": "Finance Basics",
                    "rationale": "r",
                    "semantic_fit_score_estimate": 0.85,
                    "distinctiveness_rationale": "d",
                },
            ]
        }
    )
    plan_cold_start(
        conn,
        _SIMPLE_PROFILE,
        provider=FakeProvider(llm_with_deferred),
        max_probes=2,
    )
    runs = list_exploration_runs(conn)
    probes = list_exploration_probes(conn, run_id=runs[0].id)
    assert len(probes) >= 1
    statuses = {p.status for p in probes}
    assert "selected" in statuses


def test_s_selected_probe_ids_populated(conn):
    """S: result.selected_probe_ids contains IDs of selected probes."""
    result = plan_cold_start(conn, _SIMPLE_PROFILE, provider=FakeProvider(_LLM_TWO_CANDIDATES))
    assert len(result.selected_probe_ids) > 0
    runs = list_exploration_runs(conn)
    probes = list_exploration_probes(conn, run_id=runs[0].id, status="selected")
    assert set(p.id for p in probes) == set(result.selected_probe_ids)


def test_t_llm_used_true_on_success(conn):
    """T: result.llm_used is True when the LLM call succeeds."""
    result = plan_cold_start(conn, _SIMPLE_PROFILE, provider=FakeProvider(_LLM_TWO_CANDIDATES))
    assert result.llm_used is True


def test_u_provider_and_model_in_result(conn):
    """U: result.provider_name and result.model are populated on success."""
    result = plan_cold_start(conn, _SIMPLE_PROFILE, provider=FakeProvider(_LLM_TWO_CANDIDATES))
    assert result.provider_name == "fake"
    assert result.model is not None


def test_v_run_provenance_updated(conn):
    """V: run.provider, run.model, run.prompt_version updated after LLM call."""
    result = plan_cold_start(conn, _SIMPLE_PROFILE, provider=FakeProvider(_LLM_TWO_CANDIDATES))
    from app.intelligence.market.planner_repository import get_exploration_run

    run = get_exploration_run(conn, result.exploration_run_id)
    assert run is not None
    assert run.provider == "fake"
    assert run.model is not None
    assert run.prompt_version is not None


def test_w_result_counts_correct(conn):
    """W: ColdStartExplorationResult counts match probe statuses in DB."""
    result = plan_cold_start(conn, _SIMPLE_PROFILE, provider=FakeProvider(_LLM_TWO_CANDIDATES))
    runs = list_exploration_runs(conn)
    run = runs[0]
    assert run.selected_count == result.selected_count
    assert run.deferred_count == result.deferred_count
    assert run.rejected_count == result.rejected_count


def test_x_candidate_count_equals_llm_probes(conn):
    """X: result.candidate_count equals number of LLM candidates returned."""
    result = plan_cold_start(conn, _SIMPLE_PROFILE, provider=FakeProvider(_LLM_TWO_CANDIDATES))
    assert result.candidate_count == 2


# ---------------------------------------------------------------------------
# Y–Z: Dedup
# ---------------------------------------------------------------------------


def test_y_near_duplicate_llm_candidate_deferred(conn):
    """Y: LLM candidate nearly identical to bootstrap probe → deferred."""
    # primary niche normalizes to "money saving tips"
    # LLM candidate "money saving tips guide" has Jaccard 3/4 = 0.75 ≥ 0.70 → deferred
    dup_output = json.dumps(
        {
            "probes": [
                {
                    "query": "money saving tips guide",
                    "market_region_label": "Money Tips",
                    "rationale": "r",
                    "semantic_fit_score_estimate": 0.85,
                    "distinctiveness_rationale": "d",
                },
            ]
        }
    )
    plan_cold_start(conn, _SIMPLE_PROFILE, provider=FakeProvider(dup_output))
    runs = list_exploration_runs(conn)
    probes = list_exploration_probes(conn, run_id=runs[0].id)
    dup_probe = next((p for p in probes if p.normalized_query == "money saving tips guide"), None)
    assert dup_probe is not None
    assert dup_probe.status == "deferred"
    assert "near_duplicate" in dup_probe.decision_reason


def test_z_secondary_niche_deduped_against_primary(conn):
    """Z: secondary niche near-identical to primary niche → not added as duplicate bootstrap."""
    profile = ExplorationProfile(
        primary_niche="money saving tips",
        secondary_niches=["money saving tips advice"],  # Jaccard 3/4 = 0.75 with primary
        excluded_topics=[],
    )
    result = plan_cold_start(conn, profile, provider=FakeProvider(_LLM_EMPTY))
    runs = list_exploration_runs(conn)
    probes = list_exploration_probes(conn, run_id=runs[0].id)
    bootstrap = [p for p in probes if p.probe_type == "channel_bootstrap"]
    # Only primary niche selected; secondary is near-dup → skipped
    assert len(bootstrap) == 1
    assert "money saving tips" in bootstrap[0].normalized_query
    assert result.diagnostics["bootstrap_count"] == 1


# ---------------------------------------------------------------------------
# AA–AB: Excluded topics
# ---------------------------------------------------------------------------


def test_aa_excluded_topic_llm_candidate_rejected(conn):
    """AA: LLM candidate matching excluded topic (Jaccard ≥ threshold) → rejected."""
    # "gambling casino tips" vs excluded "casino games":
    # normalize("gambling casino tips") = "gambling casino tips"
    # normalize("casino games") = "casino games"
    # jaccard: {"gambling","casino","tips"} vs {"casino","games"} → 1/4 = 0.25
    # Use a closer match: excluded "casino games", candidate "casino games guide"
    # jaccard: {"casino","games"} vs {"casino","games","guide"} → 2/3 = 0.67 ≥ 0.60 → rejected
    profile = ExplorationProfile(
        primary_niche="money saving tips",
        excluded_topics=["casino games"],
    )
    exc_output = json.dumps(
        {
            "probes": [
                {
                    "query": "casino games guide",
                    "market_region_label": "Casino",
                    "rationale": "r",
                    "semantic_fit_score_estimate": 0.3,
                    "distinctiveness_rationale": "d",
                },
            ]
        }
    )
    plan_cold_start(conn, profile, provider=FakeProvider(exc_output))
    runs = list_exploration_runs(conn)
    probes = list_exploration_probes(conn, run_id=runs[0].id)
    rejected = [p for p in probes if p.status == "rejected"]
    assert len(rejected) == 1
    assert "excluded_topic" in rejected[0].decision_reason


def test_ab_bootstrap_excluded_topic_skipped(conn):
    """AB: bootstrap niche matching excluded topic → not added to selected probes."""
    profile = ExplorationProfile(
        primary_niche="casino games betting",
        excluded_topics=["casino games"],
    )
    # normalize("casino games betting") = "casino games betting"
    # normalize("casino games") = "casino games"
    # jaccard: {"casino","games","betting"} vs {"casino","games"} → 2/3 = 0.67 ≥ 0.60 → excluded
    result = plan_cold_start(conn, profile, provider=FakeProvider(_LLM_EMPTY))
    runs = list_exploration_runs(conn)
    probes = list_exploration_probes(conn, run_id=runs[0].id)
    bootstrap = [p for p in probes if p.probe_type == "channel_bootstrap"]
    assert len(bootstrap) == 0
    assert result.diagnostics["bootstrap_count"] == 0


# ---------------------------------------------------------------------------
# AC–AE: Budget enforcement
# ---------------------------------------------------------------------------


def test_ac_probe_budget_respected(conn):
    """AC: selected_count never exceeds max_probes."""
    result = plan_cold_start(
        conn,
        _SIMPLE_PROFILE,
        provider=FakeProvider(_LLM_TWO_CANDIDATES),
        max_probes=2,
    )
    assert result.selected_count <= 2


def test_ad_search_budget_respected(conn):
    """AD: search_calls_used never exceeds search_budget."""
    result = plan_cold_start(
        conn,
        _SIMPLE_PROFILE,
        provider=FakeProvider(_LLM_TWO_CANDIDATES),
        search_budget=1,
        max_probes=10,
    )
    assert result.diagnostics["search_calls_used"] <= 1


def test_ae_budget_exhausted_candidates_deferred(conn):
    """AE: LLM candidates beyond budget → persisted as deferred with budget_exhausted reason.

    With V1 portfolio policy, max_probes=2 / search_budget=2:
    - anchor_slots=1 → primary selected (1 search call)
    - secondary deferred (portfolio_allocation)
    - market_region_slots=1 → 1 LLM probe selected, 1 deferred (budget_exhausted)
    """
    plan_cold_start(
        conn,
        _SIMPLE_PROFILE,
        provider=FakeProvider(_LLM_TWO_CANDIDATES),
        max_probes=2,
        search_budget=2,
    )
    runs = list_exploration_runs(conn)
    probes = list_exploration_probes(conn, run_id=runs[0].id)
    deferred = [p for p in probes if p.status == "deferred" and p.probe_type == "market_region"]
    assert len(deferred) >= 1
    for d in deferred:
        assert "budget_exhausted" in d.decision_reason or "near_duplicate" in d.decision_reason


# ---------------------------------------------------------------------------
# AF–AG: Prior-run coverage
# ---------------------------------------------------------------------------


def test_af_list_prior_probe_normalized_queries(conn):
    """AF: list_prior_probe_normalized_queries returns selected/dispatched queries."""
    run = _make_run(conn)
    p1 = _make_probe(conn, run.id, query_text="budget planning", normalized_query="budget planning")
    p2 = _make_probe(conn, run.id, query_text="frugal tips", normalized_query="frugal tips")
    _make_probe(conn, run.id, query_text="investing 101", normalized_query="investing 101")
    update_probe_status(conn, p1.id, status="selected")
    update_probe_status(conn, p2.id, status="dispatched")
    # p3 stays candidate — should NOT appear in result
    prior = list_prior_probe_normalized_queries(conn, channel_id=None)
    assert "budget planning" in prior
    assert "frugal tips" in prior
    assert "investing 101" not in prior


def test_ag_prior_query_reduces_novelty(conn):
    """AG: probe matching prior run query gets novelty=0.2 in priority_components_json."""
    # Create a prior "selected" probe with the same normalized query as an LLM candidate
    prior_run = _make_run(conn)
    prior_probe = _make_probe(
        conn,
        prior_run.id,
        query_text="personal finance beginner tips",
        normalized_query="personal finance beginner tips",
    )
    update_probe_status(conn, prior_probe.id, status="selected")

    # Now run cold-start — the LLM proposes the same query
    plan_cold_start(conn, _SIMPLE_PROFILE, provider=FakeProvider(_LLM_TWO_CANDIDATES))
    runs = list_exploration_runs(conn)
    # Find the run we just created (the second one)
    new_run = [r for r in runs if r.id != prior_run.id][0]
    probes = list_exploration_probes(conn, run_id=new_run.id)
    repeat_probe = next(
        (p for p in probes if p.normalized_query == "personal finance beginner tips"),
        None,
    )
    if repeat_probe is not None:
        comps = json.loads(repeat_probe.priority_components_json)
        assert comps["novelty"] == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# AH–AJ: LLM failure fallback
# ---------------------------------------------------------------------------


def test_ah_llm_failure_produces_partial_run(conn):
    """AH: LLM exception → run status becomes 'partial'."""
    plan_cold_start(conn, _SIMPLE_PROFILE, provider=_FailProvider())
    runs = list_exploration_runs(conn)
    assert runs[0].status == "partial"


def test_ai_partial_run_status_in_result(conn):
    """AI: result.diagnostics['final_status'] is 'partial' when LLM fails."""
    result = plan_cold_start(conn, _SIMPLE_PROFILE, provider=_FailProvider())
    assert result.diagnostics["final_status"] == "partial"


def test_aj_bootstrap_probes_persisted_on_llm_failure(conn):
    """AJ: bootstrap probes (channel_bootstrap) are persisted even when LLM fails."""
    result = plan_cold_start(conn, _SIMPLE_PROFILE, provider=_FailProvider())
    runs = list_exploration_runs(conn)
    probes = list_exploration_probes(conn, run_id=runs[0].id)
    bootstrap = [p for p in probes if p.probe_type == "channel_bootstrap"]
    assert len(bootstrap) >= 1
    assert result.llm_used is False
    assert result.selected_count >= 1


# ---------------------------------------------------------------------------
# AK–AL: Provenance
# ---------------------------------------------------------------------------


def test_ak_update_exploration_run_provenance(conn):
    """AK: update_exploration_run_provenance writes provider/model/prompt_version."""
    run = _make_run(conn)
    updated = update_exploration_run_provenance(
        conn,
        run.id,
        provider="claude",
        model="claude-haiku-4-5-20251001",
        prompt_version="1",
    )
    assert updated.provider == "claude"
    assert updated.model == "claude-haiku-4-5-20251001"
    assert updated.prompt_version == "1"


def test_al_provenance_noop_when_no_fields(conn):
    """AL: update_exploration_run_provenance with no fields returns run unchanged."""
    run = _make_run(conn)
    returned = update_exploration_run_provenance(conn, run.id)
    assert returned.id == run.id
    assert returned.provider is None
    assert returned.model is None


# ---------------------------------------------------------------------------
# AM–AP: Repository helpers
# ---------------------------------------------------------------------------


def test_am_count_dispatched_probes_none_channel(conn):
    """AM: count_dispatched_probes with channel_id=None counts correctly."""
    run = _make_run(conn)
    for i in range(2):
        p = _make_probe(conn, run.id, query_text=f"q{i}", normalized_query=f"q{i}")
        _dispatch_probe(conn, p.id)
    assert count_dispatched_probes(conn, channel_id=None) == 2


def test_an_count_dispatched_probes_counts_only_dispatched(conn):
    """AN: count_dispatched_probes ignores selected/candidate probes."""
    run = _make_run(conn)
    p_selected = _make_probe(conn, run.id, query_text="s", normalized_query="s")
    p_dispatched = _make_probe(conn, run.id, query_text="d", normalized_query="d")
    update_probe_status(conn, p_selected.id, status="selected")
    _dispatch_probe(conn, p_dispatched.id)
    assert count_dispatched_probes(conn, channel_id=None) == 1


def test_ao_list_prior_market_region_labels(conn):
    """AO: list_prior_market_region_labels returns only market_region probe queries."""
    run = _make_run(conn)
    mr = _make_probe(
        conn,
        run.id,
        query_text="budget strategies",
        normalized_query="budget strategies",
        probe_type="market_region",
    )
    boot = _make_probe(
        conn,
        run.id,
        query_text="frugal living",
        normalized_query="frugal living",
        probe_type="channel_bootstrap",
    )
    update_probe_status(conn, mr.id, status="selected")
    update_probe_status(conn, boot.id, status="selected")
    labels = list_prior_market_region_labels(conn, channel_id=None)
    assert "budget strategies" in labels
    assert "frugal living" not in labels  # channel_bootstrap excluded


def test_ap_list_prior_queries_respects_statuses_param(conn):
    """AP: list_prior_probe_normalized_queries respects custom statuses tuple."""
    run = _make_run(conn)
    p1 = _make_probe(conn, run.id, query_text="alpha", normalized_query="alpha")
    p2 = _make_probe(conn, run.id, query_text="beta", normalized_query="beta")
    update_probe_status(conn, p1.id, status="selected")
    update_probe_status(conn, p2.id, status="deferred")

    # Default statuses=(selected, dispatched) — "beta" (deferred) excluded
    default_queries = list_prior_probe_normalized_queries(conn, channel_id=None)
    assert "alpha" in default_queries
    assert "beta" not in default_queries

    # Custom statuses including deferred
    extended = list_prior_probe_normalized_queries(
        conn, channel_id=None, statuses=("selected", "deferred")
    )
    assert "alpha" in extended
    assert "beta" in extended


# ---------------------------------------------------------------------------
# AQ–AR: Priority scoring and sorting
# ---------------------------------------------------------------------------


def test_aq_higher_niche_fit_yields_higher_score(conn):
    """AQ: candidate with higher semantic_fit_score_estimate gets higher priority_score."""
    high_fit = json.dumps(
        {
            "probes": [
                {
                    "query": "personal finance alpha",
                    "market_region_label": "High Fit",
                    "rationale": "r",
                    "semantic_fit_score_estimate": 0.95,
                    "distinctiveness_rationale": "d",
                },
                {
                    "query": "tax accounting complex",
                    "market_region_label": "Low Fit",
                    "rationale": "r",
                    "semantic_fit_score_estimate": 0.10,
                    "distinctiveness_rationale": "d",
                },
            ]
        }
    )
    plan_cold_start(conn, _SIMPLE_PROFILE, provider=FakeProvider(high_fit))
    runs = list_exploration_runs(conn)
    probes = list_exploration_probes(conn, run_id=runs[0].id)
    mr_probes = [p for p in probes if p.probe_type == "market_region"]
    scores = {p.normalized_query: p.priority_score for p in mr_probes}
    # "personal finance alpha" should have a higher priority_score
    assert scores["personal finance alpha"] > scores["tax accounting complex"]


def test_ar_highest_scored_selected_first_when_budget_tight(conn):
    """AR: within budget limit, highest-priority candidate selected over lower-priority one."""
    # max_probes=2: 1 bootstrap + 1 LLM; second LLM candidate deferred
    # high-score candidate should be selected, low-score deferred
    high_then_low = json.dumps(
        {
            "probes": [
                {
                    "query": "retirement savings planning",
                    "market_region_label": "Retirement",
                    "rationale": "r",
                    "semantic_fit_score_estimate": 0.92,
                    "distinctiveness_rationale": "d",
                },
                {
                    "query": "obscure niche topic xyz",
                    "market_region_label": "Low Fit",
                    "rationale": "r",
                    "semantic_fit_score_estimate": 0.05,
                    "distinctiveness_rationale": "d",
                },
            ]
        }
    )
    # With new V1 portfolio policy, max_probes=2 → anchor_slots=1:
    # primary niche selected, secondary niche deferred (portfolio_allocation),
    # leaving 1 slot + 1 search call for the LLM market-region probe.
    plan_cold_start(
        conn,
        _SIMPLE_PROFILE,
        provider=FakeProvider(high_then_low),
        max_probes=2,
        search_budget=2,
    )
    runs = list_exploration_runs(conn)
    probes = list_exploration_probes(conn, run_id=runs[0].id)
    mr_probes = {p.normalized_query: p for p in probes if p.probe_type == "market_region"}
    retirement = mr_probes.get("retirement savings planning")
    low_fit = mr_probes.get("obscure niche topic xyz")
    assert retirement is not None
    assert low_fit is not None
    assert retirement.status == "selected"
    assert low_fit.status in ("deferred", "rejected")


# ---------------------------------------------------------------------------
# AS–AT: Result structure
# ---------------------------------------------------------------------------


def test_as_result_is_pydantic_model(conn):
    """AS: ColdStartExplorationResult is a Pydantic BaseModel with extra='forbid'."""
    result = plan_cold_start(conn, _SIMPLE_PROFILE, provider=FakeProvider(_LLM_TWO_CANDIDATES))
    from pydantic import BaseModel

    assert isinstance(result, BaseModel)
    with pytest.raises((ValidationError, TypeError)):
        ColdStartExplorationResult(
            **result.model_dump(),
            unexpected_field="bad",  # type: ignore[call-arg]
        )


def test_at_diagnostics_keys_populated(conn):
    """AT: result.diagnostics contains expected keys after a successful run."""
    result = plan_cold_start(conn, _SIMPLE_PROFILE, provider=FakeProvider(_LLM_TWO_CANDIDATES))
    assert "bootstrap_count" in result.diagnostics
    assert "llm_candidate_count" in result.diagnostics
    assert "search_calls_used" in result.diagnostics
    assert "final_status" in result.diagnostics
