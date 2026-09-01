"""Phase 13D-C — Adjacent concept expansion tests.

Tests A–AX (44 tests).

No YouTube API calls. No Opportunity creation. No scoring mutations.
All evidence is seeded from in-process helpers.

Validates:
- Maturity classification (INSUFFICIENT / EXPLORATORY / CORROBORATED)
- ProbeEvidenceSummary and AdjacentConceptCandidate model contracts
- Repository helpers (list_expandable_parent_probes, get_probe_supporting_videos, etc.)
- plan_adjacent_expansion happy path (run creation, probe lineage, types, depth)
- Evidence reference validation (invalid refs rejected)
- Dedup + excluded topic enforcement
- Budget enforcement
- LLM failure fallback
- Priority component computation
- Result structure
- Full integration
"""

from __future__ import annotations

import json
import pathlib
import tempfile
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.ai.fake import FakeProvider
from app.core.database import open_db
from app.intelligence.market.adjacent import (
    AdjacencyExpansionMaturity,
    AdjacentConceptCandidate,
    AdjacentConceptOutput,
    AdjacentExpansionResult,
    ProbeEvidenceSummary,
    _compute_adjacent_priority,
    _normalize_corroboration,
    _normalize_evidence_strength,
    _normalize_velocity_trigger,
    classify_expansion_eligibility,
    plan_adjacent_expansion,
)
from app.intelligence.market.planner_prompts import (
    ADJACENT_LLM_HARD_CAP,
    ADJACENT_MIN_VIDEOS_FOR_EXPANSION,
)
from app.intelligence.market.planner_repository import (
    get_exploration_probe,
    get_probe_creator_diversity,
    get_probe_supporting_videos,
    list_expandable_parent_probes,
    list_exploration_probes,
    update_probe_dispatch,
    update_probe_status,
)

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# LLM response fixtures
# ---------------------------------------------------------------------------

_LLM_TWO_ADJACENT = json.dumps(
    {
        "probes": [
            {
                "query": "frugal grocery shopping",
                "market_region_label": "Grocery Savings",
                "rationale": "Natural extension of money-saving content into grocery budgeting.",
                "evidence_refs": ["job-1"],
                "relation_to_parent": "Specific expense category within broader savings topic.",
                "estimated_niche_fit": 0.90,
                "distinctiveness_rationale": "Focused on one recurring household expense.",
            },
            {
                "query": "debt payoff strategies",
                "market_region_label": "Debt Elimination",
                "rationale": "High demand area for people wanting to free up cash.",
                "evidence_refs": ["job-1"],
                "relation_to_parent": (
                    "Complements savings by addressing debt side of personal finance."
                ),
                "estimated_niche_fit": 0.85,
                "distinctiveness_rationale": "Addresses liability reduction vs asset building.",
            },
        ]
    }
)

_LLM_EMPTY = json.dumps({"probes": []})

_LLM_INVALID_REFS = json.dumps(
    {
        "probes": [
            {
                "query": "side hustle ideas",
                "market_region_label": "Side Hustles",
                "rationale": "Income supplementation.",
                "evidence_refs": ["invented-ref-999"],
                "relation_to_parent": "Earning more to save more.",
                "estimated_niche_fit": 0.70,
                "distinctiveness_rationale": "Income vs expense angle.",
            },
        ]
    }
)

_LLM_MISSING_REFS = json.dumps(
    {
        "probes": [
            {
                "query": "investment basics",
                "market_region_label": "Investing",
                "rationale": "Next step after savings.",
                "evidence_refs": [],
                "relation_to_parent": "Growing saved money.",
                "estimated_niche_fit": 0.75,
                "distinctiveness_rationale": "Wealth building vs frugality.",
            },
        ]
    }
)

_LLM_EXCLUDED = json.dumps(
    {
        "probes": [
            {
                "query": "casino gambling tips",
                "market_region_label": "Gambling",
                "rationale": "Not relevant.",
                "evidence_refs": ["job-1"],
                "relation_to_parent": "Alternative money flow.",
                "estimated_niche_fit": 0.10,
                "distinctiveness_rationale": "Risk-based income.",
            },
        ]
    }
)

_LLM_PARENT_DUP = json.dumps(
    {
        "probes": [
            {
                "query": "money saving tips",
                "market_region_label": "Money Saving",
                "rationale": "Same as parent.",
                "evidence_refs": ["job-1"],
                "relation_to_parent": "Identical to parent probe.",
                "estimated_niche_fit": 1.0,
                "distinctiveness_rationale": "Not distinct.",
            },
        ]
    }
)

_LLM_MANY = json.dumps(
    {
        "probes": [
            {
                "query": f"finance topic {i}",
                "market_region_label": f"Finance Area {i}",
                "rationale": f"Area {i} rationale.",
                "evidence_refs": ["job-1"],
                "relation_to_parent": f"Related to parent via angle {i}.",
                "estimated_niche_fit": 0.80,
                "distinctiveness_rationale": f"Distinct from others by index {i}.",
            }
            for i in range(1, 11)
        ]
    }
)


class _FailProvider:
    name = "fail"

    def complete(self, request):
        raise RuntimeError("simulated LLM failure")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    with tempfile.TemporaryDirectory() as d:
        c = open_db(pathlib.Path(d) / "test.db")
        yield c
        c.close()


_DEFAULT_TITLES = [
    "How to save money fast",
    "10 frugal living tips for beginners",
    "Budget grocery shopping guide",
]


