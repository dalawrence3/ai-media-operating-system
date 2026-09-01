"""Phase 17G — Semantic-fit persisted cache and batch resolution service.

Covers what Phase 14C's assess_semantic_fit never addressed: making the
LLM call cost-conscious across repeated eligibility assessments, and the
new resolve_unresolved_opportunities_for_channel() batch entry point that
Phase 18 will call.

Tests:
  A  eligible result is persisted and reused (no second LLM call)
  B  ineligible result is persisted and reused
  C  failed evaluation is never cached — stays UNRESOLVED on retry
  D  fake/replay provider results are never cached
  E  cache is scoped per opportunity+channel — no cross-channel leakage
  F  a new (superseding) channel profile version invalidates the cache
  G  resolve_unresolved_opportunities_for_channel: bounded evaluation count
  H  resolve_unresolved_opportunities_for_channel: eligible/ineligible/unresolved counts
  I  resolve_unresolved_opportunities_for_channel: reuses cache, spends no LLM call
  J  resolve_unresolved_opportunities_for_channel: channel isolation
  K  resolve_unresolved_opportunities_for_channel: no provider configured -> nothing evaluated
  L  planner-facing: after resolution, ai_provider=None assessment sees the cached result
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.ai.fake import FakeProvider
from app.ai.provider import AIRequest, AIResponse
from app.intelligence.experiments.eligibility import (
    ExperimentEligibilityClassification,
)
from app.intelligence.experiments.eligibility_service import (
    assess_experiment_eligibility,
    resolve_unresolved_opportunities_for_channel,
)

# ---------------------------------------------------------------------------
# Minimal full schema — mirrors test_experiment_eligibility_14c.py's
# _minimal_db(), plus the Phase 17G cache table so caching behavior is
# actually exercised (not silently no-op'd via the missing-table guard).
# ---------------------------------------------------------------------------


def _ts(delta_hours: float = 0.0) -> str:
    dt = datetime.now(UTC) + timedelta(hours=delta_hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _minimal_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE market_cluster_signals (
            id INTEGER PRIMARY KEY,
            interpretation_run_id INTEGER NOT NULL,
            signal_maturity TEXT NOT NULL DEFAULT 'insufficient',
            confidence REAL NOT NULL DEFAULT 0.0,
            cluster_id INTEGER
        );
        CREATE TABLE market_topic_clusters (
            id INTEGER PRIMARY KEY,
            canonical_cluster_id INTEGER
        );
        CREATE TABLE market_interpretation_runs (
            id INTEGER PRIMARY KEY,
            completed_at TEXT
        );
        CREATE TABLE experiments (
            id TEXT PRIMARY KEY,
            channel_id INTEGER,
            opportunity_id INTEGER,
            status TEXT
        );
        CREATE TABLE topics (
            id INTEGER PRIMARY KEY,
            promoted_opportunity_id INTEGER
        );
        CREATE TABLE publishing_plans (
            id INTEGER PRIMARY KEY,
            topic_id INTEGER,
            production_plan_id INTEGER,
            render_manifest_id INTEGER
        );
        CREATE TABLE publications (
            id INTEGER PRIMARY KEY,
            publishing_plan_id INTEGER
        );
        CREATE TABLE analytics_snapshots (
            id INTEGER PRIMARY KEY,
            publication_id INTEGER,
            observation_state TEXT
        );
        CREATE TABLE analytics_aggregates (
            id INTEGER PRIMARY KEY,
            publication_id INTEGER,
            topic_id INTEGER,
            metric_name TEXT,
            metric_value REAL,
            period_type TEXT,
            period_key TEXT
        );
        CREATE TABLE analytics_metrics (
            id INTEGER PRIMARY KEY,
            snapshot_id INTEGER,
            publication_id INTEGER,
            metric_name TEXT,
            metric_value REAL
        );
        CREATE TABLE content_feature_snapshots (
            id INTEGER PRIMARY KEY,
            topic_id INTEGER,
            publication_id INTEGER
        );
        CREATE TABLE channels (
            id INTEGER PRIMARY KEY,
            platform TEXT,
            channel_name TEXT,
            platform_channel_id TEXT,
            operating_mode TEXT DEFAULT 'active',
            current_maturity_stage TEXT DEFAULT 'validation',
            current_profile_version_id INTEGER,
            current_strategy_id INTEGER,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE channel_profile_versions (
            id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            version INTEGER DEFAULT 1,
            strategy_id INTEGER,
            maturity_stage TEXT DEFAULT 'validation',
            primary_niche TEXT,
            secondary_niches_json TEXT DEFAULT '[]',
            excluded_topics_json TEXT DEFAULT '[]',
            audience_description TEXT,
            audience_demographics TEXT,
            audience_intent TEXT DEFAULT 'educational',
            brand_voice TEXT DEFAULT 'conversational',
            tone_notes TEXT,
            brand_rules_json TEXT DEFAULT '[]',
            content_style TEXT DEFAULT 'explainer',
            primary_format TEXT DEFAULT 'long_form',
            posting_cadence_per_week REAL DEFAULT 1.0,
            portfolio_targets_json TEXT DEFAULT '{}',
            allowed_discovery_adapters_json TEXT DEFAULT '[]',
            max_candidates_per_run INTEGER DEFAULT 20,
            min_opportunity_score REAL DEFAULT 0.5,
            duplicate_similarity_threshold REAL DEFAULT 0.8,
            signal_staleness_days INTEGER DEFAULT 30,
            scoring_policy_version TEXT DEFAULT 'v1',
            active_from TEXT,
            superseded_at TEXT,
            created_by TEXT DEFAULT 'test',
            status TEXT DEFAULT 'active',
            activated_at TEXT,
            activated_by TEXT,
            activation_reason TEXT
        );
        CREATE TABLE discovery_runs (
            id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            run_status TEXT DEFAULT 'completed',
            created_at TEXT
        );
        CREATE TABLE opportunities (
            id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            discovery_run_id INTEGER,
            normalized_topic TEXT,
            raw_topic TEXT,
            title TEXT DEFAULT '',
            topic_summary TEXT DEFAULT '',
            format_recommendation TEXT DEFAULT 'undecided',
            strategic_role TEXT DEFAULT 'discovery',
            current_lifecycle_state TEXT DEFAULT 'new',
            created_at TEXT,
            updated_at TEXT,
            canonical_cluster_id INTEGER,
            market_signal_snapshot_id INTEGER
        );
        CREATE TABLE opportunity_semantic_fit_results (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id              INTEGER NOT NULL,
            channel_id                  INTEGER NOT NULL,
            channel_profile_version_id  INTEGER,
            prompt_version              TEXT NOT NULL,
            input_hash                  TEXT NOT NULL,
            score                       REAL NOT NULL,
            fit_label                   TEXT NOT NULL DEFAULT '',
            rationale                   TEXT NOT NULL DEFAULT '',
            provider_name               TEXT NOT NULL,
            model                       TEXT NOT NULL DEFAULT '',
            evaluated_at                TEXT NOT NULL,
            UNIQUE (opportunity_id, input_hash)
        );
    """)
    return conn


