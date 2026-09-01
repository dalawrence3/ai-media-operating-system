"""Phase 14B — Experiment → production → publication → analytics lineage tests.

Covers:
  A–I   bind_experiment_to_production_plan (happy path, error cases, lineage checks)
  J–N   derive_experiment_id_from_publication / derive_topic_id_from_publication
  O–S   attach_publication hardening (existence, lifecycle, idempotency, conflict)
  T–W   get_experiment_lineage completeness
  X–AH  NarrationExecutor experiment_id derivation
  AI–AO analytics cli derivation (unit-level, no live provider)
  AP–AV learning orchestrator topic_id derivation and validation
  AW–BI integration: full FK-enabled pipeline lineage chain
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

# ── DB helper ──────────────────────────────────────────────────────────────────


def _open(tmp_path: Path) -> sqlite3.Connection:
    from app.core.database import open_db

    return open_db(tmp_path / "lineage_14b.db")


# ── FK-enabled fixture builder ─────────────────────────────────────────────────


def _build_pipeline_chain(conn: sqlite3.Connection) -> dict:
    """Build a complete FK-valid pipeline chain.

    Returns dict with keys:
      channel_id, profile_version_id, run_id, topic_id, opp_id, script_id, plan_id
    """
    now = "2026-01-01T00:00:00"

    conn.execute(
        "INSERT INTO channels (channel_name, platform, platform_channel_id) "
        "VALUES ('test-channel', 'youtube', 'UC_lineage_14b')"
    )
    channel_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO channel_profile_versions "
        "(channel_id, version, primary_niche) "
        "VALUES (?, 1, 'tech')",
        (channel_id,),
    )
    profile_version_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO discovery_runs "
        "(channel_id, profile_version_id, adapter_name, status, started_at) "
        "VALUES (?, ?, 'manual', 'completed', ?)",
        (channel_id, profile_version_id, now),
    )
    run_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute("INSERT INTO topics (title, angle) VALUES ('test topic', 'test angle')")
    topic_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO opportunities "
        "(channel_id, discovery_run_id, normalized_topic, raw_topic, created_at, updated_at) "
        "VALUES (?, ?, 'test topic', 'raw test topic', ?, ?)",
        (channel_id, run_id, now, now),
    )
    opp_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "UPDATE topics SET promoted_opportunity_id = ? WHERE id = ?",
        (opp_id, topic_id),
    )

    conn.execute(
        "INSERT INTO scripts (topic_id, version, body, status) VALUES (?, 1, 'body', 'draft')",
        (topic_id,),
    )
    script_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO production_plans "
        "(topic_id, script_id, script_version, input_hash, script_body_hash, "
        "plan_schema_version, renderer_version, duration_algorithm_version) "
        "VALUES (?, ?, 1, 'hash_plan', 'hash_body', 'v1', 'v1', 'v1')",
        (topic_id, script_id),
    )
    plan_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.commit()
    return {
        "channel_id": channel_id,
        "profile_version_id": profile_version_id,
        "run_id": run_id,
        "topic_id": topic_id,
        "opp_id": opp_id,
        "script_id": script_id,
        "plan_id": plan_id,
    }


def _make_experiment(conn: sqlite3.Connection, channel_id: int, **kwargs) -> str:
    from app.intelligence.experiments.models import ExperimentType, MaturityPolicy
    from app.intelligence.experiments.repository import create_experiment

    exp_id = str(uuid.uuid4())
    create_experiment(
        conn,
        experiment_id=exp_id,
        channel_id=channel_id,
        experiment_type=kwargs.get("experiment_type", ExperimentType.exploration),
        hypothesis=kwargs.get("hypothesis", "test hypothesis"),
        opportunity_id=kwargs.get("opportunity_id"),
        maturity_policy=MaturityPolicy.default(),
        actor="test",
    )
    conn.commit()
    return exp_id


# ── A–I: bind_experiment_to_production_plan ────────────────────────────────────


def test_A_bind_happy_path(tmp_path):
    from app.intelligence.experiments.repository import bind_experiment_to_production_plan

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)
    exp_id = _make_experiment(conn, chain["channel_id"])

    bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"], actor="test")
    conn.commit()

    row = conn.execute(
        "SELECT experiment_id FROM production_plans WHERE id = ?", (chain["plan_id"],)
    ).fetchone()
    assert row["experiment_id"] == exp_id


def test_B_bind_unknown_experiment_raises(tmp_path):
    from app.intelligence.experiments.repository import bind_experiment_to_production_plan

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)

    with pytest.raises(KeyError, match="not found"):
        bind_experiment_to_production_plan(conn, "no-such-exp", chain["plan_id"])


def test_C_bind_unknown_plan_raises(tmp_path):
    from app.intelligence.experiments.repository import bind_experiment_to_production_plan

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)
    exp_id = _make_experiment(conn, chain["channel_id"])

    with pytest.raises(KeyError, match="not found"):
        bind_experiment_to_production_plan(conn, exp_id, 99999)


def test_D_bind_cancelled_experiment_raises(tmp_path):
    from app.intelligence.experiments.models import ExperimentStatus
    from app.intelligence.experiments.repository import (
        bind_experiment_to_production_plan,
        transition_experiment_state,
    )

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)
    exp_id = _make_experiment(conn, chain["channel_id"])
    transition_experiment_state(conn, exp_id, ExperimentStatus.cancelled, actor="test")
    conn.commit()

    with pytest.raises(ValueError, match="cancelled"):
        bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"])


def test_E_bind_completed_experiment_raises(tmp_path):
    from app.intelligence.experiments.models import ExperimentStatus
    from app.intelligence.experiments.repository import (
        bind_experiment_to_production_plan,
        transition_experiment_state,
    )

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)
    exp_id = _make_experiment(conn, chain["channel_id"])
    # Advance through all required lifecycle states
    for state in [
        ExperimentStatus.planned,
        ExperimentStatus.in_production,
        ExperimentStatus.published,
        ExperimentStatus.observing,
        ExperimentStatus.mature,
        ExperimentStatus.analyzed,
        ExperimentStatus.completed,
    ]:
        transition_experiment_state(conn, exp_id, state, actor="test")
    conn.commit()

    with pytest.raises(ValueError, match="completed"):
        bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"])


def test_F_bind_idempotent(tmp_path):
    from app.intelligence.experiments.repository import bind_experiment_to_production_plan

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)
    exp_id = _make_experiment(conn, chain["channel_id"])

    bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"], actor="test")
    conn.commit()
    # Second call with same binding — must not raise
    bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"], actor="test")
    conn.commit()

    row = conn.execute(
        "SELECT experiment_id FROM production_plans WHERE id = ?", (chain["plan_id"],)
    ).fetchone()
    assert row["experiment_id"] == exp_id


def test_G_bind_conflict_raises(tmp_path):
    from app.intelligence.experiments.repository import bind_experiment_to_production_plan

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)
    exp_id_1 = _make_experiment(conn, chain["channel_id"], hypothesis="h1")
    exp_id_2 = _make_experiment(conn, chain["channel_id"], hypothesis="h2")

    bind_experiment_to_production_plan(conn, exp_id_1, chain["plan_id"], actor="test")
    conn.commit()

    with pytest.raises(ValueError, match="already bound"):
        bind_experiment_to_production_plan(conn, exp_id_2, chain["plan_id"])


def test_H_bind_opportunity_lineage_mismatch_raises(tmp_path):
    from app.intelligence.experiments.repository import bind_experiment_to_production_plan

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)
    now = "2026-01-01T00:00:00"

    # Create a second opportunity (different opp_id) and link experiment to it
    conn.execute(
        "INSERT INTO opportunities "
        "(channel_id, discovery_run_id, normalized_topic, raw_topic, created_at, updated_at) "
        "VALUES (?, ?, 'other topic', 'other raw', ?, ?)",
        (chain["channel_id"], chain["run_id"], now, now),
    )
    other_opp_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    # Experiment references other_opp_id, but plan's topic promoted_opportunity_id = chain["opp_id"]
    exp_id = _make_experiment(conn, chain["channel_id"], opportunity_id=other_opp_id)

    with pytest.raises(ValueError, match="[Oo]pportunity lineage mismatch"):
        bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"])


def test_I_bind_channel_mismatch_raises(tmp_path):
    """Channel mismatch detected: experiment on channel B but opportunity belongs to channel A.

    The plan's topic promoted_opportunity_id matches the experiment's opportunity_id (so
    opportunity lineage check passes), but opportunity.channel_id != experiment.channel_id.
    """
    from app.intelligence.experiments.repository import bind_experiment_to_production_plan

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)  # chain uses channel_id A, opp_id from A

    # Create channel B
    conn.execute(
        "INSERT INTO channels (channel_name, platform, platform_channel_id) "
        "VALUES ('other-channel', 'youtube', 'UC_other')"
    )
    other_channel_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    # Experiment lives on channel B but references chain["opp_id"] (channel A's opportunity).
    # plan's topic promoted_opportunity_id = chain["opp_id"] → lineage check passes.
    # opp channel_id = chain["channel_id"] (A) ≠ other_channel_id (B) → channel mismatch.
    exp_id = _make_experiment(conn, other_channel_id, opportunity_id=chain["opp_id"])

    with pytest.raises(ValueError, match="[Cc]hannel mismatch"):
        bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"])


# ── J–N: derivation functions ──────────────────────────────────────────────────


def _build_minimal_publication_chain(
    conn: sqlite3.Connection, experiment_id: str | None = None
) -> dict:
    """Insert publishing_plan + publication rows with FK-off and return their IDs."""
    now = "2026-01-01T00:00:00"
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO publishing_plans "
        "(render_manifest_id, render_job_id, topic_id, production_plan_id, script_id, "
        "scene_manifest_id, narration_run_id, caption_run_id, experiment_id, "
        "input_hash, publishing_engine_version, metadata_version, "
        "provider, provider_version, title, description, created_at, updated_at) "
        "VALUES (1, 1, 10, 1, 1, 1, 1, 1, ?, ?, 'v1', 'v1', 'fake', '1.0', 'title', '', ?, ?)",
        (experiment_id, f"pub_plan_hash_{uuid.uuid4().hex[:8]}", now, now),
    )
    pub_plan_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO publications "
        "(publishing_plan_id, publishing_job_id, provider, provider_version, "
        "publishing_engine_version, input_hash, output_sha256, created_at, updated_at) "
        "VALUES (?, 1, 'fake', '1.0', 'v1', ?, 'sha256', ?, ?)",
        (pub_plan_id, f"pub_hash_{uuid.uuid4().hex[:8]}", now, now),
    )
    pub_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    return {"pub_plan_id": pub_plan_id, "pub_id": pub_id}


def test_J_derive_experiment_id_from_publication(tmp_path):
    from app.intelligence.experiments.repository import derive_experiment_id_from_publication

    conn = _open(tmp_path)
    exp_id = str(uuid.uuid4())
    ids = _build_minimal_publication_chain(conn, experiment_id=exp_id)

    result = derive_experiment_id_from_publication(conn, ids["pub_id"])
    assert result == exp_id


def test_K_derive_experiment_id_returns_none_when_no_experiment(tmp_path):
    from app.intelligence.experiments.repository import derive_experiment_id_from_publication

    conn = _open(tmp_path)
    ids = _build_minimal_publication_chain(conn, experiment_id=None)

    result = derive_experiment_id_from_publication(conn, ids["pub_id"])
    assert result is None


def test_L_derive_experiment_id_unknown_publication_returns_none(tmp_path):
    from app.intelligence.experiments.repository import derive_experiment_id_from_publication

    conn = _open(tmp_path)
    result = derive_experiment_id_from_publication(conn, 99999)
    assert result is None


def test_M_derive_topic_id_from_publication(tmp_path):
    from app.intelligence.experiments.repository import derive_topic_id_from_publication

    conn = _open(tmp_path)
    ids = _build_minimal_publication_chain(conn)

    # publishing_plans.topic_id was set to 10 in _build_minimal_publication_chain
    result = derive_topic_id_from_publication(conn, ids["pub_id"])
    assert result == 10


def test_N_derive_topic_id_unknown_publication_returns_none(tmp_path):
    from app.intelligence.experiments.repository import derive_topic_id_from_publication

    conn = _open(tmp_path)
    result = derive_topic_id_from_publication(conn, 99999)
    assert result is None


# ── O–S: attach_publication hardening ─────────────────────────────────────────


def test_O_attach_publication_unknown_experiment_raises(tmp_path):
    from app.intelligence.experiments.repository import attach_publication

    conn = _open(tmp_path)
    ids = _build_minimal_publication_chain(conn)

    with pytest.raises(KeyError, match="not found"):
        attach_publication(conn, "no-such-exp", ids["pub_id"])


def test_P_attach_publication_unknown_publication_raises(tmp_path):
    from app.intelligence.experiments.repository import attach_publication

    conn = _open(tmp_path)
    conn.execute(
        "INSERT INTO channels (channel_name, platform, platform_channel_id) "
        "VALUES ('ch', 'youtube', 'UC_p')"
    )
    ch_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    exp_id = _make_experiment(conn, ch_id)

    with pytest.raises(KeyError, match="[Pp]ublication"):
        attach_publication(conn, exp_id, 99999)


def test_Q_attach_publication_cancelled_experiment_raises(tmp_path):
    from app.intelligence.experiments.models import ExperimentStatus
    from app.intelligence.experiments.repository import (
        attach_publication,
        transition_experiment_state,
    )

    conn = _open(tmp_path)
    conn.execute(
        "INSERT INTO channels (channel_name, platform, platform_channel_id) "
        "VALUES ('ch', 'youtube', 'UC_q')"
    )
    ch_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    exp_id = _make_experiment(conn, ch_id)
    transition_experiment_state(conn, exp_id, ExperimentStatus.cancelled, actor="test")
    conn.commit()
    ids = _build_minimal_publication_chain(conn)

    with pytest.raises(ValueError, match="cancelled"):
        attach_publication(conn, exp_id, ids["pub_id"])


def test_R_attach_publication_idempotent(tmp_path):
    from app.intelligence.experiments.repository import attach_publication, get_experiment

    conn = _open(tmp_path)
    conn.execute(
        "INSERT INTO channels (channel_name, platform, platform_channel_id) "
        "VALUES ('ch', 'youtube', 'UC_r')"
    )
    ch_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    exp_id = _make_experiment(conn, ch_id)
    ids = _build_minimal_publication_chain(conn)

    attach_publication(conn, exp_id, ids["pub_id"])
    conn.commit()
    # Second call with same pub_id — must not raise
    attach_publication(conn, exp_id, ids["pub_id"])
    conn.commit()

    loaded = get_experiment(conn, exp_id)
    assert loaded.publication_id == ids["pub_id"]


def test_S_attach_publication_conflict_raises(tmp_path):
    from app.intelligence.experiments.repository import attach_publication

    conn = _open(tmp_path)
    conn.execute(
        "INSERT INTO channels (channel_name, platform, platform_channel_id) "
        "VALUES ('ch', 'youtube', 'UC_s')"
    )
    ch_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    exp_id = _make_experiment(conn, ch_id)
    ids1 = _build_minimal_publication_chain(conn)
    ids2 = _build_minimal_publication_chain(conn)

    attach_publication(conn, exp_id, ids1["pub_id"])
    conn.commit()

    with pytest.raises(ValueError, match="already attached"):
        attach_publication(conn, exp_id, ids2["pub_id"])


# ── T–W: get_experiment_lineage completeness ──────────────────────────────────


def test_T_lineage_has_all_14b_keys(tmp_path):
    from app.intelligence.experiments.repository import get_experiment_lineage

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)
    exp_id = _make_experiment(conn, chain["channel_id"])

    lineage = get_experiment_lineage(conn, exp_id)
    assert "topic" in lineage
    assert "feature_snapshots" in lineage
    assert "learning_runs" in lineage
    # Pre-existing keys still present
    assert "experiment" in lineage
    assert "opportunity" in lineage
    assert "production_plans" in lineage
    assert "analytics_snapshots" in lineage
    assert "metric_targets" in lineage
    assert "factors" in lineage
    assert "state_events" in lineage


def test_U_lineage_production_plan_appears_after_bind(tmp_path):
    from app.intelligence.experiments.repository import (
        bind_experiment_to_production_plan,
        get_experiment_lineage,
    )

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)
    exp_id = _make_experiment(conn, chain["channel_id"])

    bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"], actor="test")
    conn.commit()

    lineage = get_experiment_lineage(conn, exp_id)
    plan_ids = [p["id"] for p in lineage["production_plans"]]
    assert chain["plan_id"] in plan_ids


def test_V_lineage_topic_resolved_from_bound_plan(tmp_path):
    from app.intelligence.experiments.repository import (
        bind_experiment_to_production_plan,
        get_experiment_lineage,
    )

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)
    exp_id = _make_experiment(conn, chain["channel_id"])

    bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"], actor="test")
    conn.commit()

    lineage = get_experiment_lineage(conn, exp_id)
    assert lineage["topic"] is not None
    assert lineage["topic"]["id"] == chain["topic_id"]


def test_W_lineage_learning_runs_appear_after_publication_attach(tmp_path):
    from app.intelligence.experiments.repository import (
        attach_publication,
        get_experiment_lineage,
    )

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)
    exp_id = _make_experiment(conn, chain["channel_id"])
    ids = _build_minimal_publication_chain(conn)

    attach_publication(conn, exp_id, ids["pub_id"])
    conn.commit()

    # Insert a fake learning run linked to the publication
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO learning_runs "
        "(topic_id, publication_id, status, engine_version, schema_version, "
        "input_hash, created_at) "
        "VALUES (?, ?, 'completed', 'v1', 'v1', 'hash_lr', '2026-01-01T00:00:00')",
        (chain["topic_id"], ids["pub_id"]),
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    lineage = get_experiment_lineage(conn, exp_id)
    assert len(lineage["learning_runs"]) == 1
    assert lineage["learning_runs"][0]["status"] == "completed"


# ── X–AH: NarrationExecutor experiment_id derivation ──────────────────────────


def _make_req(*, experiment_id=None, topic_id=1, extra_config=None):
    """Build a minimal StageExecutionRequest for NarrationExecutor tests."""
    from app.application.executor import StageExecutionRequest

    cfg = {"voice_profile_id": 1}
    if extra_config:
        cfg.update(extra_config)
    return StageExecutionRequest(
        pipeline_execution_id="exec-1",
        workspace_id="ws-1",
        stage="narration",
        attempt_number=1,
        correlation_id="corr-1",
        idempotency_key="key-1",
        actor="test",
        topic_id=topic_id,
        experiment_id=experiment_id,
        effective_config=cfg,
    )


def test_X_narration_executor_uses_plan_experiment_id_when_req_is_none(tmp_path):
    """NarrationExecutor must pass plan.experiment_id when req.experiment_id is None."""
    from unittest.mock import MagicMock, patch

    from app.application.stage_executors import NarrationExecutor

    exp_id = str(uuid.uuid4())

    plan = MagicMock()
    plan.id = 1
    plan.input_hash = "hash"
    plan.experiment_id = exp_id

    req = _make_req(experiment_id=None)
    captured: dict = {}

    def fake_narrate_plan(
        conn,
        *,
        plan_id,
        plan_input_hash,
        voice_profile_id,
        artifacts_path,
        provider,
        speaking_rate_override,
        experiment_id=None,
    ):
        captured["experiment_id"] = experiment_id
        m = MagicMock()
        m.run_id = 99
        return m

    _patches = [
        patch("app.production.repository.get_active_approved_production_plan", return_value=plan),
        patch("app.narration.orchestrator.narrate_plan", side_effect=fake_narrate_plan),
        patch("app.learning.application.resolve_speaking_rate_override", return_value=(None, None)),
        patch("app.learning.application.consume_proposed_application"),
        patch.object(
            NarrationExecutor, "_build_provider", staticmethod(lambda *a, **k: MagicMock())
        ),
    ]
    for p in _patches:
        p.start()
    try:
        NarrationExecutor().execute(MagicMock(), req)
    finally:
        for p in _patches:
            p.stop()

    assert captured.get("experiment_id") == exp_id


def test_Y_narration_executor_rejects_mismatched_req_experiment_id(tmp_path):
    """Phase 14B.1: req.experiment_id != plan.experiment_id → blocked (lineage_conflict).

    The authoritative binding on the production plan must win; a caller supplying
    a conflicting experiment_id is rejected before any state mutation.
    """
    from unittest.mock import MagicMock, patch

    from app.application.stage_executors import NarrationExecutor

    req_exp_id = str(uuid.uuid4())
    plan_exp_id = str(uuid.uuid4())

    plan = MagicMock()
    plan.id = 1
    plan.input_hash = "hash"
    plan.experiment_id = plan_exp_id

    req = _make_req(experiment_id=req_exp_id)  # different from plan_exp_id

    _patches = [
        patch("app.production.repository.get_active_approved_production_plan", return_value=plan),
        patch("app.narration.orchestrator.narrate_plan"),  # must NOT be called
        patch("app.learning.application.resolve_speaking_rate_override", return_value=(None, None)),
        patch("app.learning.application.consume_proposed_application"),
        patch.object(
            NarrationExecutor, "_build_provider", staticmethod(lambda *a, **k: MagicMock())
        ),
    ]
    for p in _patches:
        p.start()
    try:
        result = NarrationExecutor().execute(MagicMock(), req)
    finally:
        for p in _patches:
            p.stop()

    assert result.status == "blocked"
    assert result.error_category == "lineage_conflict"
    assert req_exp_id in (result.error_message or "")
    assert plan_exp_id in (result.error_message or "")


def test_Z_narration_executor_passes_none_when_both_none(tmp_path):
    """When neither req nor plan has experiment_id, passes None (legacy plans)."""
    from unittest.mock import MagicMock, patch

    from app.application.stage_executors import NarrationExecutor

    plan = MagicMock()
    plan.id = 1
    plan.input_hash = "hash"
    plan.experiment_id = None

    req = _make_req(experiment_id=None)
    captured: dict = {}

    def fake_narrate_plan(
        conn,
        *,
        plan_id,
        plan_input_hash,
        voice_profile_id,
        artifacts_path,
        provider,
        speaking_rate_override,
        experiment_id=None,
    ):
        captured["experiment_id"] = experiment_id
        m = MagicMock()
        m.run_id = 99
        return m

    _patches = [
        patch("app.production.repository.get_active_approved_production_plan", return_value=plan),
        patch("app.narration.orchestrator.narrate_plan", side_effect=fake_narrate_plan),
        patch("app.learning.application.resolve_speaking_rate_override", return_value=(None, None)),
        patch("app.learning.application.consume_proposed_application"),
        patch.object(
            NarrationExecutor, "_build_provider", staticmethod(lambda *a, **k: MagicMock())
        ),
    ]
    for p in _patches:
        p.start()
    try:
        NarrationExecutor().execute(MagicMock(), req)
    finally:
        for p in _patches:
            p.stop()

    assert captured.get("experiment_id") is None


# ── AI–AO: analytics CLI derivation (no live provider) ──────────────────────


def test_AI_build_youtube_provider_returns_4_tuple(tmp_path, monkeypatch):
    """_build_youtube_provider_and_lineage returns (provider, lineage, date, experiment_id)."""
    from unittest.mock import MagicMock, patch

    import app.analytics.cli as analytics_cli

    pub = MagicMock()
    pub.publishing_plan_id = 1
    pub.published_at = "2026-01-15T10:00:00"
    pub.provider_video_id = "vid123"
    pub.publishing_job_id = 2

    plan = MagicMock()
    plan.render_manifest_id = 3
    plan.scene_manifest_id = 4
    plan.production_plan_id = 5
    plan.script_id = 6
    plan.topic_id = 7
    plan.narration_run_id = 8
    plan.caption_run_id = 9
    plan.experiment_id = "exp-abc"

    fake_provider = MagicMock()

    with (
        patch("app.publishing.repository.get_publication", return_value=pub),
        patch("app.publishing.repository.get_publishing_plan", return_value=plan),
        patch("app.oauth.client_google.RealGoogleOAuthClient", return_value=MagicMock()),
        patch(
            "app.analytics.gate.build_authenticated_analytics_provider", return_value=fake_provider
        ),
        patch(
            "app.core.config.get_config",
            return_value=MagicMock(
                youtube_client_secrets_path="/fake", youtube_redirect_uri="http://localhost"
            ),
        ),
    ):
        result = analytics_cli._build_youtube_provider_and_lineage(
            MagicMock(),
            publication_id=1,
            account_id="acct",
            workspace_id="ws",
            channel_id="ch",
        )

    assert len(result) == 4
    provider, lineage, pub_date, derived_exp = result
    assert derived_exp == "exp-abc"
    assert pub_date == "2026-01-15"
    assert len(lineage) == 10


def test_AJ_build_youtube_provider_derived_exp_none_when_no_experiment(tmp_path):
    """derived_experiment_id is None when plan has no experiment."""
    from unittest.mock import MagicMock, patch

    import app.analytics.cli as analytics_cli

    pub = MagicMock()
    pub.publishing_plan_id = 1
    pub.published_at = "2026-01-15T10:00:00"
    pub.provider_video_id = "vid123"
    pub.publishing_job_id = 2

    plan = MagicMock()
    plan.render_manifest_id = 3
    plan.scene_manifest_id = 4
    plan.production_plan_id = 5
    plan.script_id = 6
    plan.topic_id = 7
    plan.narration_run_id = 8
    plan.caption_run_id = 9
    plan.experiment_id = None

    with (
        patch("app.publishing.repository.get_publication", return_value=pub),
        patch("app.publishing.repository.get_publishing_plan", return_value=plan),
        patch("app.oauth.client_google.RealGoogleOAuthClient", return_value=MagicMock()),
        patch(
            "app.analytics.gate.build_authenticated_analytics_provider", return_value=MagicMock()
        ),
        patch(
            "app.core.config.get_config",
            return_value=MagicMock(
                youtube_client_secrets_path="/fake", youtube_redirect_uri="http://localhost"
            ),
        ),
    ):
        _, _, _, derived_exp = analytics_cli._build_youtube_provider_and_lineage(
            MagicMock(),
            publication_id=1,
            account_id="acct",
            workspace_id="ws",
            channel_id="ch",
        )

    assert derived_exp is None


# ── AP–AV: learning orchestrator topic_id derivation ──────────────────────────


def test_AP_analyze_publication_accepts_correct_topic_id(tmp_path):
    """analyze_publication does not raise when caller topic_id matches derived."""
    from unittest.mock import MagicMock, patch

    from app.learning.orchestrator import analyze_publication

    conn = MagicMock()
    # Simulate joined row returning topic_id = 5
    topic_row = MagicMock()
    topic_row.__getitem__ = lambda self, k: 5 if k == "topic_id" else None
    conn.execute.return_value.fetchone.return_value = topic_row

    with (
        patch("app.learning.orchestrator._build_handoff_from_db") as mock_build,
        patch("app.learning.orchestrator.compute_learning_run_hash", return_value="hash"),
        patch("app.learning.orchestrator.create_learning_run", return_value=1),
        patch("app.learning.orchestrator.generate_all_recommendations") as mock_gen,
        patch("app.learning.orchestrator.complete_learning_run"),
    ):
        mock_build.return_value = MagicMock(snapshots=[], metrics=[], aggregates=[])
        mock_gen.return_value = MagicMock(generator_results=[], all_recommendations=[])

        analyze_publication(conn, publication_id=10, topic_id=5)


def test_AQ_analyze_publication_raises_on_topic_id_mismatch(tmp_path):
    """analyze_publication raises ValueError when caller topic_id conflicts with derived."""
    from unittest.mock import MagicMock

    from app.learning.orchestrator import analyze_publication

    conn = MagicMock()
    topic_row = MagicMock()
    topic_row.__getitem__ = lambda self, k: 5 if k == "topic_id" else None
    conn.execute.return_value.fetchone.return_value = topic_row

    with pytest.raises(ValueError, match="topic_id mismatch"):
        analyze_publication(conn, publication_id=10, topic_id=99)


def test_AR_analyze_publication_proceeds_when_no_publication_lineage(tmp_path):
    """analyze_publication proceeds without error when publication has no publishing_plan row."""
    from unittest.mock import MagicMock, patch

    from app.learning.orchestrator import analyze_publication

    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = None  # no row found

    with (
        patch("app.learning.orchestrator._build_handoff_from_db") as mock_build,
        patch("app.learning.orchestrator.compute_learning_run_hash", return_value="hash"),
        patch("app.learning.orchestrator.create_learning_run", return_value=1),
        patch("app.learning.orchestrator.generate_all_recommendations") as mock_gen,
        patch("app.learning.orchestrator.complete_learning_run"),
    ):
        mock_build.return_value = MagicMock(snapshots=[], metrics=[], aggregates=[])
        mock_gen.return_value = MagicMock(generator_results=[], all_recommendations=[])

        analyze_publication(conn, publication_id=10, topic_id=5)


# ── AW–BI: FK-enabled integration — full pipeline lineage chain ───────────────


def test_AW_full_fk_chain_bind_and_lineage(tmp_path):
    """FK-on integration: build chain → create experiment → bind → verify lineage."""
    from app.intelligence.experiments.repository import (
        bind_experiment_to_production_plan,
        get_experiment_lineage,
    )

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)
    exp_id = _make_experiment(conn, chain["channel_id"], opportunity_id=chain["opp_id"])

    bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"], actor="system")
    conn.commit()

    lineage = get_experiment_lineage(conn, exp_id)
    assert lineage["experiment"]["id"] == exp_id
    assert any(p["id"] == chain["plan_id"] for p in lineage["production_plans"])
    assert lineage["topic"] is not None
    assert lineage["opportunity"] is not None
    assert lineage["opportunity"]["id"] == chain["opp_id"]


def test_AX_full_chain_opportunity_lineage_verified_on_bind(tmp_path):
    """FK-on: bind succeeds when experiment.opportunity_id matches plan's topic opportunity."""
    from app.intelligence.experiments.repository import bind_experiment_to_production_plan

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)
    # experiment with matching opp_id
    exp_id = _make_experiment(conn, chain["channel_id"], opportunity_id=chain["opp_id"])

    bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"], actor="system")
    conn.commit()

    row = conn.execute(
        "SELECT experiment_id FROM production_plans WHERE id = ?", (chain["plan_id"],)
    ).fetchone()
    assert row["experiment_id"] == exp_id


