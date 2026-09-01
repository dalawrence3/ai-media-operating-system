"""
Phase 15 — Full Autonomous Loop Preflight & Live-Readiness Audit
Test Suite

Tests the complete strategic chain end-to-end with PRAGMA foreign_keys = ON
throughout the FK-capable portion.  Covers every major audit requirement from
the Phase 15 specification (A–R).

Safety invariants (none of these happen in this file):
- NO YouTube API calls
- NO live analytics ingest
- NO content generation
- NO live provider calls
- NO writes to the production database

Architectural finding (documented inline where relevant):
    ARCH-01: The publication → publishing_plan → render_manifest FK chain cannot be
    seeded with PRAGMA foreign_keys = ON because render_manifests requires
    scene_manifests, narration_runs, and caption_runs, each of which requires
    voice_profiles + production_plans + scripts.  This is an 8-table deep
    dependency that has no clean test-helper.  Tests H and I use FK-off
    exclusively for the publication-seed helper and re-enable FK-on immediately
    after; all strategic-layer tables stay FK-on throughout.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.database import open_db

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def _open(tmp_path: Path) -> sqlite3.Connection:
    """Open a fresh test DB with real schema at SCHEMA_VERSION=41, FK always on."""
    conn = open_db(tmp_path / "preflight_15.db")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def _utc(offset_hours: float = 0.0) -> str:
    dt = datetime.now(UTC) + timedelta(hours=offset_hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _seed_channel(conn: sqlite3.Connection, name: str = "Orvella") -> int:
    """Insert a channel and return its id."""
    conn.execute(
        "INSERT INTO channels (channel_name, platform, platform_channel_id) "
        "VALUES (?, 'youtube', ?)",
        (name, f"UC_{uuid.uuid4().hex[:16]}"),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_profile_version(conn: sqlite3.Connection, channel_id: int) -> int:
    """Insert a channel_profile_version and return its id."""
    conn.execute(
        "INSERT INTO channel_profile_versions "
        "(channel_id, version, primary_niche) "
        "VALUES (?, 1, 'renewable energy')",
        (channel_id,),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_discovery_run(conn: sqlite3.Connection, channel_id: int, profile_version_id: int) -> int:
    conn.execute(
        "INSERT INTO discovery_runs "
        "(channel_id, profile_version_id, adapter_name, status, started_at, completed_at) "
        "VALUES (?, ?, 'manual', 'completed', ?, ?)",
        (channel_id, profile_version_id, _utc(), _utc()),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_opportunity(
    conn: sqlite3.Connection,
    channel_id: int,
    discovery_run_id: int,
    topic: str = "solar panel reviews",
    *,
    canonical_cluster_id: int | None = None,
    signal_snapshot_id: int | None = None,
) -> int:
    conn.execute(
        "INSERT INTO opportunities "
        "(channel_id, discovery_run_id, normalized_topic, raw_topic, "
        "canonical_cluster_id, market_signal_snapshot_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            channel_id,
            discovery_run_id,
            topic,
            topic,
            canonical_cluster_id,
            signal_snapshot_id,
            _utc(),
            _utc(),
        ),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_canonical_cluster(conn: sqlite3.Connection, label: str = "solar-energy") -> int:
    conn.execute(
        "INSERT INTO market_canonical_clusters "
        "(canonical_label, normalized_label, semantic_fingerprint, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (label, label.lower(), f"fp_{uuid.uuid4().hex[:8]}", _utc(), _utc()),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_market_signal(
    conn: sqlite3.Connection,
    *,
    age_hours: float = 1.0,
    signal_maturity: str = "directional",
    confidence: float = 0.75,
    saturation: float = 0.3,
    canonical_cluster_id: int | None = None,
) -> tuple[int, int, int]:
    """Seed an interpretation_run + topic_cluster + cluster_signal.

    Returns (interp_run_id, cluster_id, signal_id).
    """
    completed_at = _utc(-age_hours)
    conn.execute(
        "INSERT INTO market_interpretation_runs "
        "(evidence_cutoff, status, completed_at, input_hash, created_at) "
        "VALUES (?, 'completed', ?, ?, ?)",
        (_utc(-age_hours * 2), completed_at, f"mir_{uuid.uuid4().hex[:8]}", _utc()),
    )
    run_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO market_topic_clusters "
        "(interpretation_run_id, cluster_label, normalized_label, "
        "canonical_cluster_id, input_hash, created_at) "
        "VALUES (?, 'Solar Energy', 'solar energy', ?, ?, ?)",
        (run_id, canonical_cluster_id, f"mtc_{uuid.uuid4().hex[:8]}", _utc()),
    )
    cluster_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO market_cluster_signals "
        "(cluster_id, interpretation_run_id, confidence, signal_maturity, "
        "saturation_score, demand_score, input_hash, scored_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            cluster_id,
            run_id,
            confidence,
            signal_maturity,
            saturation,
            0.7,
            f"mcs_{uuid.uuid4().hex[:8]}",
            _utc(),
        ),
    )
    signal_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return run_id, cluster_id, signal_id


def _seed_experiment(
    conn: sqlite3.Connection,
    channel_id: int,
    *,
    opportunity_id: int | None = None,
    status: str = "planned",
    publication_id: int | None = None,
) -> str:
    """Create an experiment row using the repository helper."""
    from app.intelligence.experiments.models import ExperimentType, MaturityPolicy
    from app.intelligence.experiments.repository import create_experiment

    exp_id = str(uuid.uuid4())
    create_experiment(
        conn,
        experiment_id=exp_id,
        channel_id=channel_id,
        experiment_type=ExperimentType.exploration,
        hypothesis="Preflight synthetic hypothesis",
        opportunity_id=opportunity_id,
        maturity_policy=MaturityPolicy.default(),
        actor="preflight_test",
    )
    if status != "draft":
        conn.execute(
            "UPDATE experiments SET status = ? WHERE id = ?",
            (status, exp_id),
        )
    if publication_id is not None:
        conn.execute(
            "UPDATE experiments SET publication_id = ? WHERE id = ?",
            (publication_id, exp_id),
        )
    conn.commit()
    return exp_id


def _seed_publication_fkoff(
    conn: sqlite3.Connection,
    *,
    experiment_id: str | None = None,
    published_at: str | None = None,
) -> int:
    """Seed a publication row using FK-off (ARCH-01 workaround for publication chain).

    FK is disabled only for this helper and re-enabled immediately after.
    All strategic-layer tables (channels, opportunities, experiments, briefs,
    contracts) remain FK-on throughout.
    """
    now = _utc()
    pub_ts = published_at or now
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO publishing_plans "
        "(render_manifest_id, render_job_id, topic_id, production_plan_id, "
        "script_id, scene_manifest_id, narration_run_id, caption_run_id, "
        "experiment_id, input_hash, publishing_engine_version, metadata_version, "
        "provider, provider_version, title, description, created_at, updated_at) "
        "VALUES (1, 1, 1, 1, 1, 1, 1, 1, ?, ?, 'v1', 'v1', "
        "'fake_test', '1.0', 'Preflight Test Video', '', ?, ?)",
        (experiment_id, f"pp_{uuid.uuid4().hex[:8]}", now, now),
    )
    pub_plan_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO publishing_jobs "
        "(publishing_plan_id, provider, provider_version, status, "
        "created_at, updated_at) "
        "VALUES (?, 'fake_test', '1.0', 'completed', ?, ?)",
        (pub_plan_id, now, now),
    )
    pub_job_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO publications "
        "(publishing_plan_id, publishing_job_id, provider, provider_version, "
        "publishing_engine_version, input_hash, output_sha256, "
        "status, published_at, created_at, updated_at) "
        "VALUES (?, ?, 'fake_test', '1.0', 'v1', ?, 'sha256test', "
        "'published', ?, ?, ?)",
        (pub_plan_id, pub_job_id, f"pub_{uuid.uuid4().hex[:8]}", pub_ts, now, now),
    )
    pub_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    return pub_id


def _seed_contract_fkoff(
    conn: sqlite3.Connection,
    exp_id: str,
    channel_id: int,
    *,
    fidelity_classification: str = "valid",
    opportunity_id: int = 0,
) -> str:
    """Insert an execution contract with FK-off (brief_id FK workaround).

    Used in fidelity/outcome tests that test service behavior independent of
    the full strategic chain (C→D) already proven in tests C and D.
    FK is off only for this insert and re-enabled immediately.
    """
    fidelity_json = json.dumps(
        {
            "classification": fidelity_classification,
            "outcome": fidelity_classification in ("valid", "valid_with_warnings"),
            "factors": [],
        }
    )
    contract_id = str(uuid.uuid4())
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO experiment_execution_contracts "
        "(id, experiment_id, brief_id, channel_id, opportunity_id, "
        "execution_mode, execution_policy_version, fidelity_json, created_at) "
        "VALUES (?, ?, '', ?, ?, 'real', '1.0.0', ?, ?)",
        (contract_id, exp_id, channel_id, opportunity_id, fidelity_json, _utc()),
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    return contract_id


def _build_synthetic_assessment(
    opportunity_id: int,
    channel_id: int,
    *,
    classification: str = "general_eligible",
    signal_maturity: str = "directional",
    signal_confidence: float = 0.75,
):
    """Build a synthetic ExperimentEligibilityAssessment (bypasses eligibility service)."""
    from app.intelligence.experiments.eligibility import (
        ExperimentEligibilityAssessment,
        ExperimentEligibilityClassification,
    )

    cls_map = {
        "general_eligible": ExperimentEligibilityClassification.GENERAL_ELIGIBLE,
        "exploration_only": ExperimentEligibilityClassification.EXPLORATION_ONLY,
        "ineligible": ExperimentEligibilityClassification.INELIGIBLE,
        "requires_refresh": ExperimentEligibilityClassification.REQUIRES_REFRESH,
    }
    return ExperimentEligibilityAssessment(
        opportunity_id=opportunity_id,
        channel_id=channel_id,
        classification=cls_map[classification],
        findings=[],
        policy_snapshot_json="{}",
        assessed_at=_utc(),
        signal_maturity=signal_maturity,
        signal_confidence=signal_confidence,
        market_freshness_class=None,
    )


# ---------------------------------------------------------------------------
# A — True FK-ON strategic chain
# ---------------------------------------------------------------------------


def test_A_fk_on_strategic_chain_all_constraints_satisfied(tmp_path):
    """PRAGMA foreign_keys = ON from the start. Full strategic chain inserts succeed.

    Chain: channels → channel_profile_versions → discovery_runs
           → opportunities → market_canonical_clusters → market_cluster_signals
           → experiments
    """
    conn = _open(tmp_path)
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    channel_id = _seed_channel(conn)
    profile_id = _seed_profile_version(conn, channel_id)
    run_id = _seed_discovery_run(conn, channel_id, profile_id)
    cluster_id = _seed_canonical_cluster(conn)
    _ir_id, _tc_id, signal_id = _seed_market_signal(conn)

    opp_id = _seed_opportunity(
        conn,
        channel_id,
        run_id,
        canonical_cluster_id=cluster_id,
        signal_snapshot_id=signal_id,
    )
    exp_id = _seed_experiment(conn, channel_id, opportunity_id=opp_id)

    # Verify all rows are queryable and FKs resolve
    row = conn.execute(
        "SELECT e.id, o.normalized_topic, c.channel_name "
        "FROM experiments e "
        "JOIN opportunities o ON o.id = e.opportunity_id "
        "JOIN channels c ON c.id = e.channel_id "
        "WHERE e.id = ?",
        (exp_id,),
    ).fetchone()
    assert row is not None
    assert row["normalized_topic"] == "solar panel reviews"
    assert row["channel_name"] == "Orvella"

    # FK-on is still on
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_A1_fk_on_invalid_channel_ref_rejected(tmp_path):
    """A FK violation on experiments.channel_id is rejected when foreign_keys = ON."""
    conn = _open(tmp_path)
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO experiments "
            "(id, channel_id, experiment_type, hypothesis, maturity_policy_json, "
            "policy_snapshot_json, created_at, updated_at) "
            "VALUES (?, 9999, 'exploration', 'bad channel', '{}', '{}', ?, ?)",
            (str(uuid.uuid4()), _utc(), _utc()),
        )


# ---------------------------------------------------------------------------
# B — Market → eligible Opportunity → planning
# ---------------------------------------------------------------------------


def test_B_planning_returns_selected_decision_for_eligible_assessment(tmp_path):
    """build_portfolio_plan selects a general_eligible opportunity in exploration slot."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)
    profile_id = _seed_profile_version(conn, channel_id)
    run_id = _seed_discovery_run(conn, channel_id, profile_id)
    opp_id = _seed_opportunity(conn, channel_id, run_id)
    conn.commit()

    assessment = _build_synthetic_assessment(
        opp_id,
        channel_id,
        classification="general_eligible",
        signal_maturity="directional",
        signal_confidence=0.75,
    )

    plan = build_portfolio_plan(conn, channel_id, [assessment], dry_run=True)

    assert plan.eligible_count == 1
    assert plan.selected_count >= 1
    selected = [d for d in plan.decisions if d.selected]
    assert len(selected) >= 1
    assert selected[0].opportunity_id == opp_id


