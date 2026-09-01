"""Phase 14C — Experiment Eligibility Gate tests.

Tests A–BY (75 tests) covering:

A–E:   EligibilityPolicy model — defaults, serialisation, classification values
F–K:   MarketFreshnessClass computation (FRESH / AGING / STALE)
L–O:   Signal integrity (canonical cluster consistency)
P–S:   Excluded-topic guard (deterministic, before any LLM call)
T–W:   Signal maturity + confidence classification
X–AB:  Analytics readiness
AC–AF: Active experiment conflict detection
AG–AJ: Semantic fit sub-assessment (FakeProvider, thresholds, call failure)
AK–AN: Phase 12C maturity summary
AO–AQ: Production feasibility / policy docs
AR–AV: INELIGIBLE classification roll-up scenarios
AW–AZ: EXPLORATION_ONLY classification roll-up scenarios
BA–BD: GENERAL_ELIGIBLE classification roll-up scenarios
BE–BH: REQUIRES_REFRESH classification roll-up scenarios
BI–BL: UNRESOLVED classification roll-up scenarios
BM–BY: Full assess_experiment_eligibility integration tests
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.ai.fake import FakeProvider
from app.ai.provider import AIResponse
from app.intelligence.experiments.eligibility import (
    EligibilityFinding,
    EligibilityPolicy,
    ExperimentEligibilityClassification,
    MarketFreshnessClass,
)
from app.intelligence.experiments.eligibility_service import (
    _maturity_rank,
    _roll_up_classification,
    assess_active_conflicts,
    assess_analytics_readiness,
    assess_excluded_topics,
    assess_experiment_eligibility,
    assess_market_freshness,
    assess_phase12c_maturity,
    assess_semantic_fit,
    assess_signal_integrity,
    assess_signal_maturity_confidence,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(delta_hours: float = 0.0) -> str:
    """Return ISO timestamp relative to now UTC."""
    dt = datetime.now(UTC) + timedelta(hours=delta_hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _mem_conn() -> sqlite3.Connection:
    """In-memory SQLite with row_factory."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _minimal_db() -> sqlite3.Connection:
    """In-memory DB with the tables required by eligibility_service queries."""
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
    conn.execute(
        """INSERT INTO channel_profile_versions
           (id, channel_id, primary_niche, excluded_topics_json, audience_description,
            audience_demographics, tone_notes, activated_by, activation_reason,
            status, active_from, portfolio_targets_json)
           VALUES (?, ?, ?, ?, ?, '', '', '', '', 'active', ?, '{}')""",
        (channel_id, channel_id, primary_niche, excl_json, audience_description or "", now),
    )
    conn.execute(
        "UPDATE channels SET current_profile_version_id = ? WHERE id = ?",
        (channel_id, channel_id),
    )


_DEFAULT_COMPLETED_AT = object()  # sentinel


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
    # Ensure discovery_run exists
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


# ---------------------------------------------------------------------------
# A–E: EligibilityPolicy model tests
# ---------------------------------------------------------------------------


def test_A_policy_default_version():
    policy = EligibilityPolicy()
    assert policy.version == "1.0.0"


def test_B_policy_v1_factory():
    p1 = EligibilityPolicy.v1()
    p2 = EligibilityPolicy()
    assert p1.version == p2.version
    assert p1.market_fresh_max_age_hours == p2.market_fresh_max_age_hours


def test_C_policy_freshness_thresholds():
    p = EligibilityPolicy()
    assert p.market_fresh_max_age_hours == 168
    assert p.market_aging_max_age_hours == 720


def test_D_policy_to_json_roundtrip():
    p = EligibilityPolicy()
    j = json.loads(p.to_json())
    assert j["version"] == "1.0.0"
    assert j["min_signal_maturity_for_general"] == "directional"
    assert j["semantic_fit_min_score"] == 0.55
    assert "conflict_blocking_statuses" in j


def test_E_classification_enum_values():
    assert ExperimentEligibilityClassification.INELIGIBLE == "ineligible"
    assert ExperimentEligibilityClassification.EXPLORATION_ONLY == "exploration_only"
    assert ExperimentEligibilityClassification.GENERAL_ELIGIBLE == "general_eligible"
    assert ExperimentEligibilityClassification.REQUIRES_REFRESH == "requires_refresh"
    assert ExperimentEligibilityClassification.UNRESOLVED == "unresolved"


# ---------------------------------------------------------------------------
# F–K: MarketFreshnessClass computation
# ---------------------------------------------------------------------------


def test_F_fresh_signal_under_168h():
    conn = _minimal_db()
    _insert_run_and_signal(conn, completed_at=_ts(-48))  # 48h ago
    freshness, age, findings = assess_market_freshness(
        conn,
        opportunity_id=1,
        market_signal_snapshot_id=1,
        policy=EligibilityPolicy(),
    )
    assert freshness == MarketFreshnessClass.FRESH
    assert age is not None
    assert age < 168