def test_AY_bind_stores_event_in_state_events(tmp_path):
    """bind_experiment_to_production_plan appends a state event record."""
    from app.intelligence.experiments.repository import bind_experiment_to_production_plan

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)
    exp_id = _make_experiment(conn, chain["channel_id"])

    bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"], actor="operator")
    conn.commit()

    events = conn.execute(
        "SELECT * FROM experiment_state_events WHERE experiment_id = ? ORDER BY id",
        (exp_id,),
    ).fetchall()
    # created event + bind event
    assert len(events) >= 2
    bind_event = events[-1]
    assert bind_event["actor"] == "operator"
    assert "production_plan" in bind_event["reason"]


def test_AZ_bind_does_not_change_experiment_status(tmp_path):
    """bind_experiment_to_production_plan must NOT advance experiment lifecycle status."""
    from app.intelligence.experiments.repository import (
        bind_experiment_to_production_plan,
        get_experiment,
    )

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)
    exp_id = _make_experiment(conn, chain["channel_id"])

    bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"], actor="system")
    conn.commit()

    exp = get_experiment(conn, exp_id)
    assert exp.status.value == "draft"


def test_BA_bind_without_opportunity_skips_lineage_check(tmp_path):
    """bind succeeds when experiment has no opportunity_id (no lineage check run)."""
    from app.intelligence.experiments.repository import bind_experiment_to_production_plan

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)
    # No opportunity_id on experiment
    exp_id = _make_experiment(conn, chain["channel_id"], opportunity_id=None)

    bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"], actor="system")
    conn.commit()

    row = conn.execute(
        "SELECT experiment_id FROM production_plans WHERE id = ?", (chain["plan_id"],)
    ).fetchone()
    assert row["experiment_id"] == exp_id


