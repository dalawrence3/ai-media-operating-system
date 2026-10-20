"""Phase 7C.1, Part A, §3 — targeted-discovery integration test.

Proves the real chain completes with NO synthetic/manual bridge:

    selected exploration cell
        -> targeted discovery (cold-start LLM call)
        -> probe
        -> dispatch/collection
        -> interpretation
        -> real Opportunity row (via app.intelligence.market.bridge)
        -> opportunity scored
        -> exploration_cell_selections.opportunity_id linked back

Only the two external I/O boundaries are faked (the Anthropic provider and
the YouTube collector) — matching this project's established pattern
(FakeTTSProvider, FakeProvider elsewhere). Every stage in between —
plan_cold_start, dispatch_selected_probes, run_market_interpretation,
sync_channel_market_opportunities, link_selection_to_opportunity — is the
real, unmodified production code.
"""

from __future__ import annotations

import pathlib
import tempfile
from datetime import UTC, datetime
from typing import Any

import pytest

from app.ai.provider import AIRequest, AIResponse
from app.core.database import open_db
from app.intelligence.exploration.cell import ExplorationCell
from app.intelligence.exploration.recovery import DiscoveryOutcome, attempt_recovery_discovery
from app.intelligence.market.dispatcher import dispatch_selected_probes
from app.intelligence.market.models import VIDEO_PUBLISHED_AT, VIDEO_VIEW_COUNT
from app.intelligence.market.repository import (
    link_job_observation,
    persist_observation,
    update_job_status,
)


@pytest.fixture()
def conn():
    with tempfile.TemporaryDirectory() as d:
        db = open_db(pathlib.Path(d) / "test.db")
        yield db
        db.close()


def _now_str() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _fake_response(parsed: Any) -> AIResponse:
    return AIResponse(
        raw_text="{}",
        provider_name="fake_ai",
        model="fake-model",
        input_tokens=10,
        output_tokens=20,
        duration_ms=1,
        retry_count=0,
        parsed=parsed,
    )


class FakeColdStartProvider:
    """Deterministic cold-start provider — same shape as
    test_market_exploration_orchestrator.py's FakeAIProvider, scoped to
    just the cold-start response this test needs."""

    name = "fake_ai"

    def __init__(self, queries: list[str]) -> None:
        self._queries = queries

    def complete(self, request: AIRequest) -> AIResponse:
        from app.intelligence.market.cold_start import SeedExpansionCandidate, SeedExpansionOutput

        parsed = SeedExpansionOutput(
            probes=[
                SeedExpansionCandidate(
                    query=q,
                    market_region_label=q,
                    domain_family="woodworking tools",
                    rationale="test rationale",
                    format_fit_score=0.8,
                    distance_from_current_subjects=0.7,
                    distinctiveness_rationale="distinct from existing",
                )
                for q in self._queries
            ]
        )
        return _fake_response(parsed)


class FakeCollectorWithMaturitySignals:
    """Deterministic collector that persists enough videos, with
    view_count and published_at signals, to clear compute_signal_maturity's
    'directional' gate (5-9 distinct videos) — see
    interpreter.py:compute_signal_maturity and
    interpretation_repository.py:get_videos_for_probe, which only reads
    'video_view_count'/'video_published_at' signal types."""

    def __init__(self, *, video_count: int = 6) -> None:
        self._video_count = video_count
        self.calls: list[dict] = []

    def collect_search_scan(
        self,
        conn,
        job,
        *,
        query,
        region_code,
        language_code,
        published_after,
        order,
        max_results,
        max_pages,
        max_search_calls,
    ):
        self.calls.append({"job_id": job.id, "query": query})
        update_job_status(
            conn,
            job.id,
            status="completed",
            observation_count=self._video_count,
            completed_at=_now_str(),
        )
        for i in range(self._video_count):
            video_id = f"vid-{job.id}-{i}"
            view_obs = persist_observation(
                conn,
                collector_name="FakeCollectorWithMaturitySignals",
                signal_type=VIDEO_VIEW_COUNT,
                observed_at=_now_str(),
                input_hash=f"fake-views-{job.id}-{i}",
                external_video_id=video_id,
                external_channel_id=f"ch-{i}",
                signal_value_numeric=1000.0 + i * 100,
            )
            link_job_observation(conn, job.id, view_obs.id)
            pub_obs = persist_observation(
                conn,
                collector_name="FakeCollectorWithMaturitySignals",
                signal_type=VIDEO_PUBLISHED_AT,
                observed_at=_now_str(),
                input_hash=f"fake-pub-{job.id}-{i}",
                external_video_id=video_id,
                external_channel_id=f"ch-{i}",
                signal_value_text="2026-01-01T00:00:00",
                content_age_days=30.0,
            )
            link_job_observation(conn, job.id, pub_obs.id)

        from dataclasses import dataclass

        @dataclass
        class _Result:
            job_id: int
            status: str = "completed"
            search_calls: int = 1

        return _Result(job_id=job.id)

    def close(self):
        pass


def _seed_channel_and_profile(conn) -> tuple[int, Any]:
    from app.intelligence.repository import create_channel_full

    ch, pv, _strat, _cap = create_channel_full(
        conn,
        channel_name="test_woodworking_channel",
        primary_niche="general knowledge explainers",
        audience_description="curious general audience",
    )
    return ch.id, pv