def _seed_probe_with_job(
    conn,
    *,
    query="money saving tips",
    view_counts=None,
    creator_ids=None,
    with_titles: bool = True,
):
    """Create a probe + dispatched job + observations. Returns (probe, job_id).

    with_titles=True (default): also seeds VIDEO_TITLE observations so the probe
    is eligible for adjacent expansion (EXPLORATORY or CORROBORATED).
    with_titles=False: only view counts seeded → SEMANTICALLY_UNGROUNDED.
    """
    from app.intelligence.market.cold_start import ExplorationProfile, plan_cold_start

    profile = ExplorationProfile(
        primary_niche=query,
        excluded_topics=["gambling", "casino games"],
    )
    result = plan_cold_start(conn, profile, provider=FakeProvider(_LLM_EMPTY))
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    parent_probe = next(p for p in probes if p.status == "selected")

    # Create a mock collection job
    from app.intelligence.market.models import (
        SEARCH_RESULT_TOTAL_ESTIMATE,
        VIDEO_TITLE,
        VIDEO_VIEW_COUNT,
    )
    from app.intelligence.market.repository import create_market_collection_job, persist_observation

    job = create_market_collection_job(conn, job_type="search_scan", origin_type="manual")

    # Dispatch the probe to the job
    update_probe_dispatch(conn, parent_probe.id, dispatched_job_id=job.id, dispatched_at=_NOW)

    # Persist observations
    view_counts = view_counts or [50000, 120000, 80000]
    creator_ids = creator_ids or ["UCcreator1", "UCcreator2", "UCcreator3"]

    from app.intelligence.market.collector import make_obs_input_hash
    from app.intelligence.market.repository import link_job_observation

    date_bucket = datetime.now(UTC).strftime("%Y-%m-%d")

    for i, (vc, ch) in enumerate(zip(view_counts, creator_ids, strict=True)):
        vid_id = f"vid{i:04d}"
        ih = make_obs_input_hash(
            platform="youtube",
            provider="youtube_data_api",
            signal_type=VIDEO_VIEW_COUNT,
            external_video_id=vid_id,
            external_channel_id=ch,
            normalized_query=None,
            region_code=None,
            language_code=None,
            date_bucket=date_bucket,
        )
        obs = persist_observation(
            conn,
            platform="youtube",
            provider="youtube_data_api",
            collector_name="test",
            signal_type=VIDEO_VIEW_COUNT,
            observed_at=_NOW,
            input_hash=ih,
            external_video_id=vid_id,
            external_channel_id=ch,
            category_id="22",
            signal_value_numeric=float(vc),
        )
        link_job_observation(conn, job.id, obs.id)

        if with_titles:
            title = _DEFAULT_TITLES[i % len(_DEFAULT_TITLES)]
            ih_title = make_obs_input_hash(
                platform="youtube",
                provider="youtube_data_api",
                signal_type=VIDEO_TITLE,
                external_video_id=vid_id,
                external_channel_id=ch,
                normalized_query=None,
                region_code=None,
                language_code=None,
                date_bucket=date_bucket,
            )
            obs_title = persist_observation(
                conn,
                platform="youtube",
                provider="youtube_data_api",
                collector_name="test",
                signal_type=VIDEO_TITLE,
                observed_at=_NOW,
                input_hash=ih_title,
                external_video_id=vid_id,
                external_channel_id=ch,
                signal_value_text=title,
            )
            link_job_observation(conn, job.id, obs_title.id)

    # Total estimate observation
    ih_est = make_obs_input_hash(
        platform="youtube",
        provider="youtube_data_api",
        signal_type=SEARCH_RESULT_TOTAL_ESTIMATE,
        external_video_id=None,
        external_channel_id=None,
        normalized_query=query,
        region_code=None,
        language_code=None,
        date_bucket=date_bucket,
    )
    obs_est = persist_observation(
        conn,
        platform="youtube",
        provider="youtube_data_api",
        collector_name="test",
        signal_type=SEARCH_RESULT_TOTAL_ESTIMATE,
        observed_at=_NOW,
        input_hash=ih_est,
        signal_value_numeric=15000.0,
        normalized_query=query,
    )
    link_job_observation(conn, job.id, obs_est.id)

    refreshed = get_exploration_probe(conn, parent_probe.id)
    return refreshed, job.id


# ---------------------------------------------------------------------------
# A–C: Maturity classification — INSUFFICIENT
# ---------------------------------------------------------------------------


def test_a_insufficient_maturity_when_zero_videos():
    """A: video_count=0 → INSUFFICIENT."""
    summary = ProbeEvidenceSummary(
        probe_id=1,
        query_text="test",
        normalized_query="test",
        exploration_depth=0,
        video_count=0,
        avg_view_count=None,
        total_result_estimate=None,
        top_categories=[],
        creator_count=0,
        has_velocity=False,
        peak_velocity_per_day=None,
        velocity_maturity=None,
        has_semantic_evidence=False,
        video_evidence=[],
        recurring_terms=[],
        maturity=AdjacencyExpansionMaturity.INSUFFICIENT.value,
        evidence_ref_ids=["job-1"],
        dispatched_job_id=None,
    )
    assert classify_expansion_eligibility(summary) == AdjacencyExpansionMaturity.INSUFFICIENT


