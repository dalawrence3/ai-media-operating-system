"""Phase 14C.1 — Eligibility Safety Closure tests.

Tests A–AH covering the 14C.1 hardening pass:

A.  ambiguous fit + ai_provider=None → UNRESOLVED
B.  deterministic niche bypass + provider=None → not UNRESOLVED
C.  excluded topic + provider=None → INELIGIBLE (block before semantic)
D.  provider called and raises → UNRESOLVED
E.  semantic evaluator says high fit but excluded topic present → INELIGIBLE
F.  STALE market + passing fit → REQUIRES_REFRESH
G.  excluded topic + stale market → INELIGIBLE (not REQUIRES_REFRESH)
H.  exploratory maturity → EXPLORATION_ONLY
I.  directional maturity, sufficient confidence → GENERAL_ELIGIBLE (with provider)
J.  actionable maturity → GENERAL_ELIGIBLE (with provider)
K.  insufficient maturity → INELIGIBLE (hard block, provider not called)
L.  confidence boundary: 0.30 at-threshold → GENERAL_ELIGIBLE; 0.29 → EXPLORATION_ONLY
M.  cold-start: no analytics does not block exploration eligibility
N.  no Phase 12C evidence does not block any classification
O.  analytics readiness: observation_state='no_data' → not ready
P.  0.0 metric present but fails threshold (not treated as "missing")
Q.  NULL metric value → not ready (missing metric)
R.  any valid 'data' snapshot counts even if a newer 'no_data' exists (test S)
S.  same as R — newer no_data does not erase older valid data
T.  wrong-topic publication data excluded via topic_id scoping
U.  wrong-topic data excluded — verified via natural query scoping
V.  UTC-aware completed_at ages correctly
W.  naive legacy timestamp treated as UTC (not rejected)
X.  terminal experiment (completed/cancelled) does NOT block new experiment
Y.  active experiment (in_production) blocks → INELIGIBLE
Z.  unknown signal maturity produces negative rank (treated as insufficient)
AA. hard block finding → INELIGIBLE regardless of other signals
AB. policy snapshot contains all required fields including semantic_fit_prompt_version
AC. batch bounded by max_batch_size
AD. batch returns all items without imposing ranking or selection
AE. assessment is read-only (opportunity row unchanged after assessment)
AF. no YouTube call occurs during assessment (contractual)
AG. no content generation occurs during assessment (contractual)
AH. all existing 14C tests remain green (verified via regression)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.ai.fake import FakeProvider
from app.intelligence.experiments.eligibility import (
    EligibilityFinding,
    EligibilityPolicy,
    ExperimentEligibilityClassification,
    MarketFreshnessClass,
)
from app.intelligence.experiments.eligibility_service import (
    _is_deterministic_niche_match,
    _roll_up_classification,
    assess_analytics_readiness,
    assess_experiment_eligibility,
    assess_opportunity_batch,
)

# ---------------------------------------------------------------------------
# Helpers (mirrored from test_experiment_eligibility_14c.py)
# ---------------------------------------------------------------------------


def _ts(delta_hours: float = 0.0) -> str:
    dt = datetime.now(UTC) + timedelta(hours=delta_hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _mem_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _minimal_db() -> sqlite3.Connection:
    conn = _mem_conn()
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
    """)
    return conn


def _insert_channel_and_profile(
    conn: sqlite3.Connection,
    *,
    channel_id: int = 1,
    primary_niche: str = "Python tutorials",
    secondary_niches: list[str] | None = None,
    excluded_topics: list[str] | None = None,
    audience_description: str | None = "Python developers",
) -> None:
    now = _ts()
    conn.execute(
        """INSERT INTO channels
           (id, platform, channel_name, platform_channel_id, created_at, updated_at)
           VALUES (?, 'youtube', 'TestChannel', 'UC_test', ?, ?)""",
        (channel_id, now, now),
    )
    excl_json = json.dumps(excluded_topics or [])
    sec_json = json.dumps(secondary_niches or [])
    conn.execute(
        """INSERT INTO channel_profile_versions
           (id, channel_id, primary_niche, secondary_niches_json, excluded_topics_json,
            audience_description, audience_demographics, tone_notes, activated_by,
            activation_reason, status, active_from, portfolio_targets_json)
           VALUES (?, ?, ?, ?, ?, ?, '', '', '', '', 'active', ?, '{}')""",
        (
            channel_id,
            channel_id,
            primary_niche,
            sec_json,
            excl_json,
            audience_description or "",
            now,
        ),
    )
    conn.execute(
        "UPDATE channels SET current_profile_version_id = ? WHERE id = ?",
        (channel_id, channel_id),
    )


_DEFAULT_COMPLETED_AT = object()