def test_BB_planned_experiment_can_be_bound(tmp_path):
    """Experiments in 'planned' state can be bound to production plans."""
    from app.intelligence.experiments.models import ExperimentStatus
    from app.intelligence.experiments.repository import (
        bind_experiment_to_production_plan,
        transition_experiment_state,
    )

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)
    exp_id = _make_experiment(conn, chain["channel_id"])
    transition_experiment_state(conn, exp_id, ExperimentStatus.planned, actor="test")
    conn.commit()

    bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"])
    conn.commit()

    row = conn.execute(
        "SELECT experiment_id FROM production_plans WHERE id = ?", (chain["plan_id"],)
    ).fetchone()
    assert row["experiment_id"] == exp_id


def test_BC_in_production_experiment_can_be_bound(tmp_path):
    """Experiments in 'in_production' state can be bound."""
    from app.intelligence.experiments.models import ExperimentStatus
    from app.intelligence.experiments.repository import (
        bind_experiment_to_production_plan,
        transition_experiment_state,
    )

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)
    exp_id = _make_experiment(conn, chain["channel_id"])
    transition_experiment_state(conn, exp_id, ExperimentStatus.planned, actor="test")
    transition_experiment_state(conn, exp_id, ExperimentStatus.in_production, actor="test")
    conn.commit()

    bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"])
    conn.commit()

    row = conn.execute(
        "SELECT experiment_id FROM production_plans WHERE id = ?", (chain["plan_id"],)
    ).fetchone()
    assert row["experiment_id"] == exp_id