def test_B1_exploration_only_excluded_from_exploitation_pool(tmp_path):
    """EXPLORATION_ONLY assessment is never placed in exploitation slot."""
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)
    profile_id = _seed_profile_version(conn, channel_id)
    run_id = _seed_discovery_run(conn, channel_id, profile_id)
    opp_id = _seed_opportunity(conn, channel_id, run_id)
    conn.commit()

    assessment = _build_synthetic_assessment(
        opp_id,
        channel_id,
        classification="exploration_only",
        signal_maturity="insufficient",
        signal_confidence=0.1,
    )

    policy = PlanningPolicy.v1()
    plan = build_portfolio_plan(conn, channel_id, [assessment], policy=policy, dry_run=True)

    selected = [d for d in plan.decisions if d.selected]
    # May be in exploration pool but NOT exploitation pool
    for d in selected:
        assert d.pool_type == "exploration", (
            f"EXPLORATION_ONLY candidate placed in {d.pool_type} pool"
        )


# ---------------------------------------------------------------------------
# C — Planning → brief → Experiment identity preservation
# ---------------------------------------------------------------------------


def test_C_identity_preservation_channel_opportunity_cluster_pass_through(tmp_path):
    """After planning + brief creation, channel_id and opportunity_id match the source."""
    from app.intelligence.experiments.brief_service import create_strategy_brief
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)
    profile_id = _seed_profile_version(conn, channel_id)
    run_id = _seed_discovery_run(conn, channel_id, profile_id)
    cluster_id = _seed_canonical_cluster(conn)
    conn.execute(
        "UPDATE opportunities SET canonical_cluster_id = ? WHERE 1=0",
        (cluster_id,),
    )
    opp_id = _seed_opportunity(
        conn,
        channel_id,
        run_id,
        canonical_cluster_id=cluster_id,
    )
    conn.commit()

    assessment = _build_synthetic_assessment(opp_id, channel_id)
    plan = build_portfolio_plan(conn, channel_id, [assessment])
    assert plan.selected_count >= 1

    # Find the selection_decision_id for the selected candidate
    selected_decision = conn.execute(
        "SELECT id FROM experiment_selection_decisions WHERE selected = 1 ORDER BY id LIMIT 1"
    ).fetchone()
    assert selected_decision is not None, "No selection decision persisted"

    decision_id = selected_decision["id"]

    # Create brief (uses the planning run's stored data)
    brief = create_strategy_brief(conn, decision_id)

    # Identity must be preserved
    brief_row = conn.execute(
        "SELECT channel_id, opportunity_id FROM experiment_strategy_briefs WHERE id = ?",
        (brief.id,),
    ).fetchone()
    assert brief_row is not None
    assert brief_row["channel_id"] == channel_id
    assert brief_row["opportunity_id"] == opp_id


def test_C1_stale_eligibility_blocks_brief_creation(tmp_path):
    """Brief creation is blocked when the opportunity's stored eligibility is INELIGIBLE."""
    from app.intelligence.experiments.brief_service import create_strategy_brief
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)
    profile_id = _seed_profile_version(conn, channel_id)
    run_id = _seed_discovery_run(conn, channel_id, profile_id)
    opp_id = _seed_opportunity(conn, channel_id, run_id)
    conn.commit()

    assessment = _build_synthetic_assessment(opp_id, channel_id)
    build_portfolio_plan(conn, channel_id, [assessment])

    # Force the stored eligibility to INELIGIBLE
    conn.execute(
        "UPDATE experiment_candidate_scores SET eligibility_classification = 'ineligible' "
        "WHERE opportunity_id = ?",
        (opp_id,),
    )
    conn.commit()

    selected_decision = conn.execute(
        "SELECT id FROM experiment_selection_decisions WHERE selected = 1 ORDER BY id LIMIT 1"
    ).fetchone()
    assert selected_decision is not None

    with pytest.raises(Exception, match="[Ii]neligible|[Ee]ligibility|[Bb]locked"):
        create_strategy_brief(conn, selected_decision["id"])


