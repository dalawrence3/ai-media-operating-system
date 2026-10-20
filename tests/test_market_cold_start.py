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
    DEFAULT_JACCARD_DEDUP_THRESHOLD,
)
from app.intelligence.market.planner_repository import (
    broad_discovery_complete,
    count_dispatched_probes,
    create_exploration_probe,
    create_exploration_run,
    has_running_exploration_run,
    list_current_opportunity_topics,
    list_exploration_probes,
    list_exploration_runs,
    list_prior_market_region_labels,
    list_prior_probe_normalized_queries,
    update_exploration_run_provenance,
    update_exploration_run_status,
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
                "domain_family": "personal finance",
                "rationale": "Core area for the channel",
                "format_fit_score": 0.85,
                "distance_from_current_subjects": 0.9,
                "distinctiveness_rationale": "Foundational budgeting content",
            },
            {
                "query": "investment portfolio management",
                "market_region_label": "Portfolio Management",
                "domain_family": "career skills",
                "rationale": "Advanced personal finance topic",
                "format_fit_score": 0.78,
                "distance_from_current_subjects": 0.85,
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
# A–D: is_channel_cold_start / broad_discovery_complete predicate
#
# Phase 18H.B: is_channel_cold_start() used to be
# `count_dispatched_probes() < COLD_START_DISPATCHED_PROBE_THRESHOLD`, counting
# every probe type together. That let a channel with 11 dispatched
# channel_bootstrap/adjacent_topic/validation probes and ZERO market_region
# probes report "not cold-start" — exactly Orvella's real, live state. It is
# now `not broad_discovery_complete(...)`: only a SELECTED or DISPATCHED
# market_region probe ever satisfies it. COLD_START_DISPATCHED_PROBE_THRESHOLD
# and count_dispatched_probes() remain defined and independently tested
# (AM/AN below) but are no longer consulted by this predicate.
# ---------------------------------------------------------------------------


def test_a_cold_start_true_when_no_probes(conn):
    """A: channel with 0 probes of any kind → is_channel_cold_start returns True."""
    assert is_channel_cold_start(conn, channel_id=None) is True
    assert broad_discovery_complete(conn, channel_id=None) is False


def test_b_bootstrap_only_does_not_complete_broad_discovery(conn):
    """B (Part 12 regression): many dispatched channel_bootstrap probes, zero
    market_region — broad discovery must still be considered incomplete."""
    run = _make_run(conn)
    for i in range(5):
        probe = _make_probe(
            conn,
            run.id,
            query_text=f"niche {i}",
            normalized_query=f"niche {i}",
            probe_type="channel_bootstrap",
        )
        _dispatch_probe(conn, probe.id)
    assert broad_discovery_complete(conn, channel_id=None) is False
    assert is_channel_cold_start(conn, channel_id=None) is True


def test_c_one_dispatched_market_region_probe_completes_broad_discovery(conn):
    """C (Part 16): a single SELECTED-or-DISPATCHED market_region probe is
    sufficient — no minimum count threshold is invented."""
    run = _make_run(conn)
    probe = _make_probe(conn, run.id, query_text="q0", normalized_query="q0")
    _dispatch_probe(conn, probe.id)
    assert broad_discovery_complete(conn, channel_id=None) is True
    assert is_channel_cold_start(conn, channel_id=None) is False


def test_d_cold_start_true_for_none_channel_id(conn):
    """D: channel_id=None with 0 probes → cold-start."""
    assert is_channel_cold_start(conn, channel_id=None) is True


# ---------------------------------------------------------------------------
# AU–BE: Phase 18H.B — broad_discovery_complete / recovery gate / running
# safety / prompt v2 / token budget
# ---------------------------------------------------------------------------


def test_au_adjacent_topic_only_does_not_complete_broad_discovery(conn):
    """AU (Part 13 regression): many dispatched adjacent_topic (+ validation)
    probes, zero market_region — still incomplete."""
    run = _make_run(conn)
    for i in range(5):
        probe = _make_probe(
            conn,
            run.id,
            query_text=f"adj {i}",
            normalized_query=f"adj {i}",
            probe_type="adjacent_topic",
        )
        _dispatch_probe(conn, probe.id)
    for i in range(3):
        probe = _make_probe(
            conn,
            run.id,
            query_text=f"val {i}",
            normalized_query=f"val {i}",
            probe_type="validation",
        )
        _dispatch_probe(conn, probe.id)
    assert broad_discovery_complete(conn, channel_id=None) is False
    assert is_channel_cold_start(conn, channel_id=None) is True


def test_av_exact_orvella_shape_still_eligible(conn):
    """AV: reproduces Orvella's exact live probe mix (5 adjacent_topic,
    3 channel_bootstrap, 3 validation, 0 market_region — 11 dispatched total,
    comfortably over the old threshold of 3) and confirms the corrected gate
    still reports broad discovery as needed."""
    run = _make_run(conn)
    mix = ["adjacent_topic"] * 5 + ["channel_bootstrap"] * 3 + ["validation"] * 3
    for i, ptype in enumerate(mix):
        probe = _make_probe(
            conn, run.id, query_text=f"p{i}", normalized_query=f"p{i}", probe_type=ptype
        )
        _dispatch_probe(conn, probe.id)
    assert count_dispatched_probes(conn, channel_id=None) == 11
    assert broad_discovery_complete(conn, channel_id=None) is False
    assert is_channel_cold_start(conn, channel_id=None) is True


def test_aw_selected_status_also_counts(conn):
    """AW: a market_region probe need not be fully 'dispatched' — 'selected'
    already counts (matches the SELECTED-or-DISPATCHED semantics)."""
    run = _make_run(conn)
    probe = _make_probe(conn, run.id, query_text="q0", normalized_query="q0")
    update_probe_status(conn, probe.id, status="selected")
    assert broad_discovery_complete(conn, channel_id=None) is True


def test_ax_irrelevant_statuses_do_not_falsely_complete(conn):
    """AX (Part 16): market_region probes left as candidate/deferred/rejected
    never satisfy broad_discovery_complete — only SELECTED/DISPATCHED do."""
    run = _make_run(conn)
    for status in ("candidate", "deferred", "rejected"):
        probe = _make_probe(conn, run.id, query_text=f"q-{status}", normalized_query=f"q-{status}")
        update_probe_status(conn, probe.id, status=status)
    assert broad_discovery_complete(conn, channel_id=None) is False
    assert is_channel_cold_start(conn, channel_id=None) is True


def test_ay_failed_history_recovery_no_force_required(conn):
    """AY (Part 14): multiple prior partial cold-start runs (all failed),
    zero market_region probes, no currently-running conflicting run →
    a plain (non --force) cold-start attempt is eligible again."""
    for _ in range(6):
        run = _make_run(conn)
        update_exploration_run_status(
            conn,
            run.id,
            status="partial",
            error_message="Claude response is not valid JSON: JSONDecodeError",
            completed_at="2026-08-23T19:45:24",
        )
    assert has_running_exploration_run(conn, channel_id=None) is False
    assert is_channel_cold_start(conn, channel_id=None) is True


def test_az_running_attempt_detected(conn):
    """AZ (Part 15): a RUNNING exploration run for the channel is detected —
    state-based mutual exclusion, no time-based cooldown involved."""
    run = _make_run(conn)
    update_exploration_run_status(conn, run.id, status="running")
    assert has_running_exploration_run(conn, channel_id=None) is True


def test_ba_no_running_attempt_when_all_terminal(conn):
    """BA: runs in terminal states (completed/partial/failed) never look
    'running'."""
    for status in ("completed", "partial", "failed"):
        run = _make_run(conn)
        update_exploration_run_status(conn, run.id, status=status)
    assert has_running_exploration_run(conn, channel_id=None) is False


def test_bb_success_stops_recovery(conn):
    """BB (Part 16 end-to-end): after a successful market_region dispatch,
    the ordinary future gate skips broad re-bootstrap — recovery stops."""
    run = _make_run(conn)
    probe = _make_probe(conn, run.id, query_text="q0", normalized_query="q0")
    _dispatch_probe(conn, probe.id)
    assert is_channel_cold_start(conn, channel_id=None) is False
    # A subsequent plain (non --force) explore-plan-equivalent check would
    # now correctly decline to re-run cold-start.


def test_bc_partial_run_bootstrap_success_market_region_failure(conn):
    """BC (Part 17): bootstrap probes dispatched, cold-start LLM failed
    (run status partial), 0 market_region probes → broad discovery is NOT
    complete, and the channel remains recoverable without --force."""
    run = _make_run(conn)
    for i in range(2):
        probe = _make_probe(
            conn,
            run.id,
            query_text=f"niche {i}",
            normalized_query=f"niche {i}",
            probe_type="channel_bootstrap",
        )
        _dispatch_probe(conn, probe.id)
    update_exploration_run_status(
        conn,
        run.id,
        status="partial",
        candidate_count=0,
        error_message="Claude response is not valid JSON: JSONDecodeError",
    )
    assert broad_discovery_complete(conn, channel_id=None) is False
    assert is_channel_cold_start(conn, channel_id=None) is True


def test_bd_cold_start_request_uses_4096_max_tokens(conn):
    """BD (Part 18): the cold-start AIRequest sets max_tokens=4096 — the
    value already proven by the sibling adjacent-expansion prompt — not the
    shared AIRequest default (1024), which is the confirmed likely cause of
    every historical cold-start parse failure."""

    class _CapturingProvider:
        name = "capturing"

        def __init__(self, output: str) -> None:
            self._output = output
            self.last_request: AIRequest | None = None

        def complete(self, request: AIRequest) -> AIResponse:
            self.last_request = request
            data = json.loads(self._output)
            return AIResponse(
                raw_text=self._output,
                provider_name=self.name,
                model="capturing-model",
                input_tokens=1,
                output_tokens=1,
                duration_ms=1,
                retry_count=0,
                parsed=SeedExpansionOutput.model_validate(data),
                stop_reason="end_turn",
            )

    provider = _CapturingProvider(_LLM_TWO_CANDIDATES)
    plan_cold_start(conn, _SIMPLE_PROFILE, provider=provider)
    assert provider.last_request is not None
    assert provider.last_request.max_tokens == 4096


def test_be_current_opportunity_topics_helper(conn):
    """BE (Part 7): list_current_opportunity_topics is cheap/local (no
    provider call) and returns [] for a channel with no opportunities row,
    or when channel_id is None."""
    assert list_current_opportunity_topics(conn, channel_id=None) == []
    assert list_current_opportunity_topics(conn, channel_id=999) == []


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
    """H: SeedExpansionCandidate accepts all required fields (v3 schema)."""
    c = SeedExpansionCandidate(
        query="budget tips for beginners",
        market_region_label="Beginner Budgeting",
        domain_family="personal finance",
        rationale="High search volume area",
        format_fit_score=0.82,
        distance_from_current_subjects=0.9,
        distinctiveness_rationale="Focused on first-timers",
    )
    assert c.query == "budget tips for beginners"
    assert c.domain_family == "personal finance"
    assert c.format_fit_score == 0.82
    assert c.distance_from_current_subjects == 0.9


def test_h2_seed_expansion_candidate_no_longer_has_semantic_fit_score_estimate():
    """H2 (Phase 18H.B.1): the ambiguous v1/v2 field name is gone, not aliased."""
    assert "semantic_fit_score_estimate" not in SeedExpansionCandidate.model_fields


def test_h3_format_fit_score_out_of_range_rejected():
    """H3: format_fit_score is bounded to [0, 1]."""
    with pytest.raises(ValidationError):
        SeedExpansionCandidate(
            query="q",
            market_region_label="L",
            domain_family="science",
            rationale="R",
            format_fit_score=1.5,
            distance_from_current_subjects=0.5,
            distinctiveness_rationale="D",
        )


def test_h4_distance_from_current_subjects_out_of_range_rejected():
    """H4: distance_from_current_subjects is bounded to [0, 1]."""
    with pytest.raises(ValidationError):
        SeedExpansionCandidate(
            query="q",
            market_region_label="L",
            domain_family="science",
            rationale="R",
            format_fit_score=0.5,
            distance_from_current_subjects=-0.1,
            distinctiveness_rationale="D",
        )


def test_i_seed_expansion_candidate_rejects_extra():
    """I: SeedExpansionCandidate extra='forbid' raises on unknown fields."""
    with pytest.raises(ValidationError):
        SeedExpansionCandidate(
            query="q",
            market_region_label="L",
            domain_family="science",
            rationale="R",
            format_fit_score=0.5,
            distance_from_current_subjects=0.5,
            distinctiveness_rationale="D",
            unexpected_field=True,  # type: ignore[call-arg]
        )


def test_i2_seed_expansion_candidate_rejects_old_field_name():
    """I2: semantic_fit_score_estimate is now an unrecognized extra field, not a
    silently-accepted legacy alias."""
    with pytest.raises(ValidationError):
        SeedExpansionCandidate(
            query="q",
            market_region_label="L",
            domain_family="science",
            rationale="R",
            semantic_fit_score_estimate=0.5,  # type: ignore[call-arg]
            distance_from_current_subjects=0.5,
            distinctiveness_rationale="D",
        )


def test_j_seed_expansion_output_max_length():
    """J: SeedExpansionOutput enforces max_length=12 on probes list."""
    thirteen_candidates = [
        {
            "query": f"region query {i}",
            "market_region_label": f"Label {i}",
            "domain_family": "science" if i % 3 else "history",
            "rationale": "r",
            "format_fit_score": 0.5,
            "distance_from_current_subjects": 0.5,
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
                    "domain_family": "personal finance",
                    "rationale": "r",
                    "format_fit_score": 0.85,
                    "distance_from_current_subjects": 0.9,
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
                    "domain_family": "personal finance",
                    "rationale": "r",
                    "format_fit_score": 0.85,
                    "distance_from_current_subjects": 0.1,
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
                    "domain_family": "gambling",
                    "rationale": "r",
                    "format_fit_score": 0.3,
                    "distance_from_current_subjects": 0.5,
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


def test_aq_higher_format_fit_and_distance_yields_higher_score(conn):
    """AQ (Phase 18H.B.1): a candidate with higher format_fit_score AND higher
    distance_from_current_subjects gets a higher priority_score — niche/subject
    proximity is no longer part of the formula at all."""
    high_fit = json.dumps(
        {
            "probes": [
                {
                    "query": "personal finance alpha",
                    "market_region_label": "High Fit",
                    "domain_family": "personal finance",
                    "rationale": "r",
                    "format_fit_score": 0.95,
                    "distance_from_current_subjects": 0.9,
                    "distinctiveness_rationale": "d",
                },
                {
                    "query": "tax accounting complex",
                    "market_region_label": "Low Fit",
                    "domain_family": "personal finance",
                    "rationale": "r",
                    "format_fit_score": 0.10,
                    "distance_from_current_subjects": 0.2,
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
                    "domain_family": "personal finance",
                    "rationale": "r",
                    "format_fit_score": 0.92,
                    "distance_from_current_subjects": 0.85,
                    "distinctiveness_rationale": "d",
                },
                {
                    "query": "obscure niche topic xyz",
                    "market_region_label": "Low Fit",
                    "domain_family": "history",
                    "rationale": "r",
                    "format_fit_score": 0.05,
                    "distance_from_current_subjects": 0.1,
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


# ---------------------------------------------------------------------------
# BF: Phase 18H.B — prompt v2 contract (Part 19)
# ---------------------------------------------------------------------------


def test_bf_prompt_v2_contract(conn):
    """BF: v2 remains on disk, unmutated, as prompt-version history — it is no
    longer COLD_START_PROMPT_VERSION (v3 is), but it must still load and still
    read exactly as it did when it was live."""
    from app.ai.registry import PromptRegistry

    registry = PromptRegistry()
    prompt = registry.get("market-exploration-cold-start", "2")

    system_lower = prompt.system.lower()
    assert "valid json only" in system_lower
    assert "excluded topics" in system_lower
    assert "must align with the channel's content universe" not in prompt.system
    assert "distinct" in system_lower
    assert "diversity" in system_lower

    # v1 also remains on disk, unmutated.
    v1 = registry.get("market-exploration-cold-start", "1")
    assert "must align with the channel's content universe" in v1.system


def test_bg_prompt_v4_contract(conn):
    """BG (Phase 18H.B.1, updated Phase 7C.3): v4 is now
    COLD_START_PROMPT_VERSION. It keeps every v3 contract point (the
    "Channel content universe" anchoring framing stays removed, format
    identity stays separated from subject territory, the new schema fields
    and domain-family diversity contract stay stated, and the prompt stays
    domain-general) and adds one new capability: an optional TARGET
    EXPLORATION DOMAIN override section, rendered only when a caller (recovery
    discovery) supplies target_domain — see cold_start.py's
    target_domain_section construction and the Phase 7C.3 root-cause fix for
    why this could not simply be folded into secondary_niches."""
    from app.ai.registry import PromptRegistry
    from app.intelligence.market.planner_prompts import COLD_START_PROMPT_VERSION

    assert COLD_START_PROMPT_VERSION == "4"
    registry = PromptRegistry()
    prompt = registry.get("market-exploration-cold-start", COLD_START_PROMPT_VERSION)

    system_lower = prompt.system.lower()
    # Still strict JSON only.
    assert "valid json only" in system_lower
    assert "no markdown" in system_lower
    # Still respects excluded topics.
    assert "excluded topics" in system_lower
    # The v1/v2 anchoring framing is gone entirely.
    assert "channel content universe" not in system_lower
    assert "channel content universe" not in prompt.user_template.lower()
    assert "must align with the channel's content universe" not in prompt.system
    # Format identity vs. subject territory are explicitly separated.
    assert "format identity" in system_lower
    assert "subject territory" in system_lower
    assert "already covered" in system_lower
    # Suitability must never be judged by niche proximity.
    assert "never" in system_lower and "judged" in system_lower
    # Set-level diversity contract is stated numerically, not just "be diverse".
    assert "at least 3 distinct domain_family" in system_lower
    assert "40%" in prompt.system
    # New schema fields named explicitly.
    assert "domain_family" in prompt.system
    assert "format_fit_score" in prompt.system
    assert "distance_from_current_subjects" in prompt.system
    # The ambiguous v1/v2 field name is gone.
    assert "semantic_fit_score_estimate" not in prompt.system
    # Domain-general: no hardcoded example domain leaks into the contract.
    for hardcoded in (
        "finance",
        "language learning",
        "health",
        "careers",
        "spanish",
        "investing",
        "consumer technology",
    ):
        assert hardcoded not in system_lower

    # user_template references the fields cold_start.py actually formats.
    for placeholder in (
        "{primary_niche}",
        "{secondary_niches}",
        "{excluded_topics}",
        "{audience_description}",
        "{prior_regions}",
        "{max_candidates}",
        "{target_domain_section}",
    ):
        assert placeholder in prompt.user_template

    # Phase 7C.3: the target-domain override is real, opt-in text — never
    # folded into the secondary_niches framing this same prompt tells the
    # model to diversify away from.
    assert "target exploration domain" in system_lower
    assert "opposite of" in system_lower or "opposite of already-covered" in system_lower

    # v1, v2, and v3 all remain on disk, unmutated, as prompt-version history.
    v1 = registry.get("market-exploration-cold-start", "1")
    v2 = registry.get("market-exploration-cold-start", "2")
    v3 = registry.get("market-exploration-cold-start", "3")
    assert "must align with the channel's content universe" in v1.system
    assert "channel content universe" in v2.user_template.lower()
    assert "target exploration domain" not in v3.system.lower()


# ---------------------------------------------------------------------------
# BH–BM: Phase 18H.B.1 — market_region priority formula
# ---------------------------------------------------------------------------


def test_bh_market_region_priority_formula_exact():
    """BH: priority_score = 0.45*format_fit + 0.35*distance + 0.20*novelty, exactly."""
    from app.intelligence.market.cold_start import _compute_market_region_priority_score

    score, comps = _compute_market_region_priority_score(
        format_fit=0.8,
        distance_from_current_subjects=0.6,
        novelty=1.0,
    )
    expected = 0.45 * 0.8 + 0.35 * 0.6 + 0.20 * 1.0
    assert abs(score - expected) < 1e-9
    assert comps == {
        "format_fit": 0.8,
        "distance_from_current_subjects": 0.6,
        "novelty": 1.0,
    }


def test_bi_same_novelty_still_differentiates_by_format_and_distance(conn):
    """BI: reproduces run #10's exact shape — identical novelty across every
    candidate (all brand-new queries) and no depth_factor at all in the new
    formula (it was dropped entirely, not just weighted down). The old
    formula collapsed to a pure niche_fit sort here; the new one must not
    collapse to a single constant score."""
    ten_same_novelty = json.dumps(
        {
            "probes": [
                {
                    "query": f"topic {i}",
                    "market_region_label": f"Label {i}",
                    "domain_family": f"family {i % 5}",
                    "rationale": "r",
                    "format_fit_score": 0.9 - i * 0.05,
                    "distance_from_current_subjects": 0.9 - i * 0.05,
                    "distinctiveness_rationale": "d",
                }
                for i in range(10)
            ]
        }
    )
    plan_cold_start(
        conn,
        _SIMPLE_PROFILE,
        provider=FakeProvider(ten_same_novelty),
        max_probes=10,
        search_budget=15,
    )
    runs = list_exploration_runs(conn)
    probes = list_exploration_probes(conn, run_id=runs[0].id)
    mr = [p for p in probes if p.probe_type == "market_region"]
    scores = [p.priority_score for p in mr]
    assert len(set(scores)) > 1
    by_query = {p.normalized_query: p.priority_score for p in mr}
    assert by_query["topic 0"] > by_query["topic 9"]


def test_bj_priority_components_json_has_new_keys_not_niche_fit(conn):
    """BJ: priority_components_json for a market_region probe carries the new
    component names; niche_fit_score stays NULL (never stuffed with format_fit)."""
    plan_cold_start(conn, _SIMPLE_PROFILE, provider=FakeProvider(_LLM_TWO_CANDIDATES))
    runs = list_exploration_runs(conn)
    probes = list_exploration_probes(conn, run_id=runs[0].id)
    mr = next(p for p in probes if p.probe_type == "market_region")
    comps = json.loads(mr.priority_components_json)
    assert set(comps.keys()) == {"format_fit", "distance_from_current_subjects", "novelty"}
    assert mr.niche_fit_score is None


# ---------------------------------------------------------------------------
# BK–BO: Phase 18H.B.1 — domain-family diversity contract
# ---------------------------------------------------------------------------


def _family_fixture(specs: list[tuple[str, str, float]]) -> str:
    """specs: list of (query, domain_family, format_fit_score)."""
    return json.dumps(
        {
            "probes": [
                {
                    "query": q,
                    "market_region_label": q.title(),
                    "domain_family": fam,
                    "rationale": "r",
                    "format_fit_score": fit,
                    "distance_from_current_subjects": fit,
                    "distinctiveness_rationale": "d",
                }
                for q, fam, fit in specs
            ]
        }
    )


def test_bk_sufficient_diversity_cap_respected(conn):
    """BK (Part 20-A): several distinct families, none exceeding the derived
    cap. With max_probes=10 and 2 niches, remaining market-region budget is
    8, so family_cap = max(1, int(0.40*8)) = 3."""
    specs = [(f"q{i}", f"family_{i % 4}", 0.9 - i * 0.02) for i in range(10)]
    plan_cold_start(
        conn,
        _SIMPLE_PROFILE,
        provider=FakeProvider(_family_fixture(specs)),
        max_probes=10,
        search_budget=15,
    )
    runs = list_exploration_runs(conn)
    probes = list_exploration_probes(conn, run_id=runs[0].id)
    selected = [p for p in probes if p.probe_type == "market_region" and p.status == "selected"]
    from collections import Counter

    counts = Counter(p.domain_family for p in selected)
    assert all(c <= 3 for c in counts.values())
    assert len(counts) >= 3


def test_bl_dominant_family_excess_deferred_lower_score_other_family_enters(conn):
    """BL (Part 20-B): the highest raw scores mostly belong to one family.
    The concentration cap defers the family's excess candidates even though
    they outscore some accepted different-family candidates — proving
    diversity is enforced during selection, not just when scores happen to
    favor it."""
    specs = [
        ("dominant a", "family_a", 0.90),
        ("dominant b", "family_a", 0.85),
        ("dominant c", "family_a", 0.80),
        ("dominant d", "family_a", 0.75),  # should be deferred: cap=3 already met
        ("dominant e", "family_a", 0.70),  # should be deferred: cap=3 already met
        ("other b", "family_b", 0.50),
        ("other c", "family_c", 0.45),
        ("other d", "family_d", 0.40),
        ("other e", "family_e", 0.35),
    ]
    plan_cold_start(
        conn,
        _SIMPLE_PROFILE,
        provider=FakeProvider(_family_fixture(specs)),
        max_probes=10,
        search_budget=15,
    )
    runs = list_exploration_runs(conn)
    probes = list_exploration_probes(conn, run_id=runs[0].id)
    mr = {p.normalized_query: p for p in probes if p.probe_type == "market_region"}

    family_a_selected = [
        q for q, p in mr.items() if p.domain_family == "family_a" and p.status == "selected"
    ]
    assert len(family_a_selected) == 3
    assert mr["dominant d"].status == "deferred"
    assert "domain_family_concentration" in mr["dominant d"].decision_reason
    assert mr["dominant e"].status == "deferred"
    # Lower-scored, different-family candidates still get in.
    assert mr["other e"].status == "selected"


def test_bm_insufficient_diversity_no_retry_best_feasible_persists(conn):
    """BM (Part 20-C): the provider only generates 2 domain families across
    10 candidates (target for an 8-slot budget is 3). No fabrication, no
    automatic second provider call (FakeProvider is called exactly once by
    plan_cold_start's own single-call design) — the best feasible candidates
    still persist, and a nonfatal diversity-shortfall note is recorded
    without the run reading as failed."""
    specs = [(f"sci{i}", "science", 0.9 - i * 0.02) for i in range(8)] + [
        (f"hist{i}", "history", 0.5 - i * 0.02) for i in range(2)
    ]
    plan_cold_start(
        conn,
        _SIMPLE_PROFILE,
        provider=FakeProvider(_family_fixture(specs)),
        max_probes=10,
        search_budget=15,
    )
    runs = list_exploration_runs(conn)
    run = runs[0]
    assert run.status == "completed"
    assert run.error_message is not None
    assert "diversity_shortfall" in run.error_message
    probes = list_exploration_probes(conn, run_id=run.id)
    selected = [p for p in probes if p.probe_type == "market_region" and p.status == "selected"]
    assert len(selected) > 0  # best feasible set still persisted, not discarded


def test_bn_domain_family_normalization_groups_case_and_whitespace_variants(conn):
    """BN (Part 20-D): "Science", " science ", and "SCIENCE" count as the same
    family for concentration-cap purposes, while each probe's own persisted
    domain_family keeps the model's original, unmodified text."""
    # 2 niches -> anchor_slots=2 with max_probes=3 -> remaining budget=1 -> cap=1.
    specs = [
        ("variant one", "Science", 0.90),
        ("variant two", " science ", 0.85),
        ("variant three", "SCIENCE", 0.80),
    ]
    plan_cold_start(
        conn,
        _SIMPLE_PROFILE,
        provider=FakeProvider(_family_fixture(specs)),
        max_probes=3,
        search_budget=10,
    )
    runs = list_exploration_runs(conn)
    probes = list_exploration_probes(conn, run_id=runs[0].id)
    mr = {p.normalized_query: p for p in probes if p.probe_type == "market_region"}
    selected = [q for q, p in mr.items() if p.status == "selected"]
    assert len(selected) == 1
    assert selected[0] == "variant one"  # highest format_fit_score wins the shared cap
    # Original human-readable values are preserved verbatim, not rewritten.
    assert mr["variant one"].domain_family == "Science"
    assert mr["variant two"].domain_family == " science "
    assert mr["variant three"].domain_family == "SCIENCE"
    assert mr["variant two"].status == "deferred"
    assert "domain_family_concentration" in mr["variant two"].decision_reason


def test_bo_priority_scores_independent_of_processing_order():
    """BO (Part 20-E): the exact same candidates, in reversed order, in two
    otherwise-identical fresh databases (isolating this from cross-run
    novelty history, which is separately and correctly order-independent —
    see AF/AG — but would otherwise confound this specific assertion),
    produce identical per-query scores. No candidate's score depends on
    which other candidates were scored before or after it."""
    specs = [
        ("alpha", "family_a", 0.9),
        ("beta", "family_b", 0.6),
        ("gamma", "family_c", 0.3),
    ]

    def _run_once(candidate_specs):
        with tempfile.TemporaryDirectory() as d:
            c = open_db(pathlib.Path(d) / "test.db")
            try:
                plan_cold_start(
                    c,
                    _SIMPLE_PROFILE,
                    provider=FakeProvider(_family_fixture(candidate_specs)),
                    max_probes=10,
                )
                runs = list_exploration_runs(c)
                probes = list_exploration_probes(c, run_id=runs[0].id)
                return {
                    p.normalized_query: p.priority_score
                    for p in probes
                    if p.probe_type == "market_region"
                }
            finally:
                c.close()

    forward_scores = _run_once(specs)
    reversed_scores = _run_once(list(reversed(specs)))
    assert forward_scores == reversed_scores


# ---------------------------------------------------------------------------
# Phase 7C.6, §5.E — a provider-internal transient retry (below plan_cold_start
# entirely, inside ClaudeProvider.complete()) must never duplicate state above
# it: one exploration_run, one set of probes, regardless of how many times the
# underlying SDK client was actually called.
# ---------------------------------------------------------------------------


def test_provider_internal_retry_creates_no_duplicate_run_or_probes(tmp_path):
    from unittest.mock import MagicMock

    import anthropic
    import httpx

    from app.ai.claude import ClaudeProvider

    httpx_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    msg = MagicMock()
    msg.content = [MagicMock(type="text", text=_LLM_TWO_CANDIDATES)]
    msg.model = "claude-sonnet-5"
    msg.usage.input_tokens = 10
    msg.usage.output_tokens = 5
    msg.id = "msg_test"
    msg.stop_reason = "end_turn"

    client = MagicMock()
    call_count = 0

    def side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise anthropic.APITimeoutError(request=httpx_request)
        return msg

    client.messages.create.side_effect = side_effect
    provider = ClaudeProvider("test-key", client=client, max_retries=3, sleep_fn=MagicMock())

    conn = open_db(tmp_path / "test.db")
    try:
        result = plan_cold_start(conn, _SIMPLE_PROFILE, provider=provider, max_probes=10)

        assert client.messages.create.call_count == 2  # one internal retry happened
        runs = list_exploration_runs(conn)
        assert len(runs) == 1
        probes = list_exploration_probes(conn, run_id=runs[0].id)
        market_region_probes = [p for p in probes if p.probe_type == "market_region"]
        assert len(market_region_probes) == result.candidate_count
        # No duplicate normalized queries -- each candidate created exactly once.
        normalized_queries = [p.normalized_query for p in market_region_probes]
        assert len(normalized_queries) == len(set(normalized_queries))
    finally:
        conn.close()
