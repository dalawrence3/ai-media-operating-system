"""Phase 13D-E — Selected Probe Dispatch / Reuse Orchestration tests.

67 tests A through BO covering:
  - Probe eligibility gating
  - Idempotency (already-dispatched probes)
  - Evidence reuse (freshness, compatibility, minimum observations)
  - Budget enforcement
  - Dry-run mode
  - Failed execution semantics (probe stays SELECTED)
  - Policy snapshot merging into run.policy_json
  - dispatch_probe (single-probe path)
  - Dispatch ordering (priority_score DESC, probe_id ASC)
  - Cross-channel reuse
  - Origin type mapping (ADJACENT_TOPIC vs EXPLORATION_PLANNER)
  - ProbeDispatchResult and ProbeDispatchDiagnostic contracts

No real YouTube API calls. No LLM calls.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.database import open_db
from app.intelligence.market.dispatcher import (
    DISPATCH_EXECUTION_PARAMS_KEY,
    DISPATCH_POLICY_VERSION,
    DISPATCH_REUSE_MAX_AGE_HOURS,
    DispatchAction,
    ProbeDispatchDiagnostic,
    ProbeDispatchResult,
    _has_required_reuse_observations,
    _origin_type_for_probe,
    build_dispatch_policy_snapshot,
    dispatch_probe,
    dispatch_selected_probes,
    find_reusable_job,
)
from app.intelligence.market.models import SEARCH_RESULT_RANK, MarketJobOriginType
from app.intelligence.market.planner_models import (
    CollectionPolicy,
    ExplorationProbeType,
)
from app.intelligence.market.planner_repository import (
    create_exploration_probe,
    create_exploration_run,
    get_exploration_probe,
    get_exploration_run,
    list_selected_probes_for_dispatch,
    record_probe_attempted_dispatch,
    update_probe_status,
)
from app.intelligence.market.repository import (
    create_market_collection_job,
    get_market_collection_job,
    link_job_observation,
    persist_observation,
    update_job_status,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    with tempfile.TemporaryDirectory() as d:
        db = open_db(pathlib.Path(d) / "test.db")
        yield db
        db.close()


def _make_run(conn, *, channel_id=None, workspace_id=None):
    return create_exploration_run(
        conn,
        channel_id=channel_id,
        workspace_id=workspace_id,
        planner_version="v1",
        max_depth=3,
        max_probes=10,
        search_budget=20,
    )


def _make_probe(
    conn,
    run,
    *,
    query="python tutorials",
    status="candidate",
    probe_type="market_region",
    priority_score=0.75,
    region_code="US",
    language_code="en",
    max_pages=1,
    max_results=25,
    order="relevance",
    published_after=None,
    exploration_depth=0,
    parent_job_id=None,
):
    policy = CollectionPolicy(
        max_pages=max_pages,
        max_results=max_results,
        order=order,
        published_after=published_after,
    )
    probe = create_exploration_probe(
        conn,
        run_id=run.id,
        query_text=query,
        normalized_query=query.lower().strip(),
        probe_type=probe_type,
        channel_id=None,
        workspace_id=None,
        region_code=region_code,
        language_code=language_code,
        collection_policy=policy,
        exploration_depth=exploration_depth,
        parent_job_id=parent_job_id,
    )
    if status != "candidate":
        probe = update_probe_status(
            conn,
            probe.id,
            status=status,
            decided_at="2026-01-01T00:00:00",
            priority_score=priority_score,
        )
    else:
        probe = update_probe_status(
            conn,
            probe.id,
            status="candidate",
            priority_score=priority_score,
        )
    return probe


def _seed_completed_job(
    conn,
    query: str,
    *,
    age_hours: float = 1.0,
    max_pages: int = 1,
    max_results: int = 25,
    region: str | None = "US",
    language: str | None = "en",
    order: str = "relevance",
    published_after: str | None = None,
    status: str = "completed",
    seed_observations: bool = True,
    channel_id: int | None = None,
    workspace_id: str | None = None,
) -> Any:
    """Create a completed/partial search_scan job for reuse matching tests."""
    exec_params = {
        "normalized_query": query.lower().strip(),
        "region_code": region,
        "language_code": language,
        "order": order,
        "max_pages": max_pages,
        "max_results": max_results,
        "published_after": published_after,
    }
    quota_snapshot = {
        DISPATCH_EXECUTION_PARAMS_KEY: exec_params,
        "dispatch_policy_version": DISPATCH_POLICY_VERSION,
    }
    job = create_market_collection_job(
        conn,
        job_type="search_scan",
        origin_type="exploration_planner",
        channel_id=channel_id,
        workspace_id=workspace_id,
        quota_policy_snapshot=quota_snapshot,
    )
    completed_at = (datetime.now(UTC) - timedelta(hours=age_hours)).strftime("%Y-%m-%dT%H:%M:%S")
    update_job_status(
        conn,
        job.id,
        status=status,
        observation_count=5 if seed_observations else 0,
        completed_at=completed_at,
    )
    if seed_observations:
        obs_id = persist_observation(
            conn,
            platform="youtube",
            provider="youtube_data_api",
            collector_name="YouTubeMarketCollector",
            signal_type=SEARCH_RESULT_RANK,
            observed_at="2026-01-01T00:00:00",
            input_hash=f"hash-{job.id}",
            external_video_id=f"vid-{job.id}",
            signal_value_text="1",
        )
        link_job_observation(conn, job.id, obs_id.id if hasattr(obs_id, "id") else obs_id)
    # Reload the job so completed_at is populated.
    return get_market_collection_job(conn, job.id)


@dataclass
class FakeCollectionResult:
    job_id: int
    status: str = "completed"
    observations_new: int = 5
    observations_reused: int = 0
    search_calls: int = 1
    enrichment_calls: int = 5
    partial_failures: list = field(default_factory=list)


class FakeCollector:
    """Test double for YouTubeMarketCollector — no HTTP calls."""

    def __init__(self, *, status="completed", search_calls=1, fail=False):
        self.status = status
        self.search_calls = search_calls
        self.fail = fail
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
    ) -> FakeCollectionResult:
        self.calls.append(
            dict(
                job_id=job.id,
                query=query,
                region_code=region_code,
                language_code=language_code,
            )
        )
        if self.fail:
            result_status = "failed"
        else:
            result_status = self.status

        if result_status != "failed":
            obs_id = persist_observation(
                conn,
                platform="youtube",
                provider="youtube_data_api",
                collector_name="FakeCollector",
                signal_type=SEARCH_RESULT_RANK,
                observed_at="2026-01-01T00:00:00",
                input_hash=f"fake-hash-{job.id}",
                external_video_id=f"fake-vid-{job.id}",
                signal_value_text="1",
            )
            obs_real_id = obs_id.id if hasattr(obs_id, "id") else obs_id
            link_job_observation(conn, job.id, obs_real_id)
            update_job_status(
                conn,
                job.id,
                status=result_status,
                observation_count=5,
                completed_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
            )
        else:
            update_job_status(conn, job.id, status="failed")

        return FakeCollectionResult(
            job_id=job.id,
            status=result_status,
            search_calls=self.search_calls if result_status != "failed" else self.search_calls,
        )

    def close(self):
        pass


# ---------------------------------------------------------------------------
# A: Candidate probe is not dispatched (only SELECTED probes are eligible)
# ---------------------------------------------------------------------------


def test_a_candidate_probe_not_dispatched(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="candidate")
    result = dispatch_selected_probes(conn, run.id, collector=FakeCollector())
    assert result.selected_considered == 0
    assert result.newly_dispatched == 0


# ---------------------------------------------------------------------------
# B: DEFERRED probe is not dispatched
# ---------------------------------------------------------------------------


def test_b_deferred_probe_not_dispatched(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="deferred")
    result = dispatch_selected_probes(conn, run.id, collector=FakeCollector())
    assert result.selected_considered == 0


# ---------------------------------------------------------------------------
# C: REJECTED probe is not dispatched
# ---------------------------------------------------------------------------


def test_c_rejected_probe_not_dispatched(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="rejected")
    result = dispatch_selected_probes(conn, run.id, collector=FakeCollector())
    assert result.selected_considered == 0


# ---------------------------------------------------------------------------
# D: SELECTED probe dispatches successfully
# ---------------------------------------------------------------------------


def test_d_selected_probe_dispatches(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="python tutorials")
    coll = FakeCollector()
    result = dispatch_selected_probes(conn, run.id, collector=coll)
    assert result.newly_dispatched == 1
    assert probe.id in result.dispatched_probe_ids
    refreshed = get_exploration_probe(conn, probe.id)
    assert refreshed.status == "dispatched"
    assert refreshed.dispatched_job_id is not None
    assert refreshed.dispatched_at is not None


# ---------------------------------------------------------------------------
# E: probe.status transitions to 'dispatched' after successful dispatch
# ---------------------------------------------------------------------------


def test_e_probe_status_becomes_dispatched(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="cooking basics")
    dispatch_selected_probes(conn, run.id, collector=FakeCollector())
    refreshed = get_exploration_probe(conn, probe.id)
    assert refreshed.status == "dispatched"


# ---------------------------------------------------------------------------
# F: dispatched_job_id is set to the created job's id
# ---------------------------------------------------------------------------


def test_f_dispatched_job_id_set(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="guitar chords")
    dispatch_selected_probes(conn, run.id, collector=FakeCollector())
    refreshed = get_exploration_probe(conn, probe.id)
    assert refreshed.dispatched_job_id is not None
    job = get_market_collection_job(conn, refreshed.dispatched_job_id)
    assert job is not None
    assert job.job_type == "search_scan"


# ---------------------------------------------------------------------------
# G: dispatch_selected_probes returns correctly populated ProbeDispatchResult
# ---------------------------------------------------------------------------


def test_g_result_counts(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="math proofs")
    result = dispatch_selected_probes(conn, run.id, collector=FakeCollector())
    assert result.run_id == run.id
    assert result.selected_considered == 1
    assert result.newly_dispatched == 1
    assert result.failed_count == 0
    assert result.policy_version == DISPATCH_POLICY_VERSION
    assert result.dry_run is False


# ---------------------------------------------------------------------------
# H: dispatch_selected_probes orders by priority_score DESC then probe_id ASC
# ---------------------------------------------------------------------------


def test_h_dispatch_ordering(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="low priority", priority_score=0.3)
    _make_probe(conn, run, status="selected", query="high priority", priority_score=0.9)
    coll = FakeCollector()
    dispatch_selected_probes(conn, run.id, collector=coll)
    calls = [c["query"] for c in coll.calls]
    assert calls.index("high priority") < calls.index("low priority")


# ---------------------------------------------------------------------------
# I: probe_id ASC is tie-break when priority_scores are equal
# ---------------------------------------------------------------------------


def test_i_ordering_tiebreak_probe_id(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="first probe", priority_score=0.5)
    _make_probe(conn, run, status="selected", query="second probe", priority_score=0.5)
    coll = FakeCollector()
    dispatch_selected_probes(conn, run.id, collector=coll)
    calls = [c["query"] for c in coll.calls]
    assert calls.index("first probe") < calls.index("second probe")


# ---------------------------------------------------------------------------
# J: Idempotency — already-dispatched probe with successful job is skipped
# ---------------------------------------------------------------------------


def test_j_idempotency_already_dispatched_successful(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="pandas dataframes")
    prior_job = _seed_completed_job(conn, "pandas dataframes")
    # Manually dispatch the probe to a successful job.
    from app.intelligence.market.planner_repository import update_probe_dispatch

    update_probe_dispatch(
        conn, probe.id, dispatched_job_id=prior_job.id, dispatched_at="2026-01-01T00:00:00"
    )
    coll = FakeCollector()
    result = dispatch_selected_probes(conn, run.id, collector=coll)
    # Already-dispatched probes are not returned by list_selected_probes_for_dispatch
    assert result.selected_considered == 0
    assert len(coll.calls) == 0


# ---------------------------------------------------------------------------
# K: dispatch_probe idempotency — probe already dispatched + job completed → skip
# ---------------------------------------------------------------------------


def test_k_dispatch_probe_idempotency(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="numpy arrays")
    prior_job = _seed_completed_job(conn, "numpy arrays")
    from app.intelligence.market.planner_repository import update_probe_dispatch

    update_probe_dispatch(
        conn, probe.id, dispatched_job_id=prior_job.id, dispatched_at="2026-01-01T00:00:00"
    )
    coll = FakeCollector()
    result = dispatch_probe(conn, probe.id, collector=coll)
    # Already-dispatched + successful job: no new dispatch, no new calls
    assert result.diagnostics[0].action == DispatchAction.SKIPPED_DISPATCHED
    assert len(coll.calls) == 0
    # SKIPPED_DISPATCHED probes are not added to dispatched_probe_ids (already counted in prior run)
    assert result.dispatched_probe_ids == []


# ---------------------------------------------------------------------------
# L: Reuse — compatible fresh job is found and probe is linked without collection
# ---------------------------------------------------------------------------


def test_l_reuse_fresh_compatible_job(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="yoga for beginners")
    prior = _seed_completed_job(conn, "yoga for beginners", age_hours=1.0)
    coll = FakeCollector()
    result = dispatch_selected_probes(conn, run.id, collector=coll)
    assert result.reused_count == 1
    assert probe.id in result.reused_probe_ids
    assert len(coll.calls) == 0  # no actual collection
    refreshed = get_exploration_probe(conn, probe.id)
    assert refreshed.status == "dispatched"
    assert refreshed.dispatched_job_id == prior.id


# ---------------------------------------------------------------------------
# M: Reuse — consumed 0 search calls
# ---------------------------------------------------------------------------


def test_m_reuse_zero_search_calls(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="chess openings")
    _seed_completed_job(conn, "chess openings", age_hours=0.5)
    result = dispatch_selected_probes(conn, run.id, collector=FakeCollector())
    assert result.actual_search_calls == 0
    assert result.search_calls_avoided > 0


# ---------------------------------------------------------------------------
# N: Reuse — stale job (> max_age_hours) is not reused
# ---------------------------------------------------------------------------


def test_n_stale_job_not_reused(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="rust programming")
    _seed_completed_job(conn, "rust programming", age_hours=13.0)  # > 12h
    coll = FakeCollector()
    result = dispatch_selected_probes(conn, run.id, collector=coll)
    assert result.reused_count == 0
    assert len(coll.calls) == 1  # new execution


# ---------------------------------------------------------------------------
# O: Reuse — query mismatch prevents reuse
# ---------------------------------------------------------------------------


def test_o_query_mismatch_no_reuse(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="machine learning")
    _seed_completed_job(conn, "deep learning")
    coll = FakeCollector()
    result = dispatch_selected_probes(conn, run.id, collector=coll)
    assert result.reused_count == 0
    assert len(coll.calls) == 1


# ---------------------------------------------------------------------------
# P: Reuse — region mismatch prevents reuse
# ---------------------------------------------------------------------------


def test_p_region_mismatch_no_reuse(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="football tactics", region_code="GB")
    _seed_completed_job(conn, "football tactics", region="US")
    coll = FakeCollector()
    result = dispatch_selected_probes(conn, run.id, collector=coll)
    assert result.reused_count == 0


# ---------------------------------------------------------------------------
# Q: Reuse — language mismatch prevents reuse
# ---------------------------------------------------------------------------


def test_q_language_mismatch_no_reuse(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="cocina española", language_code="es")
    _seed_completed_job(conn, "cocina española", language="en")
    coll = FakeCollector()
    result = dispatch_selected_probes(conn, run.id, collector=coll)
    assert result.reused_count == 0


# ---------------------------------------------------------------------------
# R: Reuse — max_pages too low prevents reuse
# ---------------------------------------------------------------------------


def test_r_max_pages_too_low_no_reuse(conn):
    # Probe needs max_pages=1; seed job exec_params says max_pages=0 (simulated)
    # We test by directly setting the seed job's exec param to a lower value.
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="javascript promises", max_pages=1)
    # Seed a job whose snapshot claims max_pages=0 (below probe requirement of 1).

    from app.intelligence.market.repository import create_market_collection_job, update_job_status

    exec_params = {
        "normalized_query": "javascript promises",
        "region_code": "US",
        "language_code": "en",
        "order": "relevance",
        "max_pages": 0,  # deliberately lower than probe's requirement
        "max_results": 25,
        "published_after": None,
    }
    job = create_market_collection_job(
        conn,
        job_type="search_scan",
        origin_type="exploration_planner",
        quota_policy_snapshot={"execution": exec_params},
    )
    from datetime import UTC, datetime, timedelta

    completed_at = (datetime.now(UTC) - timedelta(hours=1.0)).strftime("%Y-%m-%dT%H:%M:%S")
    update_job_status(
        conn, job.id, status="completed", observation_count=5, completed_at=completed_at
    )
    from app.intelligence.market.repository import link_job_observation, persist_observation

    obs = persist_observation(
        conn,
        platform="youtube",
        provider="youtube_data_api",
        collector_name="test",
        signal_type=SEARCH_RESULT_RANK,
        observed_at="2026-01-01T00:00:00",
        input_hash="hash-r-test",
        external_video_id="vid-r",
        signal_value_text="1",
    )
    link_job_observation(conn, job.id, obs.id)
    coll = FakeCollector()
    result = dispatch_selected_probes(conn, run.id, collector=coll)
    assert result.reused_count == 0


# ---------------------------------------------------------------------------
# S: Reuse — max_results too low prevents reuse
# ---------------------------------------------------------------------------


def test_s_max_results_too_low_no_reuse(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="vue components", max_results=50)
    _seed_completed_job(conn, "vue components", max_results=10)
    coll = FakeCollector()
    result = dispatch_selected_probes(conn, run.id, collector=coll)
    assert result.reused_count == 0


# ---------------------------------------------------------------------------
# T: Reuse — prior job with no observations is not reused
# ---------------------------------------------------------------------------


def test_t_no_observations_job_not_reused(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="react hooks")
    _seed_completed_job(conn, "react hooks", seed_observations=False)
    coll = FakeCollector()
    result = dispatch_selected_probes(conn, run.id, collector=coll)
    assert result.reused_count == 0
    assert len(coll.calls) == 1


# ---------------------------------------------------------------------------
# U: Reuse — FAILED prior job is not reused (even with observations)
# ---------------------------------------------------------------------------


def test_u_failed_job_not_reused(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="kotlin coroutines")
    _seed_completed_job(conn, "kotlin coroutines", status="failed", seed_observations=True)
    coll = FakeCollector()
    result = dispatch_selected_probes(conn, run.id, collector=coll)
    assert result.reused_count == 0


# ---------------------------------------------------------------------------
# V: Reuse — partial job WITH search observations is reused
# ---------------------------------------------------------------------------


def test_v_partial_job_with_observations_reused(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="docker containers")
    _seed_completed_job(conn, "docker containers", status="partial", seed_observations=True)
    result = dispatch_selected_probes(conn, run.id, collector=FakeCollector())
    assert result.reused_count == 1
    assert probe.id in result.reused_probe_ids


# ---------------------------------------------------------------------------
# W: Reuse — most recent matching job is selected when multiple qualify
# ---------------------------------------------------------------------------


def test_w_most_recent_job_selected_for_reuse(conn):
    # Seed two qualifying jobs with different ages; the most recent should be chosen.
    _seed_completed_job(conn, "tensorflow basics", age_hours=5.0)
    new_job = _seed_completed_job(conn, "tensorflow basics", age_hours=1.0)
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="tensorflow basics")
    dispatch_selected_probes(conn, run.id, collector=FakeCollector())
    refreshed = get_exploration_probe(conn, probe.id)
    assert refreshed.dispatched_job_id == new_job.id


# ---------------------------------------------------------------------------
# X: Budget enforcement — first probe dispatches, second deferred
# ---------------------------------------------------------------------------


def test_x_budget_exhausted_defers_second_probe(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="high prio", priority_score=0.9)
    p2 = _make_probe(conn, run, status="selected", query="low prio", priority_score=0.1)
    coll = FakeCollector(search_calls=1)
    result = dispatch_selected_probes(conn, run.id, collector=coll, max_search_calls=1)
    assert result.newly_dispatched == 1
    assert result.deferred_for_budget == 1
    assert p2.id in result.deferred_probe_ids
    refreshed_p2 = get_exploration_probe(conn, p2.id)
    assert refreshed_p2.status == "deferred"
    assert refreshed_p2.decision_reason == "dispatch_budget_exhausted"


# ---------------------------------------------------------------------------
# Y: Budget of 0 defers all probes immediately
# ---------------------------------------------------------------------------


def test_y_zero_budget_defers_all(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="angular directives")
    _make_probe(conn, run, status="selected", query="angular pipes")
    coll = FakeCollector()
    result = dispatch_selected_probes(conn, run.id, collector=coll, max_search_calls=0)
    assert result.deferred_for_budget == 2
    assert result.newly_dispatched == 0
    assert len(coll.calls) == 0


# ---------------------------------------------------------------------------
# Z: Budget is not consumed by reused probes
# ---------------------------------------------------------------------------


def test_z_reuse_does_not_consume_budget(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="svelte stores", priority_score=0.9)
    _make_probe(conn, run, status="selected", query="new query xyz", priority_score=0.1)
    _seed_completed_job(conn, "svelte stores", age_hours=1.0)
    coll = FakeCollector(search_calls=1)
    result = dispatch_selected_probes(conn, run.id, collector=coll, max_search_calls=1)
    # Reuse probe uses 0 budget; new probe uses 1 search call = budget exhausted with 2 probes
    assert result.reused_count == 1
    assert (
        result.newly_dispatched == 1
    )  # the "new query xyz" probe should be dispatched within budget


# ---------------------------------------------------------------------------
# AA: Dry-run — no jobs created
# ---------------------------------------------------------------------------


def test_aa_dry_run_no_jobs(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="scala functional")
    before = conn.execute("SELECT COUNT(*) FROM market_collection_jobs").fetchone()[0]
    result = dispatch_selected_probes(conn, run.id, collector=FakeCollector(), dry_run=True)
    after = conn.execute("SELECT COUNT(*) FROM market_collection_jobs").fetchone()[0]
    assert after == before
    assert result.dry_run is True


# ---------------------------------------------------------------------------
# AB: Dry-run — no probe status changes
# ---------------------------------------------------------------------------


def test_ab_dry_run_probe_unchanged(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="go channels")
    dispatch_selected_probes(conn, run.id, collector=FakeCollector(), dry_run=True)
    refreshed = get_exploration_probe(conn, probe.id)
    assert refreshed.status == "selected"


# ---------------------------------------------------------------------------
# AC: Dry-run — reuse still detected (age, compatibility reported)
# ---------------------------------------------------------------------------


def test_ac_dry_run_detects_reuse(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="clojure maps")
    _seed_completed_job(conn, "clojure maps", age_hours=2.0)
    result = dispatch_selected_probes(conn, run.id, collector=FakeCollector(), dry_run=True)
    assert result.reused_count == 1
    assert result.diagnostics[0].action == DispatchAction.EVIDENCE_REUSE


# ---------------------------------------------------------------------------
# AD: Dry-run — budget enforcement still simulated
# ---------------------------------------------------------------------------


def test_ad_dry_run_budget_simulated(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="erlang processes")
    _make_probe(conn, run, status="selected", query="elixir genservers")
    result = dispatch_selected_probes(
        conn, run.id, collector=FakeCollector(), dry_run=True, max_search_calls=0
    )
    assert result.deferred_for_budget == 2
    assert result.dry_run is True


# ---------------------------------------------------------------------------
# AE: Failed execution — probe stays SELECTED
# ---------------------------------------------------------------------------


def test_ae_failed_execution_probe_stays_selected(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="haskell monads")
    coll = FakeCollector(fail=True)
    result = dispatch_selected_probes(conn, run.id, collector=coll)
    assert result.failed_count == 1
    assert probe.id in result.failed_probe_ids
    refreshed = get_exploration_probe(conn, probe.id)
    assert refreshed.status == "selected"  # stays SELECTED for retry


# ---------------------------------------------------------------------------
# AF: Failed execution — dispatched_job_id is stored for audit
# ---------------------------------------------------------------------------


def test_af_failed_execution_job_id_stored(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="ocaml modules")
    coll = FakeCollector(fail=True)
    dispatch_selected_probes(conn, run.id, collector=coll)
    refreshed = get_exploration_probe(conn, probe.id)
    assert refreshed.dispatched_job_id is not None  # audit trail preserved
    assert refreshed.status == "selected"


# ---------------------------------------------------------------------------
# AG: Failed probe can be retried (re-dispatch creates new job)
# ---------------------------------------------------------------------------


def test_ag_failed_probe_retry(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="c++ templates")
    coll_fail = FakeCollector(fail=True)
    dispatch_selected_probes(conn, run.id, collector=coll_fail)
    # Probe is still SELECTED; retry with working collector
    coll_ok = FakeCollector()
    result = dispatch_selected_probes(conn, run.id, collector=coll_ok)
    assert result.newly_dispatched == 1
    refreshed = get_exploration_probe(conn, probe.id)
    assert refreshed.status == "dispatched"


# ---------------------------------------------------------------------------
# AH: Partial execution — probe is marked DISPATCHED (partial is success for dispatch)
# ---------------------------------------------------------------------------


def test_ah_partial_execution_probe_dispatched(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="ruby metaprogramming")
    coll = FakeCollector(status="partial")
    result = dispatch_selected_probes(conn, run.id, collector=coll)
    assert result.partial_count == 1
    assert result.newly_dispatched == 1
    refreshed = get_exploration_probe(conn, probe.id)
    assert refreshed.status == "dispatched"


# ---------------------------------------------------------------------------
# AI: dispatch policy snapshot merged into run.policy_json["dispatch"]
# ---------------------------------------------------------------------------


def test_ai_policy_snapshot_merged(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="ruby on rails")
    dispatch_selected_probes(conn, run.id, collector=FakeCollector())
    refreshed_run = get_exploration_run(conn, run.id)
    policy = json.loads(refreshed_run.policy_json)
    assert "dispatch" in policy
    d = policy["dispatch"]
    assert d["dispatch_policy_version"] == DISPATCH_POLICY_VERSION
    assert "reuse_max_age_hours" in d


# ---------------------------------------------------------------------------
# AJ: Policy merge preserves existing selector policy
# ---------------------------------------------------------------------------


def test_aj_policy_merge_preserves_selector(conn):
    import json

    run = _make_run(conn)
    # Pre-populate run.policy_json with a fake selector snapshot
    from app.intelligence.market.planner_repository import update_exploration_run_policy

    initial = {"selector": {"policy_version": "v1"}}
    update_exploration_run_policy(conn, run.id, json.dumps(initial))
    _make_probe(conn, run, status="selected", query="django rest")
    dispatch_selected_probes(conn, run.id, collector=FakeCollector())
    refreshed_run = get_exploration_run(conn, run.id)
    policy = json.loads(refreshed_run.policy_json)
    assert policy.get("selector", {}).get("policy_version") == "v1"
    assert "dispatch" in policy


# ---------------------------------------------------------------------------
# AK: Origin type for adjacent_topic probe is 'adjacent_topic'
# ---------------------------------------------------------------------------


def test_ak_origin_type_adjacent_topic(conn):
    assert (
        _origin_type_for_probe(ExplorationProbeType.ADJACENT_TOPIC)
        == MarketJobOriginType.ADJACENT_TOPIC
    )


# ---------------------------------------------------------------------------
# AL: Origin type for market_region probe is 'exploration_planner'
# ---------------------------------------------------------------------------


def test_al_origin_type_market_region(conn):
    assert _origin_type_for_probe("market_region") == MarketJobOriginType.EXPLORATION_PLANNER


# ---------------------------------------------------------------------------
# AM: Origin type for cold_start probe is 'exploration_planner'
# ---------------------------------------------------------------------------


def test_am_origin_type_cold_start(conn):
    assert _origin_type_for_probe("cold_start") == MarketJobOriginType.EXPLORATION_PLANNER


# ---------------------------------------------------------------------------
# AN: Created job has correct origin_type for market_region probe
# ---------------------------------------------------------------------------


def test_an_created_job_origin_type(conn):
    run = _make_run(conn)
    probe = _make_probe(
        conn, run, status="selected", query="python async", probe_type="market_region"
    )
    dispatch_selected_probes(conn, run.id, collector=FakeCollector())
    refreshed = get_exploration_probe(conn, probe.id)
    job = get_market_collection_job(conn, refreshed.dispatched_job_id)
    assert job.origin_type == "exploration_planner"


# ---------------------------------------------------------------------------
# AO: Created job has correct origin_type for adjacent_topic probe
# ---------------------------------------------------------------------------


def test_ao_adjacent_topic_job_origin_type(conn):
    run = _make_run(conn)
    probe = _make_probe(
        conn, run, status="selected", query="advanced async python", probe_type="adjacent_topic"
    )
    dispatch_selected_probes(conn, run.id, collector=FakeCollector())
    refreshed = get_exploration_probe(conn, probe.id)
    job = get_market_collection_job(conn, refreshed.dispatched_job_id)
    assert job.origin_type == "adjacent_topic"


# ---------------------------------------------------------------------------
# AP: Created job stores execution params in quota_policy_snapshot_json
# ---------------------------------------------------------------------------


def test_ap_created_job_execution_params(conn):
    run = _make_run(conn)
    _make_probe(
        conn,
        run,
        status="selected",
        query="sql window functions",
        region_code="US",
        language_code="en",
    )
    dispatch_selected_probes(conn, run.id, collector=FakeCollector())
    list_selected_probes_for_dispatch(conn, run.id)
    # probe list should be empty now (dispatched)
    all_probes = conn.execute(
        "SELECT * FROM market_exploration_probes WHERE exploration_run_id = ?", (run.id,)
    ).fetchall()
    probe_row = all_probes[0]
    job = get_market_collection_job(conn, probe_row["dispatched_job_id"])
    snapshot = json.loads(job.quota_policy_snapshot_json)
    exec_params = snapshot[DISPATCH_EXECUTION_PARAMS_KEY]
    assert exec_params["normalized_query"] == "sql window functions"
    assert exec_params["region_code"] == "US"
    assert exec_params["language_code"] == "en"


# ---------------------------------------------------------------------------
# AQ: find_reusable_job returns None when no jobs exist
# ---------------------------------------------------------------------------


def test_aq_find_reusable_no_jobs(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="no prior jobs")
    job, age = find_reusable_job(conn, probe)
    assert job is None
    assert age is None


# ---------------------------------------------------------------------------
# AR: find_reusable_job returns matching job when available
# ---------------------------------------------------------------------------


def test_ar_find_reusable_returns_job(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="matching query")
    prior = _seed_completed_job(conn, "matching query", age_hours=2.0)
    job, age = find_reusable_job(conn, probe)
    assert job is not None
    assert job.id == prior.id
    assert 1.9 < age < 2.5


# ---------------------------------------------------------------------------
# AS: _has_required_reuse_observations returns False when no search obs exist
# ---------------------------------------------------------------------------


def test_as_has_required_obs_false(conn):
    _make_run(conn)
    job = create_market_collection_job(
        conn,
        job_type="search_scan",
        origin_type="exploration_planner",
    )
    assert _has_required_reuse_observations(conn, job.id) is False


# ---------------------------------------------------------------------------
# AT: _has_required_reuse_observations returns True when search obs present
# ---------------------------------------------------------------------------


def test_at_has_required_obs_true(conn):
    _make_run(conn)
    job = create_market_collection_job(
        conn,
        job_type="search_scan",
        origin_type="exploration_planner",
    )
    obs = persist_observation(
        conn,
        platform="youtube",
        provider="youtube_data_api",
        collector_name="test",
        signal_type=SEARCH_RESULT_RANK,
        observed_at="2026-01-01T00:00:00",
        input_hash="hash-test-at",
        external_video_id="vidAT",
        signal_value_text="1",
    )
    link_job_observation(conn, job.id, obs.id if hasattr(obs, "id") else obs)
    assert _has_required_reuse_observations(conn, job.id) is True


# ---------------------------------------------------------------------------
# AU: build_dispatch_policy_snapshot returns correct structure
# ---------------------------------------------------------------------------


def test_au_policy_snapshot_structure():
    snap = build_dispatch_policy_snapshot()
    assert snap["dispatch_policy_version"] == DISPATCH_POLICY_VERSION
    assert snap["reuse_max_age_hours"] == DISPATCH_REUSE_MAX_AGE_HOURS
    assert (
        "evidence_reuse" in str(snap["reuse_compatible_statuses"])
        or "completed" in snap["reuse_compatible_statuses"]
    )
    assert snap["reuse_min_required_signal"] == SEARCH_RESULT_RANK
    assert "ordering_policy" in snap
    assert "network_boundary" in snap


# ---------------------------------------------------------------------------
# AV: ProbeDispatchResult model defaults
# ---------------------------------------------------------------------------


def test_av_result_defaults():
    r = ProbeDispatchResult(run_id=42)
    assert r.run_id == 42
    assert r.selected_considered == 0
    assert r.newly_dispatched == 0
    assert r.reused_count == 0
    assert r.failed_count == 0
    assert r.deferred_for_budget == 0
    assert r.dry_run is False
    assert r.policy_version == DISPATCH_POLICY_VERSION
    assert r.dispatched_probe_ids == []
    assert r.diagnostics == []


# ---------------------------------------------------------------------------
# AW: ProbeDispatchDiagnostic model fields
# ---------------------------------------------------------------------------


def test_aw_diagnostic_fields():
    d = ProbeDispatchDiagnostic(
        probe_id=1,
        query_text="test query",
        probe_type="market_region",
        action=DispatchAction.NEW_EXECUTION,
    )
    assert d.probe_id == 1
    assert d.query_text == "test query"
    assert d.action == DispatchAction.NEW_EXECUTION
    assert d.reused_job_id is None
    assert d.actual_search_calls == 0


# ---------------------------------------------------------------------------
# AX: dispatch_probe with non-existent probe_id returns empty result
# ---------------------------------------------------------------------------


def test_ax_dispatch_probe_not_found(conn):
    result = dispatch_probe(conn, 999999)
    assert result.run_id == 0
    assert result.selected_considered == 0


# ---------------------------------------------------------------------------
# AY: dispatch_probe with CANDIDATE probe returns empty (ineligible)
# ---------------------------------------------------------------------------


def test_ay_dispatch_probe_candidate_ineligible(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="candidate", query="swift closures")
    result = dispatch_probe(conn, probe.id, collector=FakeCollector())
    assert result.selected_considered == 0


# ---------------------------------------------------------------------------
# AZ: dispatch_probe with SELECTED probe dispatches correctly
# ---------------------------------------------------------------------------


def test_az_dispatch_probe_selected_dispatches(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="terraform modules")
    result = dispatch_probe(conn, probe.id, collector=FakeCollector())
    assert result.newly_dispatched == 1
    assert probe.id in result.dispatched_probe_ids
    refreshed = get_exploration_probe(conn, probe.id)
    assert refreshed.status == "dispatched"


# ---------------------------------------------------------------------------
# BA: dispatch_probe detects reuse for SELECTED probe
# ---------------------------------------------------------------------------


def test_ba_dispatch_probe_reuses(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="ansible playbooks")
    prior = _seed_completed_job(conn, "ansible playbooks", age_hours=1.0)
    result = dispatch_probe(conn, probe.id, collector=FakeCollector())
    assert result.reused_count == 1
    diag = result.diagnostics[0]
    assert diag.action == DispatchAction.EVIDENCE_REUSE
    assert diag.reused_job_id == prior.id


# ---------------------------------------------------------------------------
# BB: Reuse — published_after mismatch prevents reuse
# ---------------------------------------------------------------------------


def test_bb_published_after_mismatch_no_reuse(conn):
    run = _make_run(conn)
    _make_probe(
        conn, run, status="selected", query="kubernetes pods", published_after="2026-01-01T00:00:00"
    )
    _seed_completed_job(conn, "kubernetes pods", published_after=None)
    coll = FakeCollector()
    result = dispatch_selected_probes(conn, run.id, collector=coll)
    assert result.reused_count == 0


# ---------------------------------------------------------------------------
# BC: Reuse — order mismatch prevents reuse
# ---------------------------------------------------------------------------


def test_bc_order_mismatch_no_reuse(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="docker volumes", order="viewCount")
    _seed_completed_job(conn, "docker volumes", order="relevance")
    coll = FakeCollector()
    result = dispatch_selected_probes(conn, run.id, collector=coll)
    assert result.reused_count == 0


# ---------------------------------------------------------------------------
# BD: Reuse — max_pages >= required allows reuse
# ---------------------------------------------------------------------------


def test_bd_max_pages_gte_allows_reuse(conn):
    # Probe requires max_pages=1; seed job has max_pages=2 (exceeds requirement).
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="postgres indexes", max_pages=1)
    _seed_completed_job(conn, "postgres indexes", max_pages=2)  # prior has MORE pages — reuse ok
    result = dispatch_selected_probes(conn, run.id, collector=FakeCollector())
    assert result.reused_count == 1


# ---------------------------------------------------------------------------
# BE: Cross-channel reuse — different channel's job can be reused
# ---------------------------------------------------------------------------


def test_be_cross_channel_reuse(conn):
    # Cross-channel reuse: run with no channel_id can reuse a job that also has no channel_id
    # (reuse matching is query+params only, not filtered by channel)
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="graphql mutations")
    # Seed job with no channel association (simulates a prior run from any channel)
    _seed_completed_job(conn, "graphql mutations", channel_id=None, workspace_id=None)
    result = dispatch_selected_probes(conn, run.id, collector=FakeCollector())
    assert result.reused_count == 1  # cross-channel reuse allowed


# ---------------------------------------------------------------------------
# BF: dispatch_selected_probes on empty SELECTED set returns quickly
# ---------------------------------------------------------------------------


def test_bf_empty_selected_set(conn):
    run = _make_run(conn)
    result = dispatch_selected_probes(conn, run.id, collector=FakeCollector())
    assert result.selected_considered == 0
    assert result.newly_dispatched == 0
    assert result.diagnostics == []


# ---------------------------------------------------------------------------
# BG: Diagnostics list has one entry per probe
# ---------------------------------------------------------------------------


def test_bg_diagnostics_per_probe(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="redis pubsub", priority_score=0.9)
    _make_probe(conn, run, status="selected", query="redis streams", priority_score=0.5)
    result = dispatch_selected_probes(conn, run.id, collector=FakeCollector())
    assert len(result.diagnostics) == 2


# ---------------------------------------------------------------------------
# BH: Diagnostic action is 'evidence_reuse' for reused probe
# ---------------------------------------------------------------------------


def test_bh_diagnostic_evidence_reuse_action(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="celery tasks")
    _seed_completed_job(conn, "celery tasks", age_hours=1.0)
    result = dispatch_selected_probes(conn, run.id, collector=FakeCollector())
    diag = result.diagnostics[0]
    assert diag.action == DispatchAction.EVIDENCE_REUSE
    assert diag.probe_id == probe.id


# ---------------------------------------------------------------------------
# BI: Diagnostic action is 'new_execution' for fresh dispatch
# ---------------------------------------------------------------------------


def test_bi_diagnostic_new_execution_action(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="airflow dags")
    result = dispatch_selected_probes(conn, run.id, collector=FakeCollector())
    diag = result.diagnostics[0]
    assert diag.action == DispatchAction.NEW_EXECUTION
    assert diag.probe_id == probe.id


# ---------------------------------------------------------------------------
# BJ: Diagnostic action is 'deferred_budget' for budget-exhausted probe
# ---------------------------------------------------------------------------


def test_bj_diagnostic_deferred_budget_action(conn):
    run = _make_run(conn)
    _make_probe(conn, run, status="selected", query="first query", priority_score=0.9)
    p2 = _make_probe(conn, run, status="selected", query="second query", priority_score=0.1)
    coll = FakeCollector(search_calls=1)
    result = dispatch_selected_probes(conn, run.id, collector=coll, max_search_calls=1)
    deferred_diag = next(d for d in result.diagnostics if d.probe_id == p2.id)
    assert deferred_diag.action == DispatchAction.DEFERRED_BUDGET


# ---------------------------------------------------------------------------
# BK: Diagnostic action is 'failed_execution' for failed dispatch
# ---------------------------------------------------------------------------


def test_bk_diagnostic_failed_execution_action(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="kubernetes helm")
    coll = FakeCollector(fail=True)
    result = dispatch_selected_probes(conn, run.id, collector=coll)
    diag = result.diagnostics[0]
    assert diag.action == DispatchAction.FAILED_EXECUTION
    assert diag.probe_id == probe.id


# ---------------------------------------------------------------------------
# BL: record_probe_attempted_dispatch sets dispatched_job_id without status change
# ---------------------------------------------------------------------------


def test_bl_record_attempted_dispatch(conn):
    run = _make_run(conn)
    probe = _make_probe(conn, run, status="selected", query="prometheus metrics")
    job = create_market_collection_job(
        conn, job_type="search_scan", origin_type="exploration_planner"
    )
    record_probe_attempted_dispatch(conn, probe.id, job_id=job.id)
    refreshed = get_exploration_probe(conn, probe.id)
    assert refreshed.dispatched_job_id == job.id
    assert refreshed.status == "selected"  # status unchanged


# ---------------------------------------------------------------------------
# BM: list_selected_probes_for_dispatch returns only SELECTED probes
# ---------------------------------------------------------------------------


def test_bm_list_selected_probes_for_dispatch(conn):
    run = _make_run(conn)
    s1 = _make_probe(conn, run, status="selected", query="grafana panels", priority_score=0.8)
    s2 = _make_probe(conn, run, status="selected", query="loki queries", priority_score=0.4)
    _make_probe(conn, run, status="candidate", query="tempo traces")
    _make_probe(conn, run, status="deferred", query="jaeger spans")
    probes = list_selected_probes_for_dispatch(conn, run.id)
    assert len(probes) == 2
    assert {p.id for p in probes} == {s1.id, s2.id}


# ---------------------------------------------------------------------------
# BN: list_selected_probes_for_dispatch order is priority DESC then id ASC
# ---------------------------------------------------------------------------


def test_bn_list_selected_dispatch_ordering(conn):
    run = _make_run(conn)
    low = _make_probe(conn, run, status="selected", query="low score", priority_score=0.2)
    high = _make_probe(conn, run, status="selected", query="high score", priority_score=0.9)
    probes = list_selected_probes_for_dispatch(conn, run.id)
    assert probes[0].id == high.id
    assert probes[1].id == low.id


# ---------------------------------------------------------------------------
# BO: Full suite green — multi-probe mixed scenario
# ---------------------------------------------------------------------------


def test_bo_full_mixed_scenario(conn):
    """Full integration: reuse + new execution + budget deferral in one run."""
    run = _make_run(conn)
    # Probe 1: will reuse (highest priority)
    p_reuse = _make_probe(conn, run, status="selected", query="vim plugins", priority_score=0.95)
    _seed_completed_job(conn, "vim plugins", age_hours=2.0)
    # Probe 2: will execute (mid priority)
    p_new = _make_probe(conn, run, status="selected", query="emacs config", priority_score=0.6)
    # Probe 3: will be deferred (lowest priority, budget = 1)
    p_deferred = _make_probe(conn, run, status="selected", query="neovim lua", priority_score=0.2)

    coll = FakeCollector(search_calls=1)
    result = dispatch_selected_probes(conn, run.id, collector=coll, max_search_calls=1)

    assert result.selected_considered == 3
    assert result.reused_count == 1
    assert p_reuse.id in result.reused_probe_ids
    assert result.newly_dispatched == 1
    assert p_new.id in result.dispatched_probe_ids
    assert result.deferred_for_budget == 1
    assert p_deferred.id in result.deferred_probe_ids
    assert result.actual_search_calls == 1
    assert result.search_calls_avoided > 0

    # State assertions
    r_reuse = get_exploration_probe(conn, p_reuse.id)
    r_new = get_exploration_probe(conn, p_new.id)
    r_deferred = get_exploration_probe(conn, p_deferred.id)
    assert r_reuse.status == "dispatched"
    assert r_new.status == "dispatched"
    assert r_deferred.status == "deferred"
    assert r_deferred.decision_reason == "dispatch_budget_exhausted"

    # Policy snapshot written to run
    refreshed_run = get_exploration_run(conn, run.id)
    policy = json.loads(refreshed_run.policy_json)
    assert "dispatch" in policy