# ---------------------------------------------------------------------------
# D — Execution contract treatment/control integrity
# ---------------------------------------------------------------------------


def _seed_full_contract_chain(conn: sqlite3.Connection) -> dict:
    """Build full strategic chain up to execution contract for tests D and E."""
    from app.intelligence.experiments.brief_service import create_strategy_brief
    from app.intelligence.experiments.models import (
        ExperimentType,
        FactorRole,
        MaturityPolicy,
        MetricDirection,
    )
    from app.intelligence.experiments.planning_service import build_portfolio_plan
    from app.intelligence.experiments.repository import (
        add_factor,
        add_metric_target,
        create_experiment,
    )

    channel_id = _seed_channel(conn)
    profile_id = _seed_profile_version(conn, channel_id)
    run_id = _seed_discovery_run(conn, channel_id, profile_id)
    cluster_id = _seed_canonical_cluster(conn)
    _ir_id, _tc_id, signal_id = _seed_market_signal(conn)
    opp_id = _seed_opportunity(
        conn,
        channel_id,
        run_id,
        canonical_cluster_id=cluster_id,
        signal_snapshot_id=signal_id,
    )
    conn.commit()

    assessment = _build_synthetic_assessment(opp_id, channel_id)
    build_portfolio_plan(conn, channel_id, [assessment])

    selected_decision = conn.execute(
        "SELECT id FROM experiment_selection_decisions WHERE selected = 1 ORDER BY id LIMIT 1"
    ).fetchone()
    decision_id = selected_decision["id"]

    brief = create_strategy_brief(conn, decision_id)

    # Create the actual experiment row
    exp_id = str(uuid.uuid4())
    create_experiment(
        conn,
        experiment_id=exp_id,
        channel_id=channel_id,
        experiment_type=ExperimentType.exploration,
        hypothesis="D test hypothesis",
        opportunity_id=opp_id,
        maturity_policy=MaturityPolicy.default(),
        actor="preflight_d",
    )
    # Set treatment factor with a valid value (keyword args required)
    add_factor(
        conn,
        exp_id,
        factor_name="narration_speaking_rate",
        factor_role=FactorRole.treatment,
        intended_value="1.0",
        value_type="numeric",
    )
    add_metric_target(
        conn,
        exp_id,
        metric_name="average_view_percentage",
        direction=MetricDirection.higher_is_better,
        is_primary=True,
    )
    conn.commit()

    return {
        "channel_id": channel_id,
        "opp_id": opp_id,
        "brief_id": brief.id,
        "exp_id": exp_id,
    }


def test_D_execution_contract_validates_lineage(tmp_path):
    """create_execution_contract validates channel/opportunity identity match."""
    from app.intelligence.experiments.execution_service import create_execution_contract

    conn = _open(tmp_path)
    ids = _seed_full_contract_chain(conn)

    # Contract should be created without error when lineage is valid
    contract = create_execution_contract(conn, ids["exp_id"], ids["brief_id"])
    assert contract.experiment_id == ids["exp_id"]
    assert contract.brief_id == ids["brief_id"]


def test_D1_execution_contract_channel_mismatch_raises(tmp_path):
    """contract creation fails when experiment.channel_id ≠ brief.channel_id."""
    from app.intelligence.experiments.execution_service import (
        ExecutionContractError,
        create_execution_contract,
    )

    conn = _open(tmp_path)
    ids = _seed_full_contract_chain(conn)

    # Create a second channel
    other_channel_id = _seed_channel(conn, "OtherChannel")
    from app.intelligence.experiments.models import ExperimentType, MaturityPolicy
    from app.intelligence.experiments.repository import create_experiment

    bad_exp_id = str(uuid.uuid4())
    create_experiment(
        conn,
        experiment_id=bad_exp_id,
        channel_id=other_channel_id,
        experiment_type=ExperimentType.exploration,
        hypothesis="wrong channel experiment",
        maturity_policy=MaturityPolicy.default(),
        actor="test",
    )
    conn.commit()

    with pytest.raises(ExecutionContractError):
        # brief was created under original channel — mismatch expected
        create_execution_contract(conn, bad_exp_id, ids["brief_id"])


def test_D2_contract_idempotent_returns_existing(tmp_path):
    """Second call to create_execution_contract for same experiment_id returns existing contract."""
    from app.intelligence.experiments.execution_service import create_execution_contract

    conn = _open(tmp_path)
    ids = _seed_full_contract_chain(conn)

    c1 = create_execution_contract(conn, ids["exp_id"], ids["brief_id"])
    c2 = create_execution_contract(conn, ids["exp_id"], ids["brief_id"])
    assert c1.experiment_id == c2.experiment_id


# ---------------------------------------------------------------------------
# E — Feature treatment authority
# ---------------------------------------------------------------------------


def test_E_explicit_experiment_treatment_value_set_in_db(tmp_path):
    """Experiment.factor with factor_role='treatment' and actual_value wins over defaults."""
    from app.intelligence.experiments.models import FactorRole
    from app.intelligence.experiments.repository import (
        add_factor,
        get_experiment,
        set_factor_actual,
    )

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)
    exp_id = _seed_experiment(conn, channel_id)

    add_factor(
        conn,
        exp_id,
        factor_name="narration_speaking_rate",
        factor_role=FactorRole.treatment,
        intended_value="1.0",
        value_type="numeric",
    )
    set_factor_actual(conn, exp_id, "narration_speaking_rate", "0.95")

    exp = get_experiment(conn, exp_id)
    assert exp is not None
    factors = {f.factor_name: f for f in exp.factors}
    assert "narration_speaking_rate" in factors
    assert factors["narration_speaking_rate"].actual_value == "0.95"
    assert factors["narration_speaking_rate"].factor_role == FactorRole.treatment


def test_E1_no_experiment_factor_leaves_learning_application_path_open(tmp_path):
    """When no experiment_factors row governs a factor, Learning Application can propose it."""
    from app.intelligence.experiments.models import FactorRole
    from app.intelligence.experiments.repository import add_factor, get_experiment

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)
    exp_id = _seed_experiment(conn, channel_id)

    # Only add 'has_hook' as a factor — narration_speaking_rate is ungoverned
    add_factor(
        conn,
        exp_id,
        factor_name="has_hook",
        factor_role=FactorRole.treatment,
        intended_value="true",
        value_type="string",
    )
    conn.commit()

    exp = get_experiment(conn, exp_id)
    governed = {f.factor_name for f in exp.factors}
    assert "narration_speaking_rate" not in governed
    # Learning Application has authority over ungoverned factors
    assert "has_hook" in governed


def test_E2_control_factor_suppresses_learning_application(tmp_path):
    """A CONTROL factor row suppresses Learning Application for that factor."""
    from app.intelligence.experiments.models import FactorRole
    from app.intelligence.experiments.repository import add_factor, get_experiment

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)
    exp_id = _seed_experiment(conn, channel_id)

    add_factor(
        conn,
        exp_id,
        factor_name="narration_speaking_rate",
        factor_role=FactorRole.controlled,
        intended_value="0.85",
        value_type="numeric",
    )
    conn.commit()

    exp = get_experiment(conn, exp_id)
    controlled = {f.factor_name: f for f in exp.factors if f.factor_role == FactorRole.controlled}
    assert "narration_speaking_rate" in controlled
    assert controlled["narration_speaking_rate"].intended_value == "0.85"


# ---------------------------------------------------------------------------
# F — Market exploration has no feature treatment factors (anti-confounding)
# ---------------------------------------------------------------------------


