"""Unit tests for ManualSignalAdapter and YouTubeDataAPIAdapter."""

from __future__ import annotations

import httpx
import pytest

from app.intelligence.adapters.manual import ManualSignalAdapter
from app.intelligence.adapters.youtube import YouTubeDataAPIAdapter
from app.intelligence.models import AdapterName


def _make_channel_and_profile(db):
    from app.intelligence.repository import create_channel_full

    channel, profile, *_ = create_channel_full(db, channel_name="Test", primary_niche="finance")
    return channel, profile


# ---------------------------------------------------------------------------
# ManualSignalAdapter
# ---------------------------------------------------------------------------


def test_manual_adapter_creates_one_candidate_per_topic(db) -> None:
    adapter = ManualSignalAdapter()
    channel, profile = _make_channel_and_profile(db)
    candidates, quota = adapter.run(["personal finance tips", "budgeting basics"], channel, profile)
    assert len(candidates) == 2
    assert quota == 0


def test_manual_adapter_zero_quota(db) -> None:
    adapter = ManualSignalAdapter()
    channel, profile = _make_channel_and_profile(db)
    _, quota = adapter.run(["topic"], channel, profile)
    assert quota == 0


def test_manual_adapter_sets_adapter_name(db) -> None:
    adapter = ManualSignalAdapter()
    channel, profile = _make_channel_and_profile(db)
    candidates, _ = adapter.run(["finance tips"], channel, profile)
    assert candidates[0].adapter_name == AdapterName.manual


def test_manual_adapter_evidence_type_is_demand_note(db) -> None:
    adapter = ManualSignalAdapter()
    channel, profile = _make_channel_and_profile(db)
    candidates, _ = adapter.run(["finance tips"], channel, profile)
    assert len(candidates[0].evidence) == 1
    assert candidates[0].evidence[0].evidence_type == "manual_demand_note"
    assert candidates[0].evidence[0].evidence_text == "finance tips"


def test_manual_adapter_source_quality_tier_is_variable(db) -> None:
    adapter = ManualSignalAdapter()
    channel, profile = _make_channel_and_profile(db)
    candidates, _ = adapter.run(["topic"], channel, profile)
    assert candidates[0].source_quality_tier == "variable"


def test_manual_adapter_signal_age_is_none(db) -> None:
    adapter = ManualSignalAdapter()
    channel, profile = _make_channel_and_profile(db)
    candidates, _ = adapter.run(["topic"], channel, profile)
    assert candidates[0].signal_age_days is None


def test_manual_adapter_empty_topic_list_returns_empty(db) -> None:
    adapter = ManualSignalAdapter()
    channel, profile = _make_channel_and_profile(db)
    candidates, quota = adapter.run([], channel, profile)
    assert candidates == []
    assert quota == 0


def test_manual_adapter_strips_whitespace_from_topics(db) -> None:
    adapter = ManualSignalAdapter()
    channel, profile = _make_channel_and_profile(db)
    candidates, _ = adapter.run(["  finance tips  "], channel, profile)
    assert candidates[0].raw_topic == "finance tips"


def test_manual_adapter_skips_blank_topics(db) -> None:
    adapter = ManualSignalAdapter()
    channel, profile = _make_channel_and_profile(db)
    candidates, _ = adapter.run(["", "  ", "finance"], channel, profile)
    assert len(candidates) == 1


def test_manual_adapter_skips_stopword_only_topics(db) -> None:
    adapter = ManualSignalAdapter()
    channel, profile = _make_channel_and_profile(db)
    # "how to" normalizes to "" → skipped
    candidates, _ = adapter.run(["how to", "budgeting"], channel, profile)
    assert len(candidates) == 1
    assert candidates[0].raw_topic == "budgeting"


def test_manual_adapter_normalized_topic_stored(db) -> None:
    adapter = ManualSignalAdapter()
    channel, profile = _make_channel_and_profile(db)
    candidates, _ = adapter.run(["How to Save Money!"], channel, profile)
    assert candidates[0].normalized_topic == "save money"


# ---------------------------------------------------------------------------
# YouTubeDataAPIAdapter — stub client
# ---------------------------------------------------------------------------


class _StubTransport(httpx.BaseTransport):
    """Returns fixed responses keyed by URL substring."""

    def __init__(self, routes: dict[str, dict]) -> None:
        self._routes = routes

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for key, body in self._routes.items():
            if key in url:
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": "not found"})


_SEARCH_RESPONSE = {
    "items": [
        {"id": {"videoId": "vid001"}, "snippet": {"title": "How to Budget in 2024"}},
    ]
}

_VIDEOS_RESPONSE = {
    "items": [
        {
            "id": "vid001",
            "snippet": {
                "title": "How to Budget in 2024",
                "publishedAt": "2024-01-15T00:00:00Z",
            },
            "statistics": {
                "viewCount": "250000",
                "likeCount": "12000",
                "commentCount": "800",
            },
            "contentDetails": {},
        }
    ]
}


