"""Phase 13D-C.1 — Semantic Evidence Grounding tests.

Tests A–AA (27 tests).

Validates:
- VIDEO_TITLE persisted from search.list snippet (zero extra quota)
- Title observations: correct video_id, global (no channel_id column), idempotent same-day
- Different-day title → new DB row (history preserved)
- Empty/missing title not persisted
- No description or metadata stored
- No extra API requests for title
- VideoEvidenceItem and ProbeEvidenceSummary semantic fields
- _extract_recurring_terms deterministic helper
- SEMANTICALLY_UNGROUNDED maturity when video_count >= 3 but no titles
- plan_adjacent_expansion aborts cleanly on SEMANTICALLY_UNGROUNDED
- LLM prompt contains real video titles
- LLM candidate citing title evidence_id accepted
- LLM candidate citing non-existent semantic evidence rejected
- query_text not substitute for semantic titles
- Multi-creator semantic corroboration
- No Opportunity, scoring, Phase 12C, or live network mutations
"""

from __future__ import annotations

import json
import pathlib
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import pytest

from app.ai.fake import FakeProvider
from app.core.database import open_db
from app.intelligence.market import models as m
from app.intelligence.market import repository as repo
from app.intelligence.market.adjacent import (
    AdjacencyExpansionMaturity,
    ProbeEvidenceSummary,
    VideoEvidenceItem,
    _build_evidence_summary,
    _extract_recurring_terms,
    classify_expansion_eligibility,
    plan_adjacent_expansion,
)
from app.intelligence.market.collector import (
    YouTubeMarketCollector,
    make_obs_input_hash,
)
from app.intelligence.market.planner_repository import (
    get_exploration_probe,
    list_exploration_probes,
    update_probe_dispatch,
)

_NOW = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
_TODAY = datetime.now(UTC).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Transport / stub infrastructure (mirrors test_market_collector.py)
# ---------------------------------------------------------------------------


@dataclass
class _CapturingTransport(httpx.BaseTransport):
    handler: Callable[[httpx.Request], httpx.Response]
    captured: list[httpx.Request] = field(default_factory=list)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.captured.append(request)
        return self.handler(request)


def _ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body)


def _err(status: int = 403) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": status, "message": "test error"}})


# Search response WITH titles in snippet
_SEARCH_WITH_TITLES = {
    "kind": "youtube#searchListResponse",
    "pageInfo": {"totalResults": 50000, "resultsPerPage": 5},
    "items": [
        {
            "id": {"videoId": "vid001"},
            "snippet": {"channelId": "chan_A", "title": "How to save money fast"},
        },
        {
            "id": {"videoId": "vid002"},
            "snippet": {"channelId": "chan_B", "title": "10 frugal living tips"},
        },
        {
            "id": {"videoId": "vid003"},
            "snippet": {"channelId": "chan_C", "title": "Budget grocery shopping guide"},
        },
    ],
}

# Stats response for enrichment (no title in batchGetStats)
_STATS_RESPONSE = {
    "items": [
        {
            "id": "vid001",
            "statistics": {"viewCount": "500000", "likeCount": "10000", "commentCount": "500"},
            "contentDetails": {"durationMillis": "450000"},
        },
        {
            "id": "vid002",
            "statistics": {"viewCount": "120000", "likeCount": "5000", "commentCount": "200"},
            "contentDetails": {"durationMillis": "300000"},
        },
        {
            "id": "vid003",
            "statistics": {"viewCount": "80000", "likeCount": "3000", "commentCount": "100"},
            "contentDetails": {"durationMillis": "600000"},
        },
    ]
}


def _handler_with_titles(req: httpx.Request) -> httpx.Response:
    url = str(req.url)
    if "/search" in url:
        return _ok(_SEARCH_WITH_TITLES)
    if "/videos" in url:
        return _ok(_STATS_RESPONSE)
    return _err(404)


def _make_collector(handler) -> tuple[YouTubeMarketCollector, _CapturingTransport]:
    transport = _CapturingTransport(handler=handler)
    client = httpx.Client(transport=transport)
    return YouTubeMarketCollector(api_key="test_key", client=client), transport