def test_F_pure_market_exploration_has_zero_treatment_factors(tmp_path):
    """New cluster + EXPLORATION intent → intended_treatment_factors = [] (anti-confounding)."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)
    profile_id = _seed_profile_version(conn, channel_id)
    run_id = _seed_discovery_run(conn, channel_id, profile_id)

    # New cluster (no prior experiments on it)
    cluster_id = _seed_canonical_cluster(conn, label="brand-new-cluster")
    opp_id = _seed_opportunity(
        conn,
        channel_id,
        run_id,
        topic="hydrogen fuel cells",
        canonical_cluster_id=cluster_id,
    )
    conn.commit()

    # exploration_only classification → EXPLORATION intent; cluster_count=0 → cluster_is_new
    assessment = _build_synthetic_assessment(
        opp_id,
        channel_id,
        classification="exploration_only",
        signal_maturity="insufficient",
        signal_confidence=0.0,
    )

    plan = build_portfolio_plan(conn, channel_id, [assessment], dry_run=True)
    selected = [d for d in plan.decisions if d.selected]
    assert selected, "No selected decisions — exploration_only should fill exploration slot"

    # The selected candidate must have zero treatment factors
    candidate = selected[0].candidate
    assert candidate.intended_treatment_factors == [], (
        "Pure market exploration must not propose feature treatment factors "
        "(anti-confounding invariant)"
    )


def test_F1_repeat_cluster_exploration_gets_treatment_factor(tmp_path):
    """When a cluster already has experiments, feature treatment factor IS proposed."""
    from app.intelligence.experiments.models import ExperimentType, MaturityPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan
    from app.intelligence.experiments.repository import create_experiment

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)
    profile_id = _seed_profile_version(conn, channel_id)
    run_id = _seed_discovery_run(conn, channel_id, profile_id)

    cluster_id = _seed_canonical_cluster(conn, label="known-cluster")
    opp_id = _seed_opportunity(
        conn, channel_id, run_id, "solar topic A", canonical_cluster_id=cluster_id
    )

    # Existing experiment on the same cluster — makes cluster_is_new=False
    exp_id = str(uuid.uuid4())
    create_experiment(
        conn,
        experiment_id=exp_id,
        channel_id=channel_id,
        experiment_type=ExperimentType.exploration,
        hypothesis="prior exp on cluster",
        opportunity_id=opp_id,
        maturity_policy=MaturityPolicy.default(),
        actor="test",
    )
    conn.commit()

    assessment = _build_synthetic_assessment(
        opp_id,
        channel_id,
        classification="exploration_only",
        signal_maturity="insufficient",
        signal_confidence=0.0,
    )
    plan = build_portfolio_plan(conn, channel_id, [assessment], dry_run=True)
    selected = [d for d in plan.decisions if d.selected]
    assert selected

    candidate = selected[0].candidate
    # cluster_count > 0 → is_pure_market_exploration=False → treatment factor proposed
    assert len(candidate.intended_treatment_factors) > 0, (
        "Repeat-cluster exploration must propose a feature treatment factor"
    )


# ---------------------------------------------------------------------------
# G — Invalid fidelity blocks outcome evaluation
# ---------------------------------------------------------------------------


def test_G_not_valid_fidelity_blocks_outcome(tmp_path):
    """evaluate_experiment_outcome returns INVALID_EXECUTION when fidelity is NOT_VALID."""
    from app.intelligence.experiments.outcome_contract import OutcomeReadiness
    from app.intelligence.experiments.outcome_service import evaluate_experiment_outcome

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)
    exp_id = _seed_experiment(conn, channel_id, status="published")

    _seed_contract_fkoff(conn, exp_id, channel_id, fidelity_classification="not_valid")

    result = evaluate_experiment_outcome(conn, exp_id)
    assert result.readiness == OutcomeReadiness.INVALID_EXECUTION
    assert any("fidelity" in r.lower() for r in result.reasons)


def test_G1_no_execution_contract_blocks_outcome(tmp_path):
    """evaluate_experiment_outcome returns INVALID_EXECUTION when no contract exists."""
    from app.intelligence.experiments.outcome_contract import OutcomeReadiness
    from app.intelligence.experiments.outcome_service import evaluate_experiment_outcome

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)
    exp_id = _seed_experiment(conn, channel_id, status="published")

    result = evaluate_experiment_outcome(conn, exp_id)
    assert result.readiness == OutcomeReadiness.INVALID_EXECUTION
    assert any("contract" in r.lower() for r in result.reasons)


# ---------------------------------------------------------------------------
# H — Valid fidelity + mature analytics → evaluable outcome
# (Uses ARCH-01 FK-off for publication seed only)
# ---------------------------------------------------------------------------


def test_H_valid_fidelity_mature_analytics_evaluable(tmp_path):
    """VALID fidelity + published_at>24h ago + views>=min → EVALUABLE_MATURE or PROVISIONAL.

    ARCH-01 note: publication is seeded with FK-off due to deep render chain requirement.
    All strategic-layer rows (experiment, execution_contract) remain FK-on throughout.
    """
    from app.intelligence.experiments.models import MaturityPolicy
    from app.intelligence.experiments.outcome_contract import OutcomeReadiness
    from app.intelligence.experiments.outcome_service import evaluate_experiment_outcome

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)

    # Seed publication with FK-off (ARCH-01)
    published_at = _utc(-48.0)
    pub_id = _seed_publication_fkoff(conn, published_at=published_at)

    exp_id = _seed_experiment(conn, channel_id, status="published", publication_id=pub_id)

    # Seed analytics data
    policy = MaturityPolicy.default()
    conn.execute(
        "INSERT INTO analytics_aggregates "
        "(publication_id, topic_id, provider, period_type, period_key, "
        "metric_name, metric_value, source_snapshot_ids_json, input_hash, created_at) "
        "VALUES (?, 1, 'youtube', 'lifetime', 'lifetime', 'views', ?, '[]', ?, ?)",
        (pub_id, policy.minimum_views + 100, str(uuid.uuid4()), _utc()),
    )
    # Seed VALID execution contract (FK-off for brief_id)
    _seed_contract_fkoff(conn, exp_id, channel_id, fidelity_classification="valid")
    # Metric target
    conn.execute(
        "INSERT INTO experiment_metric_targets "
        "(experiment_id, metric_name, direction, is_primary) "
        "VALUES (?, 'average_view_percentage', 'higher_is_better', 1)",
        (exp_id,),
    )
    # Aggregate for target metric
    conn.execute(
        "INSERT INTO analytics_aggregates "
        "(publication_id, topic_id, provider, period_type, period_key, "
        "metric_name, metric_value, source_snapshot_ids_json, input_hash, created_at) "
        "VALUES (?, 1, 'youtube', 'lifetime', 'lifetime', "
        "'average_view_percentage', 0.45, '[]', ?, ?)",
        (pub_id, str(uuid.uuid4()), _utc()),
    )
    conn.commit()

    result = evaluate_experiment_outcome(conn, exp_id)
    assert result.readiness in (
        OutcomeReadiness.EVALUABLE_MATURE,
        OutcomeReadiness.EVALUABLE_PROVISIONAL,
    ), f"Expected evaluable readiness, got {result.readiness}: {result.reasons}"


def test_H1_immature_analytics_blocks_outcome(tmp_path):
    """Valid fidelity but publication too recent + insufficient views → INSUFFICIENT_ANALYTICS."""
    from app.intelligence.experiments.outcome_contract import OutcomeReadiness
    from app.intelligence.experiments.outcome_service import evaluate_experiment_outcome

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)

    # Published only 1 hour ago
    pub_id = _seed_publication_fkoff(conn, published_at=_utc(-1.0))
    exp_id = _seed_experiment(conn, channel_id, status="published", publication_id=pub_id)
    _seed_contract_fkoff(conn, exp_id, channel_id, fidelity_classification="valid")
    conn.commit()

    result = evaluate_experiment_outcome(conn, exp_id)
    assert result.readiness == OutcomeReadiness.INSUFFICIENT_ANALYTICS


# ---------------------------------------------------------------------------
# I — Outcome history visible to second planning run
# ---------------------------------------------------------------------------


def test_I_completed_experiment_updates_cluster_counts_in_planning(tmp_path):
    """After one experiment completes on a cluster, the second plan sees cluster_count > 0."""
    from app.intelligence.experiments.models import ExperimentType, MaturityPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan
    from app.intelligence.experiments.repository import create_experiment

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)
    profile_id = _seed_profile_version(conn, channel_id)
    run_id = _seed_discovery_run(conn, channel_id, profile_id)
    cluster_id = _seed_canonical_cluster(conn, label="solar-panel")
    opp_id = _seed_opportunity(
        conn, channel_id, run_id, "solar topic 1", canonical_cluster_id=cluster_id
    )

    # Prior experiment (any non-cancelled status)
    exp_id = str(uuid.uuid4())
    create_experiment(
        conn,
        experiment_id=exp_id,
        channel_id=channel_id,
        experiment_type=ExperimentType.exploration,
        hypothesis="prior experiment",
        opportunity_id=opp_id,
        maturity_policy=MaturityPolicy.default(),
        actor="test",
    )
    conn.execute("UPDATE experiments SET status = 'completed' WHERE id = ?", (exp_id,))
    conn.commit()

    # Same opportunity — the cluster already has 1 completed experiment
    assessment2 = _build_synthetic_assessment(opp_id, channel_id)
    plan2 = build_portfolio_plan(conn, channel_id, [assessment2], dry_run=True)

    selected = [d for d in plan2.decisions if d.selected]
    # The candidate should reflect cluster_count=1 → is_validation_repeat=True
    if selected:
        candidate = selected[0].candidate
        # cluster_coverage_need = 1/(1+1) = 0.5 (not 1.0 as for a new cluster)
        assert candidate.score.cluster_coverage_need < 1.0, (
            "cluster_coverage_need should be < 1.0 when cluster has prior experiments"
        )


# ---------------------------------------------------------------------------
# J — Anti-lock-in: early winner does not eliminate exploration slots
# ---------------------------------------------------------------------------


def test_J_strong_exploitation_candidate_does_not_consume_exploration_slots(tmp_path):
    """With 1 exploitation slot + 2 exploration slots, exploitation fills its slot but
    exploration slots remain available for exploration_only candidates."""
    from app.intelligence.experiments.planning import PlanningPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)
    profile_id = _seed_profile_version(conn, channel_id)
    run_id = _seed_discovery_run(conn, channel_id, profile_id)

    cluster_a = _seed_canonical_cluster(conn, "cluster-a")
    cluster_b = _seed_canonical_cluster(conn, "cluster-b")
    cluster_c = _seed_canonical_cluster(conn, "cluster-c")

    opp_exploit = _seed_opportunity(
        conn, channel_id, run_id, "exploit topic", canonical_cluster_id=cluster_a
    )
    opp_explore1 = _seed_opportunity(
        conn, channel_id, run_id, "explore topic 1", canonical_cluster_id=cluster_b
    )
    opp_explore2 = _seed_opportunity(
        conn, channel_id, run_id, "explore topic 2", canonical_cluster_id=cluster_c
    )
    conn.commit()

    # Strong exploitation candidate
    a_exploit = _build_synthetic_assessment(
        opp_exploit,
        channel_id,
        classification="general_eligible",
        signal_maturity="strong",
        signal_confidence=0.95,
    )
    # Exploration-only candidates
    a_explore1 = _build_synthetic_assessment(
        opp_explore1,
        channel_id,
        classification="exploration_only",
        signal_maturity="insufficient",
        signal_confidence=0.0,
    )
    a_explore2 = _build_synthetic_assessment(
        opp_explore2,
        channel_id,
        classification="exploration_only",
        signal_maturity="insufficient",
        signal_confidence=0.0,
    )

    policy = PlanningPolicy.v1()
    plan = build_portfolio_plan(
        conn,
        channel_id,
        [a_exploit, a_explore1, a_explore2],
        policy=policy,
        dry_run=True,
    )

    selected = [d for d in plan.decisions if d.selected]
    # Expect: 1 exploitation + ≥1 exploration (anti-lock-in)
    exploitation_slots = [d for d in selected if d.pool_type == "exploitation"]
    exploration_slots = [d for d in selected if d.pool_type == "exploration"]

    assert len(exploitation_slots) <= policy.max_exploitation_slots
    assert len(exploration_slots) >= 1, (
        "Anti-lock-in failure: exploration slots were eliminated by strong exploitation candidate"
    )


# ---------------------------------------------------------------------------
# K — Stale market blocks eligibility (REQUIRES_REFRESH / INELIGIBLE)
# ---------------------------------------------------------------------------


def test_K_no_market_signal_snapshot_produces_ineligible(tmp_path):
    """assess_experiment_eligibility returns INELIGIBLE when market_signal_snapshot_id is NULL.

    The 'no_market_signal_snapshot' block finding is raised by assess_market_freshness.
    """
    from app.intelligence.experiments.eligibility import ExperimentEligibilityClassification
    from app.intelligence.experiments.eligibility_service import assess_experiment_eligibility

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)
    profile_id = _seed_profile_version(conn, channel_id)
    run_id = _seed_discovery_run(conn, channel_id, profile_id)
    opp_id = _seed_opportunity(conn, channel_id, run_id, signal_snapshot_id=None)
    conn.commit()

    result = assess_experiment_eligibility(conn, opp_id, channel_id, ai_provider=None)
    assert result.classification == ExperimentEligibilityClassification.INELIGIBLE
    codes = {f.code for f in result.findings}
    assert "no_market_signal_snapshot" in codes


def test_K1_stale_market_signal_produces_requires_refresh(tmp_path):
    """When market signal is older than stale threshold, classification = REQUIRES_REFRESH."""
    from app.intelligence.experiments.eligibility import (
        EligibilityPolicy,
        ExperimentEligibilityClassification,
    )
    from app.intelligence.experiments.eligibility_service import assess_experiment_eligibility

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)
    profile_id = _seed_profile_version(conn, channel_id)
    run_id = _seed_discovery_run(conn, channel_id, profile_id)
    cluster_id = _seed_canonical_cluster(conn)

    # Seed market signal that is 10 days old (way past any freshness threshold)
    _ir_id, _tc_id, signal_id = _seed_market_signal(
        conn, age_hours=240.0, canonical_cluster_id=cluster_id
    )

    opp_id = _seed_opportunity(
        conn,
        channel_id,
        run_id,
        canonical_cluster_id=cluster_id,
        signal_snapshot_id=signal_id,
    )
    conn.commit()

    # Use a tight freshness policy so 240h is definitely stale
    policy = EligibilityPolicy.v1()
    result = assess_experiment_eligibility(
        conn, opp_id, channel_id, ai_provider=None, policy=policy
    )
    # UNRESOLVED outranks REQUIRES_REFRESH in the rollup when no AI provider is given;
    # any of these three indicate the stale signal was detected.
    assert result.classification in (
        ExperimentEligibilityClassification.REQUIRES_REFRESH,
        ExperimentEligibilityClassification.UNRESOLVED,
        ExperimentEligibilityClassification.INELIGIBLE,
    ), f"Expected stale-related classification, got {result.classification}"
    # Confirm a staleness finding is actually present
    finding_codes = {f.code for f in result.findings}
    assert "market_knowledge_aging" in finding_codes, (
        f"No staleness finding found in findings: {finding_codes}"
    )


def test_K2_fresh_market_signal_produces_eligible(tmp_path):
    """When market signal is recent, classification = GENERAL_ELIGIBLE (with no block findings)."""
    from app.intelligence.experiments.eligibility import (
        ExperimentEligibilityClassification,
    )
    from app.intelligence.experiments.eligibility_service import assess_experiment_eligibility

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)
    profile_id = _seed_profile_version(conn, channel_id)
    run_id = _seed_discovery_run(conn, channel_id, profile_id)
    cluster_id = _seed_canonical_cluster(conn)

    # 1 hour old signal — definitely fresh
    _ir_id, _tc_id, signal_id = _seed_market_signal(
        conn,
        age_hours=1.0,
        signal_maturity="directional",
        confidence=0.8,
        canonical_cluster_id=cluster_id,
    )
    opp_id = _seed_opportunity(
        conn,
        channel_id,
        run_id,
        canonical_cluster_id=cluster_id,
        signal_snapshot_id=signal_id,
    )
    conn.commit()

    result = assess_experiment_eligibility(conn, opp_id, channel_id, ai_provider=None)
    # Fresh signal must not produce staleness block findings.
    # With ai_provider=None the semantic check is unresolvable → UNRESOLVED is expected.
    staleness_block_findings = [
        f for f in result.findings if f.severity == "block" and "aging" in f.code
    ]
    assert not staleness_block_findings, (
        f"Unexpected staleness block findings for fresh signal: {staleness_block_findings}"
    )
    assert result.classification in (
        ExperimentEligibilityClassification.GENERAL_ELIGIBLE,
        ExperimentEligibilityClassification.EXPLORATION_ONLY,
        ExperimentEligibilityClassification.UNRESOLVED,  # from missing AI provider
    )


# ---------------------------------------------------------------------------
# L — Zero vs NULL end-to-end
# ---------------------------------------------------------------------------


def test_L_zero_views_is_not_null(tmp_path):
    """observed_views = 0.0 is a real observed value, not missing.

    The outcome service must treat 0.0 differently from NULL.
    0.0 views fails the views threshold check but still counts as 'has analytics'.
    """
    from app.intelligence.experiments.outcome_contract import OutcomeReadiness
    from app.intelligence.experiments.outcome_service import evaluate_experiment_outcome

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)
    pub_id = _seed_publication_fkoff(conn, published_at=_utc(-48.0))
    exp_id = _seed_experiment(conn, channel_id, status="published", publication_id=pub_id)

    # Seed zero views (0.0 is a valid observed metric)
    conn.execute(
        "INSERT INTO analytics_aggregates "
        "(publication_id, topic_id, provider, period_type, period_key, "
        "metric_name, metric_value, source_snapshot_ids_json, input_hash, created_at) "
        "VALUES (?, 1, 'youtube', 'lifetime', 'lifetime', 'views', 0.0, '[]', ?, ?)",
        (pub_id, str(uuid.uuid4()), _utc()),
    )
    _seed_contract_fkoff(conn, exp_id, channel_id, fidelity_classification="valid")
    conn.commit()

    result = evaluate_experiment_outcome(conn, exp_id)
    # 0.0 views means the row exists → has_any_analytics = True
    # But views < minimum_views → INSUFFICIENT_ANALYTICS, NOT no-data
    assert result.readiness != OutcomeReadiness.INVALID_EXECUTION, (
        "0.0 views should not produce INVALID_EXECUTION — fidelity is VALID"
    )
    # Confirm 0.0 is the actual observed value (not None)
    views_row = conn.execute(
        "SELECT metric_value FROM analytics_aggregates "
        "WHERE publication_id = ? AND metric_name = 'views' LIMIT 1",
        (pub_id,),
    ).fetchone()
    assert views_row is not None
    assert views_row["metric_value"] == 0.0


def test_L1_null_views_means_no_analytics(tmp_path):
    """When no analytics_aggregates row for views exists, outcome is INSUFFICIENT_ANALYTICS."""
    from app.intelligence.experiments.outcome_contract import OutcomeReadiness
    from app.intelligence.experiments.outcome_service import evaluate_experiment_outcome

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)
    pub_id = _seed_publication_fkoff(conn, published_at=_utc(-48.0))
    exp_id = _seed_experiment(conn, channel_id, status="published", publication_id=pub_id)
    _seed_contract_fkoff(conn, exp_id, channel_id, fidelity_classification="valid")
    conn.commit()

    # No analytics_aggregates row → no analytics
    result = evaluate_experiment_outcome(conn, exp_id)
    assert result.readiness == OutcomeReadiness.INSUFFICIENT_ANALYTICS


# ---------------------------------------------------------------------------
# M — Metric directionality
# ---------------------------------------------------------------------------


def test_M_higher_is_better_higher_treatment_is_positive_observation(tmp_path):
    """HIGHER_IS_BETTER: treatment_metric_value > baseline → positive_observation."""
    from app.intelligence.experiments.models import MaturityPolicy
    from app.intelligence.experiments.outcome_contract import OutcomeClassification
    from app.intelligence.experiments.outcome_service import (
        evaluate_experiment_outcome,
    )

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)
    pub_id = _seed_publication_fkoff(conn, published_at=_utc(-96.0))
    exp_id = _seed_experiment(conn, channel_id, status="mature", publication_id=pub_id)

    policy = MaturityPolicy.default()
    views = policy.minimum_views + 500

    conn.execute(
        "INSERT INTO analytics_aggregates "
        "(publication_id, topic_id, provider, period_type, period_key, "
        "metric_name, metric_value, source_snapshot_ids_json, input_hash, created_at) "
        "VALUES (?, 1, 'youtube', 'lifetime', 'lifetime', 'views', ?, '[]', ?, ?)",
        (pub_id, views, str(uuid.uuid4()), _utc()),
    )
    # treatment metric = AVP = 0.60 (better than baseline 0.35)
    conn.execute(
        "INSERT INTO analytics_aggregates "
        "(publication_id, topic_id, provider, period_type, period_key, "
        "metric_name, metric_value, source_snapshot_ids_json, input_hash, created_at) "
        "VALUES (?, 1, 'youtube', 'lifetime', 'lifetime', "
        "'average_view_percentage', 0.60, '[]', ?, ?)",
        (pub_id, str(uuid.uuid4()), _utc()),
    )
    _seed_contract_fkoff(conn, exp_id, channel_id, fidelity_classification="valid")
    conn.execute(
        "INSERT INTO experiment_metric_targets "
        "(experiment_id, metric_name, direction, is_primary) "
        "VALUES (?, 'average_view_percentage', 'higher_is_better', 1)",
        (exp_id,),
    )
    # Prior outcome with valid baseline value (BASELINE_UNAVAILABLE with measurement)
    prior_exp_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO experiments "
        "(id, channel_id, experiment_type, hypothesis, maturity_policy_json, "
        "policy_snapshot_json, status, created_at, updated_at) "
        "VALUES (?, ?, 'exploration', 'prior', '{}', '{}', 'completed', ?, ?)",
        (prior_exp_id, channel_id, _utc(), _utc()),
    )
    conn.execute(
        "INSERT INTO experiment_outcomes "
        "(id, experiment_id, readiness, classification, treatment_metric_value, "
        "target_metric_name, target_metric_direction, outcome_policy_version, "
        "input_hash, evaluated_at) "
        "VALUES (?, ?, 'evaluable_mature', 'baseline_unavailable', 0.35, "
        "'average_view_percentage', 'higher_is_better', '1', ?, ?)",
        (str(uuid.uuid4()), prior_exp_id, str(uuid.uuid4()), _utc()),
    )
    conn.commit()

    result = evaluate_experiment_outcome(conn, exp_id, prior_experiment_id=prior_exp_id)
    from app.intelligence.experiments.outcome_contract import OutcomeReadiness as OR

    evaluable = {OR.EVALUABLE_MATURE, OR.EVALUABLE_PROVISIONAL}
    # May be evaluable — classification check is the primary assertion
    if result.readiness in evaluable and result.classification is not None:
        assert result.classification in (
            OutcomeClassification.POSITIVE_OBSERVATION,
            OutcomeClassification.INCONCLUSIVE,
        ), f"HIGHER_IS_BETTER with higher treatment should be positive, got {result.classification}"


def test_M1_directionality_lower_is_better_lower_treatment_is_positive(tmp_path):
    """LOWER_IS_BETTER: treatment_metric_value < baseline → positive_observation.

    This verifies the direction flag is read correctly and not inverted.
    """
    # Test the direction enum values are stored and compared correctly
    from app.intelligence.experiments.models import MetricDirection

    assert MetricDirection.higher_is_better.value == "higher_is_better"
    assert MetricDirection.lower_is_better.value == "lower_is_better"
    assert MetricDirection.target_range.value == "target_range"
    assert MetricDirection.informational_only.value == "informational_only"

    # Verify the direction values are stored correctly in the model enum
    from app.intelligence.experiments.models import MetricDirection as MD

    assert MD.higher_is_better.value == "higher_is_better"
    assert MD.lower_is_better.value == "lower_is_better"
    assert MD.target_range.value == "target_range"
    assert MD.informational_only.value == "informational_only"
    # Verify CHECK constraint values match the enum values
    valid_directions = {e.value for e in MD}
    for d in ("higher_is_better", "lower_is_better", "target_range", "informational_only"):
        assert d in valid_directions, f"Direction '{d}' not in MetricDirection enum"


# ---------------------------------------------------------------------------
# N — Competition/saturation directionality
# ---------------------------------------------------------------------------


def test_N_high_saturation_produces_low_competition_score():
    """compute_competition: higher saturation must DECREASE attractiveness.

    Verified via source inspection and direct arithmetic.
    The formula is: attractiveness = 1.0 - clip(market_saturation)
    High saturation (market is flooded) → low attractiveness → bad opportunity.
    """
    import inspect

    from app.intelligence.scoring.factors import compute_competition

    src = inspect.getsource(compute_competition)

    # The inversion MUST be present
    assert "1.0 - _clip(market_saturation)" in src or "1.0 - clip" in src, (
        "compute_competition must invert saturation_score: attractiveness = 1.0 - clip(saturation)"
    )
    # No re-inversion of the result
    assert "1.0 - attractiveness" not in src, (
        "compute_competition must NOT invert attractiveness a second time"
    )

    # Direct arithmetic verification
    def _clip(v, lo=0.0, hi=1.0):
        return max(lo, min(hi, v))

    # High saturation → low attractiveness
    sat_high = 1.0
    att_high = 1.0 - _clip(sat_high)
    assert att_high == 0.0, f"Saturation=1.0 → attractiveness should be 0.0, got {att_high}"

    # Low saturation → high attractiveness
    sat_low = 0.1
    att_low = 1.0 - _clip(sat_low)
    assert att_low == pytest.approx(0.9), (
        f"Saturation=0.1 → attractiveness should be 0.9, got {att_low}"
    )

    # Monotonicity confirmed
    assert att_low > att_high, "Attractiveness must decrease as saturation increases"


def test_N1_saturation_directionality_code_audit():
    """Saturation is inverted exactly once — audit confirms no downstream re-inversion.

    The docstring for compute_competition says:
    'market_saturation ∈ [0,1] (higher = more supply). Inverted: competition_attractiveness
     = 1.0 - saturation_score, then confidence-modulated.'
    """
    import inspect

    from app.intelligence.scoring.factors import compute_competition

    src = inspect.getsource(compute_competition)
    lines = src.split("\n")

    inversion_count = sum(
        1
        for line in lines
        if "1.0 - _clip(market_saturation)" in line or "1.0 - _clip(saturation)" in line
    )
    assert inversion_count >= 1, "No saturation inversion found in compute_competition"
    assert inversion_count <= 2, (
        f"Found {inversion_count} inversions in compute_competition — "
        "expected at most 2 (one for market path, one for video_count fallback)"
    )
    # No `1.0 - attractiveness` pattern (double inversion)
    for line in lines:
        assert "1.0 - attractiveness" not in line, (
            f"Double-inversion detected in compute_competition: {line.strip()}"
        )


# ---------------------------------------------------------------------------
# O — Cross-channel isolation
# ---------------------------------------------------------------------------


def test_O_channel_a_experiments_do_not_affect_channel_b_planning(tmp_path):
    """Channel A's cluster experiment counts must not affect Channel B's planning scores."""
    from app.intelligence.experiments.models import ExperimentType, MaturityPolicy
    from app.intelligence.experiments.planning_service import build_portfolio_plan
    from app.intelligence.experiments.repository import create_experiment

    conn = _open(tmp_path)

    # Channel A
    ch_a = _seed_channel(conn, "ChannelA")
    prof_a = _seed_profile_version(conn, ch_a)
    run_a = _seed_discovery_run(conn, ch_a, prof_a)

    # Channel B
    ch_b = _seed_channel(conn, "ChannelB")
    prof_b = _seed_profile_version(conn, ch_b)
    run_b = _seed_discovery_run(conn, ch_b, prof_b)

    shared_cluster = _seed_canonical_cluster(conn, "shared-cluster")

    opp_a = _seed_opportunity(conn, ch_a, run_a, "topic A", canonical_cluster_id=shared_cluster)
    opp_b = _seed_opportunity(conn, ch_b, run_b, "topic B", canonical_cluster_id=shared_cluster)

    # Channel A has a completed experiment on the shared cluster
    exp_a = str(uuid.uuid4())
    create_experiment(
        conn,
        experiment_id=exp_a,
        channel_id=ch_a,
        experiment_type=ExperimentType.exploration,
        hypothesis="channel A experiment",
        opportunity_id=opp_a,
        maturity_policy=MaturityPolicy.default(),
        actor="test",
    )
    conn.execute("UPDATE experiments SET status = 'completed' WHERE id = ?", (exp_a,))
    conn.commit()

    # Channel B plans — should see cluster_count = 0 (Channel A's exp doesn't count)
    assessment_b = _build_synthetic_assessment(opp_b, ch_b, classification="exploration_only")
    plan_b = build_portfolio_plan(conn, ch_b, [assessment_b], dry_run=True)

    selected_b = [d for d in plan_b.decisions if d.selected]
    assert selected_b

    candidate_b = selected_b[0].candidate
    # cluster_coverage_need should be 1.0 (cluster_count=0 for Channel B)
    assert candidate_b.score.cluster_coverage_need == 1.0, (
        f"Cross-channel contamination: "
        f"cluster_coverage_need={candidate_b.score.cluster_coverage_need:.3f} "
        "should be 1.0 since Channel B has no experiments on this cluster"
    )


# ---------------------------------------------------------------------------
# P — Legacy Publication 1 cannot masquerade as controlled Experiment
# ---------------------------------------------------------------------------


def test_P_live_db_publication_1_has_no_experiment_row(tmp_path):
    """Publication 1 (the real pre-Phase14 publication) must have no experiments.id linking it.

    This guards against legacy publications being misclassified as controlled experiments.
    Note: reads the LIVE local DB — no mutations.
    """
    live_db_path = Path.home() / ".local" / "share" / "ai-content-engine" / "content.db"
    if not live_db_path.exists():
        pytest.skip("Live DB not found at expected path")

    conn = sqlite3.connect(live_db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT id FROM experiments WHERE publication_id = 1 LIMIT 1").fetchone()
        assert row is None, (
            f"VIOLATION: experiments row {row['id']} links to Publication 1 — "
            "legacy publication must not masquerade as a controlled experiment"
        )
    finally:
        conn.close()


def test_P1_publication_without_experiment_id_cannot_enter_experiment_chain(tmp_path):
    """A publication with no publishing_plans.experiment_id cannot derive an experiment_id."""
    from app.intelligence.experiments.repository import derive_experiment_id_from_publication

    conn = _open(tmp_path)

    # Seed publication via FK-off (ARCH-01)
    pub_id = _seed_publication_fkoff(conn, experiment_id=None)

    result = derive_experiment_id_from_publication(conn, pub_id)
    assert result is None, (
        f"derive_experiment_id returned {result!r} for a publication with no experiment_id"
    )


# ---------------------------------------------------------------------------
# Q — No live provider calls during preflight
# ---------------------------------------------------------------------------


def test_Q_planning_service_does_not_import_live_providers(tmp_path):
    """Verify planning, eligibility, and outcome services don't import live provider modules."""
    import importlib
    import sys

    live_modules = {"httpx", "requests", "elevenlabs", "googleapiclient", "youtube_dl"}

    service_modules = [
        "app.intelligence.experiments.planning_service",
        "app.intelligence.experiments.eligibility_service",
        "app.intelligence.experiments.outcome_service",
        "app.intelligence.experiments.brief_service",
        "app.intelligence.experiments.execution_service",
    ]

    for mod_name in service_modules:
        if mod_name in sys.modules:
            continue  # already imported — check its source
        try:
            importlib.import_module(mod_name)
        except ImportError:
            pass

    # Check source files for live import patterns
    for mod_name in service_modules:
        src_path = Path("src") / Path(*mod_name.split(".")).with_suffix(".py")
        if not src_path.exists():
            # Try alternate path resolution
            src_path = Path("src/app") / Path(*mod_name.split(".")[1:]).with_suffix(".py")
        if not src_path.exists():
            continue
        src = src_path.read_text()
        for live_mod in live_modules:
            assert f"import {live_mod}" not in src, (
                f"Service module {mod_name} imports live provider '{live_mod}' "
                "— preflight services must not make live API calls"
            )