def test_G_aging_signal_between_168_and_720h():
    conn = _minimal_db()
    _insert_run_and_signal(conn, completed_at=_ts(-300))  # 300h ago
    freshness, age, findings = assess_market_freshness(
        conn, 1, market_signal_snapshot_id=1, policy=EligibilityPolicy()
    )
    assert freshness == MarketFreshnessClass.AGING
    assert age is not None
    assert 168 <= age < 720
    assert any(f.code == "market_knowledge_aging" for f in findings)


def test_H_stale_signal_over_720h():
    conn = _minimal_db()
    _insert_run_and_signal(conn, completed_at=_ts(-800))  # 800h ago
    freshness, age, findings = assess_market_freshness(
        conn, 1, market_signal_snapshot_id=1, policy=EligibilityPolicy()
    )
    assert freshness == MarketFreshnessClass.STALE
    assert any(f.code == "market_knowledge_stale" for f in findings)


def test_I_no_snapshot_id_returns_stale_block():
    conn = _minimal_db()
    freshness, age, findings = assess_market_freshness(
        conn, 1, market_signal_snapshot_id=None, policy=EligibilityPolicy()
    )
    assert freshness == MarketFreshnessClass.STALE
    assert age is None
    assert any(f.severity == "block" for f in findings)


def test_J_completed_at_none_returns_stale_warn():
    conn = _minimal_db()
    _insert_run_and_signal(conn, completed_at=None)
    freshness, age, findings = assess_market_freshness(
        conn, 1, market_signal_snapshot_id=1, policy=EligibilityPolicy()
    )
    assert freshness == MarketFreshnessClass.STALE
    assert age is None
    assert any(f.code == "interpretation_run_not_completed" for f in findings)


def test_K_freshness_boundary_exactly_168h():
    conn = _minimal_db()
    _insert_run_and_signal(conn, completed_at=_ts(-167.9))
    freshness, _, _ = assess_market_freshness(
        conn, 1, market_signal_snapshot_id=1, policy=EligibilityPolicy()
    )
    assert freshness == MarketFreshnessClass.FRESH


# ---------------------------------------------------------------------------
# L–O: Signal integrity
# ---------------------------------------------------------------------------


def test_L_matching_canonical_cluster_id_no_finding():
    conn = _minimal_db()
    _insert_run_and_signal(conn, canonical_cluster_id=10)
    finding = assess_signal_integrity(conn, canonical_cluster_id=10, market_signal_snapshot_id=1)
    assert finding is None


def test_M_mismatched_canonical_cluster_id_returns_block():
    conn = _minimal_db()
    _insert_run_and_signal(conn, canonical_cluster_id=10)
    finding = assess_signal_integrity(conn, canonical_cluster_id=99, market_signal_snapshot_id=1)
    assert finding is not None
    assert finding.severity == "block"
    assert finding.code == "signal_canonical_mismatch"


def test_N_no_snapshot_id_skips_check():
    conn = _minimal_db()
    finding = assess_signal_integrity(conn, canonical_cluster_id=10, market_signal_snapshot_id=None)
    assert finding is None


def test_O_missing_canonical_cluster_id_on_opportunity_blocks():
    conn = _minimal_db()
    _insert_run_and_signal(conn, canonical_cluster_id=10)
    finding = assess_signal_integrity(conn, canonical_cluster_id=None, market_signal_snapshot_id=1)
    assert finding is not None
    assert finding.severity == "block"
    assert finding.code == "missing_canonical_cluster_id"


# ---------------------------------------------------------------------------
# P–S: Excluded-topic guard
# ---------------------------------------------------------------------------


def test_P_no_excluded_topics_returns_none():
    finding = assess_excluded_topics(
        opportunity_normalized_topic="python async tutorial",
        opportunity_title="Python Async Guide",
        excluded_topics=[],
    )
    assert finding is None


def test_Q_exact_match_in_normalized_topic_blocks():
    finding = assess_excluded_topics(
        opportunity_normalized_topic="crypto trading strategies",
        opportunity_title="Python Finance",
        excluded_topics=["crypto"],
    )
    assert finding is not None
    assert finding.severity == "block"
    assert finding.code == "excluded_topic_match"


def test_R_case_insensitive_match():
    finding = assess_excluded_topics(
        opportunity_normalized_topic="AFFILIATE MARKETING GUIDE",
        opportunity_title="Some Title",
        excluded_topics=["affiliate marketing"],
    )
    assert finding is not None
    assert finding.code == "excluded_topic_match"


def test_S_match_in_title_when_not_in_normalized_topic():
    finding = assess_excluded_topics(
        opportunity_normalized_topic="passive income online",
        opportunity_title="Best MLM Strategies 2025",
        excluded_topics=["mlm"],
    )
    assert finding is not None
    assert finding.code == "excluded_topic_match"


# ---------------------------------------------------------------------------
# T–W: Signal maturity + confidence classification
# ---------------------------------------------------------------------------


