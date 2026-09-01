"""Phase 14A — Experiment Ledger Tests (A–T+).

Tests cover:
  A  Fresh DB has experiments table with correct columns
  B  Fresh DB has experiment_state_events, experiment_metric_targets, experiment_factors
  C  SCHEMA_VERSION is 37
  D  create_experiment returns Experiment in draft with state event
  E  create_experiment is idempotent via input_hash
  F  get_experiment raises KeyError for unknown ID
  G  list_experiments filters by channel_id
  H  list_experiments filters by status
  I  list_experiments filters by experiment_type
  J  transition_experiment_state advances draft→planned, records event
  K  transition_experiment_state raises ValueError for invalid transition
  L  Full happy path: draft→planned→in_production→published→observing→mature→analyzed→completed
  M  Cancellation is reachable from any non-terminal state
  N  add_metric_target upserts; direction constraint enforced
  O  Primary metric flag
  P  add_factor upserts; factor_role constraint enforced
  Q  set_factor_actual fills actual_value; KeyError on unknown factor
  R  attach_publication sets publication_id
  S  get_experiment_lineage returns correct structure
  T  Migration from v36→v37 via open_db preserves existing data
  U  v37 migration is idempotent (calling twice is safe)
  V  Experiment does not reference YouTube / content generation
  W  No Phase 12C tables altered by v37 migration
  X  experiment_id TEXT PK is compatible with production_plans.experiment_id TEXT FK
  Y  opportunity FK enforces referential integrity
  Z  Multiple experiments per channel/opportunity allowed (no spurious UNIQUE)
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from app.core.database import SCHEMA_VERSION, open_db

# ── helpers ───────────────────────────────────────────────────────────────────


def _open(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "exp_test.db"
    conn = open_db(db)
    conn.row_factory = sqlite3.Row
    return conn


def _make_channel(conn: sqlite3.Connection) -> int:
    conn.execute(
        "INSERT INTO channels (channel_name, platform, platform_channel_id) "
        "VALUES ('test-ch', 'youtube', 'UC_test')"
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _make_opportunity(conn: sqlite3.Connection, channel_id: int) -> int:
    # Disable FK checks to avoid building the full profile_version/discovery_run chain.
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO discovery_runs "
        "(channel_id, profile_version_id, adapter_name, status, started_at) "
        "VALUES (?, 1, 'manual', 'completed', '2024-01-01T00:00:00')",
        (channel_id,),
    )
    run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO opportunities "
        "(channel_id, discovery_run_id, normalized_topic, raw_topic, created_at, updated_at) "
        "VALUES (?, ?, 'test topic', 'Test Topic', '2024-01-01', '2024-01-01')",
        (channel_id, run_id),
    )
    opp_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    return opp_id


def _new_id() -> str:
    return str(uuid.uuid4())


def _create(conn, channel_id, **kwargs):
    from app.intelligence.experiments.models import ExperimentType
    from app.intelligence.experiments.repository import create_experiment

    return create_experiment(
        conn,
        experiment_id=kwargs.pop("experiment_id", _new_id()),
        channel_id=channel_id,
        experiment_type=kwargs.pop("experiment_type", ExperimentType.exploration),
        hypothesis=kwargs.pop("hypothesis", "Views will increase"),
        **kwargs,
    )


# ── A: schema — experiments table ─────────────────────────────────────────────


def test_A_experiments_table_exists(tmp_path):
    conn = _open(tmp_path)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='experiments'"
    ).fetchone()
    assert row is not None


def test_A_experiments_columns(tmp_path):
    conn = _open(tmp_path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(experiments)").fetchall()}
    required = {
        "id",
        "channel_id",
        "opportunity_id",
        "experiment_type",
        "status",
        "hypothesis",
        "hypothesis_null",
        "hypothesis_metric",
        "input_hash",
        "maturity_policy_json",
        "policy_snapshot_json",
        "created_at",
        "updated_at",
        "planned_at",
        "in_production_at",
        "published_at",
        "observing_at",
        "matured_at",
        "analyzed_at",
        "completed_at",
        "cancelled_at",
        "cancelled_reason",
        "publication_id",
    }
    assert required <= cols


# ── B: schema — supporting tables ────────────────────────────────────────────


def test_B_state_events_table_exists(tmp_path):
    conn = _open(tmp_path)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='experiment_state_events'"
    ).fetchone()
    assert row is not None


def test_B_metric_targets_table_exists(tmp_path):
    conn = _open(tmp_path)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='experiment_metric_targets'"
    ).fetchone()
    assert row is not None


def test_B_factors_table_exists(tmp_path):
    conn = _open(tmp_path)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='experiment_factors'"
    ).fetchone()
    assert row is not None


# ── C: SCHEMA_VERSION ─────────────────────────────────────────────────────────


def test_C_schema_version_is_37():
    assert SCHEMA_VERSION == 51


def test_C_db_version_is_37(tmp_path):
    conn = _open(tmp_path)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert row["version"] == 51


# ── D: create_experiment ──────────────────────────────────────────────────────


def test_D_create_returns_draft(tmp_path):
    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp = _create(conn, ch)
    assert exp.status.value == "draft"
    assert exp.channel_id == ch


def test_D_create_records_state_event(tmp_path):
    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp = _create(conn, ch)
    events = conn.execute(
        "SELECT * FROM experiment_state_events WHERE experiment_id = ?", (exp.id,)
    ).fetchall()
    assert len(events) == 1
    assert events[0]["from_state"] is None
    assert events[0]["to_state"] == "draft"


def test_D_create_stores_maturity_policy(tmp_path):
    from app.intelligence.experiments.models import MaturityPolicy

    conn = _open(tmp_path)
    ch = _make_channel(conn)
    policy = MaturityPolicy(minimum_age_hours=72, minimum_views=20, observation_window_hours=336)
    exp = _create(conn, ch, maturity_policy=policy)
    stored = MaturityPolicy.from_json(exp.maturity_policy_json)
    assert stored.minimum_age_hours == 72
    assert stored.minimum_views == 20


# ── E: idempotency ────────────────────────────────────────────────────────────


def test_E_idempotent_create_returns_same_experiment(tmp_path):
    conn = _open(tmp_path)
    ch = _make_channel(conn)
    eid = _new_id()
    exp1 = _create(conn, ch, experiment_id=eid, hypothesis="Views will increase")
    exp2 = _create(conn, ch, experiment_id=_new_id(), hypothesis="Views will increase")
    assert exp1.id == exp2.id


def test_E_idempotent_does_not_insert_duplicate(tmp_path):
    conn = _open(tmp_path)
    ch = _make_channel(conn)
    _create(conn, ch, hypothesis="Same hypothesis")
    _create(conn, ch, hypothesis="Same hypothesis")
    count = conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
    assert count == 1


# ── F: get_experiment ─────────────────────────────────────────────────────────


def test_F_get_unknown_raises_key_error(tmp_path):
    from app.intelligence.experiments.repository import get_experiment

    conn = _open(tmp_path)
    with pytest.raises(KeyError):
        get_experiment(conn, "nonexistent-id")


# ── G: list_experiments — channel filter ──────────────────────────────────────


def test_G_list_filters_by_channel(tmp_path):
    from app.intelligence.experiments.repository import list_experiments

    conn = _open(tmp_path)
    ch1 = _make_channel(conn)
    ch2 = _make_channel(conn)
    _create(conn, ch1, hypothesis="H1")
    _create(conn, ch2, hypothesis="H2")
    results = list_experiments(conn, channel_id=ch1)
    assert all(e.channel_id == ch1 for e in results)
    assert len(results) == 1


# ── H: list_experiments — status filter ───────────────────────────────────────


def test_H_list_filters_by_status(tmp_path):
    from app.intelligence.experiments.models import ExperimentStatus
    from app.intelligence.experiments.repository import (
        list_experiments,
        transition_experiment_state,
    )

    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp = _create(conn, ch, hypothesis="H-status")
    transition_experiment_state(conn, exp.id, ExperimentStatus.planned)
    drafts = list_experiments(conn, status=ExperimentStatus.draft)
    planned = list_experiments(conn, status=ExperimentStatus.planned)
    assert len(drafts) == 0
    assert len(planned) == 1


# ── I: list_experiments — type filter ─────────────────────────────────────────


def test_I_list_filters_by_type(tmp_path):
    from app.intelligence.experiments.models import ExperimentType
    from app.intelligence.experiments.repository import list_experiments

    conn = _open(tmp_path)
    ch = _make_channel(conn)
    _create(conn, ch, experiment_type=ExperimentType.exploration, hypothesis="exp H")
    _create(conn, ch, experiment_type=ExperimentType.exploitation, hypothesis="expl H")
    results = list_experiments(conn, experiment_type=ExperimentType.exploitation)
    assert len(results) == 1
    assert results[0].experiment_type == ExperimentType.exploitation


# ── J: valid transition ────────────────────────────────────────────────────────


def test_J_transition_draft_to_planned(tmp_path):
    from app.intelligence.experiments.models import ExperimentStatus
    from app.intelligence.experiments.repository import transition_experiment_state

    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp = _create(conn, ch)
    updated = transition_experiment_state(
        conn, exp.id, ExperimentStatus.planned, actor="operator", reason="approved"
    )
    assert updated.status == ExperimentStatus.planned
    assert updated.planned_at is not None


def test_J_transition_records_event(tmp_path):
    from app.intelligence.experiments.models import ExperimentStatus
    from app.intelligence.experiments.repository import transition_experiment_state

    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp = _create(conn, ch)
    transition_experiment_state(conn, exp.id, ExperimentStatus.planned, actor="op", reason="ok")
    events = conn.execute(
        "SELECT * FROM experiment_state_events WHERE experiment_id = ? ORDER BY id",
        (exp.id,),
    ).fetchall()
    assert len(events) == 2
    assert events[1]["from_state"] == "draft"
    assert events[1]["to_state"] == "planned"
    assert events[1]["actor"] == "op"


# ── K: invalid transition ──────────────────────────────────────────────────────


def test_K_invalid_transition_raises_value_error(tmp_path):
    from app.intelligence.experiments.models import ExperimentStatus
    from app.intelligence.experiments.repository import transition_experiment_state

    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp = _create(conn, ch)
    with pytest.raises(ValueError, match="Invalid transition"):
        transition_experiment_state(conn, exp.id, ExperimentStatus.completed)


def test_K_completed_has_no_forward_transitions(tmp_path):
    from app.intelligence.experiments.models import ALLOWED_TRANSITIONS, ExperimentStatus

    assert ALLOWED_TRANSITIONS[ExperimentStatus.completed] == set()


# ── L: full happy path ────────────────────────────────────────────────────────


def test_L_full_lifecycle_happy_path(tmp_path):
    from app.intelligence.experiments.models import ExperimentStatus
    from app.intelligence.experiments.repository import transition_experiment_state

    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp = _create(conn, ch)
    for state in [
        ExperimentStatus.planned,
        ExperimentStatus.in_production,
        ExperimentStatus.published,
        ExperimentStatus.observing,
        ExperimentStatus.mature,
        ExperimentStatus.analyzed,
        ExperimentStatus.completed,
    ]:
        exp = transition_experiment_state(conn, exp.id, state)
    assert exp.status == ExperimentStatus.completed
    assert exp.planned_at is not None
    assert exp.in_production_at is not None
    assert exp.published_at is not None
    assert exp.observing_at is not None
    assert exp.matured_at is not None
    assert exp.analyzed_at is not None
    assert exp.completed_at is not None


# ── M: cancellation ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "stop_at",
    ["draft", "planned", "in_production", "published", "observing", "mature", "analyzed"],
)
def test_M_cancellation_from_any_live_state(tmp_path, stop_at):
    from app.intelligence.experiments.models import ExperimentStatus
    from app.intelligence.experiments.repository import transition_experiment_state

    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp = _create(conn, ch, hypothesis=f"H-{stop_at}")

    chain = ["planned", "in_production", "published", "observing", "mature", "analyzed"]
    for s in chain:
        if s == stop_at:
            break
        exp = transition_experiment_state(conn, exp.id, ExperimentStatus(s))
        if stop_at == s:
            break

    result = transition_experiment_state(
        conn, exp.id, ExperimentStatus.cancelled, cancelled_reason="test cancel"
    )
    assert result.status == ExperimentStatus.cancelled
    assert result.cancelled_at is not None


def test_M_cancelled_blocks_further_transitions(tmp_path):
    from app.intelligence.experiments.models import ExperimentStatus
    from app.intelligence.experiments.repository import transition_experiment_state

    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp = _create(conn, ch)
    transition_experiment_state(conn, exp.id, ExperimentStatus.cancelled)
    with pytest.raises(ValueError, match="Invalid transition"):
        transition_experiment_state(conn, exp.id, ExperimentStatus.planned)


# ── N: metric targets ─────────────────────────────────────────────────────────


def test_N_add_metric_target_stores_correctly(tmp_path):
    from app.intelligence.experiments.models import MetricDirection
    from app.intelligence.experiments.repository import add_metric_target

    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp = _create(conn, ch)
    t = add_metric_target(
        conn,
        exp.id,
        metric_name="view_count",
        direction=MetricDirection.higher_is_better,
        is_primary=True,
    )
    assert t.metric_name == "view_count"
    assert t.direction == MetricDirection.higher_is_better
    assert t.is_primary is True


def test_N_metric_target_upserts(tmp_path):
    from app.intelligence.experiments.models import MetricDirection
    from app.intelligence.experiments.repository import add_metric_target

    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp = _create(conn, ch)
    add_metric_target(conn, exp.id, metric_name="ctr", direction=MetricDirection.higher_is_better)
    add_metric_target(conn, exp.id, metric_name="ctr", direction=MetricDirection.lower_is_better)
    count = conn.execute(
        "SELECT COUNT(*) FROM experiment_metric_targets WHERE experiment_id = ?", (exp.id,)
    ).fetchone()[0]
    assert count == 1
    row = conn.execute(
        "SELECT direction FROM experiment_metric_targets "
        "WHERE experiment_id = ? AND metric_name = 'ctr'",
        (exp.id,),
    ).fetchone()
    assert row["direction"] == "lower_is_better"


def test_N_direction_constraint_enforced(tmp_path):
    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp = _create(conn, ch)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO experiment_metric_targets (experiment_id, metric_name, direction) "
            "VALUES (?, 'x', 'bad_direction')",
            (exp.id,),
        )


# ── O: primary metric ─────────────────────────────────────────────────────────


def test_O_all_metric_directions_representable(tmp_path):
    from app.intelligence.experiments.models import MetricDirection
    from app.intelligence.experiments.repository import add_metric_target, get_experiment

    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp = _create(conn, ch)
    for i, direction in enumerate(MetricDirection):
        add_metric_target(conn, exp.id, metric_name=f"m{i}", direction=direction)
    loaded = get_experiment(conn, exp.id)
    directions = {t.direction for t in loaded.metric_targets}
    assert directions == set(MetricDirection)


# ── P: factors ────────────────────────────────────────────────────────────────


def test_P_add_factor_stores_correctly(tmp_path):
    from app.intelligence.experiments.models import FactorRole
    from app.intelligence.experiments.repository import add_factor

    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp = _create(conn, ch)
    f = add_factor(
        conn,
        exp.id,
        factor_name="title_style",
        factor_role=FactorRole.treatment,
        intended_value="question",
    )
    assert f.factor_name == "title_style"
    assert f.factor_role == FactorRole.treatment
    assert f.intended_value == "question"
    assert f.actual_value is None


def test_P_factor_upserts(tmp_path):
    from app.intelligence.experiments.models import FactorRole
    from app.intelligence.experiments.repository import add_factor

    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp = _create(conn, ch)
    add_factor(
        conn,
        exp.id,
        factor_name="duration",
        factor_role=FactorRole.controlled,
        intended_value="10m",
    )
    add_factor(
        conn,
        exp.id,
        factor_name="duration",
        factor_role=FactorRole.controlled,
        intended_value="15m",
    )
    count = conn.execute(
        "SELECT COUNT(*) FROM experiment_factors WHERE experiment_id = ?", (exp.id,)
    ).fetchone()[0]
    assert count == 1


def test_P_factor_role_constraint_enforced(tmp_path):
    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp = _create(conn, ch)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO experiment_factors (experiment_id, factor_name, factor_role) "
            "VALUES (?, 'x', 'bad_role')",
            (exp.id,),
        )


# ── Q: set_factor_actual ──────────────────────────────────────────────────────


def test_Q_set_factor_actual_updates_value(tmp_path):
    from app.intelligence.experiments.models import FactorRole
    from app.intelligence.experiments.repository import add_factor, set_factor_actual

    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp = _create(conn, ch)
    add_factor(
        conn,
        exp.id,
        factor_name="hook_style",
        factor_role=FactorRole.treatment,
        intended_value="story",
    )
    result = set_factor_actual(conn, exp.id, "hook_style", "story-with-stats")
    assert result.actual_value == "story-with-stats"


def test_Q_set_factor_actual_unknown_raises_key_error(tmp_path):
    from app.intelligence.experiments.repository import set_factor_actual

    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp = _create(conn, ch)
    with pytest.raises(KeyError):
        set_factor_actual(conn, exp.id, "nonexistent_factor", "value")


# ── R: attach_publication ─────────────────────────────────────────────────────


def test_R_attach_publication_sets_field(tmp_path):
    from app.intelligence.experiments.repository import attach_publication, get_experiment

    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp = _create(conn, ch)
    # Disable FK checks to insert stub publication row without the full pipeline chain.
    # Must commit open transaction first; SQLite ignores PRAGMA foreign_keys inside a txn.
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO publications (id, publishing_plan_id, publishing_job_id, provider, "
        "provider_version, publishing_engine_version, input_hash, output_sha256, "
        "created_at, updated_at) "
        "VALUES (42, 1, 1, 'fake', '1.0', '1.0', 'hash_stub_42', 'sha_stub', "
        "'2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    attach_publication(conn, exp.id, 42)
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    loaded = get_experiment(conn, exp.id)
    assert loaded.publication_id == 42


def test_R_attach_publication_unknown_experiment_raises(tmp_path):
    from app.intelligence.experiments.repository import attach_publication

    conn = _open(tmp_path)
    with pytest.raises(KeyError):
        attach_publication(conn, "no-such-id", 1)


# ── S: get_experiment_lineage ─────────────────────────────────────────────────


def test_S_lineage_structure(tmp_path):
    from app.intelligence.experiments.repository import get_experiment_lineage

    conn = _open(tmp_path)
    ch = _make_channel(conn)
    opp_id = _make_opportunity(conn, ch)
    exp = _create(conn, ch, opportunity_id=opp_id)
    lineage = get_experiment_lineage(conn, exp.id)
    assert "experiment" in lineage
    assert "opportunity" in lineage
    assert "production_plans" in lineage
    assert "analytics_snapshots" in lineage
    assert "metric_targets" in lineage
    assert "factors" in lineage
    assert "state_events" in lineage
    assert lineage["experiment"]["id"] == exp.id
    assert lineage["opportunity"]["id"] == opp_id


def test_S_lineage_includes_metric_targets_and_factors(tmp_path):
    from app.intelligence.experiments.models import FactorRole, MetricDirection
    from app.intelligence.experiments.repository import (
        add_factor,
        add_metric_target,
        get_experiment_lineage,
    )

    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp = _create(conn, ch)
    add_metric_target(
        conn,
        exp.id,
        metric_name="views",
        direction=MetricDirection.higher_is_better,
        is_primary=True,
    )
    add_factor(conn, exp.id, factor_name="hook", factor_role=FactorRole.treatment)
    lineage = get_experiment_lineage(conn, exp.id)
    assert len(lineage["metric_targets"]) == 1
    assert lineage["metric_targets"][0]["metric_name"] == "views"
    assert len(lineage["factors"]) == 1
    assert lineage["factors"][0]["factor_name"] == "hook"


# ── T: v36→v37 migration ─────────────────────────────────────────────────────


def _build_v36_db(db_path: Path) -> None:
    """Construct a v36-shaped DB without the v37 tables."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (36)")
    # Minimal tables so open_db migration doesn't crash on missing FKs.
    conn.execute("""
        CREATE TABLE channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_name TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'youtube',
            platform_channel_id TEXT,
            operating_mode TEXT NOT NULL DEFAULT 'manual',
            current_maturity_stage TEXT NOT NULL DEFAULT 'validation',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
        )
    """)
    conn.execute("INSERT INTO channels (channel_name) VALUES ('existing-channel')")
    conn.commit()
    conn.close()