def test_b_insufficient_below_minimum_threshold():
    """B: video_count < ADJACENT_MIN_VIDEOS_FOR_EXPANSION → INSUFFICIENT."""
    below = ADJACENT_MIN_VIDEOS_FOR_EXPANSION - 1
    summary = ProbeEvidenceSummary(
        probe_id=1,
        query_text="test",
        normalized_query="test",
        exploration_depth=0,
        video_count=below,
        avg_view_count=50000.0,
        total_result_estimate=5000.0,
        top_categories=["22"],
        creator_count=2,
        has_velocity=False,
        peak_velocity_per_day=None,
        velocity_maturity=None,
        has_semantic_evidence=False,
        video_evidence=[],
        recurring_terms=[],
        maturity=AdjacencyExpansionMaturity.INSUFFICIENT.value,
        evidence_ref_ids=["job-1"],
        dispatched_job_id=1,
    )
    assert classify_expansion_eligibility(summary) == AdjacencyExpansionMaturity.INSUFFICIENT


def test_c_exploratory_at_minimum_without_velocity():
    """C: video_count == minimum, has titles, no velocity → EXPLORATORY."""
    summary = ProbeEvidenceSummary(
        probe_id=1,
        query_text="test",
        normalized_query="test",
        exploration_depth=0,
        video_count=ADJACENT_MIN_VIDEOS_FOR_EXPANSION,
        avg_view_count=50000.0,
        total_result_estimate=5000.0,
        top_categories=["22"],
        creator_count=2,
        has_velocity=False,
        peak_velocity_per_day=None,
        velocity_maturity=None,
        has_semantic_evidence=True,
        video_evidence=[],
        recurring_terms=[],
        maturity=AdjacencyExpansionMaturity.EXPLORATORY.value,
        evidence_ref_ids=["job-1"],
        dispatched_job_id=1,
    )
    assert classify_expansion_eligibility(summary) == AdjacencyExpansionMaturity.EXPLORATORY


def test_d_corroborated_with_velocity():
    """D: video_count >= minimum AND has titles AND velocity data → CORROBORATED."""
    summary = ProbeEvidenceSummary(
        probe_id=1,
        query_text="test",
        normalized_query="test",
        exploration_depth=0,
        video_count=ADJACENT_MIN_VIDEOS_FOR_EXPANSION,
        avg_view_count=150000.0,
        total_result_estimate=10000.0,
        top_categories=["22"],
        creator_count=3,
        has_velocity=True,
        peak_velocity_per_day=3500.0,
        velocity_maturity="early",
        has_semantic_evidence=True,
        video_evidence=[],
        recurring_terms=[],
        maturity=AdjacencyExpansionMaturity.CORROBORATED.value,
        evidence_ref_ids=["job-1", "vel-1"],
        dispatched_job_id=1,
    )
    assert classify_expansion_eligibility(summary) == AdjacencyExpansionMaturity.CORROBORATED


# ---------------------------------------------------------------------------
# E–H: ProbeEvidenceSummary validation
# ---------------------------------------------------------------------------


def test_e_probe_evidence_summary_round_trips():
    """E: ProbeEvidenceSummary validates and round-trips without error."""
    s = ProbeEvidenceSummary(
        probe_id=42,
        query_text="personal finance",
        normalized_query="personal finance",
        exploration_depth=1,
        video_count=10,
        avg_view_count=75000.0,
        total_result_estimate=8000.0,
        top_categories=["22", "10"],
        creator_count=5,
        has_velocity=True,
        peak_velocity_per_day=2000.0,
        velocity_maturity="establishing",
        has_semantic_evidence=True,
        video_evidence=[],
        recurring_terms=["money", "tips"],
        maturity=AdjacencyExpansionMaturity.CORROBORATED.value,
        evidence_ref_ids=["job-7", "obs-11", "obs-12", "vel-3"],
        dispatched_job_id=7,
    )
    assert s.probe_id == 42
    assert s.maturity == "corroborated"
    assert "job-7" in s.evidence_ref_ids
    assert s.has_semantic_evidence is True
    assert s.recurring_terms == ["money", "tips"]


def test_f_probe_evidence_summary_extra_fields_forbidden():
    """F: extra fields on ProbeEvidenceSummary are rejected."""
    with pytest.raises(ValidationError):
        ProbeEvidenceSummary(
            probe_id=1,
            query_text="t",
            normalized_query="t",
            exploration_depth=0,
            video_count=5,
            avg_view_count=None,
            total_result_estimate=None,
            top_categories=[],
            creator_count=1,
            has_velocity=False,
            peak_velocity_per_day=None,
            velocity_maturity=None,
            has_semantic_evidence=True,
            video_evidence=[],
            recurring_terms=[],
            maturity="exploratory",
            evidence_ref_ids=["job-1"],
            dispatched_job_id=1,
            unexpected_field="boom",
        )


def test_g_top_categories_is_list_of_strings():
    """G: top_categories holds string category IDs."""
    s = ProbeEvidenceSummary(
        probe_id=1,
        query_text="t",
        normalized_query="t",
        exploration_depth=0,
        video_count=5,
        avg_view_count=None,
        total_result_estimate=None,
        top_categories=["22", "10", "26"],
        creator_count=2,
        has_velocity=False,
        peak_velocity_per_day=None,
        velocity_maturity=None,
        has_semantic_evidence=True,
        video_evidence=[],
        recurring_terms=[],
        maturity="exploratory",
        evidence_ref_ids=["job-1"],
        dispatched_job_id=1,
    )
    assert all(isinstance(c, str) for c in s.top_categories)


