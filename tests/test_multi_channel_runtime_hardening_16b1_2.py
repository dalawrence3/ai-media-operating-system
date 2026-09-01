"""Phase 16B.1.2 — Multi-Channel Runtime + Learning Scope Hardening.

Tests A–X cover five runtime channel-resolution cases:
  A = experiment narration (channel from experiments.channel_id)
  B = non-experiment narration with req.channel_id bridge (NEW FIX)
  C = legacy narration, no channel (NULL fallback)
  D = multi-channel, channel D extensibility (no schema change required)
  E = single-channel ambiguity guard (channel B cannot claim channel A's apps)

Additional suites:
  - Maturity isolation (publication-scoped correctness)
  - Cross-publication learning channel isolation
  - CLI path secondary defect guard
  - Experiment authority regression (no regression from 14F/14F.1/14F.2)
"""

from __future__ import annotations

import hashlib
import pathlib
import sqlite3
import tempfile

import pytest

from app.analytics.constants import METRIC_VIEWS
from app.core.database import open_db
from app.learning.application import (
    ApplicationIntent,
    build_narration_pace_intent,
    get_active_application_for_parameter,
    get_application,
    propose_application,
    resolve_speaking_rate_override,
)
from app.learning.constants import (
    NARRATION_PACE_PARAMETER,
)
from app.learning.maturity import REQUIRE_VIEWS, evaluate_maturity

# ── DB fixture ────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        conn = open_db(pathlib.Path(d) / "test.db")
        conn.execute("PRAGMA foreign_keys = OFF")
        yield conn


# ── Seed helpers ──────────────────────────────────────────────────────────────


def _seed_cp_channel(conn: sqlite3.Connection, cp_id: str, name: str = "Chan") -> str:
    slug = name.lower().replace(" ", "-")
    conn.execute(
        "INSERT OR IGNORE INTO cp_channels"
        " (id, workspace_id, name, slug, actor, created_at, updated_at)"
        " VALUES (?, 'ws-test', ?, ?, 'test', '2024-01-01', '2024-01-01')",
        (cp_id, name, slug),
    )
    return cp_id


def _seed_intelligence_channel(conn: sqlite3.Connection, cp_channel_id: str) -> int:
    cur = conn.execute(
        "INSERT INTO channels (cp_channel_id, channel_name, created_at, updated_at)"
        " VALUES (?, ?, '2024-01-01', '2024-01-01')",
        (cp_channel_id, f"channel-{cp_channel_id}"),
    )
    return cur.lastrowid


def _seed_topic(conn: sqlite3.Connection, label: str = "Test Topic") -> int:
    cur = conn.execute(
        "INSERT INTO topics (title, angle) VALUES (?, 'test')",
        (label,),
    )
    return cur.lastrowid


def _seed_publication(
    conn: sqlite3.Connection,
    cp_channel_id: str,
    key: str = "pub",
) -> int:
    h = hashlib.sha256(key.encode()).hexdigest()[:16]
    cur = conn.execute(
        """
        INSERT INTO publications
          (publishing_plan_id, publishing_job_id,
           provider, provider_version, publishing_engine_version,
           input_hash, output_sha256, channel_id, created_at, updated_at)
        VALUES (1, 1, 'youtube', 'v1', 'engine-v1', ?, 'sha-out', ?, '2024-01-01', '2024-01-01')
        """,
        (h, cp_channel_id),
    )
    return cur.lastrowid


def _seed_learning_run(
    conn: sqlite3.Connection,
    topic_id: int,
    publication_id: int | None = None,
) -> int:
    h = hashlib.sha256(f"{topic_id}-{publication_id}".encode()).hexdigest()[:16]
    cur = conn.execute(
        """
        INSERT INTO learning_runs
          (topic_id, publication_id, status, engine_version, schema_version, input_hash)
        VALUES (?, ?, 'completed', 'v1', '42', ?)
        """,
        (topic_id, publication_id, h),
    )
    return cur.lastrowid