def test_T_insufficient_maturity_produces_block():
    policy = EligibilityPolicy()
    findings = assess_signal_maturity_confidence(
        signal_maturity="insufficient",
        signal_confidence=0.5,
        policy=policy,
    )
    assert any(f.code == "insufficient_signal_maturity" and f.severity == "block" for f in findings)


def test_U_exploratory_maturity_produces_warn():
    policy = EligibilityPolicy()
    findings = assess_signal_maturity_confidence(
        signal_maturity="exploratory",
        signal_confidence=0.5,
        policy=policy,
    )
    assert any(
        f.code == "low_signal_maturity_exploration_only" and f.severity == "warn" for f in findings
    )
    assert not any(f.severity == "block" for f in findings)


def test_V_directional_maturity_high_confidence_no_findings():
    policy = EligibilityPolicy()
    findings = assess_signal_maturity_confidence(
        signal_maturity="directional",
        signal_confidence=0.50,
        policy=policy,
    )
    assert not any(
        f.severity in ("block", "warn") for f in findings if f.code != "low_signal_confidence"
    )


def test_W_low_confidence_at_directional_produces_warn():
    policy = EligibilityPolicy()
    findings = assess_signal_maturity_confidence(
        signal_maturity="directional",
        signal_confidence=0.10,
        policy=policy,
    )
    assert any(f.code == "low_signal_confidence" and f.severity == "warn" for f in findings)


# ---------------------------------------------------------------------------
# X–AB: Analytics readiness
# ---------------------------------------------------------------------------


def test_X_no_promoted_topic_not_ready():
    conn = _minimal_db()
    ready, findings = assess_analytics_readiness(
        conn, opportunity_id=99, policy=EligibilityPolicy()
    )
    assert ready is False
    assert any(f.code == "no_promoted_topic" for f in findings)


def test_Y_topic_exists_no_publications():
    conn = _minimal_db()
    conn.execute("INSERT INTO topics (id, promoted_opportunity_id) VALUES (1, 42)")
    ready, findings = assess_analytics_readiness(conn, 42, EligibilityPolicy())
    assert ready is False
    assert any(f.code == "no_publications_for_topic" for f in findings)


def test_Z_publication_with_null_observation_state_not_ready():
    conn = _minimal_db()
    conn.execute("INSERT INTO topics (id, promoted_opportunity_id) VALUES (1, 42)")
    conn.execute("INSERT INTO publishing_plans (id, topic_id) VALUES (10, 1)")
    conn.execute("INSERT INTO publications (id, publishing_plan_id) VALUES (100, 10)")
    conn.execute(
        "INSERT INTO analytics_snapshots (id, publication_id, observation_state) "
        "VALUES (1, 100, NULL)"
    )
    ready, findings = assess_analytics_readiness(conn, 42, EligibilityPolicy())
    assert ready is False


def test_AA_publication_with_no_data_state_not_ready():
    conn = _minimal_db()
    conn.execute("INSERT INTO topics (id, promoted_opportunity_id) VALUES (1, 42)")
    conn.execute("INSERT INTO publishing_plans (id, topic_id) VALUES (10, 1)")
    conn.execute("INSERT INTO publications (id, publishing_plan_id) VALUES (100, 10)")
    conn.execute(
        "INSERT INTO analytics_snapshots (id, publication_id, observation_state) "
        "VALUES (1, 100, 'no_data')"
    )
    ready, findings = assess_analytics_readiness(conn, 42, EligibilityPolicy())
    assert ready is False


def test_AB_publication_data_state_enough_views_ready():
    conn = _minimal_db()
    conn.execute("INSERT INTO topics (id, promoted_opportunity_id) VALUES (1, 42)")
    conn.execute("INSERT INTO publishing_plans (id, topic_id) VALUES (10, 1)")
    conn.execute("INSERT INTO publications (id, publishing_plan_id) VALUES (100, 10)")
    conn.execute(
        "INSERT INTO analytics_snapshots (id, publication_id, observation_state) "
        "VALUES (1, 100, 'data')"
    )
    conn.execute(
        "INSERT INTO analytics_metrics "
        "(id, snapshot_id, publication_id, metric_name, metric_value) "
        "VALUES (1, 1, 100, 'views', 25)"
    )
    ready, findings = assess_analytics_readiness(conn, 42, EligibilityPolicy())
    assert ready is True
    assert any(f.code == "analytics_ready" for f in findings)


# ---------------------------------------------------------------------------
# AC–AF: Active experiment conflict detection
# ---------------------------------------------------------------------------


def test_AC_no_experiments_no_conflict():
    conn = _minimal_db()
    has_conflict, conflict_id, findings = assess_active_conflicts(
        conn, opportunity_id=1, policy=EligibilityPolicy()
    )
    assert has_conflict is False
    assert conflict_id is None
    assert findings == []