# ---------------------------------------------------------------------------
# DB / seeding helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    with tempfile.TemporaryDirectory() as d:
        c = open_db(pathlib.Path(d) / "test.db")
        yield c
        c.close()


def _seed_title_obs(
    conn, job_id: int, vid_id: str, title: str, ch_id: str, date_bucket: str = _TODAY
):
    """Persist a VIDEO_TITLE observation for a video and link it to a job."""
    ih = make_obs_input_hash(
        platform="youtube",
        provider="youtube_data_api",
        signal_type=m.VIDEO_TITLE,
        external_video_id=vid_id,
        external_channel_id=ch_id,
        normalized_query=None,
        region_code=None,
        language_code=None,
        date_bucket=date_bucket,
    )
    obs = repo.persist_observation(
        conn,
        platform="youtube",
        provider="youtube_data_api",
        collector_name="test",
        signal_type=m.VIDEO_TITLE,
        observed_at=_NOW,
        input_hash=ih,
        external_video_id=vid_id,
        external_channel_id=ch_id,
        signal_value_text=title,
    )
    repo.link_job_observation(conn, job_id, obs.id)
    return obs


def _seed_view_obs(conn, job_id: int, vid_id: str, view_count: float, ch_id: str):
    """Persist a VIDEO_VIEW_COUNT observation and link to job."""
    ih = make_obs_input_hash(
        platform="youtube",
        provider="youtube_data_api",
        signal_type=m.VIDEO_VIEW_COUNT,
        external_video_id=vid_id,
        external_channel_id=ch_id,
        normalized_query=None,
        region_code=None,
        language_code=None,
        date_bucket=_TODAY,
    )
    obs = repo.persist_observation(
        conn,
        platform="youtube",
        provider="youtube_data_api",
        collector_name="test",
        signal_type=m.VIDEO_VIEW_COUNT,
        observed_at=_NOW,
        input_hash=ih,
        external_video_id=vid_id,
        external_channel_id=ch_id,
        signal_value_numeric=view_count,
    )
    repo.link_job_observation(conn, job_id, obs.id)
    return obs


def _seed_probe_dispatched(conn, *, query: str = "money saving tips"):
    """Create a probe dispatched to a job, with 3 view observations + 3 title observations."""
    from app.intelligence.market.cold_start import ExplorationProfile, plan_cold_start

    profile = ExplorationProfile(
        primary_niche=query,
        excluded_topics=["gambling", "casino games"],
    )
    result = plan_cold_start(conn, profile, provider=FakeProvider(json.dumps({"probes": []})))
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    parent_probe = next(p for p in probes if p.status == "selected")

    job = repo.create_market_collection_job(conn, job_type="search_scan", origin_type="manual")
    update_probe_dispatch(conn, parent_probe.id, dispatched_job_id=job.id, dispatched_at=_NOW)

    videos = [
        ("vid0001", "chan_A", 50000.0, "How to save money fast"),
        ("vid0002", "chan_B", 120000.0, "10 frugal living tips"),
        ("vid0003", "chan_C", 80000.0, "Budget grocery shopping guide"),
    ]
    title_obs_ids = []
    for vid_id, ch_id, vc, title in videos:
        _seed_view_obs(conn, job.id, vid_id, vc, ch_id)
        t_obs = _seed_title_obs(conn, job.id, vid_id, title, ch_id)
        title_obs_ids.append(t_obs.id)

    refreshed = get_exploration_probe(conn, parent_probe.id)
    return refreshed, job.id, title_obs_ids