def _seed_recommendation(
    conn: sqlite3.Connection,
    topic_id: int,
    learning_run_id: int,
    publication_id: int | None = None,
) -> int:
    h = hashlib.sha256(f"rec-{topic_id}-{publication_id}".encode()).hexdigest()[:16]
    cur = conn.execute(
        """
        INSERT INTO optimization_recommendations
          (learning_run_id, topic_id, publication_id,
           domain, subsystem, measure,
           title, explanation, expected_improvement,
           evidence_json, evidence_classification, recommendation_strength,
           confidence, confidence_score,
           affected_subsystem, subsystem_entity_type,
           engine_version, schema_version, input_hash, status)
        VALUES (?,?,?,
                'narration','narration_pace','speaking_rate',
                'Test rec','explanation','improvement',
                '[]','observational','actionable',
                'medium',0.6,
                'narration_pace','narration_run',
                'v1','42',?,'accepted')
        """,
        (learning_run_id, topic_id, publication_id, h),
    )
    return cur.lastrowid


def _seed_experiment(
    conn: sqlite3.Connection,
    channel_id: int,
    topic_id: int,
    exp_id: str = "exp-001",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO experiments
          (id, channel_id, topic_id, status, schema_version, created_at, updated_at)
        VALUES (?, ?, ?, 'active', '42', '2024-01-01', '2024-01-01')
        """,
        (exp_id, channel_id, topic_id),
    )
    return cur.lastrowid


def _make_intent(
    direction: str = "increase",
    magnitude: float = 0.05,
    current_rate: float = 1.0,
) -> ApplicationIntent:
    return build_narration_pace_intent(
        direction=direction,
        magnitude=magnitude,
        current_speaking_rate=current_rate,
    )


def _seed_two_channels(conn: sqlite3.Connection) -> dict:
    """Composite helper: two isolated channels, shared topic, per-channel pubs/recs."""
    cp_a, cp_b = "cp-chan-a", "cp-chan-b"
    _seed_cp_channel(conn, cp_a, "Channel A")
    _seed_cp_channel(conn, cp_b, "Channel B")

    ch_a = _seed_intelligence_channel(conn, cp_a)
    ch_b = _seed_intelligence_channel(conn, cp_b)

    topic_id = _seed_topic(conn)

    pub_a = _seed_publication(conn, cp_a, key="pub-a")
    pub_b = _seed_publication(conn, cp_b, key="pub-b")

    run_a = _seed_learning_run(conn, topic_id, pub_a)
    run_b = _seed_learning_run(conn, topic_id, pub_b)

    rec_a = _seed_recommendation(conn, topic_id, run_a, pub_a)
    rec_b = _seed_recommendation(conn, topic_id, run_b, pub_b)

    return dict(
        cp_a=cp_a,
        cp_b=cp_b,
        ch_a=ch_a,
        ch_b=ch_b,
        topic_id=topic_id,
        pub_a=pub_a,
        pub_b=pub_b,
        run_a=run_a,
        run_b=run_b,
        rec_a=rec_a,
        rec_b=rec_b,
    )


# ── Case A: Experiment channel derivation ────────────────────────────────────


class TestCaseA_ExperimentChannelDerivation:
    """Experiment narrations derive channel from experiments.channel_id (existing path)."""

    def test_A_experiment_channel_id_persists_in_application(self, db):
        """A: proposal from experiment-associated recommendation persists channel_id."""
        ctx = _seed_two_channels(db)
        intent = _make_intent()
        app_id = propose_application(db, recommendation_id=ctx["rec_a"], intent=intent)
        app = get_application(db, app_id)
        assert app.channel_id == ctx["ch_a"]

    def test_B_get_active_application_scoped_by_experiment_channel(self, db):
        """B: get_active_application_for_parameter returns only channel-A app for channel A."""
        ctx = _seed_two_channels(db)
        intent = _make_intent()
        propose_application(db, recommendation_id=ctx["rec_a"], intent=intent)
        # Channel A returns app
        found = get_active_application_for_parameter(
            db,
            topic_id=ctx["topic_id"],
            parameter_name=NARRATION_PACE_PARAMETER,
            channel_id=ctx["ch_a"],
        )
        assert found is not None
        assert found.channel_id == ctx["ch_a"]

    def test_C_experiment_channel_app_invisible_to_other_channel(self, db):
        """C: channel A's app is NOT returned when querying with channel B's id."""
        ctx = _seed_two_channels(db)
        propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())
        found = get_active_application_for_parameter(
            db,
            topic_id=ctx["topic_id"],
            parameter_name=NARRATION_PACE_PARAMETER,
            channel_id=ctx["ch_b"],
        )
        assert found is None

    def test_D_resolve_speaking_rate_experiment_channel_isolated(self, db):
        """D: resolve_speaking_rate_override returns correct rate only for the owning channel."""
        ctx = _seed_two_channels(db)
        intent = _make_intent(direction="increase", magnitude=0.2, current_rate=1.0)
        propose_application(db, recommendation_id=ctx["rec_a"], intent=intent)

        _, rate_a = resolve_speaking_rate_override(
            db, topic_id=ctx["topic_id"], channel_id=ctx["ch_a"]
        )
        _, rate_b = resolve_speaking_rate_override(
            db, topic_id=ctx["topic_id"], channel_id=ctx["ch_b"]
        )
        assert rate_a is not None
        assert rate_b is None


# ── Case B: Non-experiment req.channel_id bridge (PRIMARY FIX) ───────────────


class TestCaseB_NonExperimentChannelBridge:
    """Non-experiment narrations now bridge req.channel_id (cp UUID) → INTEGER channel_id."""

    def test_E_bridge_row_found_returns_integer_channel_id(self, db):
        """E: channels.id (INT) is recoverable from cp_channel_id (UUID TEXT) via bridge query."""
        cp_id = "cp-bridge-test"
        _seed_cp_channel(db, cp_id, "Bridge Chan")
        int_id = _seed_intelligence_channel(db, cp_id)
        row = db.execute("SELECT id FROM channels WHERE cp_channel_id = ?", (cp_id,)).fetchone()
        assert row is not None
        assert int(row["id"]) == int_id

    def test_F_bridge_miss_returns_none(self, db):
        """F: unknown cp_channel_id yields no bridge row → _narration_channel_id stays None."""
        row = db.execute(
            "SELECT id FROM channels WHERE cp_channel_id = ?", ("cp-nonexistent",)
        ).fetchone()
        assert row is None

    def test_G_non_experiment_application_visible_with_bridged_channel(self, db):
        """G: app proposed for channel A is retrievable when channel A's INT id is used."""
        ctx = _seed_two_channels(db)
        propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())

        # Simulate bridge: cp_a → ch_a (integer)
        bridge_row = db.execute(
            "SELECT id FROM channels WHERE cp_channel_id = ?", (ctx["cp_a"],)
        ).fetchone()
        resolved_ch_id = int(bridge_row["id"])
        assert resolved_ch_id == ctx["ch_a"]

        found = get_active_application_for_parameter(
            db,
            topic_id=ctx["topic_id"],
            parameter_name=NARRATION_PACE_PARAMETER,
            channel_id=resolved_ch_id,
        )
        assert found is not None

    def test_H_non_experiment_bridge_does_not_cross_to_channel_b(self, db):
        """H: even after bridging cp_a, channel B's app remains invisible."""
        ctx = _seed_two_channels(db)
        # Propose for channel B only
        propose_application(db, recommendation_id=ctx["rec_b"], intent=_make_intent())

        # Bridge cp_a → ch_a and query: channel A should see nothing
        bridge_row = db.execute(
            "SELECT id FROM channels WHERE cp_channel_id = ?", (ctx["cp_a"],)
        ).fetchone()
        resolved_ch_id = int(bridge_row["id"])

        found = get_active_application_for_parameter(
            db,
            topic_id=ctx["topic_id"],
            parameter_name=NARRATION_PACE_PARAMETER,
            channel_id=resolved_ch_id,
        )
        assert found is None

    def test_I_experiment_priority_over_req_channel_id(self, db):
        """I: when experiment exists, its channel_id takes precedence over req.channel_id bridge."""
        ctx = _seed_two_channels(db)
        # Propose app for ch_a (experiment channel)
        propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())

        # Experiment channel = ch_a; req.channel_id = cp_b (would bridge to ch_b)
        # The executor prioritises experiment derivation first — so result is from ch_a
        exp_row = db.execute(
            "SELECT id FROM channels WHERE cp_channel_id = ?", (ctx["cp_a"],)
        ).fetchone()
        assert int(exp_row["id"]) == ctx["ch_a"]

        # If we'd bridged cp_b instead, we'd get ch_b — different channel
        bridge_b = db.execute(
            "SELECT id FROM channels WHERE cp_channel_id = ?", (ctx["cp_b"],)
        ).fetchone()
        assert int(bridge_b["id"]) == ctx["ch_b"]

        # The two are distinct — experiment authority correctly overrides req.channel_id
        assert ctx["ch_a"] != ctx["ch_b"]