def _stub_client() -> httpx.Client:
    return httpx.Client(
        transport=_StubTransport({"search": _SEARCH_RESPONSE, "videos": _VIDEOS_RESPONSE})
    )


def test_youtube_adapter_produces_candidates(db) -> None:
    channel, profile = _make_channel_and_profile(db)
    adapter = YouTubeDataAPIAdapter(api_key="fake", client=_stub_client())
    candidates, _ = adapter.run(["budgeting"], channel, profile)
    assert len(candidates) == 1


def test_youtube_adapter_sets_adapter_name(db) -> None:
    channel, profile = _make_channel_and_profile(db)
    adapter = YouTubeDataAPIAdapter(api_key="fake", client=_stub_client())
    candidates, _ = adapter.run(["budgeting"], channel, profile)
    assert candidates[0].adapter_name == AdapterName.youtube_data_api


def test_youtube_adapter_quota_cost(db) -> None:
    channel, profile = _make_channel_and_profile(db)
    adapter = YouTubeDataAPIAdapter(api_key="fake", client=_stub_client())
    _, quota = adapter.run(["budgeting"], channel, profile)
    # 100 (search) + 1 (videos) = 101
    assert quota == 101


def test_youtube_adapter_evidence_types(db) -> None:
    channel, profile = _make_channel_and_profile(db)
    adapter = YouTubeDataAPIAdapter(api_key="fake", client=_stub_client())
    candidates, _ = adapter.run(["budgeting"], channel, profile)
    ev_types = {ev.evidence_type for ev in candidates[0].evidence}
    assert "view_count" in ev_types
    assert "like_count" in ev_types
    assert "comment_count" in ev_types
    assert "top_video_age_days" in ev_types


def test_youtube_adapter_evidence_values(db) -> None:
    channel, profile = _make_channel_and_profile(db)
    adapter = YouTubeDataAPIAdapter(api_key="fake", client=_stub_client())
    candidates, _ = adapter.run(["budgeting"], channel, profile)
    ev = {e.evidence_type: e for e in candidates[0].evidence}
    assert ev["view_count"].evidence_value == pytest.approx(250_000.0)
    assert ev["like_count"].evidence_value == pytest.approx(12_000.0)
    assert ev["comment_count"].evidence_value == pytest.approx(800.0)


def test_youtube_adapter_source_quality_tier_is_high(db) -> None:
    channel, profile = _make_channel_and_profile(db)
    adapter = YouTubeDataAPIAdapter(api_key="fake", client=_stub_client())
    candidates, _ = adapter.run(["budgeting"], channel, profile)
    assert candidates[0].source_quality_tier == "high"


def test_youtube_adapter_raw_payload_stored(db) -> None:
    channel, profile = _make_channel_and_profile(db)
    adapter = YouTubeDataAPIAdapter(api_key="fake", client=_stub_client())
    candidates, _ = adapter.run(["budgeting"], channel, profile)
    payload = candidates[0].raw_payload
    assert payload.get("id") == "vid001"


def test_youtube_adapter_empty_search_results(db) -> None:
    channel, profile = _make_channel_and_profile(db)
    empty_transport = _StubTransport({"search": {"items": []}, "videos": {"items": []}})
    client = httpx.Client(transport=empty_transport)
    adapter = YouTubeDataAPIAdapter(api_key="fake", client=client)
    candidates, quota = adapter.run(["budgeting"], channel, profile)
    assert candidates == []
    assert quota == 100  # search was called, videos was not


def test_youtube_adapter_respects_max_candidates(db) -> None:
    from app.intelligence.repository import create_channel_full, get_active_profile_version

    channel, *_ = create_channel_full(db, channel_name="Tiny", primary_niche="y")
    # Patch the profile object in-memory (max_candidates_per_run is read from profile by adapter)
    base_profile = get_active_profile_version(db, channel.id)
    assert base_profile is not None
    profile = base_profile.model_copy(update={"max_candidates_per_run": 1})

    # Build a search response with 2 videos
    search = {
        "items": [
            {"id": {"videoId": "v1"}, "snippet": {"title": "Topic A"}},
            {"id": {"videoId": "v2"}, "snippet": {"title": "Topic B"}},
        ]
    }
    videos = {
        "items": [
            {
                "id": "v1",
                "snippet": {"title": "Topic A", "publishedAt": "2024-01-01T00:00:00Z"},
                "statistics": {"viewCount": "1000"},
                "contentDetails": {},
            },
            {
                "id": "v2",
                "snippet": {"title": "Topic B", "publishedAt": "2024-01-01T00:00:00Z"},
                "statistics": {"viewCount": "2000"},
                "contentDetails": {},
            },
        ]
    }
    client = httpx.Client(transport=_StubTransport({"search": search, "videos": videos}))
    adapter = YouTubeDataAPIAdapter(api_key="fake", client=client)
    candidates, _ = adapter.run(["finance"], channel, profile)
    # max_candidates_per_run=1, so maxResults passed to search is 1
    assert len(candidates) <= 1