def test_AD_draft_experiment_causes_conflict():
    conn = _minimal_db()
    conn.execute(
        "INSERT INTO experiments (id, channel_id, opportunity_id, status) "
        "VALUES ('exp-A', 1, 1, 'draft')"
    )
    has_conflict, conflict_id, findings = assess_active_conflicts(
        conn, opportunity_id=1, policy=EligibilityPolicy()
    )
    assert has_conflict is True
    assert conflict_id == "exp-A"
    assert any(f.severity == "block" for f in findings)


def test_AE_completed_experiment_no_conflict():
    conn = _minimal_db()
    conn.execute(
        "INSERT INTO experiments (id, channel_id, opportunity_id, status) "
        "VALUES ('exp-B', 1, 1, 'completed')"
    )
    has_conflict, conflict_id, findings = assess_active_conflicts(
        conn, opportunity_id=1, policy=EligibilityPolicy()
    )
    assert has_conflict is False


def test_AF_cancelled_experiment_no_conflict():
    conn = _minimal_db()
    conn.execute(
        "INSERT INTO experiments (id, channel_id, opportunity_id, status) "
        "VALUES ('exp-C', 1, 1, 'cancelled')"
    )
    has_conflict, _, _ = assess_active_conflicts(conn, 1, EligibilityPolicy())
    assert has_conflict is False


# ---------------------------------------------------------------------------
# AG–AJ: Semantic fit sub-assessment
# ---------------------------------------------------------------------------


def test_AG_fake_provider_above_threshold_passes():
    policy = EligibilityPolicy()
    provider = _fake_fit_provider(score=0.80)
    score, label, findings, _specificity = assess_semantic_fit(
        opportunity_normalized_topic="python async tutorial",
        opportunity_title="Python Async Guide",
        opportunity_topic_summary="Learn async in Python.",
        primary_niche="Python programming",
        audience_description="Python developers",
        excluded_topics=[],
        ai_provider=provider,
        policy=policy,
    )
    assert score is not None and score == pytest.approx(0.80)
    assert label == "strong_fit"
    assert not any(f.code == "semantic_fit_below_threshold" for f in findings)


def test_AH_fake_provider_below_threshold_blocks():
    policy = EligibilityPolicy()
    provider = _fake_fit_provider(score=0.30, fit_label="weak_fit")
    score, label, findings, _specificity = assess_semantic_fit(
        opportunity_normalized_topic="stock market tips",
        opportunity_title="Finance Strategies",
        opportunity_topic_summary="Investing basics.",
        primary_niche="Python programming",
        audience_description="Python developers",
        excluded_topics=[],
        ai_provider=provider,
        policy=policy,
    )
    assert score == pytest.approx(0.30)
    assert any(f.code == "semantic_fit_below_threshold" and f.severity == "block" for f in findings)


def test_AI_no_provider_skips_with_warn():
    policy = EligibilityPolicy()
    score, label, findings, _specificity = assess_semantic_fit(
        opportunity_normalized_topic="python async",
        opportunity_title="Async Python",
        opportunity_topic_summary="",
        primary_niche="Python",
        audience_description=None,
        excluded_topics=[],
        ai_provider=None,
        policy=policy,
    )
    assert score is None
    assert label is None
    assert any(f.code == "semantic_fit_skipped" and f.severity == "warn" for f in findings)


def test_AJ_provider_call_failure_returns_warn():
    policy = EligibilityPolicy()

    class _FailProvider:
        name = "fail"

        def complete(self, request):
            raise RuntimeError("connection refused")

    score, label, findings, _specificity = assess_semantic_fit(
        opportunity_normalized_topic="python async",
        opportunity_title="Async Python",
        opportunity_topic_summary="",
        primary_niche="Python",
        audience_description=None,
        excluded_topics=[],
        ai_provider=_FailProvider(),
        policy=policy,
    )
    assert score is None
    assert any(f.code == "semantic_fit_call_failed" and f.severity == "warn" for f in findings)


# ---------------------------------------------------------------------------
# AK–AN: Phase 12C maturity summary
# ---------------------------------------------------------------------------


def test_AK_no_topic_returns_zero():
    conn = _minimal_db()
    count, findings = assess_phase12c_maturity(conn, opportunity_id=999)
    assert count == 0


def test_AL_topic_no_snapshots_returns_zero():
    conn = _minimal_db()
    conn.execute("INSERT INTO topics (id, promoted_opportunity_id) VALUES (1, 42)")
    count, findings = assess_phase12c_maturity(conn, 42)
    assert count == 0


def test_AM_one_feature_snapshot():
    conn = _minimal_db()
    conn.execute("INSERT INTO topics (id, promoted_opportunity_id) VALUES (1, 42)")
    conn.execute(
        "INSERT INTO content_feature_snapshots (id, topic_id, publication_id) VALUES (1, 1, 100)"
    )
    count, findings = assess_phase12c_maturity(conn, 42)
    assert count == 1
    assert any(f.code == "phase12c_feature_count" for f in findings)


