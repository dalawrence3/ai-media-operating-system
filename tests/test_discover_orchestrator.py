"""Integration tests for the discovery orchestrator (run_discovery)."""

from __future__ import annotations

import sqlite3

import httpx
import pytest

from app.intelligence.models import AdapterName, LifecycleState, RunStatus
from app.intelligence.repository import (
    create_channel_full,
    list_observations,
    list_opportunities,
    list_state_events,
)


def _run(db, channel_id, topics, *, adapter=AdapterName.manual, youtube_client=None):
    from app.intelligence.discovery import run_discovery

    return run_discovery(db, channel_id, adapter, topics, youtube_client=youtube_client)


# ---------------------------------------------------------------------------
# ManualSignalAdapter end-to-end
# ---------------------------------------------------------------------------


def test_run_discovery_creates_discovery_run(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    run = _run(db, channel.id, ["personal finance tips"])
    assert run.id is not None
    assert run.status == RunStatus.completed
    assert run.adapter_name == AdapterName.manual


def test_run_discovery_creates_opportunity(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    run = _run(db, channel.id, ["personal finance tips"])
    assert run.new_opportunity_count == 1
    opps = list_opportunities(db, channel.id)
    assert len(opps) == 1
    assert opps[0].raw_topic == "personal finance tips"
    assert opps[0].current_lifecycle_state == LifecycleState.new


def test_run_discovery_creates_observation(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    _run(db, channel.id, ["personal finance tips"])
    opps = list_opportunities(db, channel.id)
    obs = list_observations(db, opps[0].id)
    assert len(obs) == 1
    assert obs[0].was_deduplicated is False
    assert obs[0].candidate_topic is None


def test_run_discovery_creates_state_event(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    _run(db, channel.id, ["personal finance tips"])
    opps = list_opportunities(db, channel.id)
    events = list_state_events(db, opps[0].id)
    assert len(events) == 1
    assert events[0].from_state is None
    assert events[0].to_state == LifecycleState.new


def test_run_discovery_multiple_topics(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    run = _run(db, channel.id, ["personal finance tips", "budgeting basics", "saving money"])
    assert run.new_opportunity_count == 3
    assert run.dedup_count == 0
    assert list_opportunities(db, channel.id).__len__() == 3


def test_run_discovery_dedup_attaches_to_existing(db: sqlite3.Connection) -> None:
    """Near-duplicate must not create a new opportunity row."""
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    # First run: creates canonical opportunity
    run1 = _run(db, channel.id, ["personal finance tips"])
    assert run1.new_opportunity_count == 1
    opp_id = list_opportunities(db, channel.id)[0].id

    # Second run: near-duplicate above threshold (identical → Jaccard = 1.0)
    run2 = _run(db, channel.id, ["personal finance tips"])
    assert run2.dedup_count == 1
    assert run2.new_opportunity_count == 0

    # Still only one opportunity
    assert len(list_opportunities(db, channel.id)) == 1

    # But two observations on the same opportunity
    obs = list_observations(db, opp_id)
    assert len(obs) == 2
    dedup_obs = next(o for o in obs if o.was_deduplicated)
    assert dedup_obs.candidate_topic == "personal finance tips"
    assert dedup_obs.dedup_similarity_score == pytest.approx(1.0)


def test_run_discovery_below_threshold_creates_new(db: sqlite3.Connection) -> None:
    """A candidate below the dedup threshold must become a new opportunity."""
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    _run(db, channel.id, ["personal finance tips"])
    # "finance tips guide" normalized: {finance, tips, guide}
    # "personal finance tips" normalized: {personal, finance, tips}
    # Jaccard = 2/4 = 0.5 < 0.70 → new
    run2 = _run(db, channel.id, ["finance tips guide"])
    assert run2.new_opportunity_count == 1
    assert len(list_opportunities(db, channel.id)) == 2


def test_run_discovery_failed_candidate_does_not_block_others(
    db: sqlite3.Connection, monkeypatch
) -> None:
    """One failed candidate (forced UNIQUE violation) must not block others; run → partial."""
    import app.intelligence.discovery as disc_mod

    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")

    # Patch find_existing_opportunity to always return None so both identical topics
    # bypass dedup and the second hits the DB UNIQUE constraint.
    monkeypatch.setattr(disc_mod, "find_existing_opportunity", lambda *a, **kw: None)

    # Both topics normalize to "save money" → first creates opportunity, second fails UNIQUE.
    run = _run(db, channel.id, ["Save Money", "Save, Money!"])
    assert run.new_opportunity_count == 1
    assert run.failed_count == 1
    assert run.status == RunStatus.partial
    assert len(list_opportunities(db, channel.id)) == 1


def test_run_discovery_run_status_completed_when_no_failures(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    run = _run(db, channel.id, ["finance tips"])
    assert run.status == RunStatus.completed
    assert run.failed_count == 0


def test_run_discovery_missing_channel_raises(db: sqlite3.Connection) -> None:
    from app.intelligence.discovery import run_discovery

    with pytest.raises(ValueError, match="not found"):
        run_discovery(db, 9999, AdapterName.manual, ["topic"])


def test_run_discovery_no_active_profile_raises(db: sqlite3.Connection) -> None:
    from app.intelligence.discovery import run_discovery
    from app.intelligence.repository import supersede_profile_version

    channel, profile, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    # Supersede the only profile so there is no active one
    supersede_profile_version(db, profile.id)
    db.commit()

    with pytest.raises(ValueError, match="active profile"):
        run_discovery(db, channel.id, AdapterName.manual, ["topic"])


def test_run_discovery_quota_zero_for_manual(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    run = _run(db, channel.id, ["finance"])
    assert run.quota_units_consumed == 0


# ---------------------------------------------------------------------------
# YouTubeDataAPIAdapter end-to-end (stub)
# ---------------------------------------------------------------------------


class _StubTransport(httpx.BaseTransport):
    def __init__(self, routes: dict[str, dict]) -> None:
        self._routes = routes

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for key, body in self._routes.items():
            if key in url:
                return httpx.Response(200, json=body)
        return httpx.Response(404)


_SEARCH_RESP = {"items": [{"id": {"videoId": "v1"}, "snippet": {"title": "Budget Like a Pro"}}]}
_VIDEOS_RESP = {
    "items": [
        {
            "id": "v1",
            "snippet": {"title": "Budget Like a Pro", "publishedAt": "2024-06-01T00:00:00Z"},
            "statistics": {"viewCount": "50000", "likeCount": "2500", "commentCount": "300"},
            "contentDetails": {},
        }
    ]
}


def _stub_youtube_client() -> httpx.Client:
    return httpx.Client(transport=_StubTransport({"search": _SEARCH_RESP, "videos": _VIDEOS_RESP}))


def test_youtube_discovery_creates_opportunity(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    run = _run(
        db,
        channel.id,
        ["budgeting"],
        adapter=AdapterName.youtube_data_api,
        youtube_client=_stub_youtube_client(),
    )
    assert run.new_opportunity_count == 1
    assert run.status == RunStatus.completed


def test_youtube_discovery_evidence_persisted(db: sqlite3.Connection) -> None:
    from app.intelligence.repository import list_evidence

    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    _run(
        db,
        channel.id,
        ["budgeting"],
        adapter=AdapterName.youtube_data_api,
        youtube_client=_stub_youtube_client(),
    )
    opps = list_opportunities(db, channel.id)
    obs = list_observations(db, opps[0].id)
    evidence = list_evidence(db, obs[0].id)
    ev_types = {e.evidence_type for e in evidence}
    assert "view_count" in ev_types
    assert "like_count" in ev_types


def test_youtube_discovery_quota_tracked(db: sqlite3.Connection) -> None:
    channel, *_ = create_channel_full(db, channel_name="X", primary_niche="y")
    run = _run(
        db,
        channel.id,
        ["budgeting"],
        adapter=AdapterName.youtube_data_api,
        youtube_client=_stub_youtube_client(),
    )
    assert run.quota_units_consumed == 101  # 100 search + 1 videos