def _seed_probe_no_titles(conn, *, query: str = "money saving tips"):
    """Create a probe dispatched to a job with view observations but NO title observations."""
    from app.intelligence.market.cold_start import ExplorationProfile, plan_cold_start

    profile = ExplorationProfile(
        primary_niche=query,
        excluded_topics=["gambling", "casino games"],
    )
    result = plan_cold_start(conn, profile, provider=FakeProvider(json.dumps({"probes": []})))
    probes = list_exploration_probes(conn, run_id=result.exploration_run_id)
    parent_probe = next(p for p in probes if p.status == "selected")

    job = repo.create_market_collection_job(conn, job_type="search_scan", origin_type="manual")
    update_probe_dispatch(conn, parent_probe.id, dispatched_job_id=job.id, dispatched_at=_NOW)

    for i, (ch_id, vc) in enumerate(
        [("chan_A", 50000.0), ("chan_B", 120000.0), ("chan_C", 80000.0)]
    ):
        vid_id = f"vid{i:04d}"
        _seed_view_obs(conn, job.id, vid_id, vc, ch_id)

    refreshed = get_exploration_probe(conn, parent_probe.id)
    return refreshed, job.id


# ---------------------------------------------------------------------------
# A — VIDEO_TITLE persisted from search.list snippet
# ---------------------------------------------------------------------------


def test_a_video_title_persisted_from_search_snippet(conn):
    """A: collect_search_scan persists VIDEO_TITLE from search.list snippet.title."""
    coll, _ = _make_collector(_handler_with_titles)
    job = repo.create_market_collection_job(conn, job_type="search_scan", origin_type="manual")
    coll.collect_search_scan(conn, job, query="money saving tips")

    title_obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_TITLE)
    assert title_obs, "Expected VIDEO_TITLE observation for vid001"
    assert title_obs[0].signal_value_text == "How to save money fast"


# ---------------------------------------------------------------------------
# B — title associated with correct external_video_id
# ---------------------------------------------------------------------------


def test_b_title_linked_to_correct_video_id(conn):
    """B: Each VIDEO_TITLE observation is keyed to its correct external_video_id."""
    coll, _ = _make_collector(_handler_with_titles)
    job = repo.create_market_collection_job(conn, job_type="search_scan", origin_type="manual")
    coll.collect_search_scan(conn, job, query="money saving tips")

    for vid_id, expected_title in [
        ("vid001", "How to save money fast"),
        ("vid002", "10 frugal living tips"),
        ("vid003", "Budget grocery shopping guide"),
    ]:
        obs = repo.list_observations_for_video(conn, vid_id, signal_type=m.VIDEO_TITLE)
        assert obs, f"No VIDEO_TITLE for {vid_id}"
        assert obs[0].signal_value_text == expected_title, (
            f"{vid_id}: got '{obs[0].signal_value_text}', expected '{expected_title}'"
        )
        assert obs[0].external_video_id == vid_id


# ---------------------------------------------------------------------------
# C — title observation is global (market_intelligence_observations has no channel_id)
# ---------------------------------------------------------------------------


def test_c_title_observation_is_global(conn):
    """C: VIDEO_TITLE observations live in market_intelligence_observations
    which has no channel_id column."""
    coll, _ = _make_collector(_handler_with_titles)
    job = repo.create_market_collection_job(conn, job_type="search_scan", origin_type="manual")
    coll.collect_search_scan(conn, job, query="money saving tips")

    # The table has no channel_id column — verify schema does not include it
    cols = [
        r[1] for r in conn.execute("PRAGMA table_info(market_intelligence_observations)").fetchall()
    ]
    assert "channel_id" not in cols, "market_intelligence_observations must not have channel_id"

    # Observation is linked to a video + channel but NOT scoped by workspace channel_id
    obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_TITLE)
    assert obs[0].external_channel_id == "chan_A"  # creator-level channel ID
    assert obs[0].signal_value_text is not None


# ---------------------------------------------------------------------------
# D — same title observation idempotent (INSERT OR IGNORE)
# ---------------------------------------------------------------------------


def test_d_same_day_title_is_idempotent(conn):
    """D: Two collect_search_scan calls on the same day produce only one
    VIDEO_TITLE row per video."""
    coll, _ = _make_collector(_handler_with_titles)
    job1 = repo.create_market_collection_job(conn, job_type="search_scan", origin_type="manual")
    job2 = repo.create_market_collection_job(conn, job_type="search_scan", origin_type="manual")
    coll.collect_search_scan(conn, job1, query="money saving tips")
    coll.collect_search_scan(conn, job2, query="money saving tips")

    title_obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_TITLE)
    assert len(title_obs) == 1, (
        f"Expected 1 VIDEO_TITLE row for same-day same-video, got {len(title_obs)}"
    )