def _seed_active_scoring_policy(conn, channel_id: int) -> Any:
    from app.intelligence.models import MissingDataPolicy, ScoringPolicy
    from app.intelligence.repository import activate_scoring_policy, create_scoring_policy

    policy = create_scoring_policy(
        conn,
        ScoringPolicy(
            channel_id=channel_id,
            label="test-policy",
            policy_version="1.0.0",
            weight_trend_strength=0.05,
            weight_audience_demand=0.20,
            weight_competition=0.15,
            weight_evergreen_value=0.20,
            weight_audience_fit=0.30,
            weight_content_novelty=0.10,
            missing_trend_strength=MissingDataPolicy.reweight_available,
            missing_audience_demand=MissingDataPolicy.reweight_available,
            missing_competition=MissingDataPolicy.reweight_available,
            missing_evergreen_value=MissingDataPolicy.reweight_available,
            missing_audience_fit=MissingDataPolicy.reweight_available,
            missing_content_novelty=MissingDataPolicy.reweight_available,
        ),
    )
    activate_scoring_policy(conn, policy.id)
    return policy


class TestTargetedDiscoveryProducesARealOpportunityWithNoManualBridge:
    def test_full_chain_completes_with_no_synthetic_opportunity(self, conn):
        channel_id, _profile_version = _seed_channel_and_profile(conn)
        _seed_active_scoring_policy(conn, channel_id)

        fake_provider = FakeColdStartProvider(
            queries=["woodworking joinery techniques", "hand tool restoration"]
        )
        fake_collector = FakeCollectorWithMaturitySignals(video_count=6)

        def discover_fn(conn, *, channel_id: int, cell: ExplorationCell) -> DiscoveryOutcome:
            """The real chain, real functions, fake external I/O only."""
            from app.intelligence.market.cold_start import ExplorationProfile, plan_cold_start
            from app.intelligence.repository import get_active_profile_version

            profile_row = get_active_profile_version(conn, channel_id)
            profile = ExplorationProfile(
                primary_niche=profile_row.primary_niche,
                secondary_niches=[*profile_row.secondary_niches, cell.domain],
                excluded_topics=profile_row.excluded_topics or [],
                audience_description=profile_row.audience_description or "",
            )
            result = plan_cold_start(
                conn,
                profile,
                provider=fake_provider,
                channel_id=channel_id,
                max_probes=2,
                max_depth=1,
                search_budget=4,
            )
            dispatch_selected_probes(
                conn,
                result.exploration_run_id,
                api_key="fake-key",
                collector=fake_collector,
            )
            return DiscoveryOutcome(
                attempted=True,
                probes_created=result.candidate_count,
                opportunities_created=0,  # never synchronous — see module docstring
            )

        recovery_result = attempt_recovery_discovery(
            conn,
            channel_id,
            workspace_id="test-ws",
            consecutive_no_eligible_streak=3,
            discover_fn=discover_fn,
        )
        assert recovery_result.recovery_attempted is True
        assert recovery_result.outcome is not None
        assert recovery_result.outcome.attempted is True
        assert recovery_result.outcome.probes_created == 2
        chosen_cell = recovery_result.cell
        assert chosen_cell is not None

        # ── The real market-refresh cycle: interpretation + bridge sync ────
        from app.intelligence.market.refresh_service import run_market_refresh_cycle

        before_opp_ids = {r[0] for r in conn.execute("SELECT id FROM opportunities").fetchall()}
        refresh_result = run_market_refresh_cycle(
            conn,
            channel_id=channel_id,
            workspace_id="test-ws",
            sync_min_maturity="directional",
        )
        assert refresh_result.sync_skip_reason is None, refresh_result.sync_skip_reason
        after_opp_ids = {r[0] for r in conn.execute("SELECT id FROM opportunities").fetchall()}
        new_opp_ids = after_opp_ids - before_opp_ids
        assert new_opp_ids, f"expected a new real opportunity row; refresh_result={refresh_result}"
        new_opp_id = next(iter(new_opp_ids))

        # ── Opportunity is genuinely scored (not a placeholder row) ────────
        score_row = conn.execute(
            "SELECT composite_score FROM opportunity_scores WHERE opportunity_id = ?",
            (new_opp_id,),
        ).fetchone()
        assert score_row is not None
        assert score_row[0] is not None

        # ── Cell linkage — the real function, not a raw UPDATE ─────────────
        from app.intelligence.exploration.repository import link_selection_to_opportunity

        selection_row = conn.execute(
            "SELECT id FROM exploration_cell_selections ORDER BY id DESC LIMIT 1"
        ).fetchone()
        link_selection_to_opportunity(conn, selection_row[0], new_opp_id)
        conn.commit()

        # ── Prove domain/format/audience_value/designation survive through
        #    the lineage via this one linkage row. ─────────────────────────
        linked = conn.execute(
            "SELECT domain, format, audience_value, designation, opportunity_id "
            "FROM exploration_cell_selections WHERE id = ?",
            (selection_row[0],),
        ).fetchone()
        assert linked["opportunity_id"] == new_opp_id
        assert linked["domain"] == chosen_cell.domain
        assert linked["format"] == chosen_cell.format
        assert linked["audience_value"] == chosen_cell.audience_value
        assert linked["designation"] == chosen_cell.designation