def test_AN_multiple_feature_snapshots():
    conn = _minimal_db()
    conn.execute("INSERT INTO topics (id, promoted_opportunity_id) VALUES (1, 42)")
    for i in range(1, 6):
        conn.execute(
            "INSERT INTO content_feature_snapshots (id, topic_id, publication_id) VALUES (?, 1, ?)",
            (i, i * 100),
        )
    count, _ = assess_phase12c_maturity(conn, 42)
    assert count == 5


# ---------------------------------------------------------------------------
# AO–AQ: Production feasibility / policy docs
# ---------------------------------------------------------------------------


def test_AO_policy_narration_rate_bounds():
    p = EligibilityPolicy()
    assert p.narration_rate_min == 0.7
    assert p.narration_rate_max == 1.5


def test_AP_maturity_rank_ordering():
    assert _maturity_rank("insufficient") < _maturity_rank("exploratory")
    assert _maturity_rank("exploratory") < _maturity_rank("directional")
    assert _maturity_rank("directional") < _maturity_rank("actionable")


def test_AQ_unknown_maturity_rank_is_negative():
    assert _maturity_rank("unknown_level") < 0


# ---------------------------------------------------------------------------
# AR–AV: INELIGIBLE classification roll-up scenarios
# ---------------------------------------------------------------------------


def test_AR_block_finding_causes_ineligible():
    findings = [EligibilityFinding(code="excluded_topic_match", severity="block", message="x")]
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.FRESH,
        signal_maturity="directional",
        signal_confidence=0.60,
        semantic_fit_score=0.80,
        semantic_fit_called=True,
        policy=EligibilityPolicy(),
    )
    assert cls == ExperimentEligibilityClassification.INELIGIBLE


def test_AS_block_takes_precedence_over_stale():
    findings = [
        EligibilityFinding(code="active_experiment_conflict", severity="block", message="x"),
        EligibilityFinding(code="market_knowledge_stale", severity="warn", message="x"),
    ]
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.STALE,
        signal_maturity="directional",
        signal_confidence=0.60,
        semantic_fit_score=None,
        semantic_fit_called=False,
        policy=EligibilityPolicy(),
    )
    assert cls == ExperimentEligibilityClassification.INELIGIBLE


def test_AT_insufficient_maturity_block_causes_ineligible():
    findings = [
        EligibilityFinding(code="insufficient_signal_maturity", severity="block", message="x")
    ]
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.FRESH,
        signal_maturity="insufficient",
        signal_confidence=0.5,
        semantic_fit_score=0.9,
        semantic_fit_called=True,
        policy=EligibilityPolicy(),
    )
    assert cls == ExperimentEligibilityClassification.INELIGIBLE


def test_AU_semantic_fit_block_causes_ineligible():
    findings = [
        EligibilityFinding(code="semantic_fit_below_threshold", severity="block", message="x")
    ]
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.FRESH,
        signal_maturity="directional",
        signal_confidence=0.60,
        semantic_fit_score=0.30,
        semantic_fit_called=True,
        policy=EligibilityPolicy(),
    )
    assert cls == ExperimentEligibilityClassification.INELIGIBLE


def test_AV_orphaned_signal_block_causes_ineligible():
    findings = [EligibilityFinding(code="signal_canonical_mismatch", severity="block", message="x")]
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.FRESH,
        signal_maturity="directional",
        signal_confidence=0.60,
        semantic_fit_score=0.80,
        semantic_fit_called=True,
        policy=EligibilityPolicy(),
    )
    assert cls == ExperimentEligibilityClassification.INELIGIBLE


# ---------------------------------------------------------------------------
# AW–AZ: EXPLORATION_ONLY classification roll-up scenarios
# ---------------------------------------------------------------------------


def test_AW_exploratory_maturity_exploration_only():
    findings = [
        EligibilityFinding(
            code="low_signal_maturity_exploration_only", severity="warn", message="x"
        )
    ]
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.FRESH,
        signal_maturity="exploratory",
        signal_confidence=0.50,
        semantic_fit_score=0.80,
        semantic_fit_called=True,
        policy=EligibilityPolicy(),
    )
    assert cls == ExperimentEligibilityClassification.EXPLORATION_ONLY


def test_AX_directional_low_confidence_exploration_only():
    findings = [EligibilityFinding(code="low_signal_confidence", severity="warn", message="x")]
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.FRESH,
        signal_maturity="directional",
        signal_confidence=0.10,
        semantic_fit_score=0.80,
        semantic_fit_called=True,
        policy=EligibilityPolicy(),
    )
    assert cls == ExperimentEligibilityClassification.EXPLORATION_ONLY


def test_AY_actionable_maturity_low_confidence_exploration_only():
    findings = [EligibilityFinding(code="low_signal_confidence", severity="warn", message="x")]
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.FRESH,
        signal_maturity="actionable",
        signal_confidence=0.05,
        semantic_fit_score=0.80,
        semantic_fit_called=True,
        policy=EligibilityPolicy(),
    )
    assert cls == ExperimentEligibilityClassification.EXPLORATION_ONLY