# ---------------------------------------------------------------------------
# E — changed title on different day creates new row
# ---------------------------------------------------------------------------


def test_e_different_day_creates_new_title_row(conn):
    """E: Two title observations for the same video on different date_buckets → 2 rows."""
    job = repo.create_market_collection_job(conn, job_type="search_scan", origin_type="manual")
    # Manually seed two title observations on different days
    _seed_title_obs(conn, job.id, "vid001", "Original title", "chan_A", date_bucket="2026-01-01")
    _seed_title_obs(conn, job.id, "vid001", "Updated title", "chan_A", date_bucket="2026-01-02")

    title_obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_TITLE)
    assert len(title_obs) == 2, (
        f"Expected 2 VIDEO_TITLE rows for different days, got {len(title_obs)}"
    )
    texts = {o.signal_value_text for o in title_obs}
    assert "Original title" in texts
    assert "Updated title" in texts


# ---------------------------------------------------------------------------
# F — missing/empty title not persisted
# ---------------------------------------------------------------------------


def test_f_empty_title_not_persisted(conn):
    """F: A search item with empty/missing snippet.title does not produce a VIDEO_TITLE row."""
    search_no_title = {
        "kind": "youtube#searchListResponse",
        "pageInfo": {"totalResults": 1000, "resultsPerPage": 5},
        "items": [
            {"id": {"videoId": "vid999"}, "snippet": {"channelId": "chan_Z", "title": ""}},
            {"id": {"videoId": "vid998"}, "snippet": {"channelId": "chan_Y"}},  # no title key
        ],
    }
    stats_none = {"items": []}

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/search" in url:
            return _ok(search_no_title)
        if "/videos" in url:
            return _ok(stats_none)
        return _err(404)

    coll, _ = _make_collector(handler)
    job = repo.create_market_collection_job(conn, job_type="search_scan", origin_type="manual")
    coll.collect_search_scan(conn, job, query="test query")

    for vid_id in ["vid999", "vid998"]:
        title_obs = repo.list_observations_for_video(conn, vid_id, signal_type=m.VIDEO_TITLE)
        assert not title_obs, f"Expected no VIDEO_TITLE for {vid_id} with empty/missing title"


# ---------------------------------------------------------------------------
# G — no full description stored
# ---------------------------------------------------------------------------


def test_g_description_not_stored(conn):
    """G: VIDEO_TITLE observation stores only the title, not a description or body text."""
    coll, _ = _make_collector(_handler_with_titles)
    job = repo.create_market_collection_job(conn, job_type="search_scan", origin_type="manual")
    coll.collect_search_scan(conn, job, query="money saving tips")

    title_obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_TITLE)
    assert title_obs
    # Title is a short string — not a paragraph-length description
    assert len(title_obs[0].signal_value_text or "") <= 200
    # No signal_value_numeric for a title
    assert title_obs[0].signal_value_numeric is None


# ---------------------------------------------------------------------------
# H — no extra API request for title (uses existing search snippet)
# ---------------------------------------------------------------------------


def test_h_no_extra_api_request_for_title(conn):
    """H: VIDEO_TITLE persistence adds zero extra HTTP requests — title comes
    from search.list snippet."""
    coll, transport = _make_collector(_handler_with_titles)
    job = repo.create_market_collection_job(conn, job_type="search_scan", origin_type="manual")
    coll.collect_search_scan(conn, job, query="money saving tips")

    title_obs = repo.list_observations_for_video(conn, "vid001", signal_type=m.VIDEO_TITLE)
    assert title_obs, "Expected VIDEO_TITLE to be persisted"

    search_calls = [r for r in transport.captured if "/search" in str(r.url)]
    stats_calls = [r for r in transport.captured if "/videos" in str(r.url)]
    # 1 search call + 1 stats call — no additional calls for titles
    assert len(search_calls) == 1
    assert len(stats_calls) == 1