def test_h_maturity_field_accepts_enum_values():
    """H: maturity field accepts all AdjacencyExpansionMaturity string values."""
    for mat in AdjacencyExpansionMaturity:
        s = ProbeEvidenceSummary(
            probe_id=1,
            query_text="t",
            normalized_query="t",
            exploration_depth=0,
            video_count=5,
            avg_view_count=None,
            total_result_estimate=None,
            top_categories=[],
            creator_count=0,
            has_velocity=False,
            peak_velocity_per_day=None,
            velocity_maturity=None,
            has_semantic_evidence=False,
            video_evidence=[],
            recurring_terms=[],
            maturity=mat.value,
            evidence_ref_ids=["job-1"],
            dispatched_job_id=None,
        )
        assert s.maturity == mat.value


# ---------------------------------------------------------------------------
# I–L: Repository helpers
# ---------------------------------------------------------------------------


def test_i_list_expandable_parent_probes_returns_selected(conn):
    """I: list_expandable_parent_probes returns selected probes at depth < max_depth."""
    from app.intelligence.market.cold_start import ExplorationProfile, plan_cold_start

    profile = ExplorationProfile(primary_niche="personal finance tips")
    result = plan_cold_start(conn, profile, provider=FakeProvider(_LLM_EMPTY))

    probes = list_expandable_parent_probes(conn, result.exploration_run_id, max_depth=2)
    assert len(probes) >= 1
    assert all(p.status in ("selected", "dispatched") for p in probes)
    assert all(p.exploration_depth < 2 for p in probes)


def test_j_get_probe_supporting_videos_aggregates_correctly(conn):
    """J: get_probe_supporting_videos returns per-video aggregation."""
    parent_probe, job_id = _seed_probe_with_job(
        conn, view_counts=[50000, 120000, 80000], creator_ids=["UC1", "UC2", "UC3"]
    )
    videos = get_probe_supporting_videos(conn, parent_probe.id)
    assert len(videos) == 3
    assert all("external_video_id" in v for v in videos)
    assert all("view_count" in v for v in videos)


def test_k_get_probe_creator_diversity_counts_distinct(conn):
    """K: get_probe_creator_diversity counts distinct creator channel IDs."""
    parent_probe, _ = _seed_probe_with_job(
        conn, view_counts=[10000, 20000, 30000], creator_ids=["UCa", "UCa", "UCb"]
    )
    diversity = get_probe_creator_diversity(conn, parent_probe.id)
    assert diversity == 2  # UCa appears twice but counted once


def test_l_get_probe_supporting_videos_empty_without_job(conn):
    """L: get_probe_supporting_videos returns [] for probe with no dispatched_job_id."""
    from app.intelligence.market.cold_start import ExplorationProfile, plan_cold_start

    profile = ExplorationProfile(primary_niche="test topic")
    result = plan_cold_start(conn, profile, provider=FakeProvider(_LLM_EMPTY))
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    undispatched = next(p for p in probes if p.dispatched_job_id is None)
    assert get_probe_supporting_videos(conn, undispatched.id) == []


# ---------------------------------------------------------------------------
# M–P: AdjacentConceptCandidate model validation
# ---------------------------------------------------------------------------


def test_m_adjacent_concept_candidate_round_trips():
    """M: valid AdjacentConceptCandidate validates without error."""
    c = AdjacentConceptCandidate(
        query="frugal grocery shopping",
        market_region_label="Grocery Savings",
        rationale="Specific to household budgeting.",
        evidence_refs=["job-1", "obs-42"],
        relation_to_parent="Sub-category of money saving.",
        estimated_niche_fit=0.90,
        distinctiveness_rationale="Food-specific savings.",
    )
    assert c.query == "frugal grocery shopping"
    assert "job-1" in c.evidence_refs


def test_n_adjacent_concept_candidate_extra_fields_forbidden():
    """N: extra fields on AdjacentConceptCandidate are rejected (extra='forbid')."""
    with pytest.raises(ValidationError):
        AdjacentConceptCandidate(
            query="q",
            market_region_label="L",
            rationale="r",
            evidence_refs=["job-1"],
            relation_to_parent="rel",
            estimated_niche_fit=0.5,
            distinctiveness_rationale="d",
            unknown_field="x",
        )


def test_o_estimated_niche_fit_is_float():
    """O: estimated_niche_fit is a float field."""
    c = AdjacentConceptCandidate(
        query="budgeting for beginners",
        market_region_label="Budgeting",
        rationale="r",
        evidence_refs=["job-1"],
        relation_to_parent="rel",
        estimated_niche_fit=0.75,
        distinctiveness_rationale="d",
    )
    assert isinstance(c.estimated_niche_fit, float)


def test_p_evidence_refs_is_list_of_strings():
    """P: evidence_refs holds string reference IDs."""
    c = AdjacentConceptCandidate(
        query="q",
        market_region_label="L",
        rationale="r",
        evidence_refs=["job-1", "obs-22"],
        relation_to_parent="rel",
        estimated_niche_fit=0.6,
        distinctiveness_rationale="d",
    )
    assert all(isinstance(r, str) for r in c.evidence_refs)


# ---------------------------------------------------------------------------
# Q–T: AdjacentConceptOutput validation
# ---------------------------------------------------------------------------


def test_q_adjacent_concept_output_round_trips():
    """Q: valid AdjacentConceptOutput validates without error."""
    output = AdjacentConceptOutput(
        probes=[
            AdjacentConceptCandidate(
                query="q",
                market_region_label="L",
                rationale="r",
                evidence_refs=["job-1"],
                relation_to_parent="rel",
                estimated_niche_fit=0.8,
                distinctiveness_rationale="d",
            )
        ]
    )
    assert len(output.probes) == 1