def _insert_channel_and_profile(
    conn: sqlite3.Connection,
    *,
    channel_id: int = 1,
    primary_niche: str = "Python tutorials",
    excluded_topics: list[str] | None = None,
    audience_description: str | None = "Python developers",
    profile_id: int | None = None,
) -> int:
    now = _ts()
    conn.execute(
        "INSERT OR IGNORE INTO channels "
        "(id, platform, channel_name, platform_channel_id, created_at, updated_at) "
        "VALUES (?, 'youtube', 'TestChannel', 'UC_test', ?, ?)",
        (channel_id, now, now),
    )
    pid = profile_id if profile_id is not None else channel_id
    excl_json = json.dumps(excluded_topics or [])
    conn.execute(
        """INSERT INTO channel_profile_versions
           (id, channel_id, primary_niche, excluded_topics_json, audience_description,
            audience_demographics, tone_notes, activated_by, activation_reason,
            status, active_from, portfolio_targets_json)
           VALUES (?, ?, ?, ?, ?, '', '', '', '', 'active', ?, '{}')""",
        (pid, channel_id, primary_niche, excl_json, audience_description or "", now),
    )
    conn.execute(
        "UPDATE channels SET current_profile_version_id = ? WHERE id = ?",
        (pid, channel_id),
    )
    return pid