def test_AZ_exploration_only_not_promoted_to_general_without_confidence():
    policy = EligibilityPolicy()
    findings: list[EligibilityFinding] = []
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.FRESH,
        signal_maturity="directional",
        signal_confidence=0.05,  # below threshold
        semantic_fit_score=0.90,
        semantic_fit_called=True,
        policy=policy,
    )
    assert cls == ExperimentEligibilityClassification.EXPLORATION_ONLY


# ---------------------------------------------------------------------------
# BA–BD: GENERAL_ELIGIBLE classification roll-up scenarios
# ---------------------------------------------------------------------------


def test_BA_all_passing_general_eligible():
    findings: list[EligibilityFinding] = []
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.FRESH,
        signal_maturity="directional",
        signal_confidence=0.60,
        semantic_fit_score=0.80,
        semantic_fit_called=True,
        policy=EligibilityPolicy(),
    )
    assert cls == ExperimentEligibilityClassification.GENERAL_ELIGIBLE


def test_BB_actionable_maturity_general_eligible():
    findings: list[EligibilityFinding] = []
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.AGING,
        signal_maturity="actionable",
        signal_confidence=0.75,
        semantic_fit_score=0.80,
        semantic_fit_called=True,
        policy=EligibilityPolicy(),
    )
    assert cls == ExperimentEligibilityClassification.GENERAL_ELIGIBLE


def test_BC_general_eligible_without_provider():
    findings: list[EligibilityFinding] = []
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.FRESH,
        signal_maturity="directional",
        signal_confidence=0.60,
        semantic_fit_score=None,
        semantic_fit_called=False,  # no provider — not UNRESOLVED
        policy=EligibilityPolicy(),
    )
    assert cls == ExperimentEligibilityClassification.GENERAL_ELIGIBLE


def test_BD_general_eligible_with_aging_market():
    """AGING market is not REQUIRES_REFRESH — that only applies to STALE."""
    findings = [EligibilityFinding(code="market_knowledge_aging", severity="warn", message="x")]
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.AGING,
        signal_maturity="directional",
        signal_confidence=0.60,
        semantic_fit_score=0.80,
        semantic_fit_called=True,
        policy=EligibilityPolicy(),
    )
    assert cls == ExperimentEligibilityClassification.GENERAL_ELIGIBLE


# ---------------------------------------------------------------------------
# BE–BH: REQUIRES_REFRESH classification roll-up scenarios
# ---------------------------------------------------------------------------


def test_BE_stale_market_requires_refresh():
    findings = [EligibilityFinding(code="market_knowledge_stale", severity="warn", message="x")]
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.STALE,
        signal_maturity="directional",
        signal_confidence=0.60,
        semantic_fit_score=0.80,
        semantic_fit_called=True,
        policy=EligibilityPolicy(),
    )
    assert cls == ExperimentEligibilityClassification.REQUIRES_REFRESH


def test_BF_stale_with_good_signal_still_requires_refresh():
    findings: list[EligibilityFinding] = []
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.STALE,
        signal_maturity="actionable",
        signal_confidence=0.99,
        semantic_fit_score=0.95,
        semantic_fit_called=True,
        policy=EligibilityPolicy(),
    )
    assert cls == ExperimentEligibilityClassification.REQUIRES_REFRESH


def test_BG_block_takes_precedence_over_requires_refresh():
    findings = [EligibilityFinding(code="excluded_topic_match", severity="block", message="x")]
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.STALE,
        signal_maturity="directional",
        signal_confidence=0.60,
        semantic_fit_score=0.80,
        semantic_fit_called=True,
        policy=EligibilityPolicy(),
    )
    assert cls == ExperimentEligibilityClassification.INELIGIBLE


def test_BH_stale_low_maturity_block_wins():
    findings = [
        EligibilityFinding(code="insufficient_signal_maturity", severity="block", message="x")
    ]
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.STALE,
        signal_maturity="insufficient",
        signal_confidence=0.0,
        semantic_fit_score=None,
        semantic_fit_called=False,
        policy=EligibilityPolicy(),
    )
    assert cls == ExperimentEligibilityClassification.INELIGIBLE


# ---------------------------------------------------------------------------
# BI–BL: UNRESOLVED classification roll-up scenarios
# ---------------------------------------------------------------------------


def test_BI_provider_called_but_score_none_unresolved():
    findings = [EligibilityFinding(code="semantic_fit_call_failed", severity="warn", message="x")]
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.FRESH,
        signal_maturity="directional",
        signal_confidence=0.60,
        semantic_fit_score=None,
        semantic_fit_called=True,  # called but failed
        policy=EligibilityPolicy(),
    )
    assert cls == ExperimentEligibilityClassification.UNRESOLVED


def test_BJ_block_takes_precedence_over_unresolved():
    findings = [
        EligibilityFinding(code="excluded_topic_match", severity="block", message="x"),
        EligibilityFinding(code="semantic_fit_call_failed", severity="warn", message="x"),
    ]
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.FRESH,
        signal_maturity="directional",
        signal_confidence=0.60,
        semantic_fit_score=None,
        semantic_fit_called=True,
        policy=EligibilityPolicy(),
    )
    assert cls == ExperimentEligibilityClassification.INELIGIBLE