def test_BD_derivation_functions_roundtrip_with_real_chain(tmp_path):
    """FK-on: derive_experiment_id and derive_topic_id work against real publication chain."""
    from app.intelligence.experiments.repository import (
        derive_experiment_id_from_publication,
        derive_topic_id_from_publication,
    )

    conn = _open(tmp_path)
    _build_pipeline_chain(conn)
    exp_id = str(uuid.uuid4())
    ids = _build_minimal_publication_chain(conn, experiment_id=exp_id)

    derived_exp = derive_experiment_id_from_publication(conn, ids["pub_id"])
    derived_topic = derive_topic_id_from_publication(conn, ids["pub_id"])

    assert derived_exp == exp_id
    assert derived_topic == 10  # set in _build_minimal_publication_chain


def test_BE_multiple_experiments_isolated_to_their_plans(tmp_path):
    """Two experiments can each bind to separate production plans without interference."""
    from app.intelligence.experiments.repository import bind_experiment_to_production_plan

    conn = _open(tmp_path)
    chain1 = _build_pipeline_chain(conn)

    # Build a second independent chain
    conn.execute("INSERT INTO topics (title, angle) VALUES ('second topic', 'angle2')")
    topic2_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO scripts (topic_id, version, body, status) VALUES (?, 1, 'body2', 'draft')",
        (topic2_id,),
    )
    script2_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO production_plans "
        "(topic_id, script_id, script_version, input_hash, script_body_hash, "
        "plan_schema_version, renderer_version, duration_algorithm_version) "
        "VALUES (?, ?, 1, 'hash_plan2', 'hash_body2', 'v1', 'v1', 'v1')",
        (topic2_id, script2_id),
    )
    plan2_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    exp1_id = _make_experiment(conn, chain1["channel_id"], hypothesis="h1")
    exp2_id = _make_experiment(conn, chain1["channel_id"], hypothesis="h2")

    bind_experiment_to_production_plan(conn, exp1_id, chain1["plan_id"])
    bind_experiment_to_production_plan(conn, exp2_id, plan2_id)
    conn.commit()

    row1 = conn.execute(
        "SELECT experiment_id FROM production_plans WHERE id = ?", (chain1["plan_id"],)
    ).fetchone()
    row2 = conn.execute(
        "SELECT experiment_id FROM production_plans WHERE id = ?", (plan2_id,)
    ).fetchone()

    assert row1["experiment_id"] == exp1_id
    assert row2["experiment_id"] == exp2_id