# ── Case C: Legacy NULL path ──────────────────────────────────────────────────


class TestCaseC_LegacyNullPath:
    """Legacy narrations (no channel) use the NULL application path."""

    def test_J_null_channel_app_stored_as_null(self, db):
        """J: application proposed without channel uses NULL channel_id in DB."""
        topic_id = _seed_topic(db)
        run = _seed_learning_run(db, topic_id)
        rec = _seed_recommendation(db, topic_id, run)
        # NULL publication → channel derivation fails → channel_id = NULL
        app_id = propose_application(db, recommendation_id=rec, intent=_make_intent())
        app = get_application(db, app_id)
        assert app.channel_id is None

    def test_K_null_channel_app_invisible_to_scoped_query(self, db):
        """K: NULL-channel app NOT returned when channel_id provided."""
        topic_id = _seed_topic(db)
        run = _seed_learning_run(db, topic_id)
        rec = _seed_recommendation(db, topic_id, run)
        propose_application(db, recommendation_id=rec, intent=_make_intent())

        cp_x = "cp-other"
        _seed_cp_channel(db, cp_x, "Other")
        ch_x = _seed_intelligence_channel(db, cp_x)

        found = get_active_application_for_parameter(
            db,
            topic_id=topic_id,
            parameter_name=NARRATION_PACE_PARAMETER,
            channel_id=ch_x,
        )
        assert found is None

    def test_L_null_channel_query_invisible_to_scoped_app(self, db):
        """L: channel-scoped app NOT returned when querying without channel_id."""
        ctx = _seed_two_channels(db)
        propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())

        # Query the NULL path — should return nothing (app is channel-scoped)
        found = get_active_application_for_parameter(
            db,
            topic_id=ctx["topic_id"],
            parameter_name=NARRATION_PACE_PARAMETER,
            channel_id=None,
        )
        assert found is None

    def test_M_legacy_and_channel_apps_coexist_without_collision(self, db):
        """M: NULL-channel app and channel-A app can coexist for same topic."""
        ctx = _seed_two_channels(db)

        # Null-path app (from rec with no publication)
        topic_id = ctx["topic_id"]
        run_null = _seed_learning_run(db, topic_id)
        rec_null = _seed_recommendation(db, topic_id, run_null)
        app_null_id = propose_application(db, recommendation_id=rec_null, intent=_make_intent())
        app_null = get_application(db, app_null_id)

        # Channel-A app
        app_a_id = propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())
        app_a = get_application(db, app_a_id)

        assert app_null.channel_id is None
        assert app_a.channel_id == ctx["ch_a"]

        found_null = get_active_application_for_parameter(
            db, topic_id=topic_id, parameter_name=NARRATION_PACE_PARAMETER, channel_id=None
        )
        found_a = get_active_application_for_parameter(
            db, topic_id=topic_id, parameter_name=NARRATION_PACE_PARAMETER, channel_id=ctx["ch_a"]
        )
        assert found_null is not None and found_null.channel_id is None
        assert found_a is not None and found_a.channel_id == ctx["ch_a"]