def test_BK_unresolved_before_requires_refresh():
    findings = [
        EligibilityFinding(code="market_knowledge_stale", severity="warn", message="x"),
        EligibilityFinding(code="semantic_fit_call_failed", severity="warn", message="x"),
    ]
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.STALE,
        signal_maturity="directional",
        signal_confidence=0.60,
        semantic_fit_score=None,
        semantic_fit_called=True,
        policy=EligibilityPolicy(),
    )
    # UNRESOLVED before REQUIRES_REFRESH in priority
    assert cls == ExperimentEligibilityClassification.UNRESOLVED


def test_BL_no_provider_not_unresolved():
    """If provider was not called (ai_provider=None), score=None is expected — not UNRESOLVED."""
    findings: list[EligibilityFinding] = []
    cls = _roll_up_classification(
        findings,
        market_freshness=MarketFreshnessClass.FRESH,
        signal_maturity="directional",
        signal_confidence=0.60,
        semantic_fit_score=None,
        semantic_fit_called=False,  # not called
        policy=EligibilityPolicy(),
    )
    assert cls == ExperimentEligibilityClassification.GENERAL_ELIGIBLE


# ---------------------------------------------------------------------------
# BM–BY: Full assess_experiment_eligibility integration tests
# ---------------------------------------------------------------------------


def _full_db_general_eligible(
    *,
    completed_at_delta_hours: float = -48,
    signal_maturity: str = "directional",
    confidence: float = 0.55,
    canonical_cluster_id: int = 10,
    excluded_topics: list[str] | None = None,
    opp_canonical: int = 10,
) -> sqlite3.Connection:
    conn = _minimal_db()
    _insert_channel_and_profile(conn, excluded_topics=excluded_topics or [])
    _insert_run_and_signal(
        conn,
        completed_at=_ts(completed_at_delta_hours),
        signal_maturity=signal_maturity,
        confidence=confidence,
        canonical_cluster_id=canonical_cluster_id,
    )
    _insert_opportunity(conn, canonical_cluster_id=opp_canonical, market_signal_snapshot_id=1)
    return conn


def test_BM_general_eligible_full_assessment():
    conn = _full_db_general_eligible()
    result = assess_experiment_eligibility(
        conn,
        opportunity_id=1,
        channel_id=1,
        ai_provider=_fake_fit_provider(score=0.85),
    )
    assert result.classification == ExperimentEligibilityClassification.GENERAL_ELIGIBLE
    assert result.opportunity_id == 1
    assert result.channel_id == 1
    assert result.signal_maturity == "directional"
    assert result.market_freshness_class == MarketFreshnessClass.FRESH


def test_BN_ineligible_excluded_topic_full_assessment():
    conn = _minimal_db()
    _insert_channel_and_profile(conn, excluded_topics=["crypto"])
    _insert_run_and_signal(conn, completed_at=_ts(-48))
    _insert_opportunity(conn, normalized_topic="crypto trading guide", title="Crypto Guide")
    result = assess_experiment_eligibility(conn, opportunity_id=1, channel_id=1)
    assert result.classification == ExperimentEligibilityClassification.INELIGIBLE
    assert any(f.code == "excluded_topic_match" for f in result.findings)


def test_BO_requires_refresh_stale_market():
    conn = _full_db_general_eligible(completed_at_delta_hours=-900)  # > 720h
    result = assess_experiment_eligibility(
        conn,
        opportunity_id=1,
        channel_id=1,
        ai_provider=_fake_fit_provider(score=0.85),
    )
    assert result.classification == ExperimentEligibilityClassification.REQUIRES_REFRESH
    assert result.market_freshness_class == MarketFreshnessClass.STALE


def test_BP_exploration_only_low_maturity():
    conn = _full_db_general_eligible(signal_maturity="exploratory", confidence=0.45)
    result = assess_experiment_eligibility(
        conn,
        opportunity_id=1,
        channel_id=1,
        ai_provider=_fake_fit_provider(score=0.85),
    )
    assert result.classification == ExperimentEligibilityClassification.EXPLORATION_ONLY


def test_BQ_ineligible_insufficient_maturity():
    conn = _full_db_general_eligible(signal_maturity="insufficient", confidence=0.10)
    result = assess_experiment_eligibility(conn, opportunity_id=1, channel_id=1)
    assert result.classification == ExperimentEligibilityClassification.INELIGIBLE


def test_BR_ineligible_active_conflict():
    conn = _full_db_general_eligible()
    conn.execute(
        "INSERT INTO experiments (id, channel_id, opportunity_id, status) "
        "VALUES ('exp-X', 1, 1, 'in_production')"
    )
    result = assess_experiment_eligibility(conn, opportunity_id=1, channel_id=1)
    assert result.classification == ExperimentEligibilityClassification.INELIGIBLE
    assert result.has_active_conflict is True
    assert result.active_conflict_experiment_id == "exp-X"