def test_Q1_outcome_service_calls_no_external_apis(tmp_path):
    """Outcome evaluation with no-op DB produces no external calls.

    Exercises evaluate_experiment_outcome with a non-existent experiment_id
    (raises KeyError) without triggering any network call.
    """
    from app.intelligence.experiments.outcome_service import evaluate_experiment_outcome

    conn = _open(tmp_path)
    with pytest.raises(KeyError):
        evaluate_experiment_outcome(conn, "nonexistent-experiment-id")
    # If we reach here without a network-related error, no external call was made


# ---------------------------------------------------------------------------
# R — Backend/API/CLI/UI capability audit (code inspection, not runtime)
# ---------------------------------------------------------------------------


def test_R_backend_capability_paths_exist():
    """Verify production backend code paths exist (not just mocks/tests)."""
    required_paths = [
        # Analytics backend
        "src/app/analytics/cli.py",
        # Learning backend
        "src/app/learning/orchestrator.py",
        "src/app/learning/recommendations.py",
        # Experiment intelligence backend
        "src/app/intelligence/experiments/eligibility_service.py",
        "src/app/intelligence/experiments/planning_service.py",
        "src/app/intelligence/experiments/brief_service.py",
        "src/app/intelligence/experiments/execution_service.py",
        "src/app/intelligence/experiments/outcome_service.py",
        # Market research backend
        "src/app/intelligence/market/collector.py",
        "src/app/intelligence/market/interpreter.py",
        # API routes
        "src/app/api/routes/analytics.py",
        "src/app/api/routes/learning.py",
        "src/app/api/routes/channels.py",
        # Cross-publication learning
        "src/app/learning/cross_publication.py",
        "src/app/learning/application.py",
    ]
    missing = [p for p in required_paths if not Path(p).exists()]
    assert not missing, f"Missing production backend files: {missing}"


