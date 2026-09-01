"""Phase 14B.2 — True FK lineage validation + experiment/publication identity audit.

Covers:
  A–I   FK chain construction layer by layer (channels → publications), FK ON throughout
  J–K   Experiment binding in full FK chain
  L–P   Production plan lineage gap in attach_publication (THE CORE 14B.2 FIX)
  Q–S   Dual-source-of-truth documentation and detection
  T–V   Derivation chain queries (publishing_plan path, production_plan path)
  W–X   FK violation detection (FK ON actually enforced)
  Y     End-to-end regression: full FK chain + bind + attach + lineage read
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

# ── DB helper ──────────────────────────────────────────────────────────────────


def _open(tmp_path: Path) -> sqlite3.Connection:
    from app.core.database import open_db

    conn = open_db(tmp_path / "lineage_14b2.db")
    # Verify FK enforcement is active
    fk_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk_on == 1, "open_db must enable PRAGMA foreign_keys = ON"
    return conn


def _make_experiment(conn: sqlite3.Connection, channel_id: int, **kwargs) -> str:
    from app.intelligence.experiments.models import ExperimentType, MaturityPolicy
    from app.intelligence.experiments.repository import create_experiment

    exp_id = str(uuid.uuid4())
    create_experiment(
        conn,
        experiment_id=exp_id,
        channel_id=channel_id,
        experiment_type=kwargs.get("experiment_type", ExperimentType.exploration),
        hypothesis=kwargs.get("hypothesis", "14b2 test hypothesis"),
        opportunity_id=kwargs.get("opportunity_id"),
        maturity_policy=MaturityPolicy.default(),
        actor="test",
    )
    conn.commit()
    return exp_id


def _build_full_fk_chain(
    conn: sqlite3.Connection,
    *,
    prod_plan_exp_id: str | None = None,
    pub_plan_exp_id: str | None = None,
) -> dict:
    """Build the complete lineage with PRAGMA foreign_keys = ON active throughout.

    Inserts every table in dependency order: channels → channel_profile_versions →
    discovery_runs → topics → opportunities → scripts → voice_profiles →
    production_plans → narration_runs → caption_runs → scene_manifests →
    render_manifests → publishing_plans → publishing_jobs → publications.

    Returns a dict of all inserted IDs keyed by table role.
    The caller must NOT issue PRAGMA foreign_keys = OFF before calling this
    function — that would defeat the FK-ON proof.
    """
    now = "2026-01-01T00:00:00"
    suffix = uuid.uuid4().hex[:8]

    conn.execute(
        "INSERT INTO channels (channel_name, platform, platform_channel_id) "
        "VALUES (?, 'youtube', ?)",
        (f"fk-chain-{suffix}", f"UC_14b2_{suffix}"),
    )
    channel_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO channel_profile_versions (channel_id, version, primary_niche) "
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

    conn.execute(
        "INSERT INTO topics (title, angle) VALUES (?, ?)",
        (f"14b2-topic-{suffix}", f"14b2-angle-{suffix}"),
    )
    topic_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO opportunities "
        "(channel_id, discovery_run_id, normalized_topic, raw_topic, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (channel_id, run_id, f"norm-{suffix}", f"raw-{suffix}", now, now),
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
        "INSERT INTO voice_profiles (provider, model, voice_id, name) "
        "VALUES ('elevenlabs', 'eleven_v2', ?, ?)",
        (f"voice_{suffix}", f"Test Voice {suffix}"),
    )
    voice_profile_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO production_plans "
        "(topic_id, script_id, script_version, input_hash, script_body_hash, "
        "plan_schema_version, renderer_version, duration_algorithm_version, experiment_id) "
        "VALUES (?, ?, 1, ?, 'sbhash', 'v1', 'v1', 'v1', ?)",
        (topic_id, script_id, f"pp_ih_{suffix}", prod_plan_exp_id),
    )
    plan_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO narration_runs "
        "(plan_id, plan_input_hash, voice_profile_id, voice_profile_version, "
        "language, speaking_rate, settings_json, output_format, sample_rate_hz, input_hash) "
        "VALUES (?, ?, ?, 1, 'en-US', 1.0, '{}', 'mp3', 44100, ?)",
        (plan_id, f"pih_{plan_id}", voice_profile_id, f"nr_ih_{suffix}"),
    )
    narration_run_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO caption_runs "
        "(narration_run_id, plan_id, script_id, topic_id, input_hash, "
        "caption_schema_version, segmentation_version, timing_algorithm_version, "
        "style_version, exporter_version) "
        "VALUES (?, ?, ?, ?, ?, 'v1', 'v1', 'v1', 'v1', 'v1')",
        (narration_run_id, plan_id, script_id, topic_id, f"cr_ih_{suffix}"),
    )
    caption_run_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO scene_manifests "
        "(caption_run_id, narration_run_id, plan_id, script_id, topic_id, "
        "input_hash, manifest_schema_version, planner_version) "
        "VALUES (?, ?, ?, ?, ?, ?, 'v1', 'v1')",
        (caption_run_id, narration_run_id, plan_id, script_id, topic_id, f"sm_ih_{suffix}"),
    )
    scene_manifest_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO render_manifests "
        "(scene_manifest_id, narration_run_id, caption_run_id, topic_id, plan_id, script_id, "
        "input_hash, render_schema_version, compositor_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'v1', 'v1')",
        (
            scene_manifest_id,
            narration_run_id,
            caption_run_id,
            topic_id,
            plan_id,
            script_id,
            f"rm_ih_{suffix}",
        ),
    )
    render_manifest_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO publishing_plans "
        "(render_manifest_id, topic_id, production_plan_id, script_id, "
        "scene_manifest_id, narration_run_id, caption_run_id, experiment_id, "
        "input_hash, publishing_engine_version, metadata_version, "
        "provider, provider_version, title, description, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'v1', 'v1', 'youtube', 'v1', "
        "'14b2 title', '', ?, ?)",
        (
            render_manifest_id,
            topic_id,
            plan_id,
            script_id,
            scene_manifest_id,
            narration_run_id,
            caption_run_id,
            pub_plan_exp_id,
            f"pubplan_ih_{suffix}",
            now,
            now,
        ),
    )
    publishing_plan_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO publishing_jobs "
        "(publishing_plan_id, provider, provider_version, created_at, updated_at) "
        "VALUES (?, 'youtube', 'v1', ?, ?)",
        (publishing_plan_id, now, now),
    )
    publishing_job_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO publications "
        "(publishing_plan_id, publishing_job_id, provider, provider_version, "
        "publishing_engine_version, input_hash, output_sha256, created_at, updated_at) "
        "VALUES (?, ?, 'youtube', 'v1', 'v1', ?, 'sha256_output', ?, ?)",
        (publishing_plan_id, publishing_job_id, f"pub_ih_{suffix}", now, now),
    )
    publication_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.commit()
    return {
        "channel_id": channel_id,
        "profile_version_id": profile_version_id,
        "run_id": run_id,
        "topic_id": topic_id,
        "opp_id": opp_id,
        "script_id": script_id,
        "voice_profile_id": voice_profile_id,
        "plan_id": plan_id,
        "narration_run_id": narration_run_id,
        "caption_run_id": caption_run_id,
        "scene_manifest_id": scene_manifest_id,
        "render_manifest_id": render_manifest_id,
        "publishing_plan_id": publishing_plan_id,
        "publishing_job_id": publishing_job_id,
        "publication_id": publication_id,
    }


# ── A–I: FK chain construction layer by layer ─────────────────────────────────


def test_A_channels_through_production_plans_with_fk_on(tmp_path):
    """First segment of the FK chain: channels → production_plans with FK ON."""
    conn = _open(tmp_path)
    now = "2026-01-01T00:00:00"
    sfx = uuid.uuid4().hex[:8]

    conn.execute(
        "INSERT INTO channels (channel_name, platform, platform_channel_id) "
        "VALUES (?, 'youtube', ?)",
        (f"ch-{sfx}", f"UC_{sfx}"),
    )
    channel_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO channel_profile_versions (channel_id, version, primary_niche) "
        "VALUES (?, 1, 'tech')",
        (channel_id,),
    )
    profile_version_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO discovery_runs "
        "(channel_id, profile_version_id, adapter_name, status, started_at) "
        "VALUES (?, ?, 'manual', 'completed', ?)",
        (channel_id, profile_version_id, now),
    )
    run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute("INSERT INTO topics (title, angle) VALUES ('t', 'a')")
    topic_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO opportunities "
        "(channel_id, discovery_run_id, normalized_topic, raw_topic, created_at, updated_at) "
        "VALUES (?, ?, 'n', 'r', ?, ?)",
        (channel_id, run_id, now, now),
    )
    opp_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("UPDATE topics SET promoted_opportunity_id = ? WHERE id = ?", (opp_id, topic_id))

    conn.execute(
        "INSERT INTO scripts (topic_id, version, body, status) VALUES (?, 1, 'b', 'draft')",
        (topic_id,),
    )
    script_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO production_plans "
        "(topic_id, script_id, script_version, input_hash, script_body_hash, "
        "plan_schema_version, renderer_version, duration_algorithm_version) "
        "VALUES (?, ?, 1, ?, 'sbh', 'v1', 'v1', 'v1')",
        (topic_id, script_id, f"ih_{sfx}"),
    )
    plan_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    assert (
        conn.execute("SELECT id FROM production_plans WHERE id = ?", (plan_id,)).fetchone()
        is not None
    )


def test_B_voice_profile_inserted_with_fk_on(tmp_path):
    """voice_profiles can be inserted with FK ON (channel_id is nullable)."""
    conn = _open(tmp_path)

    conn.execute(
        "INSERT INTO voice_profiles (provider, model, voice_id, name) "
        "VALUES ('elevenlabs', 'eleven_v2', 'voice1', 'Test')"
    )
    vp_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    assert (
        conn.execute("SELECT id FROM voice_profiles WHERE id = ?", (vp_id,)).fetchone() is not None
    )


def test_C_narration_run_inserted_with_fk_on(tmp_path):
    """narration_runs inserted with FK ON enforcing plan_id and voice_profile_id."""
    conn = _open(tmp_path)
    chain = _build_full_fk_chain(conn)

    row = conn.execute(
        "SELECT id, plan_id, voice_profile_id FROM narration_runs WHERE id = ?",
        (chain["narration_run_id"],),
    ).fetchone()
    assert row is not None
    assert row["plan_id"] == chain["plan_id"]
    assert row["voice_profile_id"] == chain["voice_profile_id"]


def test_D_caption_run_inserted_with_fk_on(tmp_path):
    """caption_runs inserted with FK ON enforcing narration_run_id, plan_id, script_id, topic_id."""
    conn = _open(tmp_path)
    chain = _build_full_fk_chain(conn)

    row = conn.execute(
        "SELECT narration_run_id, plan_id, script_id, topic_id FROM caption_runs WHERE id = ?",
        (chain["caption_run_id"],),
    ).fetchone()
    assert row is not None
    assert row["narration_run_id"] == chain["narration_run_id"]
    assert row["plan_id"] == chain["plan_id"]
    assert row["script_id"] == chain["script_id"]
    assert row["topic_id"] == chain["topic_id"]


def test_E_scene_manifest_inserted_with_fk_on(tmp_path):
    """scene_manifests inserted with FK ON enforcing all required FKs."""
    conn = _open(tmp_path)
    chain = _build_full_fk_chain(conn)

    row = conn.execute(
        "SELECT caption_run_id, narration_run_id, plan_id FROM scene_manifests WHERE id = ?",
        (chain["scene_manifest_id"],),
    ).fetchone()
    assert row is not None
    assert row["caption_run_id"] == chain["caption_run_id"]
    assert row["narration_run_id"] == chain["narration_run_id"]
    assert row["plan_id"] == chain["plan_id"]


def test_F_render_manifest_inserted_with_fk_on(tmp_path):
    """render_manifests inserted with FK ON enforcing scene_manifest_id FK."""
    conn = _open(tmp_path)
    chain = _build_full_fk_chain(conn)

    row = conn.execute(
        "SELECT scene_manifest_id, narration_run_id, caption_run_id "
        "FROM render_manifests WHERE id = ?",
        (chain["render_manifest_id"],),
    ).fetchone()
    assert row is not None
    assert row["scene_manifest_id"] == chain["scene_manifest_id"]


def test_G_publishing_plan_inserted_with_fk_on(tmp_path):
    """publishing_plans inserted with FK ON enforcing render_manifest_id FK."""
    conn = _open(tmp_path)
    chain = _build_full_fk_chain(conn)

    row = conn.execute(
        "SELECT render_manifest_id, production_plan_id FROM publishing_plans WHERE id = ?",
        (chain["publishing_plan_id"],),
    ).fetchone()
    assert row is not None
    assert row["render_manifest_id"] == chain["render_manifest_id"]
    assert row["production_plan_id"] == chain["plan_id"]


def test_H_publishing_job_inserted_with_fk_on(tmp_path):
    """publishing_jobs inserted with FK ON enforcing publishing_plan_id FK."""
    conn = _open(tmp_path)
    chain = _build_full_fk_chain(conn)

    row = conn.execute(
        "SELECT publishing_plan_id FROM publishing_jobs WHERE id = ?",
        (chain["publishing_job_id"],),
    ).fetchone()
    assert row is not None
    assert row["publishing_plan_id"] == chain["publishing_plan_id"]


def test_I_publication_inserted_with_fk_on(tmp_path):
    """publications inserted with FK ON enforcing publishing_plan_id and publishing_job_id FKs."""
    conn = _open(tmp_path)
    chain = _build_full_fk_chain(conn)

    row = conn.execute(
        "SELECT publishing_plan_id, publishing_job_id FROM publications WHERE id = ?",
        (chain["publication_id"],),
    ).fetchone()
    assert row is not None
    assert row["publishing_plan_id"] == chain["publishing_plan_id"]
    assert row["publishing_job_id"] == chain["publishing_job_id"]


# ── J–K: Experiment binding in full FK chain ──────────────────────────────────


def test_J_experiment_bound_to_production_plan_via_full_chain(tmp_path):
    """Experiment can be bound to a production_plan that is part of the full FK chain."""
    from app.intelligence.experiments.repository import bind_experiment_to_production_plan

    conn = _open(tmp_path)
    chain = _build_full_fk_chain(conn)
    exp_id = _make_experiment(conn, chain["channel_id"])

    bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"], actor="test")
    conn.commit()

    row = conn.execute(
        "SELECT experiment_id FROM production_plans WHERE id = ?", (chain["plan_id"],)
    ).fetchone()
    assert row["experiment_id"] == exp_id


def test_K_attach_publication_succeeds_with_consistent_full_chain(tmp_path):
    """attach_publication succeeds when all experiment_ids in the chain agree."""
    from app.intelligence.experiments.repository import attach_publication, get_experiment

    conn = _open(tmp_path)
    exp_id = str(uuid.uuid4())
    # Build chain with same experiment_id on both production_plan and publishing_plan
    chain = _build_full_fk_chain(conn, prod_plan_exp_id=exp_id, pub_plan_exp_id=exp_id)

    # Create the experiment so attach_publication can look it up
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO experiments "
        "(id, channel_id, experiment_type, status, hypothesis, input_hash, "
        "maturity_policy_json, policy_snapshot_json) "
        "VALUES (?, ?, 'exploration', 'draft', 'h', ?, '{}', '{}')",
        (exp_id, chain["channel_id"], f"ih_{uuid.uuid4().hex}"),
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()

    attach_publication(conn, exp_id, chain["publication_id"])
    conn.commit()

    exp = get_experiment(conn, exp_id)
    assert exp.publication_id == chain["publication_id"]


# ── L–P: Production plan lineage gap — THE CORE 14B.2 FIX ────────────────────


def test_L_attach_publication_rejects_via_production_plan_lineage_when_pub_plan_null(tmp_path):
    """CORE 14B.2: publishing_plan.exp_id=NULL but production_plan.exp_id=exp-A.

    Calling attach_publication(exp-B, pub_id) must raise — the production_plan
    is already authoritative for exp-A. Before 14B.2 this incorrectly succeeded.
    """
    from app.intelligence.experiments.repository import attach_publication

    conn = _open(tmp_path)
    exp_a = str(uuid.uuid4())
    exp_b = str(uuid.uuid4())
    # publishing_plan.experiment_id = NULL (default)
    # production_plan.experiment_id = exp_a
    chain = _build_full_fk_chain(conn, prod_plan_exp_id=exp_a, pub_plan_exp_id=None)

    # Insert exp_b so attach_publication can find it
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO experiments "
        "(id, channel_id, experiment_type, status, hypothesis, input_hash, "
        "maturity_policy_json, policy_snapshot_json) "
        "VALUES (?, ?, 'exploration', 'draft', 'h', ?, '{}', '{}')",
        (exp_b, chain["channel_id"], f"ih_{uuid.uuid4().hex}"),
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()

    with pytest.raises(ValueError, match="production_plan lineage"):
        attach_publication(conn, exp_b, chain["publication_id"])


def test_M_attach_publication_accepts_via_production_plan_lineage_match(tmp_path):
    """publishing_plan.exp_id=NULL, production_plan.exp_id=exp-A → attach(exp-A) succeeds."""
    from app.intelligence.experiments.repository import attach_publication, get_experiment

    conn = _open(tmp_path)
    exp_a = str(uuid.uuid4())
    chain = _build_full_fk_chain(conn, prod_plan_exp_id=exp_a, pub_plan_exp_id=None)

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO experiments "
        "(id, channel_id, experiment_type, status, hypothesis, input_hash, "
        "maturity_policy_json, policy_snapshot_json) "
        "VALUES (?, ?, 'exploration', 'draft', 'h', ?, '{}', '{}')",
        (exp_a, chain["channel_id"], f"ih_{uuid.uuid4().hex}"),
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()

    attach_publication(conn, exp_a, chain["publication_id"])
    conn.commit()

    exp = get_experiment(conn, exp_a)
    assert exp.publication_id == chain["publication_id"]


def test_N_attach_publication_accepts_when_both_experiment_ids_null(tmp_path):
    """Both publishing_plan.exp_id and production_plan.exp_id are NULL → attach freely."""
    from app.intelligence.experiments.repository import attach_publication, get_experiment

    conn = _open(tmp_path)
    chain = _build_full_fk_chain(conn, prod_plan_exp_id=None, pub_plan_exp_id=None)

    conn.execute("PRAGMA foreign_keys = OFF")
    exp_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO experiments "
        "(id, channel_id, experiment_type, status, hypothesis, input_hash, "
        "maturity_policy_json, policy_snapshot_json) "
        "VALUES (?, ?, 'exploration', 'draft', 'h', ?, '{}', '{}')",
        (exp_id, chain["channel_id"], f"ih_{uuid.uuid4().hex}"),
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()

    attach_publication(conn, exp_id, chain["publication_id"])
    conn.commit()

    exp = get_experiment(conn, exp_id)
    assert exp.publication_id == chain["publication_id"]


def test_O_attach_publication_rejects_via_publishing_plan_check(tmp_path):
    """Existing 14B.1 check: publishing_plan.exp_id=exp-A → attach(exp-B) raises."""
    from app.intelligence.experiments.repository import attach_publication

    conn = _open(tmp_path)
    exp_a = str(uuid.uuid4())
    exp_b = str(uuid.uuid4())
    # Both set; publishing_plan check fires first
    chain = _build_full_fk_chain(conn, prod_plan_exp_id=exp_a, pub_plan_exp_id=exp_a)

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO experiments "
        "(id, channel_id, experiment_type, status, hypothesis, input_hash, "
        "maturity_policy_json, policy_snapshot_json) "
        "VALUES (?, ?, 'exploration', 'draft', 'h', ?, '{}', '{}')",
        (exp_b, chain["channel_id"], f"ih_{uuid.uuid4().hex}"),
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()

    with pytest.raises(ValueError, match="publishing_plan lineage"):
        attach_publication(conn, exp_b, chain["publication_id"])


def test_P_production_plan_lineage_query_returns_experiment(tmp_path):
    """The publication→publishing_plan→production_plan JOIN correctly surfaces exp_id."""
    conn = _open(tmp_path)
    exp_a = str(uuid.uuid4())
    chain = _build_full_fk_chain(conn, prod_plan_exp_id=exp_a, pub_plan_exp_id=None)

    row = conn.execute(
        """
        SELECT pp2.experiment_id
        FROM publications pub
        JOIN publishing_plans pp ON pp.id = pub.publishing_plan_id
        JOIN production_plans pp2 ON pp2.id = pp.production_plan_id
        WHERE pub.id = ?
        """,
        (chain["publication_id"],),
    ).fetchone()
    assert row is not None
    assert row["experiment_id"] == exp_a


# ── Q–S: Dual-source-of-truth documentation ───────────────────────────────────


def test_Q_both_sources_consistent_after_bind_and_attach(tmp_path):
    """After bind_experiment + attach_publication, both lineage paths agree.

    Source 1: experiments.publication_id → publication_id → publishing_plan → production_plan
    Source 2: production_plans.experiment_id → experiment
    Both must name the same experiment.
    """
    from app.intelligence.experiments.repository import (
        attach_publication,
        bind_experiment_to_production_plan,
    )

    conn = _open(tmp_path)
    chain = _build_full_fk_chain(conn)
    exp_id = _make_experiment(conn, chain["channel_id"])

    bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"], actor="test")
    conn.commit()

    # Ensure publishing_plan also carries the experiment (simulating normal pipeline flow)
    conn.execute(
        "UPDATE publishing_plans SET experiment_id = ? WHERE id = ?",
        (exp_id, chain["publishing_plan_id"]),
    )
    conn.commit()

    attach_publication(conn, exp_id, chain["publication_id"])
    conn.commit()

    # Source 1: experiments.publication_id should point to the publication
    exp_row = conn.execute(
        "SELECT publication_id FROM experiments WHERE id = ?", (exp_id,)
    ).fetchone()
    assert exp_row["publication_id"] == chain["publication_id"]

    # Source 2: production_plans.experiment_id should match
    plan_row = conn.execute(
        "SELECT experiment_id FROM production_plans WHERE id = ?", (chain["plan_id"],)
    ).fetchone()
    assert plan_row["experiment_id"] == exp_id

    # Both paths yield the same experiment_id
    assert exp_row["publication_id"] == chain["publication_id"]
    assert plan_row["experiment_id"] == exp_id


def test_R_dual_source_query_shows_both_paths(tmp_path):
    """Demonstrate the dual-source-of-truth query that surfaces both lineage paths.

    This query is useful for auditing consistency between experiments.publication_id
    and production_plans.experiment_id.
    """
    from app.intelligence.experiments.repository import (
        attach_publication,
        bind_experiment_to_production_plan,
    )

    conn = _open(tmp_path)
    chain = _build_full_fk_chain(conn)
    exp_id = _make_experiment(conn, chain["channel_id"])

    bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"], actor="test")
    conn.commit()
    conn.execute(
        "UPDATE publishing_plans SET experiment_id = ? WHERE id = ?",
        (exp_id, chain["publishing_plan_id"]),
    )
    conn.commit()
    attach_publication(conn, exp_id, chain["publication_id"])
    conn.commit()

    # Dual-source audit query
    row = conn.execute(
        """
        SELECT
            e.id                    AS experiment_id,
            e.publication_id        AS exp_pub_id,
            pp.experiment_id        AS prod_plan_exp_id,
            pub_pp.experiment_id    AS pub_plan_exp_id,
            CASE
                WHEN pp.experiment_id = e.id AND pub_pp.experiment_id = e.id THEN 'consistent'
                ELSE 'diverged'
            END AS lineage_status
        FROM experiments e
        LEFT JOIN publications pub ON pub.id = e.publication_id
        LEFT JOIN publishing_plans pub_pp ON pub_pp.id = pub.publishing_plan_id
        LEFT JOIN production_plans pp ON pp.id = pub_pp.production_plan_id
        WHERE e.id = ?
        """,
        (exp_id,),
    ).fetchone()

    assert row["experiment_id"] == exp_id
    assert row["exp_pub_id"] == chain["publication_id"]
    assert row["prod_plan_exp_id"] == exp_id
    assert row["pub_plan_exp_id"] == exp_id
    assert row["lineage_status"] == "consistent"


def test_S_production_plan_path_returns_none_when_no_experiment(tmp_path):
    """When production_plan.experiment_id is NULL, the derivation path returns None."""
    conn = _open(tmp_path)
    # Build with no experiment_ids set anywhere
    chain = _build_full_fk_chain(conn, prod_plan_exp_id=None, pub_plan_exp_id=None)

    row = conn.execute(
        """
        SELECT pp2.experiment_id
        FROM publications pub
        JOIN publishing_plans pp ON pp.id = pub.publishing_plan_id
        JOIN production_plans pp2 ON pp2.id = pp.production_plan_id
        WHERE pub.id = ?
        """,
        (chain["publication_id"],),
    ).fetchone()
    assert row is not None
    assert row["experiment_id"] is None


# ── T–V: Derivation chain queries ─────────────────────────────────────────────


def test_T_derive_experiment_id_from_publication_uses_publishing_plan_path(tmp_path):
    """derive_experiment_id_from_publication returns exp via publishing_plan.experiment_id."""
    from app.intelligence.experiments.repository import derive_experiment_id_from_publication

    conn = _open(tmp_path)
    exp_id = str(uuid.uuid4())
    # exp_id on publishing_plan only; production_plan NULL
    chain = _build_full_fk_chain(conn, prod_plan_exp_id=None, pub_plan_exp_id=exp_id)

    result = derive_experiment_id_from_publication(conn, chain["publication_id"])
    assert result == exp_id


def test_U_production_plan_experiment_derivation_via_full_join(tmp_path):
    """Experiment is recoverable from publication via production_plan path.

    This is the path derive_experiment_id_from_publication does NOT currently use
    (it uses the publishing_plan path). The production_plan path is the authoritative
    one and is now validated in attach_publication().
    """
    conn = _open(tmp_path)
    exp_id = str(uuid.uuid4())
    # Only production_plan carries the experiment_id; publishing_plan is NULL
    chain = _build_full_fk_chain(conn, prod_plan_exp_id=exp_id, pub_plan_exp_id=None)

    # Primary path (publishing_plan.experiment_id) returns None
    primary_row = conn.execute(
        "SELECT pp.experiment_id FROM publications pub "
        "JOIN publishing_plans pp ON pp.id = pub.publishing_plan_id "
        "WHERE pub.id = ?",
        (chain["publication_id"],),
    ).fetchone()
    assert primary_row["experiment_id"] is None

    # Deeper path (production_plan.experiment_id) reveals the authoritative experiment
    deeper_row = conn.execute(
        "SELECT pp2.experiment_id FROM publications pub "
        "JOIN publishing_plans pp ON pp.id = pub.publishing_plan_id "
        "JOIN production_plans pp2 ON pp2.id = pp.production_plan_id "
        "WHERE pub.id = ?",
        (chain["publication_id"],),
    ).fetchone()
    assert deeper_row["experiment_id"] == exp_id


def test_V_content_feature_snapshot_experiment_derivable_via_production_plan(tmp_path):
    """content_feature_snapshots has no direct experiment_id; recoverable via production_plan.

    This test documents the derivation path:
    content_feature_snapshots.production_plan_id → production_plans.experiment_id
    """
    conn = _open(tmp_path)
    exp_id = str(uuid.uuid4())
    chain = _build_full_fk_chain(conn, prod_plan_exp_id=exp_id)

    # Insert a content_feature_snapshot linked to the production_plan (FK-off for brevity)
    now = "2026-01-01T00:00:00"
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT OR IGNORE INTO content_feature_snapshots "
        "(publication_id, topic_id, feature_schema_version, extractor_version, "
        "input_hash, extracted_at, created_at, "
        "publishing_plan_id, production_plan_id, script_id, "
        "narration_run_id, caption_run_id, scene_manifest_id, render_manifest_id, "
        "voice_profile_id) "
        "VALUES (?, ?, '1.0', '1.0', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            chain["publication_id"],
            chain["topic_id"],
            f"cfs_ih_{uuid.uuid4().hex[:8]}",
            now,
            now,
            chain["publishing_plan_id"],
            chain["plan_id"],
            chain["script_id"],
            chain["narration_run_id"],
            chain["caption_run_id"],
            chain["scene_manifest_id"],
            chain["render_manifest_id"],
            chain["voice_profile_id"],
        ),
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()

    # Derivation query: content_feature_snapshots has no experiment_id column;
    # recover via production_plan
    row = conn.execute(
        """
        SELECT pp.experiment_id
        FROM content_feature_snapshots cfs
        JOIN production_plans pp ON pp.id = cfs.production_plan_id
        WHERE cfs.production_plan_id = ?
        """,
        (chain["plan_id"],),
    ).fetchone()
    assert row is not None
    assert row["experiment_id"] == exp_id


# ── W–X: FK violation detection ───────────────────────────────────────────────


def test_W_fk_on_catches_bogus_narration_run_plan_id(tmp_path):
    """FK ON raises IntegrityError when narration_run references a non-existent production_plan."""
    conn = _open(tmp_path)

    # Insert a real voice_profile (no FK dependency)
    conn.execute(
        "INSERT INTO voice_profiles (provider, model, voice_id, name) "
        "VALUES ('elevenlabs', 'eleven_v2', 'voice1', 'Test')"
    )
    voice_profile_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    sfx = uuid.uuid4().hex[:8]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO narration_runs "
            "(plan_id, plan_input_hash, voice_profile_id, voice_profile_version, "
            "language, speaking_rate, settings_json, output_format, sample_rate_hz, input_hash) "
            "VALUES (99999, 'pih', ?, 1, 'en-US', 1.0, '{}', 'mp3', 44100, ?)",
            (voice_profile_id, f"bogus_ih_{sfx}"),
        )


def test_X_fk_on_catches_bogus_publication_publishing_plan_id(tmp_path):
    """FK ON raises IntegrityError when publication references a non-existent publishing_plan."""
    conn = _open(tmp_path)
    chain = _build_full_fk_chain(conn)
    now = "2026-01-01T00:00:00"
    sfx = uuid.uuid4().hex[:8]

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO publications "
            "(publishing_plan_id, publishing_job_id, provider, provider_version, "
            "publishing_engine_version, input_hash, output_sha256, created_at, updated_at) "
            "VALUES (99999, ?, 'youtube', 'v1', 'v1', ?, 'sha256', ?, ?)",
            (chain["publishing_job_id"], f"bogus_ih_{sfx}", now, now),
        )


# ── Y: End-to-end regression ──────────────────────────────────────────────────


def test_Y_end_to_end_full_fk_chain_bind_attach_lineage(tmp_path):
    """End-to-end regression: full FK chain with experiment binding and publication attachment.

    Proves the entire Experiment → Production Plan → Publication lineage can exist
    under real DB constraints (FK ON throughout), and all lineage reads are consistent.
    """
    from app.intelligence.experiments.repository import (
        attach_publication,
        bind_experiment_to_production_plan,
        derive_experiment_id_from_publication,
        get_experiment,
        get_experiment_lineage,
    )

    conn = _open(tmp_path)

    # 1. Build the complete FK chain
    chain = _build_full_fk_chain(conn)

    # 2. Create an experiment on the channel
    exp_id = _make_experiment(conn, chain["channel_id"])

    # 3. Bind experiment to production_plan
    bind_experiment_to_production_plan(conn, exp_id, chain["plan_id"], actor="test")
    conn.commit()

    # 4. Propagate experiment_id to publishing_plan (as normal pipeline flow would)
    conn.execute(
        "UPDATE publishing_plans SET experiment_id = ? WHERE id = ?",
        (exp_id, chain["publishing_plan_id"]),
    )
    conn.commit()

    # 5. Attach publication to experiment
    attach_publication(conn, exp_id, chain["publication_id"])
    conn.commit()

    # 6. Verify all three lineage paths agree
    exp = get_experiment(conn, exp_id)
    assert exp.publication_id == chain["publication_id"]

    plan_row = conn.execute(
        "SELECT experiment_id FROM production_plans WHERE id = ?", (chain["plan_id"],)
    ).fetchone()
    assert plan_row["experiment_id"] == exp_id

    pp_row = conn.execute(
        "SELECT experiment_id FROM publishing_plans WHERE id = ?",
        (chain["publishing_plan_id"],),
    ).fetchone()
    assert pp_row["experiment_id"] == exp_id

    # 7. Derivation function returns correct experiment
    derived = derive_experiment_id_from_publication(conn, chain["publication_id"])
    assert derived == exp_id

    # 8. Lineage read includes the bound production_plan
    lineage = get_experiment_lineage(conn, exp_id)
    plan_ids = [p["id"] for p in lineage["production_plans"]]
    assert chain["plan_id"] in plan_ids

    # 9. Full-chain JOIN proves consistency
    audit_row = conn.execute(
        """
        SELECT
            e.id AS exp_id,
            e.publication_id,
            pp.experiment_id AS prod_plan_exp_id,
            pub_pp.experiment_id AS pub_plan_exp_id
        FROM experiments e
        JOIN publications pub ON pub.id = e.publication_id
        JOIN publishing_plans pub_pp ON pub_pp.id = pub.publishing_plan_id
        JOIN production_plans pp ON pp.id = pub_pp.production_plan_id
        WHERE e.id = ?
        """,
        (exp_id,),
    ).fetchone()
    assert audit_row["exp_id"] == exp_id
    assert audit_row["publication_id"] == chain["publication_id"]
    assert audit_row["prod_plan_exp_id"] == exp_id
    assert audit_row["pub_plan_exp_id"] == exp_id