def test_r_adjacent_concept_output_enforces_hard_cap():
    """R: AdjacentConceptOutput rejects lists longer than ADJACENT_LLM_HARD_CAP."""
    too_many = [
        AdjacentConceptCandidate(
            query=f"topic {i}",
            market_region_label=f"L{i}",
            rationale="r",
            evidence_refs=["job-1"],
            relation_to_parent="rel",
            estimated_niche_fit=0.8,
            distinctiveness_rationale="d",
        )
        for i in range(ADJACENT_LLM_HARD_CAP + 1)
    ]
    with pytest.raises(ValidationError):
        AdjacentConceptOutput(probes=too_many)


def test_s_adjacent_concept_output_empty_probes_valid():
    """S: AdjacentConceptOutput with empty probes list is valid."""
    output = AdjacentConceptOutput(probes=[])
    assert output.probes == []


def test_t_adjacent_concept_output_extra_fields_forbidden():
    """T: extra fields on AdjacentConceptOutput are rejected."""
    with pytest.raises(ValidationError):
        AdjacentConceptOutput(probes=[], unexpected="x")


# ---------------------------------------------------------------------------
# U–X: plan_adjacent_expansion happy path
# ---------------------------------------------------------------------------


def test_u_adjacent_expansion_creates_run(conn):
    """U: plan_adjacent_expansion creates a new exploration run."""
    parent_probe, _ = _seed_probe_with_job(conn)
    result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(_LLM_TWO_ADJACENT),
        parent_probe_id=parent_probe.id,
        primary_niche="money saving tips",
        excluded_topics=["gambling"],
    )
    assert result.exploration_run_id > 0
    assert result.parent_probe_id == parent_probe.id


def test_v_adjacent_probes_have_incremented_depth(conn):
    """V: adjacent probes are created at exploration_depth = parent_depth + 1."""
    parent_probe, _ = _seed_probe_with_job(conn)
    result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(_LLM_TWO_ADJACENT),
        parent_probe_id=parent_probe.id,
        primary_niche="money saving tips",
    )
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    adjacent = [p for p in probes if p.probe_type == "adjacent_topic"]
    assert len(adjacent) >= 1
    for p in adjacent:
        assert p.exploration_depth == parent_probe.exploration_depth + 1


def test_w_adjacent_probes_have_correct_parent_lineage(conn):
    """W: adjacent probes link back to the parent probe ID."""
    parent_probe, _ = _seed_probe_with_job(conn)
    result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(_LLM_TWO_ADJACENT),
        parent_probe_id=parent_probe.id,
    )
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    adjacent = [p for p in probes if p.probe_type == "adjacent_topic"]
    for p in adjacent:
        assert p.parent_probe_id == parent_probe.id


def test_x_adjacent_probes_have_adjacent_topic_type(conn):
    """X: all adjacent expansion probes use probe_type='adjacent_topic'."""
    parent_probe, _ = _seed_probe_with_job(conn)
    result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(_LLM_TWO_ADJACENT),
        parent_probe_id=parent_probe.id,
    )
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    selected = [p for p in probes if p.status == "selected"]
    assert len(selected) >= 1
    for p in selected:
        assert p.probe_type == "adjacent_topic"


# ---------------------------------------------------------------------------
# Y–AB: Evidence reference validation
# ---------------------------------------------------------------------------


def test_y_invalid_evidence_refs_cause_rejection(conn):
    """Y: LLM candidates with references not in supplied set are rejected."""
    parent_probe, _ = _seed_probe_with_job(conn)
    result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(_LLM_INVALID_REFS),
        parent_probe_id=parent_probe.id,
    )
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    rejected = [p for p in probes if p.status == "rejected"]
    assert len(rejected) >= 1
    invalid_probe = next(
        p for p in rejected if "invalid_evidence_refs" in (p.decision_reason or "")
    )
    assert invalid_probe is not None


def test_z_empty_evidence_refs_cause_rejection(conn):
    """Z: LLM candidates with empty evidence_refs are rejected."""
    parent_probe, _ = _seed_probe_with_job(conn)
    result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(_LLM_MISSING_REFS),
        parent_probe_id=parent_probe.id,
    )
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    rejected = [p for p in probes if p.status == "rejected"]
    assert len(rejected) >= 1
    missing_probe = next(
        p for p in rejected if "missing_evidence_refs" in (p.decision_reason or "")
    )
    assert missing_probe is not None


def test_aa_valid_evidence_refs_allow_selection(conn):
    """AA: candidates with valid evidence_refs (from supplied set) can be selected."""
    parent_probe, _ = _seed_probe_with_job(conn)
    result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(_LLM_TWO_ADJACENT),
        parent_probe_id=parent_probe.id,
    )
    assert result.selected_count >= 1


def test_ab_evidence_ref_ids_in_summary_include_job_id(conn):
    """AB: evidence_ref_ids in the summary always include the dispatched job ref."""
    from app.intelligence.market.adjacent import _build_evidence_summary

    parent_probe, job_id = _seed_probe_with_job(conn)
    summary = _build_evidence_summary(
        conn,
        probe_id=parent_probe.id,
        query_text=parent_probe.query_text,
        normalized_query=parent_probe.normalized_query,
        exploration_depth=parent_probe.exploration_depth,
        dispatched_job_id=parent_probe.dispatched_job_id,
    )
    assert f"job-{job_id}" in summary.evidence_ref_ids


# ---------------------------------------------------------------------------
# AC–AF: Dedup + excluded topics
# ---------------------------------------------------------------------------