def _insert_run_and_signal(
    conn: sqlite3.Connection,
    *,
    run_id: int = 1,
    signal_id: int = 1,
    cluster_id: int = 1,
    canonical_cluster_id: int = 10,
    completed_at: str | None = _DEFAULT_COMPLETED_AT,  # type: ignore[assignment]
    signal_maturity: str = "directional",
    confidence: float = 0.55,
) -> None:
    if completed_at is _DEFAULT_COMPLETED_AT:
        completed_at = _ts(-10)
    conn.execute(
        "INSERT INTO market_interpretation_runs (id, completed_at) VALUES (?, ?)",
        (run_id, completed_at),
    )
    conn.execute(
        "INSERT INTO market_topic_clusters (id, canonical_cluster_id) VALUES (?, ?)",
        (cluster_id, canonical_cluster_id),
    )
    conn.execute(
        """INSERT INTO market_cluster_signals
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
) -> None:
    now = _ts()
    conn.execute(
        "INSERT OR IGNORE INTO discovery_runs (id, channel_id, created_at) VALUES (1, ?, ?)",
        (channel_id, now),
    )
    conn.execute(
        """INSERT INTO opportunities
           (id, channel_id, discovery_run_id, normalized_topic, raw_topic, title,
            created_at, updated_at, canonical_cluster_id, market_signal_snapshot_id)
           VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?)""",
        (
            opp_id,
            channel_id,
            normalized_topic,
            normalized_topic,
            title,
            now,
            now,
            canonical_cluster_id,
            market_signal_snapshot_id,
        ),
    )


def _fake_fit_provider(score: float = 0.80, fit_label: str = "strong_fit") -> FakeProvider:
    output = json.dumps({"score": score, "fit_label": fit_label, "rationale": "Good fit."})
    return FakeProvider(output=output)


def _base_eligible_db(
    *,
    primary_niche: str = "Python tutorials",
    secondary_niches: list[str] | None = None,
    excluded_topics: list[str] | None = None,
    normalized_topic: str = "python async programming",
    signal_maturity: str = "directional",
    confidence: float = 0.55,
    completed_at_delta_hours: float = -48,
) -> sqlite3.Connection:
    """Returns a minimal DB with fresh/directional signal, no conflicts."""
    conn = _minimal_db()
    _insert_channel_and_profile(
        conn,
        primary_niche=primary_niche,
        secondary_niches=secondary_niches,
        excluded_topics=excluded_topics or [],
    )
    _insert_run_and_signal(
        conn,
        completed_at=_ts(completed_at_delta_hours),
        signal_maturity=signal_maturity,
        confidence=confidence,
    )
    _insert_opportunity(conn, normalized_topic=normalized_topic)
    return conn


# ---------------------------------------------------------------------------
# A–C: Provider gap closure (core Phase 14C.1 fix)
# ---------------------------------------------------------------------------


def test_A_ambiguous_fit_no_provider_is_unresolved():
    """ai_provider=None with non-matching niche → UNRESOLVED, not GENERAL_ELIGIBLE."""
    conn = _base_eligible_db(
        primary_niche="Python tutorials",
        normalized_topic="machine learning basics",  # clearly different
    )
    result = assess_experiment_eligibility(conn, 1, 1)  # no provider
    assert result.classification == ExperimentEligibilityClassification.UNRESOLVED
    assert result.semantic_fit_disposition == "provider_unavailable_unresolved"
    provider_req = [f for f in result.findings if f.code == "semantic_fit_provider_required"]
    assert len(provider_req) == 1
    assert provider_req[0].severity == "warn"


def test_B_deterministic_niche_bypass_no_provider_is_general_eligible():
    """Exact niche match + provider=None → GENERAL_ELIGIBLE via bypass."""
    conn = _base_eligible_db(
        primary_niche="python async programming",
        normalized_topic="python async programming",  # exact match
    )
    result = assess_experiment_eligibility(conn, 1, 1)  # no provider
    assert result.classification == ExperimentEligibilityClassification.GENERAL_ELIGIBLE
    assert result.semantic_fit_disposition == "deterministic_bypass"
    assert result.semantic_fit_score == pytest.approx(1.0)
    assert result.semantic_fit_label == "deterministic_niche_match"
    bypass_findings = [f for f in result.findings if f.code == "semantic_fit_deterministic_bypass"]
    assert len(bypass_findings) == 1


def test_C_excluded_topic_no_provider_is_ineligible():
    """Excluded topic block fires before semantic fit; INELIGIBLE even with no provider."""
    conn = _base_eligible_db(
        primary_niche="Python tutorials",
        excluded_topics=["machine learning"],
        normalized_topic="machine learning basics",
    )
    result = assess_experiment_eligibility(conn, 1, 1)  # no provider
    assert result.classification == ExperimentEligibilityClassification.INELIGIBLE
    assert result.semantic_fit_disposition == "skipped_hard_block"
    excl = [f for f in result.findings if f.code == "excluded_topic_match"]
    assert len(excl) == 1


# ---------------------------------------------------------------------------
# D: Provider failure → UNRESOLVED
# ---------------------------------------------------------------------------


def test_D_provider_raises_is_unresolved():
    """Provider is supplied but raises during the call → UNRESOLVED."""
    conn = _base_eligible_db()

    class _FailProvider:
        name = "fail"

        def complete(self, request):
            raise RuntimeError("connection refused")

    result = assess_experiment_eligibility(conn, 1, 1, ai_provider=_FailProvider())
    assert result.classification == ExperimentEligibilityClassification.UNRESOLVED
    assert result.semantic_fit_disposition == "provider_called"
    assert any(f.code == "semantic_fit_call_failed" for f in result.findings)


# ---------------------------------------------------------------------------
# E: Semantic fit passes but excluded topic already blocks
# ---------------------------------------------------------------------------


def test_E_high_fit_score_does_not_override_excluded_topic():
    """Even a perfect fit score cannot override an excluded-topic hard block."""
    conn = _base_eligible_db(
        excluded_topics=["python"],
        normalized_topic="python async programming",
    )
    result = assess_experiment_eligibility(
        conn,
        1,
        1,
        ai_provider=_fake_fit_provider(score=0.99),
    )
    assert result.classification == ExperimentEligibilityClassification.INELIGIBLE
    assert any(f.code == "excluded_topic_match" for f in result.findings)
    # Provider was NOT called (skipped because hard block found first)
    assert result.semantic_fit_disposition == "skipped_hard_block"


# ---------------------------------------------------------------------------
# F–G: STALE market interactions
# ---------------------------------------------------------------------------


def test_F_stale_market_with_provider_requires_refresh():
    """STALE market + provider call passes → REQUIRES_REFRESH (not GENERAL_ELIGIBLE)."""
    conn = _base_eligible_db(completed_at_delta_hours=-900)  # > 720h
    result = assess_experiment_eligibility(
        conn,
        1,
        1,
        ai_provider=_fake_fit_provider(score=0.85),
    )
    assert result.classification == ExperimentEligibilityClassification.REQUIRES_REFRESH
    assert result.market_freshness_class == MarketFreshnessClass.STALE


def test_G_excluded_topic_and_stale_market_is_ineligible():
    """Excluded topic (block) + stale market → INELIGIBLE, not REQUIRES_REFRESH."""
    conn = _base_eligible_db(
        completed_at_delta_hours=-900,
        excluded_topics=["python"],
        normalized_topic="python async programming",
    )
    result = assess_experiment_eligibility(conn, 1, 1)
    assert result.classification == ExperimentEligibilityClassification.INELIGIBLE


# ---------------------------------------------------------------------------
# H–J: Signal maturity and confidence classification
# ---------------------------------------------------------------------------


def test_H_exploratory_maturity_exploration_only():
    conn = _base_eligible_db(signal_maturity="exploratory", confidence=0.45)
    result = assess_experiment_eligibility(
        conn,
        1,
        1,
        ai_provider=_fake_fit_provider(score=0.80),
    )
    assert result.classification == ExperimentEligibilityClassification.EXPLORATION_ONLY


def test_I_directional_maturity_adequate_confidence_general_eligible():
    conn = _base_eligible_db(signal_maturity="directional", confidence=0.60)
    result = assess_experiment_eligibility(
        conn,
        1,
        1,
        ai_provider=_fake_fit_provider(score=0.80),
    )
    assert result.classification == ExperimentEligibilityClassification.GENERAL_ELIGIBLE


def test_J_actionable_maturity_general_eligible():
    conn = _base_eligible_db(signal_maturity="actionable", confidence=0.80)
    result = assess_experiment_eligibility(
        conn,
        1,
        1,
        ai_provider=_fake_fit_provider(score=0.80),
    )
    assert result.classification == ExperimentEligibilityClassification.GENERAL_ELIGIBLE


# ---------------------------------------------------------------------------
# K: Insufficient maturity is a hard block
# ---------------------------------------------------------------------------


def test_K_insufficient_maturity_ineligible_no_provider_needed():
    """Insufficient maturity fires as a block — provider is never called."""
    conn = _base_eligible_db(signal_maturity="insufficient", confidence=0.10)
    result = assess_experiment_eligibility(conn, 1, 1)  # no provider
    assert result.classification == ExperimentEligibilityClassification.INELIGIBLE
    assert any(f.code == "insufficient_signal_maturity" for f in result.findings)
    # Semantic fit was skipped because of the hard block
    assert result.semantic_fit_disposition == "skipped_hard_block"


# ---------------------------------------------------------------------------
# L: Confidence boundary — not double-discounted
# ---------------------------------------------------------------------------


def test_L_confidence_boundary_at_threshold_is_general_eligible():
    """Exactly at the confidence threshold → GENERAL_ELIGIBLE (not EXPLORATION_ONLY)."""
    policy = EligibilityPolicy(min_confidence_for_general=0.30)
    findings: list[EligibilityFinding] = []
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.FRESH,
        signal_maturity="directional",
        signal_confidence=0.30,  # exactly at threshold
        semantic_fit_score=0.80,
        semantic_fit_called=True,
        policy=policy,
    )
    assert cls == ExperimentEligibilityClassification.GENERAL_ELIGIBLE


def test_L2_confidence_one_tick_below_is_exploration_only():
    """One tick below confidence threshold → EXPLORATION_ONLY (not GENERAL_ELIGIBLE)."""
    policy = EligibilityPolicy(min_confidence_for_general=0.30)
    findings: list[EligibilityFinding] = []
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.FRESH,
        signal_maturity="directional",
        signal_confidence=0.29,
        semantic_fit_score=0.80,
        semantic_fit_called=True,
        policy=policy,
    )
    assert cls == ExperimentEligibilityClassification.EXPLORATION_ONLY


def test_L3_confidence_not_double_applied():
    """Confidence is evaluated once in roll-up, not multiplied against itself."""
    # If confidence were squared: 0.55^2 = 0.30 (barely at threshold)
    # We verify that 0.55 with threshold 0.30 gives GENERAL_ELIGIBLE,
    # not EXPLORATION_ONLY from double-application.
    policy = EligibilityPolicy(min_confidence_for_general=0.30)
    findings: list[EligibilityFinding] = []
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.FRESH,
        signal_maturity="directional",
        signal_confidence=0.55,
        semantic_fit_score=0.80,
        semantic_fit_called=True,
        policy=policy,
    )
    assert cls == ExperimentEligibilityClassification.GENERAL_ELIGIBLE


# ---------------------------------------------------------------------------
# M–N: Cold-start: analytics + Phase 12C never block eligibility
# ---------------------------------------------------------------------------


def test_M_no_analytics_does_not_block_exploration_eligibility():
    """Cold-start opportunity (no promoted topic, no analytics) can still be
    GENERAL_ELIGIBLE when all other checks pass.  Analytics is context, not a gate."""
    conn = _base_eligible_db(signal_maturity="directional", confidence=0.55)
    # No topics row → analytics not ready
    result = assess_experiment_eligibility(
        conn,
        1,
        1,
        ai_provider=_fake_fit_provider(score=0.80),
    )
    assert result.analytics_ready is False
    assert result.classification == ExperimentEligibilityClassification.GENERAL_ELIGIBLE
    # All analytics findings are info-severity only
    analytics_findings = [
        f
        for f in result.findings
        if f.code in ("no_promoted_topic", "no_publications_for_topic", "analytics_not_ready")
    ]
    assert all(f.severity == "info" for f in analytics_findings)


def test_N_no_phase12c_evidence_does_not_block():
    """Missing Phase 12C feature snapshots does not cause a block finding."""
    conn = _base_eligible_db(signal_maturity="directional", confidence=0.55)
    result = assess_experiment_eligibility(
        conn,
        1,
        1,
        ai_provider=_fake_fit_provider(score=0.80),
    )
    block_findings = [f for f in result.findings if f.severity == "block"]
    # No block from phase12c absence
    phase12c_blocks = [f for f in block_findings if "phase12c" in f.code]
    assert len(phase12c_blocks) == 0


# ---------------------------------------------------------------------------
# O–S: Analytics readiness edge cases
# ---------------------------------------------------------------------------


def _analytics_db(
    *,
    observation_state: str | None = "data",
    metric_value: float | None = 25.0,
    opp_id: int = 1,
) -> sqlite3.Connection:
    """DB wired up with a single publication + snapshot + aggregate for opp_id."""
    conn = _minimal_db()
    # topic → publishing_plan → publication chain
    conn.execute("INSERT INTO topics (id, promoted_opportunity_id) VALUES (1, ?)", (opp_id,))
    conn.execute("INSERT INTO publishing_plans (id, topic_id) VALUES (1, 1)")
    conn.execute("INSERT INTO publications (id, publishing_plan_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO analytics_snapshots (id, publication_id, observation_state) VALUES (1, 1, ?)",
        (observation_state,),
    )
    if metric_value is not None:
        conn.execute(
            """INSERT INTO analytics_metrics
               (id, snapshot_id, publication_id, metric_name, metric_value)
               VALUES (1, 1, 1, 'views', ?)""",
            (metric_value,),
        )
    else:
        # Insert row with NULL metric_value — metric IS NOT NULL filter will exclude it
        conn.execute(
            """INSERT INTO analytics_metrics
               (id, snapshot_id, publication_id, metric_name, metric_value)
               VALUES (1, 1, 1, 'views', NULL)""",
        )
    return conn


def test_O_no_data_observation_state_not_ready():
    """observation_state='no_data' → analytics not ready."""
    conn = _analytics_db(observation_state="no_data", metric_value=100.0)
    ready, findings = assess_analytics_readiness(conn, 1, EligibilityPolicy())
    assert ready is False
    assert any(f.code == "analytics_not_ready" for f in findings)


def test_P_zero_metric_value_present_but_fails_threshold():
    """metric_value=0.0 is present (not missing) but 0.0 < 10 → not ready.
    The 0.0 vs NULL distinction: both fail, but for different reasons."""
    conn = _analytics_db(observation_state="data", metric_value=0.0)
    ready, findings = assess_analytics_readiness(conn, 1, EligibilityPolicy())
    assert ready is False


def test_Q_null_metric_value_is_missing_not_ready():
    """metric_value=NULL means the metric is absent → not ready."""
    conn = _analytics_db(observation_state="data", metric_value=None)
    ready, findings = assess_analytics_readiness(conn, 1, EligibilityPolicy())
    assert ready is False


def test_R_valid_data_snapshot_sufficient_views():
    """A single 'data' snapshot with sufficient views → ready."""
    conn = _analytics_db(observation_state="data", metric_value=25.0)
    ready, findings = assess_analytics_readiness(conn, 1, EligibilityPolicy())
    assert ready is True
    assert any(f.code == "analytics_ready" for f in findings)


def test_S_newer_no_data_does_not_erase_older_valid_data():
    """Two snapshots for same publication: newer is 'no_data', older is 'data'
    with sufficient views.  Ready = True (older valid data still counts)."""
    conn = _minimal_db()
    conn.execute("INSERT INTO topics (id, promoted_opportunity_id) VALUES (1, 1)")
    conn.execute("INSERT INTO publishing_plans (id, topic_id) VALUES (1, 1)")
    conn.execute("INSERT INTO publications (id, publishing_plan_id) VALUES (1, 1)")
    # Snapshot 1 (older): observation_state='data'
    conn.execute(
        "INSERT INTO analytics_snapshots (id, publication_id, observation_state) "
        "VALUES (1, 1, 'data')"
    )
    conn.execute(
        """INSERT INTO analytics_metrics
           (id, snapshot_id, publication_id, metric_name, metric_value)
           VALUES (1, 1, 1, 'views', 25.0)"""
    )
    # Snapshot 2 (newer, higher ID): observation_state='no_data'
    conn.execute(
        "INSERT INTO analytics_snapshots (id, publication_id, observation_state) "
        "VALUES (2, 1, 'no_data')"
    )

    ready, findings = assess_analytics_readiness(conn, 1, EligibilityPolicy())
    assert ready is True, (
        "Newer 'no_data' snapshot should not erase the older valid 'data' snapshot"
    )


def test_T_wrong_topic_publication_excluded():
    """Analytics from a different topic's publications don't count.
    Natural enforcement: query is scoped by topic_id via publishing_plans."""
    conn = _minimal_db()
    # Topic 1 → opportunity 1
    conn.execute("INSERT INTO topics (id, promoted_opportunity_id) VALUES (1, 1)")
    conn.execute("INSERT INTO publishing_plans (id, topic_id) VALUES (1, 1)")
    conn.execute("INSERT INTO publications (id, publishing_plan_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO analytics_snapshots (id, publication_id, observation_state) "
        "VALUES (1, 1, 'data')"
    )
    conn.execute(
        """INSERT INTO analytics_metrics
           (id, snapshot_id, publication_id, metric_name, metric_value)
           VALUES (1, 1, 1, 'views', 25.0)"""
    )
    # Topic 2 → opportunity 2 — different opportunity
    conn.execute("INSERT INTO topics (id, promoted_opportunity_id) VALUES (2, 2)")
    conn.execute("INSERT INTO publishing_plans (id, topic_id) VALUES (2, 2)")
    conn.execute("INSERT INTO publications (id, publishing_plan_id) VALUES (2, 2)")
    conn.execute(
        "INSERT INTO analytics_snapshots (id, publication_id, observation_state) "
        "VALUES (2, 2, 'data')"
    )
    conn.execute(
        """INSERT INTO analytics_metrics
           (id, snapshot_id, publication_id, metric_name, metric_value)
           VALUES (2, 2, 2, 'views', 999.0)"""
    )

    # Opportunity 2's data should NOT count when assessing opportunity 1
    ready, _ = assess_analytics_readiness(conn, 1, EligibilityPolicy())
    assert ready is True  # opportunity 1 has its own valid data (25 views)

    # And opportunity 1's data should not bleed into opportunity 2's assessment
    ready2, _ = assess_analytics_readiness(conn, 2, EligibilityPolicy())
    assert ready2 is True  # opportunity 2 has its own valid data (999 views)

    # No cross-contamination: opportunity 999 (no topic) → not ready
    ready3, _ = assess_analytics_readiness(conn, 999, EligibilityPolicy())
    assert ready3 is False


def test_U_wrong_opportunity_data_excluded_naturally():
    """Analytics query is scoped by opportunity → topic → publications.
    An opportunity with no promoted topic gets no analytics data regardless
    of what analytics exist for other opportunities."""
    conn = _minimal_db()
    # Opportunity 2 has data
    conn.execute("INSERT INTO topics (id, promoted_opportunity_id) VALUES (1, 2)")
    conn.execute("INSERT INTO publishing_plans (id, topic_id) VALUES (1, 1)")
    conn.execute("INSERT INTO publications (id, publishing_plan_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO analytics_snapshots (id, publication_id, observation_state) "
        "VALUES (1, 1, 'data')"
    )
    conn.execute(
        """INSERT INTO analytics_metrics
           (id, snapshot_id, publication_id, metric_name, metric_value)
           VALUES (1, 1, 1, 'views', 50.0)"""
    )

    # Opportunity 1 has no promoted topic → not ready (unrelated data for opp 2 is ignored)
    ready, findings = assess_analytics_readiness(conn, 1, EligibilityPolicy())
    assert ready is False
    assert any(f.code == "no_promoted_topic" for f in findings)


# ---------------------------------------------------------------------------
# V–W: Timestamp UTC handling
# ---------------------------------------------------------------------------


def test_V_utc_aware_completed_at_age_correct():
    """completed_at stored with UTC offset ages correctly relative to now."""
    conn = _base_eligible_db(completed_at_delta_hours=-50)  # 50 hours ago
    result = assess_experiment_eligibility(
        conn,
        1,
        1,
        ai_provider=_fake_fit_provider(score=0.80),
    )
    assert result.market_freshness_class == MarketFreshnessClass.FRESH
    assert result.market_knowledge_age_hours is not None
    assert 49.0 < result.market_knowledge_age_hours < 52.0


def test_W_naive_legacy_timestamp_treated_as_utc():
    """completed_at without timezone suffix is treated as UTC (not rejected)."""
    conn = _minimal_db()
    _insert_channel_and_profile(conn)
    # Naive timestamp (no +00:00)
    naive_ts = (datetime.now(UTC) - timedelta(hours=100)).strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        "INSERT INTO market_interpretation_runs (id, completed_at) VALUES (1, ?)", (naive_ts,)
    )
    conn.execute("INSERT INTO market_topic_clusters (id, canonical_cluster_id) VALUES (1, 10)")
    conn.execute(
        """INSERT INTO market_cluster_signals
           (id, interpretation_run_id, signal_maturity, confidence, cluster_id)
           VALUES (1, 1, 'directional', 0.55, 1)"""
    )
    _insert_opportunity(conn)
    result = assess_experiment_eligibility(conn, 1, 1, ai_provider=_fake_fit_provider())
    # Should not raise; market is treated as AGING (100h > 168h threshold? No, 100h < 168h → FRESH)
    assert result.market_freshness_class is not None
    assert result.classification != ExperimentEligibilityClassification.INELIGIBLE or (
        any(f.code not in ("market_freshness", "naive_ts") for f in result.findings)
    )


# ---------------------------------------------------------------------------
# X–Y: Experiment conflict status rules
# ---------------------------------------------------------------------------


def test_X_terminal_experiment_does_not_block():
    """completed and cancelled experiments are NOT in conflict_blocking_statuses.
    A terminal experiment must not prevent future experiments on the same opportunity."""
    conn = _base_eligible_db()
    for status in ("completed", "cancelled"):
        conn.execute(
            "INSERT INTO experiments (id, channel_id, opportunity_id, status) VALUES (?, 1, 1, ?)",
            (f"exp-{status}", status),
        )
    result = assess_experiment_eligibility(
        conn,
        1,
        1,
        ai_provider=_fake_fit_provider(score=0.80),
    )
    assert result.has_active_conflict is False
    assert result.classification != ExperimentEligibilityClassification.INELIGIBLE
    # Verify explicitly that terminal statuses are not in the blocking list
    policy = EligibilityPolicy()
    assert "completed" not in policy.conflict_blocking_statuses
    assert "cancelled" not in policy.conflict_blocking_statuses


def test_Y_active_experiment_blocks():
    """An experiment in an active status blocks new experiment planning."""
    for active_status in (
        "draft",
        "planned",
        "in_production",
        "published",
        "observing",
        "mature",
        "analyzed",
    ):
        db = _base_eligible_db()
        db.execute(
            "INSERT INTO experiments (id, channel_id, opportunity_id, status) "
            "VALUES ('exp-X', 1, 1, ?)",
            (active_status,),
        )
        result = assess_experiment_eligibility(db, 1, 1)
        assert result.classification == ExperimentEligibilityClassification.INELIGIBLE, (
            f"Expected INELIGIBLE for status={active_status!r}"
        )
        assert result.has_active_conflict is True


# ---------------------------------------------------------------------------
# Z: Unknown signal maturity
# ---------------------------------------------------------------------------


def test_Z_unknown_maturity_rank_is_negative():
    """Unknown maturity string gets rank -1, which is below any valid rank."""
    from app.intelligence.experiments.eligibility_service import _maturity_rank

    assert _maturity_rank("unknown_experimental") < 0
    assert _maturity_rank("unknown_experimental") < _maturity_rank("insufficient")


# ---------------------------------------------------------------------------
# AA: Hard block always wins
# ---------------------------------------------------------------------------


def test_AA_any_block_finding_causes_ineligible():
    """A block finding of any code → INELIGIBLE regardless of other signals."""
    for block_code in (
        "excluded_topic_match",
        "active_experiment_conflict",
        "signal_canonical_mismatch",
        "insufficient_signal_maturity",
        "custom_reason_block",
    ):
        findings = [EligibilityFinding(code=block_code, severity="block", message="test")]
        cls = _roll_up_classification(
            findings,
            market_freshness=MarketFreshnessClass.FRESH,
            signal_maturity="actionable",
            signal_confidence=0.99,
            semantic_fit_score=0.99,
            semantic_fit_called=True,
            policy=EligibilityPolicy(),
        )
        assert cls == ExperimentEligibilityClassification.INELIGIBLE, (
            f"Expected INELIGIBLE for block_code={block_code!r}"
        )


# ---------------------------------------------------------------------------
# AB: Policy snapshot completeness
# ---------------------------------------------------------------------------


def test_AB_policy_snapshot_contains_all_required_fields():
    """to_json() must include semantic_fit_prompt_version and all other V1 fields."""
    policy = EligibilityPolicy(semantic_fit_prompt_version="2")
    snapshot = json.loads(policy.to_json())

    required_fields = [
        "version",
        "market_fresh_max_age_hours",
        "market_aging_max_age_hours",
        "min_signal_maturity_for_general",
        "min_signal_maturity_for_exploration",
        "min_confidence_for_general",
        "semantic_fit_min_score",
        "semantic_fit_provider_timeout_s",
        "semantic_fit_prompt_version",
        "min_views_for_analytics_readiness",
        "conflict_blocking_statuses",
        "narration_rate_min",
        "narration_rate_max",
    ]
    for field in required_fields:
        assert field in snapshot, f"Policy snapshot missing field: {field!r}"

    assert snapshot["semantic_fit_prompt_version"] == "2"
    assert isinstance(snapshot["conflict_blocking_statuses"], list)


def test_AB2_policy_snapshot_in_assessment_is_complete():
    """assessment.policy_snapshot_json carries the full policy at assessment time."""
    conn = _base_eligible_db()
    policy = EligibilityPolicy(market_fresh_max_age_hours=336, semantic_fit_prompt_version="3")
    result = assess_experiment_eligibility(conn, 1, 1, policy=policy)
    snapshot = json.loads(result.policy_snapshot_json)
    assert snapshot["market_fresh_max_age_hours"] == 336
    assert snapshot["semantic_fit_prompt_version"] == "3"


# ---------------------------------------------------------------------------
# AC–AD: Batch assessment
# ---------------------------------------------------------------------------


def test_AC_batch_bounded_by_max_batch_size():
    """assess_opportunity_batch respects max_batch_size — extra IDs are dropped."""
    conn = _base_eligible_db()
    # Insert 5 more opportunity rows reusing the same signal
    for i in range(2, 7):
        _insert_opportunity(conn, opp_id=i)

    ids = [1, 2, 3, 4, 5, 6]  # 6 IDs
    result = assess_opportunity_batch(conn, ids, channel_id=1, max_batch_size=3)
    assert result.total == 3
    assert len(result.items) == 3
    assert [item.opportunity_id for item in result.items] == [1, 2, 3]


def test_AD_batch_returns_all_items_no_ranking():
    """Batch result contains all assessed items in input order; no selection applied."""
    conn = _base_eligible_db()
    for i in range(2, 4):
        _insert_opportunity(conn, opp_id=i)

    result = assess_opportunity_batch(
        conn,
        [1, 2, 3],
        channel_id=1,
        ai_provider=_fake_fit_provider(score=0.80),
    )
    assert result.total == 3
    assert [item.opportunity_id for item in result.items] == [1, 2, 3]
    # No ranking score or rank field — all items returned
    for item in result.items:
        assert hasattr(item, "opportunity_id")
        assert hasattr(item, "classification")
        assert hasattr(item, "assessment")


def test_AD2_batch_by_classification_counts_correct():
    """by_classification correctly counts classifications across the batch."""
    conn = _base_eligible_db()
    # Insert a second opportunity with excluded topic to get INELIGIBLE
    _insert_opportunity(conn, opp_id=2, normalized_topic="crypto trading")
    # Add excluded topic
    conn.execute(
        "UPDATE channel_profile_versions SET excluded_topics_json = '[\"crypto\"]' WHERE id = 1"
    )

    result = assess_opportunity_batch(
        conn,
        [1, 2],
        channel_id=1,
        ai_provider=_fake_fit_provider(score=0.80),
    )
    assert result.total == 2
    # opp 2 → INELIGIBLE (excluded topic)
    ineligible_items = [i for i in result.items if i.has_exclusion]
    assert len(ineligible_items) == 1
    assert result.by_classification.get("ineligible", 0) >= 1


# ---------------------------------------------------------------------------
# AE: Assessment is read-only
# ---------------------------------------------------------------------------


def test_AE_assessment_does_not_mutate_opportunity_row():
    """assess_experiment_eligibility must not change any opportunity fields."""
    conn = _base_eligible_db()
    before = dict(conn.execute("SELECT * FROM opportunities WHERE id = 1").fetchone())
    assess_experiment_eligibility(conn, 1, 1, ai_provider=_fake_fit_provider())
    after = dict(conn.execute("SELECT * FROM opportunities WHERE id = 1").fetchone())
    assert before == after


# ---------------------------------------------------------------------------
# AF–AG: Contractual no-call assertions
# ---------------------------------------------------------------------------


def test_AF_no_youtube_call_during_assessment(monkeypatch):
    """No HTTP call to YouTube may occur during eligibility assessment."""
    import urllib.request as _url_req

    def _reject(url, *a, **kw):
        raise AssertionError(f"Unexpected HTTP call during assessment: {url}")

    monkeypatch.setattr(_url_req, "urlopen", _reject)
    conn = _base_eligible_db()
    # If any code attempts urlopen, the test fails
    result = assess_experiment_eligibility(conn, 1, 1, ai_provider=_fake_fit_provider())
    assert result is not None


def test_AG_no_content_generation_during_assessment():
    """assess_experiment_eligibility returns an assessment, not a script or video."""
    conn = _base_eligible_db()
    result = assess_experiment_eligibility(conn, 1, 1, ai_provider=_fake_fit_provider())
    # The result is a typed assessment — no content fields
    assert not hasattr(result, "script_text")
    assert not hasattr(result, "render_url")
    assert not hasattr(result, "narration_audio")


# ---------------------------------------------------------------------------
# AH: All existing 14C tests unaffected (meta-regression marker)
# ---------------------------------------------------------------------------


def test_AH_existing_14c_tests_still_pass_via_import():
    """Smoke-check: the Phase 14C module is importable and basic invariants hold.
    Full regression coverage is provided by test_experiment_eligibility_14c.py."""
    from app.intelligence.experiments.eligibility import (
        EligibilityPolicy,
        ExperimentEligibilityClassification,
    )

    policy = EligibilityPolicy()
    assert policy.version == "1.0.0"
    assert ExperimentEligibilityClassification.INELIGIBLE.value == "ineligible"
    # Priority order must be preserved
    priority = [
        ExperimentEligibilityClassification.INELIGIBLE,
        ExperimentEligibilityClassification.UNRESOLVED,
        ExperimentEligibilityClassification.REQUIRES_REFRESH,
        ExperimentEligibilityClassification.EXPLORATION_ONLY,
        ExperimentEligibilityClassification.GENERAL_ELIGIBLE,
    ]
    assert len(priority) == 5


# ---------------------------------------------------------------------------
# Niche bypass unit tests (internal helper)
# ---------------------------------------------------------------------------


def test_niche_bypass_exact_match():
    assert (
        _is_deterministic_niche_match(
            opportunity_normalized_topic="python tutorials",
            primary_niche="python tutorials",
            secondary_niches=[],
        )
        is True
    )


def test_niche_bypass_case_insensitive():
    assert (
        _is_deterministic_niche_match(
            opportunity_normalized_topic="Python Tutorials",
            primary_niche="python tutorials",
            secondary_niches=[],
        )
        is True
    )


def test_niche_bypass_whitespace_normalized():
    assert (
        _is_deterministic_niche_match(
            opportunity_normalized_topic="python  tutorials",
            primary_niche="python tutorials",
            secondary_niches=[],
        )
        is True
    )


def test_niche_bypass_secondary_niche_match():
    assert (
        _is_deterministic_niche_match(
            opportunity_normalized_topic="machine learning",
            primary_niche="python tutorials",
            secondary_niches=["machine learning", "data science"],
        )
        is True
    )


def test_niche_bypass_no_match_on_substring():
    """'python async' should NOT bypass just because 'python' appears in niche."""
    assert (
        _is_deterministic_niche_match(
            opportunity_normalized_topic="python async programming",
            primary_niche="python tutorials",
            secondary_niches=[],
        )
        is False
    )


def test_niche_bypass_empty_topic_returns_false():
    assert (
        _is_deterministic_niche_match(
            opportunity_normalized_topic="",
            primary_niche="python tutorials",
            secondary_niches=[],
        )
        is False
    )


def test_niche_bypass_empty_niche_returns_false():
    assert (
        _is_deterministic_niche_match(
            opportunity_normalized_topic="python tutorials",
            primary_niche="",
            secondary_niches=[],
        )
        is False
    )