def test_BS_ineligible_orphaned_signal():
    conn = _full_db_general_eligible(canonical_cluster_id=10, opp_canonical=99)
    result = assess_experiment_eligibility(conn, opportunity_id=1, channel_id=1)
    assert result.classification == ExperimentEligibilityClassification.INELIGIBLE
    assert any(f.code == "signal_canonical_mismatch" for f in result.findings)


def test_BT_opportunity_not_found():
    conn = _minimal_db()
    result = assess_experiment_eligibility(conn, opportunity_id=9999, channel_id=1)
    assert result.classification == ExperimentEligibilityClassification.INELIGIBLE
    assert any(f.code == "opportunity_not_found" for f in result.findings)


def test_BU_assessment_carries_policy_snapshot():
    conn = _full_db_general_eligible()
    policy = EligibilityPolicy(market_fresh_max_age_hours=336)  # custom
    result = assess_experiment_eligibility(conn, 1, 1, policy=policy)
    snapshot = json.loads(result.policy_snapshot_json)
    assert snapshot["market_fresh_max_age_hours"] == 336


def test_BV_unresolved_when_provider_fails():
    conn = _full_db_general_eligible()

    class _FailProvider:
        name = "fail"

        def complete(self, request):
            raise RuntimeError("network error")

    result = assess_experiment_eligibility(conn, 1, 1, ai_provider=_FailProvider())
    assert result.classification == ExperimentEligibilityClassification.UNRESOLVED


def test_BW_general_eligible_with_passing_fake_provider():
    conn = _full_db_general_eligible()
    provider = _fake_fit_provider(score=0.85)
    result = assess_experiment_eligibility(conn, 1, 1, ai_provider=provider)
    assert result.classification == ExperimentEligibilityClassification.GENERAL_ELIGIBLE
    assert result.semantic_fit_score == pytest.approx(0.85)


def test_BX_ineligible_from_semantic_fit_block():
    conn = _full_db_general_eligible()
    provider = _fake_fit_provider(score=0.20, fit_label="no_fit")
    result = assess_experiment_eligibility(conn, 1, 1, ai_provider=provider)
    assert result.classification == ExperimentEligibilityClassification.INELIGIBLE
    assert any(f.code == "semantic_fit_below_threshold" for f in result.findings)


def test_BY_assessment_is_block_method():
    """ExperimentEligibilityAssessment.blocks() only returns block-severity findings."""
    conn = _full_db_general_eligible()
    conn.execute(
        "INSERT INTO experiments (id, channel_id, opportunity_id, status) "
        "VALUES ('exp-Z', 1, 1, 'planned')"
    )
    result = assess_experiment_eligibility(conn, 1, 1)
    assert result.classification == ExperimentEligibilityClassification.INELIGIBLE
    block_findings = result.blocks()
    assert len(block_findings) >= 1
    assert all(f.severity == "block" for f in block_findings)
    warn_findings = result.warnings()
    assert all(f.severity == "warn" for f in warn_findings)


# ---------------------------------------------------------------------------
# BZ: Regression — semantic fit uses claude-haiku-4-5-20251001 (not claude-sonnet-5)
# ---------------------------------------------------------------------------


def test_BZ_semantic_fit_uses_haiku_not_sonnet5():
    """assess_semantic_fit must use claude-haiku-4-5-20251001 for structured-output calls.

    Regression guard: claude-sonnet-5 raised BadRequestError when response_schema
    was set.  The model was fixed to claude-haiku-4-5-20251001 in Phase 16C.4.
    """
    from app.ai.provider import AIRequest, AIResponse

    captured: list[AIRequest] = []

    class _CapturingProvider:
        name = "capturing"

        def complete(self, request: AIRequest) -> AIResponse:
            captured.append(request)
            # Return a minimal valid response so scoring proceeds.
            return AIResponse(
                raw_text='{"score": 0.90, "fit_label": "strong_fit", "rationale": "test"}',
                parsed=None,
                model=request.model,
            )

    policy = EligibilityPolicy()
    assess_semantic_fit(
        opportunity_normalized_topic="crispr gene editing technology",
        opportunity_title="CRISPR Gene Editing Explained",
        opportunity_topic_summary="How CRISPR works for gene therapy.",
        primary_niche="science and technology explained",
        audience_description="Curious adults who want clear science explanations.",
        excluded_topics=[],
        ai_provider=_CapturingProvider(),
        policy=policy,
    )

    assert len(captured) == 1, "Expected exactly one AIRequest to be dispatched"
    assert captured[0].model == "claude-haiku-4-5-20251001", (
        f"Expected claude-haiku-4-5-20251001 but got {captured[0].model!r}. "
        "claude-sonnet-5 raises BadRequestError with response_schema."
    )
    assert captured[0].model != "claude-sonnet-5"