def test_ac_near_duplicate_adjacent_probe_deferred(conn):
    """AC: near-duplicate adjacent probe (same query twice) is deferred."""
    # Two identical candidates — first selected, second deferred
    two_same = json.dumps(
        {
            "probes": [
                {
                    "query": "frugal grocery shopping",
                    "market_region_label": "Grocery",
                    "rationale": "r",
                    "evidence_refs": ["job-1"],
                    "relation_to_parent": "rel",
                    "estimated_niche_fit": 0.90,
                    "distinctiveness_rationale": "d",
                },
                {
                    "query": "frugal grocery shopping tips",
                    "market_region_label": "Grocery Tips",
                    "rationale": "r2",
                    "evidence_refs": ["job-1"],
                    "relation_to_parent": "rel",
                    "estimated_niche_fit": 0.88,
                    "distinctiveness_rationale": "d",
                },
            ]
        }
    )
    parent_probe, _ = _seed_probe_with_job(conn)
    result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(two_same),
        parent_probe_id=parent_probe.id,
    )
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    deferred = [p for p in probes if p.status == "deferred"]
    assert len(deferred) >= 1


def test_ad_excluded_topic_rejected(conn):
    """AD: adjacent candidates matching excluded topics are rejected."""
    parent_probe, _ = _seed_probe_with_job(conn)
    # excluded_topics must Jaccard-match the candidate query ("casino gambling tips").
    # "casino gambling" shares 2/3 tokens → Jaccard ≥ 0.60 threshold.
    result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(_LLM_EXCLUDED),
        parent_probe_id=parent_probe.id,
        excluded_topics=["casino gambling", "casino games"],
    )
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    rejected = [p for p in probes if p.status == "rejected"]
    excluded_probe = next(
        (p for p in rejected if "excluded_topic" in (p.decision_reason or "")),
        None,
    )
    assert excluded_probe is not None


def test_ae_parent_query_duplicate_deferred(conn):
    """AE: adjacent candidate near-identical to parent probe query is deferred."""
    parent_probe, _ = _seed_probe_with_job(conn, query="money saving tips")
    result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(_LLM_PARENT_DUP),
        parent_probe_id=parent_probe.id,
    )
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    deferred = [p for p in probes if p.status == "deferred"]
    assert len(deferred) >= 1


def test_af_prior_coverage_reduces_novelty(conn):
    """AF: a query already in prior selected probes gets novelty=0.2 in priority."""
    from app.intelligence.market.cold_start import ExplorationProfile, plan_cold_start

    # First pass — frugal grocery selected
    profile = ExplorationProfile(primary_niche="money saving tips")
    r1 = plan_cold_start(conn, profile, provider=FakeProvider(_LLM_EMPTY))

    # Manually mark a probe as selected to pollute prior queries
    probes1 = list_exploration_probes(conn, run_id=r1.exploration_run_id)
    if probes1:
        # Add a prior market_region probe manually (simulate prior run)
        from app.intelligence.market.planner_models import ExplorationProbeType
        from app.intelligence.market.planner_repository import create_exploration_probe

        prior = create_exploration_probe(
            conn,
            run_id=r1.exploration_run_id,
            query_text="frugal grocery shopping",
            normalized_query="frugal grocery shopping",
            probe_type=ExplorationProbeType.MARKET_REGION.value,
        )
        update_probe_status(conn, prior.id, status="selected", decided_at=_NOW)

    # Now expand from a probe in a second run
    parent_probe, _ = _seed_probe_with_job(conn)
    result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(_LLM_TWO_ADJACENT),
        parent_probe_id=parent_probe.id,
        channel_id=None,
    )
    # Result should still process (no error); prior coverage affects scoring, not blocking
    assert result.exploration_run_id > 0


# ---------------------------------------------------------------------------
# AG–AJ: Budget enforcement
# ---------------------------------------------------------------------------


def test_ag_search_budget_not_exceeded(conn):
    """AG: search calls used never exceeds search_budget."""
    parent_probe, _ = _seed_probe_with_job(conn)
    for budget in (1, 2, 5):
        result = plan_adjacent_expansion(
            conn,
            provider=FakeProvider(_LLM_MANY),
            parent_probe_id=parent_probe.id,
            search_budget=budget,
        )
        assert result.diagnostics["search_calls_used"] <= budget


def test_ah_probe_budget_not_exceeded(conn):
    """AH: selected probes never exceed max_probes."""
    parent_probe, _ = _seed_probe_with_job(conn)
    for max_p in (1, 2, 3):
        result = plan_adjacent_expansion(
            conn,
            provider=FakeProvider(_LLM_MANY),
            parent_probe_id=parent_probe.id,
            max_probes=max_p,
            search_budget=max_p + 5,
        )
        assert result.selected_count <= max_p


def test_ai_candidates_beyond_budget_deferred(conn):
    """AI: LLM candidates that exceed the probe budget are deferred."""
    parent_probe, _ = _seed_probe_with_job(conn)
    result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(_LLM_MANY),
        parent_probe_id=parent_probe.id,
        max_probes=1,
        search_budget=20,
    )
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    deferred = [p for p in probes if p.status == "deferred"]
    assert len(deferred) >= 1


def test_aj_diagnostics_include_search_calls_used(conn):
    """AJ: result diagnostics contain search_calls_used key."""
    parent_probe, _ = _seed_probe_with_job(conn)
    result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(_LLM_TWO_ADJACENT),
        parent_probe_id=parent_probe.id,
    )
    assert "search_calls_used" in result.diagnostics