# ---------------------------------------------------------------------------
# I — adjacent evidence contains real video titles
# ---------------------------------------------------------------------------


def test_i_adjacent_evidence_contains_real_titles(conn):
    """I: _build_evidence_summary returns VideoEvidenceItem objects with real titles from DB."""
    probe, job_id, title_obs_ids = _seed_probe_dispatched(conn)

    summary = _build_evidence_summary(
        conn,
        probe_id=probe.id,
        query_text=probe.query_text,
        normalized_query=probe.normalized_query,
        exploration_depth=probe.exploration_depth,
        dispatched_job_id=job_id,
    )

    assert summary.has_semantic_evidence is True
    assert len(summary.video_evidence) == 3

    titles = {item.title for item in summary.video_evidence}
    assert "How to save money fast" in titles
    assert "10 frugal living tips" in titles
    assert "Budget grocery shopping guide" in titles


# ---------------------------------------------------------------------------
# J — adjacent evidence contains correct external_video_id
# ---------------------------------------------------------------------------


def test_j_adjacent_evidence_correct_video_ids(conn):
    """J: Each VideoEvidenceItem has the correct external_video_id."""
    probe, job_id, _ = _seed_probe_dispatched(conn)
    summary = _build_evidence_summary(
        conn,
        probe_id=probe.id,
        query_text=probe.query_text,
        normalized_query=probe.normalized_query,
        exploration_depth=probe.exploration_depth,
        dispatched_job_id=job_id,
    )

    video_ids = {item.external_video_id for item in summary.video_evidence}
    assert "vid0001" in video_ids
    assert "vid0002" in video_ids
    assert "vid0003" in video_ids


# ---------------------------------------------------------------------------
# K — adjacent evidence contains correct creator IDs
# ---------------------------------------------------------------------------


def test_k_adjacent_evidence_correct_creator_ids(conn):
    """K: Each VideoEvidenceItem has the correct external_channel_id."""
    probe, job_id, _ = _seed_probe_dispatched(conn)
    summary = _build_evidence_summary(
        conn,
        probe_id=probe.id,
        query_text=probe.query_text,
        normalized_query=probe.normalized_query,
        exploration_depth=probe.exploration_depth,
        dispatched_job_id=job_id,
    )

    channel_ids = {item.external_channel_id for item in summary.video_evidence}
    assert "chan_A" in channel_ids
    assert "chan_B" in channel_ids
    assert "chan_C" in channel_ids


# ---------------------------------------------------------------------------
# L — recurring terms derived deterministically
# ---------------------------------------------------------------------------


def test_l_recurring_terms_deterministic():
    """L: _extract_recurring_terms produces consistent, stopword-free output."""
    titles = [
        "How to save money fast",
        "10 ways to save more money",
        "Saving money for beginners",
        "Budget tips for saving",
    ]
    result_1 = _extract_recurring_terms(titles)
    result_2 = _extract_recurring_terms(titles)

    # Must be deterministic
    assert result_1 == result_2

    # "save"/"saving" and "money" appear in 3+ titles — should appear in recurring terms
    assert any(t in result_1 for t in ["save", "saving", "money", "budget"])

    # Stopwords excluded
    for stopword in ["to", "how", "for", "the", "and"]:
        assert stopword not in result_1, f"Stopword '{stopword}' found in recurring terms"

    # Max 20 terms
    assert len(result_1) <= 20


def test_l2_recurring_terms_min_doc_freq():
    """L2: Terms appearing in only 1 title are excluded."""
    titles = ["unique cryptic xylophone", "money tips fast", "save money today"]
    result = _extract_recurring_terms(titles, min_doc_freq=2)

    assert "xylophone" not in result  # appears only in 1 title
    assert "cryptic" not in result  # appears only in 1 title
    # "money" appears in 2 titles — should be present
    assert "money" in result


# ---------------------------------------------------------------------------
# M — evidence prompt contains actual semantic evidence
# ---------------------------------------------------------------------------