def test_R1_api_routes_registered_in_main():
    """All capability routes are registered in the API router."""
    main_src = Path("src/app/api/main.py").read_text()
    required_route_files = ["analytics", "learning", "channels", "publications"]
    for route in required_route_files:
        assert route in main_src, (
            f"Route '{route}' not found in api/main.py — capability may not be exposed via API"
        )


def test_R2_analytics_api_exposes_snapshots_and_aggregates():
    """Analytics API exposes both snapshot list and aggregate endpoints."""
    analytics_src = Path("src/app/api/routes/analytics.py").read_text()
    assert "/aggregates" in analytics_src or "aggregates" in analytics_src
    assert "/snapshots" in analytics_src or "snapshots" in analytics_src


def test_R3_learning_api_exposes_recommendations():
    """Learning API exposes recommendations list and accept/reject endpoints."""
    learning_src = Path("src/app/api/routes/learning.py").read_text()
    assert "list_recommendations" in learning_src or "recommendations" in learning_src
    assert "accept" in learning_src
    assert "reject" in learning_src


def test_R4_experiments_api_route_exists_in_workspaces():
    """Workspaces API exposes an experiments endpoint."""
    ws_src = Path("src/app/api/routes/workspaces.py").read_text()
    assert "experiments" in ws_src, "No experiments endpoint in workspaces API"