def test_T_v36_to_v37_migration_creates_tables(tmp_path):
    db_path = tmp_path / "v36.db"
    _build_v36_db(db_path)
    conn = open_db(db_path)
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "experiments" in tables
    assert "experiment_state_events" in tables
    assert "experiment_metric_targets" in tables
    assert "experiment_factors" in tables
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == 51


def test_T_migration_preserves_existing_channels(tmp_path):
    db_path = tmp_path / "v36b.db"
    _build_v36_db(db_path)
    conn = open_db(db_path)
    count = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
    assert count == 1


# ── U: idempotent migration ───────────────────────────────────────────────────


def test_U_v37_migration_idempotent(tmp_path):
    from app.core.database import _apply_v37_experiment_ledger

    conn = _open(tmp_path)
    # Already at v37; calling again should be a no-op.
    _apply_v37_experiment_ledger(conn)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'experiment%'"
        ).fetchall()
    }
    assert "experiments" in tables


# ── V: scope guard — no YouTube / content generation ─────────────────────────


def test_V_no_youtube_calls_in_repository():
    import inspect

    from app.intelligence.experiments import repository

    src = inspect.getsource(repository)
    forbidden = ["youtube", "YouTubeService", "generate_script", "render_video", "narration"]
    for kw in forbidden:
        assert kw not in src, f"Forbidden term {kw!r} found in experiments/repository.py"