def test_m_llm_prompt_contains_video_titles(conn):
    """M: plan_adjacent_expansion passes real video titles to the LLM prompt."""
    probe, job_id, title_obs_ids = _seed_probe_dispatched(conn)

    captured_prompts: list[str] = []

    class _CapturingProvider:
        name = "capturing"

        def complete(self, request):
            captured_prompts.append(request.user)
            from app.ai.provider import AIResponse

            return AIResponse(
                content=json.dumps({"probes": []}),
                model="fake-model",
                parsed=None,
            )

    plan_adjacent_expansion(
        conn,
        provider=_CapturingProvider(),
        parent_probe_id=probe.id,
        excluded_topics=["gambling"],
        primary_niche="personal finance",
    )

    assert captured_prompts, "No LLM prompt was sent"
    prompt_text = captured_prompts[0]
    assert "How to save money fast" in prompt_text
    assert "10 frugal living tips" in prompt_text
    assert "Budget grocery shopping guide" in prompt_text


# ---------------------------------------------------------------------------
# N — LLM candidate supported by title evidence accepted
# ---------------------------------------------------------------------------


def test_n_candidate_citing_title_evidence_accepted(conn):
    """N: A candidate that cites a real obs-{id} from title observations is accepted."""
    probe, job_id, title_obs_ids = _seed_probe_dispatched(conn)
    valid_ref = f"obs-{title_obs_ids[0]}"

    llm_response = json.dumps(
        {
            "probes": [
                {
                    "query": "frugal grocery shopping",
                    "market_region_label": "Grocery Savings",
                    "rationale": "Grocery budgeting is a natural adjacent to savings.",
                    "evidence_refs": [valid_ref],
                    "relation_to_parent": "Extends savings into specific spending category.",
                    "estimated_niche_fit": 0.85,
                    "distinctiveness_rationale": "Focused on one recurring expense.",
                }
            ]
        }
    )

    result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(llm_response),
        parent_probe_id=probe.id,
        excluded_topics=["gambling"],
        primary_niche="personal finance",
    )

    assert result.selected_count >= 1, "Expected candidate citing valid title obs to be selected"


# ---------------------------------------------------------------------------
# O — LLM candidate citing nonexistent semantic evidence rejected
# ---------------------------------------------------------------------------


def test_o_candidate_citing_nonexistent_evidence_rejected(conn):
    """O: A candidate that cites an invented obs-id not in the supplied set is rejected."""
    probe, job_id, _ = _seed_probe_dispatched(conn)

    llm_response = json.dumps(
        {
            "probes": [
                {
                    "query": "side hustle ideas",
                    "market_region_label": "Side Hustles",
                    "rationale": "Income supplement for savings.",
                    "evidence_refs": ["obs-999999"],  # does not exist
                    "relation_to_parent": "Earning more to save more.",
                    "estimated_niche_fit": 0.70,
                    "distinctiveness_rationale": "Income vs expense angle.",
                }
            ]
        }
    )

    result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(llm_response),
        parent_probe_id=probe.id,
        excluded_topics=["gambling"],
        primary_niche="personal finance",
    )

    assert result.rejected_count >= 1
    assert result.selected_count == 0


# ---------------------------------------------------------------------------
# P — SEMANTICALLY_UNGROUNDED when video_count >= 3 but no semantic titles
# ---------------------------------------------------------------------------


def test_p_semantically_ungrounded_without_titles():
    """P: classify_expansion_eligibility returns SEMANTICALLY_UNGROUNDED when
    video_count >= 3 but no titles."""
    summary = ProbeEvidenceSummary(
        probe_id=1,
        query_text="test query",
        normalized_query="test query",
        exploration_depth=1,
        video_count=5,
        avg_view_count=50000.0,
        total_result_estimate=20000.0,
        top_categories=["22"],
        creator_count=3,
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
    assert (
        classify_expansion_eligibility(summary)
        == AdjacencyExpansionMaturity.SEMANTICALLY_UNGROUNDED
    )


# ---------------------------------------------------------------------------
# Q — missing semantic evidence explicitly classified
# ---------------------------------------------------------------------------


def test_q_semantically_ungrounded_classification_explicit(conn):
    """Q: plan_adjacent_expansion returns run_id=-1 and
    maturity=semantically_ungrounded for no-title probe."""
    probe, job_id = _seed_probe_no_titles(conn)

    result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(json.dumps({"probes": []})),
        parent_probe_id=probe.id,
    )

    assert result.exploration_run_id == -1
    assert result.maturity == AdjacencyExpansionMaturity.SEMANTICALLY_UNGROUNDED.value
    assert result.llm_used is False
    assert result.diagnostics["has_semantic_evidence"] is False