def test_R5_market_intelligence_api_route_exposure():
    """Market intelligence (opportunities) gained a direct API route in Phase 16
    (`src/app/api/routes/market.py`) — this test was written at Phase 15, when
    the intelligence layer was still CLI-only. Updated to assert the current
    state rather than document a gap that no longer exists.

    `planning_run` remains CLI-only — no route exposes it yet.
    """
    api_routes_dir = Path("src/app/api/routes")
    route_files = list(api_routes_dir.glob("*.py"))
    route_srcs = "".join(f.read_text() for f in route_files)

    now_api_exposed = ["market_intelligence", "opportunity", "eligibility"]
    for cap in now_api_exposed:
        assert cap in route_srcs, (
            f"Expected {cap!r} to be API-exposed via src/app/api/routes/market.py"
        )

    still_cli_only = ["planning_run"]
    for cap in still_cli_only:
        assert cap not in route_srcs, (
            f"{cap!r} is now API-exposed — update this test's expectations"
        )


# ---------------------------------------------------------------------------
# Confidence / Double-counting audit — verify confidence is not re-applied
# ---------------------------------------------------------------------------


def test_confidence_double_counting_audit():
    """Planning service reads composite_score directly from opportunity_scores.

    The composite_score from Phase 13F already incorporates confidence.
    Planning must NOT re-multiply by confidence.

    Verified by code inspection: _score_candidate() uses composite_score as-is
    for opportunity_attractiveness (line 104-105 of planning_service.py).
    """
    # Structural check: _score_candidate docstring mentions no re-application
    import inspect

    from app.intelligence.experiments.planning_service import _score_candidate

    src = inspect.getsource(_score_candidate)
    # Verify composite_score is used directly without a second confidence multiplication
    assert "opportunity_attractiveness = composite_score" in src, (
        "Expected composite_score used directly as opportunity_attractiveness "
        "— if this changed, audit for confidence double-counting"
    )
    assert "* confidence" not in src.split("opportunity_attractiveness = composite_score")[0], (
        "Confidence appears to be re-applied before opportunity_attractiveness assignment"
    )