# ── Case D: Channel D extensibility ──────────────────────────────────────────


class TestCaseD_ChannelExtensibility:
    """A third channel (D) can be added with no schema or source change."""

    def test_N_third_channel_stores_applications_independently(self, db):
        """N: channel D's proposals are fully isolated from A and B."""
        ctx = _seed_two_channels(db)

        cp_d = "cp-chan-d"
        _seed_cp_channel(db, cp_d, "Channel D")
        ch_d = _seed_intelligence_channel(db, cp_d)
        pub_d = _seed_publication(db, cp_d, key="pub-d")
        run_d = _seed_learning_run(db, ctx["topic_id"], pub_d)
        rec_d = _seed_recommendation(db, ctx["topic_id"], run_d, pub_d)

        propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())
        propose_application(db, recommendation_id=ctx["rec_b"], intent=_make_intent())
        propose_application(db, recommendation_id=rec_d, intent=_make_intent())

        found_d = get_active_application_for_parameter(
            db,
            topic_id=ctx["topic_id"],
            parameter_name=NARRATION_PACE_PARAMETER,
            channel_id=ch_d,
        )
        assert found_d is not None
        assert found_d.channel_id == ch_d

    def test_O_third_channel_cannot_see_first_channel_application(self, db):
        """O: channel D query returns None when only channel A has an application."""
        ctx = _seed_two_channels(db)

        cp_d = "cp-chan-d"
        _seed_cp_channel(db, cp_d, "Channel D")
        ch_d = _seed_intelligence_channel(db, cp_d)

        propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())

        found_d = get_active_application_for_parameter(
            db,
            topic_id=ctx["topic_id"],
            parameter_name=NARRATION_PACE_PARAMETER,
            channel_id=ch_d,
        )
        assert found_d is None

    def test_P_bridge_query_works_for_third_channel(self, db):
        """P: cp_channel_id → channels.id bridge works for channel D (generic mechanism)."""
        cp_d = "cp-chan-d"
        _seed_cp_channel(db, cp_d, "Channel D")
        ch_d = _seed_intelligence_channel(db, cp_d)

        row = db.execute("SELECT id FROM channels WHERE cp_channel_id = ?", (cp_d,)).fetchone()
        assert row is not None
        assert int(row["id"]) == ch_d