def test_V_no_llm_calls_in_repository():
    import inspect

    from app.intelligence.experiments import repository

    src = inspect.getsource(repository)
    llm_terms = ["anthropic", "openai", "litellm", "generate_content", "call_llm"]
    for term in llm_terms:
        assert term not in src, f"LLM call term {term!r} found in experiments/repository.py"


# ── W: Phase 12C tables unaffected ────────────────────────────────────────────


def test_W_cross_pub_learning_tables_intact(tmp_path):
    conn = _open(tmp_path)
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "channel_performance_baselines" in tables
    assert "feature_performance_observations" in tables


def test_W_channel_performance_baselines_columns_intact(tmp_path):
    conn = _open(tmp_path)
    cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(channel_performance_baselines)").fetchall()
    }
    assert "channel_id" in cols
    assert "metric_name" in cols
    assert "mean" in cols


# ── X: TEXT PK compatible with existing experiment_id columns ─────────────────


def test_X_production_plans_experiment_id_column_is_text(tmp_path):
    """Confirm production_plans.experiment_id is a TEXT column, compatible
    with experiments.id (TEXT PK)."""
    conn = _open(tmp_path)
    cols = {
        r["name"]: r["type"] for r in conn.execute("PRAGMA table_info(production_plans)").fetchall()
    }
    assert "experiment_id" in cols
    # SQLite stores the affinity, not always the exact type string; TEXT affinity covers TEXT/UUID.
    assert cols["experiment_id"].upper() in {"TEXT", ""}  # empty = BLOB/NUMERIC, TEXT expected here