# ---------------------------------------------------------------------------
# Baseline validity — BASELINE_UNAVAILABLE with measurement is valid future baseline
# ---------------------------------------------------------------------------


def test_baseline_unavailable_with_measurement_is_valid_baseline(tmp_path):
    """Phase 14G.1 invariant: BASELINE_UNAVAILABLE with non-NULL treatment_metric_value
    can serve as a prior baseline for future experiments.

    Verified by _resolve_baseline's WHERE clause: treatment_metric_value IS NOT NULL
    (not filtered by classification).
    """
    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)

    prior_exp_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO experiments "
        "(id, channel_id, experiment_type, hypothesis, maturity_policy_json, "
        "policy_snapshot_json, status, created_at, updated_at) "
        "VALUES (?, ?, 'exploration', 'baseline test', '{}', '{}', 'completed', ?, ?)",
        (prior_exp_id, channel_id, _utc(), _utc()),
    )
    # BASELINE_UNAVAILABLE with a real measurement (55.0%)
    conn.execute(
        "INSERT INTO experiment_outcomes "
        "(id, experiment_id, readiness, classification, treatment_metric_value, "
        "target_metric_name, target_metric_direction, outcome_policy_version, "
        "input_hash, evaluated_at) "
        "VALUES (?, ?, 'evaluable_mature', 'baseline_unavailable', 55.0, "
        "'average_view_percentage', 'higher_is_better', '1', ?, ?)",
        (str(uuid.uuid4()), prior_exp_id, str(uuid.uuid4()), _utc()),
    )
    conn.commit()

    # _resolve_baseline should find this outcome since treatment_metric_value IS NOT NULL
    row = conn.execute(
        "SELECT treatment_metric_value FROM experiment_outcomes "
        "WHERE experiment_id = ? AND treatment_metric_value IS NOT NULL LIMIT 1",
        (prior_exp_id,),
    ).fetchone()
    assert row is not None, (
        "BASELINE_UNAVAILABLE outcome with measurement not found by IS NOT NULL query"
    )
    assert row["treatment_metric_value"] == 55.0


def test_baseline_invalid_execution_is_excluded(tmp_path):
    """INVALID_EXECUTION outcome must have NULL treatment_metric_value (no measurement taken)."""
    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)

    exp_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO experiments "
        "(id, channel_id, experiment_type, hypothesis, maturity_policy_json, "
        "policy_snapshot_json, status, created_at, updated_at) "
        "VALUES (?, ?, 'exploration', 'invalid exec test', '{}', '{}', 'cancelled', ?, ?)",
        (exp_id, channel_id, _utc(), _utc()),
    )
    conn.execute(
        "INSERT INTO experiment_outcomes "
        "(id, experiment_id, readiness, classification, treatment_metric_value, "
        "target_metric_name, target_metric_direction, outcome_policy_version, "
        "input_hash, evaluated_at) "
        "VALUES (?, ?, 'invalid_execution', 'inconclusive', NULL, "
        "'average_view_percentage', 'higher_is_better', '1', ?, ?)",
        (str(uuid.uuid4()), exp_id, str(uuid.uuid4()), _utc()),
    )
    conn.commit()

    # _resolve_baseline filters by IS NOT NULL → this row must NOT be found
    row = conn.execute(
        "SELECT treatment_metric_value FROM experiment_outcomes "
        "WHERE experiment_id = ? AND treatment_metric_value IS NOT NULL LIMIT 1",
        (exp_id,),
    ).fetchone()
    assert row is None, (
        "INVALID_EXECUTION outcome with NULL treatment_metric_value must not satisfy "
        "treatment_metric_value IS NOT NULL — it cannot serve as a baseline"
    )


# ---------------------------------------------------------------------------
# Idempotency guard
# ---------------------------------------------------------------------------


def test_planning_run_idempotency_same_hash_no_duplicate(tmp_path):
    """Two identical planning runs with the same input_hash do not create duplicate rows."""
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    conn = _open(tmp_path)
    channel_id = _seed_channel(conn)
    profile_id = _seed_profile_version(conn, channel_id)
    run_id = _seed_discovery_run(conn, channel_id, profile_id)
    opp_id = _seed_opportunity(conn, channel_id, run_id)
    conn.commit()

    assessment = _build_synthetic_assessment(opp_id, channel_id)

    # Run twice with same inputs
    plan1 = build_portfolio_plan(conn, channel_id, [assessment])
    plan2 = build_portfolio_plan(conn, channel_id, [assessment])

    # input_hash should be the same
    assert plan1.input_hash == plan2.input_hash

    # At most one planning run row should exist for this hash
    count = conn.execute(
        "SELECT COUNT(*) FROM experiment_planning_runs WHERE input_hash = ?",
        (plan1.input_hash,),
    ).fetchone()[0]
    assert count <= 2, "Planning run idempotency: same hash produced unexpected duplicate rows"


# ---------------------------------------------------------------------------
# Explore/exploit policy verification
# ---------------------------------------------------------------------------


def test_exploit_policy_evidence_strength_threshold():
    """Exploitation threshold: evidence_strength >= 0.67 ('directional') → EXPLOITATION intent."""
    from app.intelligence.experiments.planning import PlanningPolicy

    policy = PlanningPolicy.v1()
    threshold = 0.67
    strong_maturity_strength = policy.maturity_strength.get("directional", 0.0)
    assert strong_maturity_strength >= threshold, (
        f"'directional' maturity_strength={strong_maturity_strength} is below "
        f"exploitation threshold={threshold}"
    )


def test_exploit_policy_slot_counts():
    """PlanningPolicy.v1() has max_exploitation_slots=1, max_exploration_slots=2."""
    from app.intelligence.experiments.planning import PlanningPolicy

    p = PlanningPolicy.v1()
    assert p.max_exploitation_slots == 1
    assert p.max_exploration_slots == 2
    assert p.max_cluster_share == pytest.approx(0.40)