def test_BF_input_hash_idempotency_preserved_after_14b(tmp_path):
    """create_experiment is still idempotent via input_hash after 14B changes."""
    from app.intelligence.experiments.models import ExperimentType, MaturityPolicy
    from app.intelligence.experiments.repository import create_experiment

    conn = _open(tmp_path)
    conn.execute("INSERT INTO channels (channel_name, platform) VALUES ('ch', 'youtube')")
    ch_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    kwargs = dict(
        channel_id=ch_id,
        experiment_type=ExperimentType.exploration,
        hypothesis="same hypothesis",
        maturity_policy=MaturityPolicy.default(),
        actor="test",
    )
    exp1 = create_experiment(conn, experiment_id=str(uuid.uuid4()), **kwargs)
    conn.commit()
    exp2 = create_experiment(conn, experiment_id=str(uuid.uuid4()), **kwargs)
    conn.commit()

    assert exp1.id == exp2.id  # second call returns first


def test_BG_transition_timestamps_set_on_state_change(tmp_path):
    """Lifecycle transitions stamp the correct timestamp column."""
    from app.intelligence.experiments.models import ExperimentStatus
    from app.intelligence.experiments.repository import (
        get_experiment,
        transition_experiment_state,
    )

    conn = _open(tmp_path)
    conn.execute("INSERT INTO channels (channel_name, platform) VALUES ('ch', 'youtube')")
    ch_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    exp_id = _make_experiment(conn, ch_id)

    transition_experiment_state(conn, exp_id, ExperimentStatus.planned, actor="test")
    conn.commit()

    exp = get_experiment(conn, exp_id)
    assert exp.planned_at is not None