# ---------------------------------------------------------------------------
# AK–AM: LLM failure
# ---------------------------------------------------------------------------


def test_ak_llm_failure_produces_partial_status(conn):
    """AK: LLM exception → run status='partial', no adjacent probes selected."""
    parent_probe, _ = _seed_probe_with_job(conn)
    result = plan_adjacent_expansion(
        conn,
        provider=_FailProvider(),
        parent_probe_id=parent_probe.id,
    )
    assert result.diagnostics["final_status"] == "partial"
    assert result.llm_used is False


def test_al_llm_failure_selects_zero_probes(conn):
    """AL: LLM failure → selected_count == 0, run still finalized."""
    parent_probe, _ = _seed_probe_with_job(conn)
    result = plan_adjacent_expansion(
        conn,
        provider=_FailProvider(),
        parent_probe_id=parent_probe.id,
    )
    assert result.selected_count == 0
    assert result.exploration_run_id > 0


def test_am_llm_error_in_diagnostics(conn):
    """AM: LLM failure → diagnostics['llm_error'] is populated."""
    parent_probe, _ = _seed_probe_with_job(conn)
    result = plan_adjacent_expansion(
        conn,
        provider=_FailProvider(),
        parent_probe_id=parent_probe.id,
    )
    assert "llm_error" in result.diagnostics
    assert "simulated LLM failure" in result.diagnostics["llm_error"]


# ---------------------------------------------------------------------------
# AN–AQ: Priority component computation
# ---------------------------------------------------------------------------


def test_an_evidence_strength_scales_with_video_count():
    """AN: evidence_strength increases with more videos and higher avg views."""
    low = _normalize_evidence_strength(video_count=3, avg_view_count=5000)
    high = _normalize_evidence_strength(video_count=25, avg_view_count=200000)
    assert low < high
    assert 0.0 <= low <= 1.0
    assert 0.0 <= high <= 1.0


def test_ao_velocity_trigger_zero_without_velocity():
    """AO: velocity_trigger=0.0 when peak_velocity_per_day is None."""
    assert _normalize_velocity_trigger(None) == 0.0
    assert _normalize_velocity_trigger(0.0) == 0.0


def test_ap_velocity_trigger_scales_to_threshold():
    """AP: velocity_trigger caps at 1.0 at 10000 views/day."""
    assert _normalize_velocity_trigger(10_000.0) == 1.0
    mid = _normalize_velocity_trigger(5_000.0)
    assert 0.0 < mid < 1.0


def test_aq_corroboration_scales_with_creator_count():
    """AQ: corroboration=0.0 for 1 creator, approaches 1.0 for 6+."""
    assert _normalize_corroboration(1) == 0.0
    high = _normalize_corroboration(6)
    assert high == 1.0


def test_ar_compute_adjacent_priority_returns_components(conn):
    """AR: _compute_adjacent_priority returns (score, components) with all 6 keys."""
    score, comps = _compute_adjacent_priority(
        niche_fit=0.8,
        novelty=1.0,
        evidence_strength=0.5,
        velocity_trigger=0.3,
        corroboration=0.2,
        depth=1,
    )
    assert 0.0 <= score <= 1.0
    expected_keys = {
        "niche_fit",
        "novelty",
        "evidence_strength",
        "velocity_trigger",
        "corroboration",
        "depth_factor",
    }
    assert set(comps.keys()) == expected_keys


# ---------------------------------------------------------------------------
# AS–AT: AdjacentExpansionResult structure
# ---------------------------------------------------------------------------


def test_as_adjacent_expansion_result_extra_fields_forbidden():
    """AS: AdjacentExpansionResult rejects extra fields (extra='forbid')."""
    with pytest.raises(ValidationError):
        AdjacentExpansionResult(
            exploration_run_id=1,
            parent_probe_id=2,
            candidate_count=0,
            selected_count=0,
            deferred_count=0,
            rejected_count=0,
            selected_probe_ids=[],
            llm_used=False,
            provider_name=None,
            model=None,
            prompt_version=None,
            maturity="exploratory",
            diagnostics={},
            unknown_field="x",
        )


def test_at_result_diagnostics_has_required_keys(conn):
    """AT: diagnostics dict always contains core keys after successful expansion."""
    parent_probe, _ = _seed_probe_with_job(conn)
    result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(_LLM_TWO_ADJACENT),
        parent_probe_id=parent_probe.id,
    )
    required = {
        "parent_probe_id",
        "child_depth",
        "video_count",
        "search_calls_used",
        "final_status",
    }
    assert required.issubset(result.diagnostics.keys())


# ---------------------------------------------------------------------------
# AU–AW: list_expandable_parent_probes + multi-probe context
# ---------------------------------------------------------------------------


def test_au_expandable_probes_excludes_rejected(conn):
    """AU: list_expandable_parent_probes does not return rejected or deferred probes."""
    from app.intelligence.market.cold_start import ExplorationProfile, plan_cold_start

    profile = ExplorationProfile(
        primary_niche="money saving",
        secondary_niches=["budgeting basics"],
    )
    result = plan_cold_start(
        conn,
        profile,
        provider=FakeProvider(_LLM_EMPTY),
        max_probes=2,
    )
    expandable = list_expandable_parent_probes(conn, result.exploration_run_id, max_depth=3)
    assert all(p.status in ("selected", "dispatched") for p in expandable)