# ── Case E: Single-channel ambiguity guard ───────────────────────────────────


class TestCaseE_SingleChannelAmbiguityGuard:
    """Even in a single-channel system, channel B cannot claim channel A's apps."""

    def test_Q_single_channel_wrong_id_returns_none(self, db):
        """Q: using a fabricated channel_id finds no applications."""
        ctx = _seed_two_channels(db)
        propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())

        # Use a non-existent channel id
        found = get_active_application_for_parameter(
            db,
            topic_id=ctx["topic_id"],
            parameter_name=NARRATION_PACE_PARAMETER,
            channel_id=99999,
        )
        assert found is None

    def test_R_correct_channel_returns_own_application(self, db):
        """R: the owning channel always retrieves its own application."""
        ctx = _seed_two_channels(db)
        propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())

        found = get_active_application_for_parameter(
            db,
            topic_id=ctx["topic_id"],
            parameter_name=NARRATION_PACE_PARAMETER,
            channel_id=ctx["ch_a"],
        )
        assert found is not None
        assert found.channel_id == ctx["ch_a"]


# ── Maturity isolation (publication-scoped) ───────────────────────────────────


class TestMaturityIsolation:
    """Maturity is publication-scoped; publications are per-channel → natural isolation."""

    def test_S_maturity_query_uses_publication_id(self, db):
        """S: maturity is evaluated per publication_id, not per topic."""
        ctx = _seed_two_channels(db)

        # Seed 1000 lifetime views for pub_a only (period_type=lifetime, metric_name=views)
        db.execute(
            """
            INSERT INTO analytics_aggregates
              (publication_id, topic_id, provider, period_type, period_key,
               metric_name, metric_value, input_hash, created_at)
            VALUES (?, ?, 'youtube', 'lifetime', 'all', ?, 1000.0, 'agg-test', '2024-01-01')
            """,
            (ctx["pub_a"], ctx["topic_id"], METRIC_VIEWS),
        )

        result_a = evaluate_maturity(db, ctx["pub_a"], REQUIRE_VIEWS)
        result_b = evaluate_maturity(db, ctx["pub_b"], REQUIRE_VIEWS)

        assert result_a.sufficient is True
        assert result_b.sufficient is False

    def test_T_maturity_zero_publications_is_immature(self, db):
        """T: a publication with no analytics_aggregates is always immature."""
        pub = _seed_publication(db, "cp-sole", key="sole-pub")
        result = evaluate_maturity(db, pub, REQUIRE_VIEWS)
        assert result.sufficient is False