# ---------------------------------------------------------------------------
# R — query_text is not treated as substitute for video semantic evidence
# ---------------------------------------------------------------------------


def test_r_query_text_not_substitute_for_titles(conn):
    """R: A probe with 3+ views but no title observations is
    SEMANTICALLY_UNGROUNDED, not EXPLORATORY."""
    probe, job_id = _seed_probe_no_titles(conn)

    result = plan_adjacent_expansion(
        conn,
        provider=FakeProvider(json.dumps({"probes": []})),
        parent_probe_id=probe.id,
    )

    # Must NOT fall through to EXPLORATORY even though it has query_text
    assert result.maturity != AdjacencyExpansionMaturity.EXPLORATORY.value
    assert result.maturity == AdjacencyExpansionMaturity.SEMANTICALLY_UNGROUNDED.value


# ---------------------------------------------------------------------------
# S — multi-video semantic corroboration supported
# ---------------------------------------------------------------------------


def test_s_multi_video_semantic_corroboration(conn):
    """S: ProbeEvidenceSummary lists all seeded videos when multiple have titles."""
    probe, job_id, _ = _seed_probe_dispatched(conn)

    summary = _build_evidence_summary(
        conn,
        probe_id=probe.id,
        query_text=probe.query_text,
        normalized_query=probe.normalized_query,
        exploration_depth=probe.exploration_depth,
        dispatched_job_id=job_id,
    )

    assert len(summary.video_evidence) == 3
    assert summary.has_semantic_evidence is True


# ---------------------------------------------------------------------------
# T — same concept across multiple creators visible in evidence
# ---------------------------------------------------------------------------


def test_t_multiple_creator_evidence_visible(conn):
    """T: VideoEvidenceItem objects represent multiple distinct creators."""
    probe, job_id, _ = _seed_probe_dispatched(conn)

    summary = _build_evidence_summary(
        conn,
        probe_id=probe.id,
        query_text=probe.query_text,
        normalized_query=probe.normalized_query,
        exploration_depth=probe.exploration_depth,
        dispatched_job_id=job_id,
    )

    channel_ids = {item.external_channel_id for item in summary.video_evidence}
    assert len(channel_ids) >= 3, "Expected evidence from at least 3 distinct creators"


# ---------------------------------------------------------------------------
# U — existing velocity normalization intact
# ---------------------------------------------------------------------------


def test_u_velocity_normalization_intact():
    """U: _normalize_velocity_trigger still works correctly — not affected by 13D-C.1."""
    from app.intelligence.market.adjacent import _normalize_velocity_trigger

    assert _normalize_velocity_trigger(None) == 0.0
    assert _normalize_velocity_trigger(0.0) == 0.0
    assert _normalize_velocity_trigger(10000.0) == 1.0
    assert _normalize_velocity_trigger(5000.0) == 0.5
    assert _normalize_velocity_trigger(20000.0) == 1.0  # capped


# ---------------------------------------------------------------------------
# V — existing evidence-strength logic intact
# ---------------------------------------------------------------------------


def test_v_evidence_strength_logic_intact():
    """V: _normalize_evidence_strength still returns expected values."""
    from app.intelligence.market.adjacent import _normalize_evidence_strength

    assert _normalize_evidence_strength(0, None) == 0.0
    strength_low = _normalize_evidence_strength(5, 10000.0)
    strength_high = _normalize_evidence_strength(25, 100000.0)
    assert strength_high > strength_low
    assert 0.0 <= strength_low <= 1.0
    assert 0.0 <= strength_high <= 1.0