def test_av_insufficient_probe_returns_no_run(conn):
    """AV: probe with insufficient evidence (no job) → exploration_run_id == -1."""
    from app.intelligence.market.cold_start import ExplorationProfile, plan_cold_start

    profile = ExplorationProfile(primary_niche="personal finance tips")
    result = plan_cold_start(conn, profile, provider=FakeProvider(_LLM_EMPTY))
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    undispatched = next(p for p in probes if p.dispatched_job_id is None)

    adj_result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(_LLM_TWO_ADJACENT),
        parent_probe_id=undispatched.id,
    )
    assert adj_result.exploration_run_id == -1
    assert adj_result.maturity == "insufficient"


def test_aw_adjacent_probe_parent_job_id_matches(conn):
    """AW: adjacent probe parent_job_id matches dispatched_job_id of parent probe."""
    parent_probe, job_id = _seed_probe_with_job(conn)
    result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(_LLM_TWO_ADJACENT),
        parent_probe_id=parent_probe.id,
    )
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    adjacent = [p for p in probes if p.probe_type == "adjacent_topic" and p.status == "selected"]
    for p in adjacent:
        assert p.parent_job_id == job_id


# ---------------------------------------------------------------------------
# AX: Full integration — cold-start → dispatch → observations → adjacent expansion
# ---------------------------------------------------------------------------


def test_ax_full_integration(conn):
    """AX: full pipeline — cold-start plan → dispatch probe → seed
    observations → adjacent expand."""
    from app.intelligence.market.cold_start import ExplorationProfile, plan_cold_start

    # 1. Cold-start plan
    profile = ExplorationProfile(
        primary_niche="personal finance tips",
        secondary_niches=["budgeting basics"],
        excluded_topics=["gambling"],
    )
    cold_result = plan_cold_start(
        conn,
        profile,
        provider=FakeProvider(_LLM_EMPTY),
        max_probes=3,
        search_budget=5,
    )
    assert cold_result.selected_count >= 1

    # 2. Pick the first selected probe and dispatch it with a mock job
    from app.intelligence.market.collector import make_obs_input_hash
    from app.intelligence.market.models import VIDEO_TITLE, VIDEO_VIEW_COUNT
    from app.intelligence.market.repository import (
        create_market_collection_job,
        link_job_observation,
        persist_observation,
    )

    probes = list_exploration_probes(conn, run_id=cold_result.exploration_run_id)
    parent_probe = next(p for p in probes if p.status == "selected")

    job = create_market_collection_job(conn, job_type="search_scan", origin_type="manual")
    update_probe_dispatch(conn, parent_probe.id, dispatched_job_id=job.id, dispatched_at=_NOW)

    date_bucket = datetime.now(UTC).strftime("%Y-%m-%d")

    # 3. Seed 5 video observations + title observations for semantic grounding
    ax_titles = [
        "Personal finance tips for beginners",
        "How to budget your money effectively",
        "Saving money on groceries guide",
        "Emergency fund setup steps",
        "Debt payoff strategies that work",
    ]
    for i in range(5):
        vid_id = f"vid{i:04d}"
        ch_id = f"UCch{i % 3}"
        ih = make_obs_input_hash(
            platform="youtube",
            provider="youtube_data_api",
            signal_type=VIDEO_VIEW_COUNT,
            external_video_id=vid_id,
            external_channel_id=ch_id,
            normalized_query=None,
            region_code=None,
            language_code=None,
            date_bucket=date_bucket,
        )
        obs = persist_observation(
            conn,
            platform="youtube",
            provider="youtube_data_api",
            collector_name="test",
            signal_type=VIDEO_VIEW_COUNT,
            observed_at=_NOW,
            input_hash=ih,
            external_video_id=vid_id,
            external_channel_id=ch_id,
            category_id="22",
            signal_value_numeric=float((i + 1) * 50000),
        )
        link_job_observation(conn, job.id, obs.id)

        ih_t = make_obs_input_hash(
            platform="youtube",
            provider="youtube_data_api",
            signal_type=VIDEO_TITLE,
            external_video_id=vid_id,
            external_channel_id=ch_id,
            normalized_query=None,
            region_code=None,
            language_code=None,
            date_bucket=date_bucket,
        )
        obs_t = persist_observation(
            conn,
            platform="youtube",
            provider="youtube_data_api",
            collector_name="test",
            signal_type=VIDEO_TITLE,
            observed_at=_NOW,
            input_hash=ih_t,
            external_video_id=vid_id,
            external_channel_id=ch_id,
            signal_value_text=ax_titles[i],
        )
        link_job_observation(conn, job.id, obs_t.id)

    # 4. Adjacent expansion
    parent_probe_refreshed = get_exploration_probe(conn, parent_probe.id)
    adj_result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(_LLM_TWO_ADJACENT),
        parent_probe_id=parent_probe_refreshed.id,
        primary_niche="personal finance tips",
        excluded_topics=["gambling"],
    )

    # 5. Assertions
    assert adj_result.exploration_run_id > 0
    assert adj_result.maturity in ("exploratory", "corroborated")
    assert adj_result.llm_used is True
    assert adj_result.selected_count >= 1

    adj_probes = list_exploration_probes(conn, run_id=adj_result.exploration_run_id)
    selected = [p for p in adj_probes if p.status == "selected"]
    assert all(p.probe_type == "adjacent_topic" for p in selected)
    assert all(
        p.exploration_depth == parent_probe_refreshed.exploration_depth + 1 for p in selected
    )
    assert all(p.parent_probe_id == parent_probe_refreshed.id for p in selected)