def _insert_run_and_signal(
    conn: sqlite3.Connection,
    *,
    run_id: int = 1,
    signal_id: int = 1,
    cluster_id: int = 1,
    canonical_cluster_id: int = 10,
    completed_at: str | None = None,
    signal_maturity: str = "directional",
    confidence: float = 0.55,
) -> None:
    if completed_at is None:
        completed_at = _ts(-10)
    conn.execute(
        "INSERT OR IGNORE INTO market_interpretation_runs (id, completed_at) VALUES (?, ?)",
        (run_id, completed_at),
    )
    conn.execute(
        "INSERT OR IGNORE INTO market_topic_clusters (id, canonical_cluster_id) VALUES (?, ?)",
        (cluster_id, canonical_cluster_id),
    )
    conn.execute(
        """INSERT OR IGNORE INTO market_cluster_signals
           (id, interpretation_run_id, signal_maturity, confidence, cluster_id)
           VALUES (?, ?, ?, ?, ?)""",
        (signal_id, run_id, signal_maturity, confidence, cluster_id),
    )


def _insert_opportunity(
    conn: sqlite3.Connection,
    *,
    opp_id: int = 1,
    channel_id: int = 1,
    normalized_topic: str = "python async programming",
    title: str = "Python async tutorial",
    canonical_cluster_id: int | None = 10,
    market_signal_snapshot_id: int | None = 1,
    lifecycle_state: str = "new",
) -> None:
    now = _ts()
    conn.execute(
        "INSERT OR IGNORE INTO discovery_runs (id, channel_id, created_at) VALUES (1, ?, ?)",
        (channel_id, now),
    )
    conn.execute(
        """INSERT INTO opportunities
           (id, channel_id, discovery_run_id, normalized_topic, raw_topic, title,
            current_lifecycle_state, created_at, updated_at, canonical_cluster_id,
            market_signal_snapshot_id)
           VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            opp_id,
            channel_id,
            normalized_topic,
            normalized_topic,
            title,
            lifecycle_state,
            now,
            now,
            canonical_cluster_id,
            market_signal_snapshot_id,
        ),
    )


def _full_db_general_eligible(
    *,
    channel_id: int = 1,
    opp_id: int = 1,
    primary_niche: str = "Python tutorials",
) -> sqlite3.Connection:
    conn = _minimal_db()
    _insert_channel_and_profile(conn, channel_id=channel_id, primary_niche=primary_niche)
    _insert_run_and_signal(conn)
    _insert_opportunity(
        conn,
        opp_id=opp_id,
        channel_id=channel_id,
        normalized_topic="rust memory safety deep dive",
        title="Rust Memory Safety",
    )
    return conn


class _CountingProvider:
    """A real-looking provider (name='claude') that counts calls and never fails."""

    name = "claude"

    def __init__(self, score: float = 0.85, fit_label: str = "strong_fit"):
        self.score = score
        self.fit_label = fit_label
        self.call_count = 0

    def complete(self, request: AIRequest) -> AIResponse:
        self.call_count += 1
        raw = json.dumps(
            {
                "score": self.score,
                "fit_label": self.fit_label,
                "rationale": "Solid alignment.",
            }
        )
        return AIResponse(
            raw_text=raw,
            provider_name="claude",
            model=request.model,
            input_tokens=10,
            output_tokens=10,
            duration_ms=1,
            retry_count=0,
            parsed=None,
        )


class _FailingProvider:
    name = "claude"

    def __init__(self):
        self.call_count = 0

    def complete(self, request: AIRequest) -> AIResponse:
        self.call_count += 1
        raise RuntimeError("simulated provider outage")


def _fake_fit_provider(score: float = 0.80, fit_label: str = "strong_fit") -> FakeProvider:
    output = json.dumps({"score": score, "fit_label": fit_label, "rationale": "Good fit."})
    return FakeProvider(output=output)


# ---------------------------------------------------------------------------
# A-F: assess_experiment_eligibility caching behavior
# ---------------------------------------------------------------------------


def test_A_eligible_result_is_cached_and_reused():
    conn = _full_db_general_eligible()
    provider = _CountingProvider(score=0.85)

    first = assess_experiment_eligibility(conn, 1, 1, ai_provider=provider)
    assert first.classification == ExperimentEligibilityClassification.GENERAL_ELIGIBLE
    assert provider.call_count == 1

    row = conn.execute("SELECT * FROM opportunity_semantic_fit_results").fetchone()
    assert row is not None
    assert row["score"] == pytest.approx(0.85)
    assert row["provider_name"] == "claude"

    second = assess_experiment_eligibility(conn, 1, 1, ai_provider=provider)
    assert second.classification == ExperimentEligibilityClassification.GENERAL_ELIGIBLE
    assert provider.call_count == 1, (
        "second assessment must reuse the cached result, not call the LLM again"
    )


def test_B_ineligible_result_is_cached_and_reused():
    conn = _full_db_general_eligible()
    provider = _CountingProvider(score=0.20, fit_label="no_fit")

    first = assess_experiment_eligibility(conn, 1, 1, ai_provider=provider)
    assert first.classification == ExperimentEligibilityClassification.INELIGIBLE
    assert provider.call_count == 1

    second = assess_experiment_eligibility(conn, 1, 1, ai_provider=provider)
    assert second.classification == ExperimentEligibilityClassification.INELIGIBLE
    assert provider.call_count == 1


def test_C_failed_call_is_never_cached_stays_unresolved():
    conn = _full_db_general_eligible()
    provider = _FailingProvider()

    first = assess_experiment_eligibility(conn, 1, 1, ai_provider=provider)
    assert first.classification == ExperimentEligibilityClassification.UNRESOLVED
    assert provider.call_count == 1

    row = conn.execute("SELECT * FROM opportunity_semantic_fit_results").fetchone()
    assert row is None, "a failed call must never be persisted"

    # Retrying still calls the provider again (nothing to reuse) and stays UNRESOLVED.
    second = assess_experiment_eligibility(conn, 1, 1, ai_provider=provider)
    assert second.classification == ExperimentEligibilityClassification.UNRESOLVED
    assert provider.call_count == 2


def test_D_fake_and_replay_providers_are_never_cached():
    conn = _full_db_general_eligible()
    fake = _fake_fit_provider(score=0.85)
    assess_experiment_eligibility(conn, 1, 1, ai_provider=fake)
    row = conn.execute("SELECT * FROM opportunity_semantic_fit_results").fetchone()
    assert row is None, "FakeProvider results (test doubles) must never be cached"


def test_E_cache_scoped_per_opportunity_no_cross_opportunity_leakage():
    conn = _full_db_general_eligible()
    _insert_opportunity(
        conn,
        opp_id=2,
        channel_id=1,
        normalized_topic="javascript closures explained",
        title="JS Closures",
    )
    provider = _CountingProvider(score=0.85)

    assess_experiment_eligibility(conn, 1, 1, ai_provider=provider)
    assert provider.call_count == 1

    # A different opportunity has a different input_hash -> genuine cache miss.
    assess_experiment_eligibility(conn, 2, 1, ai_provider=provider)
    assert provider.call_count == 2


def test_F_new_profile_version_invalidates_cache():
    conn = _full_db_general_eligible(primary_niche="Python tutorials")
    provider = _CountingProvider(score=0.85)

    first = assess_experiment_eligibility(conn, 1, 1, ai_provider=provider)
    assert first.classification == ExperimentEligibilityClassification.GENERAL_ELIGIBLE
    assert provider.call_count == 1

    # Same opportunity, but the channel profile materially changed (a new,
    # superseding profile version with a different audience description) ->
    # new channel_profile_version_id -> different input_hash -> genuine
    # re-evaluation. No excluded-topic hard block is involved here, so the
    # request actually reaches the semantic-fit cache check again.
    conn.execute(
        "UPDATE channel_profile_versions SET status = 'superseded' "
        "WHERE channel_id = 1 AND status = 'active'"
    )
    _insert_channel_and_profile(
        conn,
        channel_id=1,
        primary_niche="Python tutorials",
        audience_description="Backend engineers learning systems programming",
        profile_id=999,
    )
    second = assess_experiment_eligibility(conn, 1, 1, ai_provider=provider)
    assert provider.call_count == 2, (
        "a materially changed profile version must invalidate the cache"
    )
    assert second.classification == ExperimentEligibilityClassification.GENERAL_ELIGIBLE


# ---------------------------------------------------------------------------
# G-L: resolve_unresolved_opportunities_for_channel
# ---------------------------------------------------------------------------


def _seed_n_unresolved_opportunities(
    conn: sqlite3.Connection, n: int, *, channel_id: int = 1
) -> None:
    _insert_channel_and_profile(conn, channel_id=channel_id)
    _insert_run_and_signal(conn)
    for i in range(1, n + 1):
        _insert_opportunity(
            conn,
            opp_id=i,
            channel_id=channel_id,
            normalized_topic=f"unique niche topic number {i}",
            title=f"Topic {i}",
        )


def test_G_bounded_evaluation_count():
    conn = _minimal_db()
    _seed_n_unresolved_opportunities(conn, 5)
    provider = _CountingProvider(score=0.85)

    result = resolve_unresolved_opportunities_for_channel(
        conn,
        channel_id=1,
        ai_provider=provider,
        max_evaluations=2,
    )
    assert result.considered == 5
    assert result.unresolved_found == 5
    assert result.evaluated == 2
    assert provider.call_count == 2
    assert result.still_unresolved == 3


def test_H_eligible_ineligible_unresolved_counts():
    conn = _minimal_db()
    _seed_n_unresolved_opportunities(conn, 3)

    class _MixedProvider:
        name = "claude"

        def __init__(self):
            self.n = 0

        def complete(self, request: AIRequest) -> AIResponse:
            self.n += 1
            # opp 1 -> eligible, opp 2 -> ineligible, opp 3 -> eligible
            score = 0.20 if self.n == 2 else 0.85
            raw = json.dumps({"score": score, "fit_label": "x", "rationale": "y"})
            return AIResponse(
                raw_text=raw,
                provider_name="claude",
                model=request.model,
                input_tokens=10,
                output_tokens=10,
                duration_ms=1,
                retry_count=0,
                parsed=None,
            )

    result = resolve_unresolved_opportunities_for_channel(
        conn,
        channel_id=1,
        ai_provider=_MixedProvider(),
        max_evaluations=10,
    )
    assert result.evaluated == 3
    assert result.eligible == 2
    assert result.ineligible == 1
    assert result.still_unresolved == 0


def test_I_resolution_reuses_cache_spends_no_llm_call():
    conn = _minimal_db()
    _seed_n_unresolved_opportunities(conn, 2)
    provider = _CountingProvider(score=0.85)

    first = resolve_unresolved_opportunities_for_channel(conn, channel_id=1, ai_provider=provider)
    assert first.evaluated == 2
    assert provider.call_count == 2

    # Re-running immediately: pass 1 now resolves both from cache for free.
    second = resolve_unresolved_opportunities_for_channel(conn, channel_id=1, ai_provider=provider)
    assert second.unresolved_found == 0
    assert second.evaluated == 0
    assert provider.call_count == 2, "no new LLM calls when nothing is unresolved"


def test_J_channel_isolation():
    conn = _minimal_db()
    _seed_n_unresolved_opportunities(conn, 2, channel_id=1)
    _insert_channel_and_profile(conn, channel_id=2, profile_id=2)
    _insert_opportunity(
        conn,
        opp_id=100,
        channel_id=2,
        normalized_topic="channel two exclusive topic",
        title="Other Channel Topic",
    )
    provider = _CountingProvider(score=0.85)

    result = resolve_unresolved_opportunities_for_channel(conn, channel_id=1, ai_provider=provider)
    assert result.considered == 2
    assert 100 not in result.opportunity_ids_evaluated

    result2 = resolve_unresolved_opportunities_for_channel(conn, channel_id=2, ai_provider=provider)
    assert result2.considered == 1
    assert result2.opportunity_ids_evaluated == [100]


def test_K_no_provider_configured_nothing_evaluated():
    conn = _minimal_db()
    _seed_n_unresolved_opportunities(conn, 3)

    result = resolve_unresolved_opportunities_for_channel(conn, channel_id=1, ai_provider=None)
    assert result.evaluated == 0
    assert result.unresolved_found == 3
    assert result.still_unresolved == 3


def test_L_planner_sees_cached_result_without_a_provider():
    conn = _minimal_db()
    _seed_n_unresolved_opportunities(conn, 1)
    provider = _CountingProvider(score=0.85)

    resolve_unresolved_opportunities_for_channel(conn, channel_id=1, ai_provider=provider)
    assert provider.call_count == 1

    # This mirrors the real planner CLI path: assess_experiment_eligibility
    # called with no ai_provider at all (as `ace experiment plan` does today).
    planner_view = assess_experiment_eligibility(conn, 1, 1, ai_provider=None)
    assert planner_view.classification == ExperimentEligibilityClassification.GENERAL_ELIGIBLE
    assert provider.call_count == 1, "the planner path must never trigger a live LLM call itself"