# ---------------------------------------------------------------------------
# W — no Opportunity creation
# ---------------------------------------------------------------------------


def test_w_no_opportunity_creation(conn):
    """W: plan_adjacent_expansion creates no rows in any opportunity table."""
    probe, job_id, _ = _seed_probe_dispatched(conn)

    plan_adjacent_expansion(
        conn,
        provider=FakeProvider(json.dumps({"probes": []})),
        parent_probe_id=probe.id,
    )

    # Opportunities are not part of Phase 13D
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%opportunit%'"
        ).fetchall()
    ]
    for tbl in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        assert count == 0, f"Unexpected row in {tbl}"


# ---------------------------------------------------------------------------
# X — no scoring changes
# ---------------------------------------------------------------------------


def test_x_no_scoring_changes(conn):
    """X: plan_adjacent_expansion does not mutate learning_recommendations or related tables."""
    probe, job_id, _ = _seed_probe_dispatched(conn)

    plan_adjacent_expansion(
        conn,
        provider=FakeProvider(json.dumps({"probes": []})),
        parent_probe_id=probe.id,
    )

    # Verify recommendations table untouched
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%recommendation%'"
        ).fetchall()
    ]
    for tbl in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        assert count == 0, f"Unexpected row in {tbl}"


# ---------------------------------------------------------------------------
# Y — no Phase 12C mutation
# ---------------------------------------------------------------------------


def test_y_no_cross_publication_mutation(conn):
    """Y: plan_adjacent_expansion does not touch cross_publication tables."""
    probe, job_id, _ = _seed_probe_dispatched(conn)

    plan_adjacent_expansion(
        conn,
        provider=FakeProvider(json.dumps({"probes": []})),
        parent_probe_id=probe.id,
    )

    cross_pub_tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%cross_publication%'"
        ).fetchall()
    ]
    for tbl in cross_pub_tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        assert count == 0, f"Unexpected row in {tbl}"


# ---------------------------------------------------------------------------
# Z — no live network calls
# ---------------------------------------------------------------------------


def test_z_no_live_network_calls(conn):
    """Z: plan_adjacent_expansion makes no YouTube API calls via FakeProvider."""
    import socket

    probe, job_id, _ = _seed_probe_dispatched(conn)

    # Monkey-patch socket.getaddrinfo to detect any real network lookups
    original_getaddrinfo = socket.getaddrinfo
    network_calls: list[str] = []

    def patched_getaddrinfo(host, *args, **kwargs):
        if "google" in str(host).lower() or "youtube" in str(host).lower():
            network_calls.append(host)
        return original_getaddrinfo(host, *args, **kwargs)

    socket.getaddrinfo = patched_getaddrinfo
    try:
        plan_adjacent_expansion(
            conn,
            provider=FakeProvider(json.dumps({"probes": []})),
            parent_probe_id=probe.id,
        )
    finally:
        socket.getaddrinfo = original_getaddrinfo

    assert not network_calls, f"Unexpected network calls to: {network_calls}"


# ---------------------------------------------------------------------------
# AA — existing Phase 13A/B/C/D tests remain green (import smoke test)
# ---------------------------------------------------------------------------


def test_aa_core_imports_intact():
    """AA: All core Phase 13A/B/C/D modules import cleanly after 13D-C.1 changes."""
    from app.intelligence.market import (
        adjacent,  # noqa: F401
        cold_start,  # noqa: F401
        collector,  # noqa: F401
        models,  # noqa: F401
        planner_prompts,  # noqa: F401
        planner_repository,  # noqa: F401
        repository,  # noqa: F401
        velocity,  # noqa: F401
    )
    from app.intelligence.market.adjacent import (
        AdjacencyExpansionMaturity,
        _extract_recurring_terms,
    )
    from app.intelligence.market.models import VIDEO_TITLE

    assert VIDEO_TITLE == "video_title"
    assert AdjacencyExpansionMaturity.SEMANTICALLY_UNGROUNDED.value == "semantically_ungrounded"
    assert VideoEvidenceItem is not None
    assert callable(_extract_recurring_terms)