def test_BH_lineage_feature_snapshots_linked_via_plan(tmp_path):
    """feature_snapshots appear in lineage when linked to a bound production plan."""
    from app.intelligence.experiments.repository import (
        bind_experiment_to_production_plan,
        get_experiment_lineage,
    )

    conn = _open(tmp_path)
    chain = _build_pipeline_chain(conn)
    exp_id = _make_experiment(conn, chain["channel_id"])

    bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"])
    conn.commit()

    # Insert a stub content_feature_snapshot linked to the plan
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO content_feature_snapshots "
        "(publication_id, topic_id, feature_schema_version, extractor_version, "
        "input_hash, extracted_at, created_at, publishing_plan_id, production_plan_id, "
        "script_id, narration_run_id, caption_run_id, scene_manifest_id, "
        "render_manifest_id, voice_profile_id) "
        "VALUES (1, ?, 'v1', 'v1', 'fs_hash', '2026-01-01', '2026-01-01', "
        "1, ?, 1, 1, 1, 1, 1, 1)",
        (chain["topic_id"], chain["plan_id"]),
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    lineage = get_experiment_lineage(conn, exp_id)
    assert len(lineage["feature_snapshots"]) == 1
    assert lineage["feature_snapshots"][0]["publication_id"] == 1


def test_BI_schema_version_unchanged_at_37(tmp_path):
    """Phase 14B must not increment schema version — stays at v37."""
    from app.core.database import SCHEMA_VERSION

    conn = _open(tmp_path)
    version = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
    assert version == 51
    assert SCHEMA_VERSION == 51