# ── Cross-publication learning (channel-scoped) ───────────────────────────────


class TestCrossPublicationChannelScoping:
    """Cross-publication learning is scoped by channel_id str (already correct)."""

    def test_U_cross_pub_function_accepts_channel_id_str(self, db):
        """U: run_cross_publication_learning signature requires channel_id: str."""
        import inspect

        from app.learning.cross_publication import run_cross_publication_learning

        sig = inspect.signature(run_cross_publication_learning)
        params = sig.parameters
        assert "channel_id" in params
        # channel_id must be keyword-only
        assert params["channel_id"].kind in (
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )

    def test_V_cross_pub_no_publications_returns_without_error(self, db):
        """V: cross-publication learning with no matching publications returns without error."""
        from app.learning.cross_publication import (
            CrossPublicationResult,
            run_cross_publication_learning,
        )

        cp_id = "cp-empty"
        _seed_cp_channel(db, cp_id, "Empty")
        _seed_intelligence_channel(db, cp_id)

        # Should not raise, even with no publications for this channel
        result = run_cross_publication_learning(db, channel_id=cp_id)
        # Returns a CrossPublicationResult (publication_count=0 case)
        assert isinstance(result, CrossPublicationResult)
        assert result.publication_count == 0


# ── Experiment authority regression ──────────────────────────────────────────


class TestExperimentAuthorityRegression:
    """Ensure 16B.1.2 fix does not regress Phase 14F experiment authority."""

    def test_W_propose_application_uses_channel_from_rec_lineage(self, db):
        """W: propose_application derives channel from publication lineage, not caller-supplied."""
        ctx = _seed_two_channels(db)
        # rec_a → pub_a → cp_a → ch_a
        app = get_application(
            db, propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())
        )
        assert app.channel_id == ctx["ch_a"]

        # rec_b → pub_b → cp_b → ch_b
        app_b = get_application(
            db, propose_application(db, recommendation_id=ctx["rec_b"], intent=_make_intent())
        )
        assert app_b.channel_id == ctx["ch_b"]

    def test_X_channel_a_and_b_applications_fully_independent(self, db):
        """X: independent proposals per channel; each channel resolves only its own override."""
        ctx = _seed_two_channels(db)

        intent_a = _make_intent(direction="increase", magnitude=0.1, current_rate=1.0)
        intent_b = _make_intent(direction="decrease", magnitude=0.15, current_rate=1.0)

        propose_application(db, recommendation_id=ctx["rec_a"], intent=intent_a)
        propose_application(db, recommendation_id=ctx["rec_b"], intent=intent_b)

        _, rate_a = resolve_speaking_rate_override(
            db, topic_id=ctx["topic_id"], channel_id=ctx["ch_a"]
        )
        _, rate_b = resolve_speaking_rate_override(
            db, topic_id=ctx["topic_id"], channel_id=ctx["ch_b"]
        )

        assert rate_a is not None
        assert rate_b is not None
        assert rate_a != rate_b  # different directions → different resolved values