def test_X_analytics_snapshots_experiment_id_column_is_text(tmp_path):
    """Confirm analytics_snapshots.experiment_id is TEXT (pipeline attribution)."""
    conn = _open(tmp_path)
    cols = {
        r["name"]: r["type"]
        for r in conn.execute("PRAGMA table_info(analytics_snapshots)").fetchall()
    }
    assert "experiment_id" in cols


# ── Y: opportunity FK ─────────────────────────────────────────────────────────


def test_Y_opportunity_fk_enforced(tmp_path):
    conn = _open(tmp_path)
    ch = _make_channel(conn)
    eid = _new_id()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO experiments "
            "(id, channel_id, opportunity_id, experiment_type, hypothesis, "
            "maturity_policy_json, policy_snapshot_json, created_at, updated_at) "
            "VALUES (?, ?, 9999, 'exploration', 'H', '{}', '{}', '2024-01-01', '2024-01-01')",
            (eid, ch),
        )


# ── Z: multiple experiments per channel/opportunity allowed ───────────────────


def test_Z_multiple_experiments_per_channel_allowed(tmp_path):
    conn = _open(tmp_path)
    ch = _make_channel(conn)
    exp1 = _create(conn, ch, hypothesis="Hypothesis A")
    exp2 = _create(conn, ch, hypothesis="Hypothesis B")
    assert exp1.id != exp2.id
    count = conn.execute("SELECT COUNT(*) FROM experiments WHERE channel_id = ?", (ch,)).fetchone()[
        0
    ]
    assert count == 2


def test_Z_multiple_experiments_per_opportunity_allowed(tmp_path):
    conn = _open(tmp_path)
    ch = _make_channel(conn)
    opp_id = _make_opportunity(conn, ch)
    exp1 = _create(conn, ch, opportunity_id=opp_id, hypothesis="H1 opp")
    exp2 = _create(conn, ch, opportunity_id=opp_id, hypothesis="H2 opp")
    assert exp1.id != exp2.id
    count = conn.execute(
        "SELECT COUNT(*) FROM experiments WHERE opportunity_id = ?", (opp_id,)
    ).fetchone()[0]
    assert count == 2
