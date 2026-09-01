"""Phase 16B.1.1 — Multi-Channel Learning Application Isolation.

Tests A–T prove the channel-scoping invariant:
  Channel A publication → recommendation → proposed application → production override
  must NEVER be consumable by Channel B.

Uses production DDL (open_db) and real schema (SCHEMA_VERSION=42).
FK constraints are disabled during seeding so we can insert minimal rows
without walking the entire FK chain.
"""

from __future__ import annotations

import hashlib
import pathlib
import sqlite3
import tempfile

import pytest

from app.core.database import open_db
from app.learning.application import (
    ApplicationIntent,
    DuplicateApplicationError,
    build_narration_pace_intent,
    get_active_application_for_parameter,
    get_application,
    propose_application,
    resolve_speaking_rate_override,
)
from app.learning.constants import (
    APPLICATION_STATUS_PROPOSED,
    NARRATION_PACE_PARAMETER,
)

# ── DB fixture ────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        conn = open_db(pathlib.Path(d) / "test.db")
        conn.execute("PRAGMA foreign_keys = OFF")
        yield conn


# ── Low-level seed helpers ────────────────────────────────────────────────────


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


# ── Two-channel seeding helper ────────────────────────────────────────────────


def _seed_two_channels(conn: sqlite3.Connection) -> dict:
    cp_a = "cp-chan-a"
    cp_b = "cp-chan-b"
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

    conn.commit()

    return {
        "cp_a": cp_a,
        "cp_b": cp_b,
        "ch_a": ch_a,
        "ch_b": ch_b,
        "topic_id": topic_id,
        "pub_a": pub_a,
        "pub_b": pub_b,
        "run_a": run_a,
        "run_b": run_b,
        "rec_a": rec_a,
        "rec_b": rec_b,
    }


# ── Tests A–D: propose_application writes the correct channel_id ──────────────


class TestProposalChannelPersistence:
    def test_A_propose_populates_channel_id_from_lineage(self, db):
        """A: proposal via Channel A publication → channel_id = ch_a."""
        ctx = _seed_two_channels(db)
        app_id = propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())
        row = db.execute(
            "SELECT channel_id FROM recommendation_applications WHERE id = ?", (app_id,)
        ).fetchone()
        assert row["channel_id"] == ctx["ch_a"], (
            f"Expected channel_id={ctx['ch_a']}, got {row['channel_id']}"
        )

    def test_B_propose_b_populates_channel_b(self, db):
        """B: proposal via Channel B publication → channel_id = ch_b."""
        ctx = _seed_two_channels(db)
        app_id = propose_application(db, recommendation_id=ctx["rec_b"], intent=_make_intent())
        row = db.execute(
            "SELECT channel_id FROM recommendation_applications WHERE id = ?", (app_id,)
        ).fetchone()
        assert row["channel_id"] == ctx["ch_b"]

    def test_C_channel_a_and_b_independent_proposals(self, db):
        """C: Same topic, same parameter — A and B each get independent proposed rows."""
        ctx = _seed_two_channels(db)
        app_a = propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())
        app_b = propose_application(db, recommendation_id=ctx["rec_b"], intent=_make_intent())

        assert app_a != app_b
        row_a = db.execute(
            "SELECT channel_id FROM recommendation_applications WHERE id = ?", (app_a,)
        ).fetchone()
        row_b = db.execute(
            "SELECT channel_id FROM recommendation_applications WHERE id = ?", (app_b,)
        ).fetchone()
        assert row_a["channel_id"] == ctx["ch_a"]
        assert row_b["channel_id"] == ctx["ch_b"]

    def test_D_null_publication_recommendation_stays_null(self, db):
        """D: Recommendation with no publication_id → channel_id stays NULL (legacy path)."""
        topic_id = _seed_topic(db, "Legacy Topic")
        run_id = _seed_learning_run(db, topic_id, None)
        rec_id = _seed_recommendation(db, topic_id, run_id, None)
        db.commit()

        app_id = propose_application(db, recommendation_id=rec_id, intent=_make_intent())
        row = db.execute(
            "SELECT channel_id FROM recommendation_applications WHERE id = ?", (app_id,)
        ).fetchone()
        assert row["channel_id"] is None, "Legacy (no-publication) row must have NULL channel_id"


# ── Tests E–J: get_active_application_for_parameter() channel-scoping ─────────


class TestGetActiveApplicationIsolation:
    def test_E_channel_a_lookup_returns_only_a_row(self, db):
        """E: Lookup with ch_a returns Channel A's application, not Channel B's."""
        ctx = _seed_two_channels(db)
        propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())
        propose_application(db, recommendation_id=ctx["rec_b"], intent=_make_intent())

        result = get_active_application_for_parameter(
            db,
            topic_id=ctx["topic_id"],
            parameter_name=NARRATION_PACE_PARAMETER,
            channel_id=ctx["ch_a"],
        )
        assert result is not None
        assert result.channel_id == ctx["ch_a"]

    def test_F_channel_b_lookup_returns_only_b_row(self, db):
        """F: Lookup with ch_b returns Channel B's application, not Channel A's."""
        ctx = _seed_two_channels(db)
        propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())
        propose_application(db, recommendation_id=ctx["rec_b"], intent=_make_intent())

        result = get_active_application_for_parameter(
            db,
            topic_id=ctx["topic_id"],
            parameter_name=NARRATION_PACE_PARAMETER,
            channel_id=ctx["ch_b"],
        )
        assert result is not None
        assert result.channel_id == ctx["ch_b"]

    def test_G_channel_a_lookup_returns_none_when_only_b_proposed(self, db):
        """G: ch_a lookup returns None when only ch_b has a proposed application."""
        ctx = _seed_two_channels(db)
        propose_application(db, recommendation_id=ctx["rec_b"], intent=_make_intent())

        result = get_active_application_for_parameter(
            db,
            topic_id=ctx["topic_id"],
            parameter_name=NARRATION_PACE_PARAMETER,
            channel_id=ctx["ch_a"],
        )
        assert result is None, "Channel A must not see Channel B's application"

    def test_H_channel_b_lookup_returns_none_when_only_a_proposed(self, db):
        """H: ch_b lookup returns None when only ch_a has a proposed application."""
        ctx = _seed_two_channels(db)
        propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())

        result = get_active_application_for_parameter(
            db,
            topic_id=ctx["topic_id"],
            parameter_name=NARRATION_PACE_PARAMETER,
            channel_id=ctx["ch_b"],
        )
        assert result is None, "Channel B must not see Channel A's application"

    def test_I_null_channel_lookup_excludes_scoped_rows(self, db):
        """I: Legacy (channel_id=None) lookup returns None when only channel-scoped rows exist."""
        ctx = _seed_two_channels(db)
        propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())

        result = get_active_application_for_parameter(
            db,
            topic_id=ctx["topic_id"],
            parameter_name=NARRATION_PACE_PARAMETER,
            channel_id=None,
        )
        assert result is None, "Legacy lookup must NOT return channel-scoped rows"

    def test_J_scoped_lookup_excludes_null_channel_rows(self, db):
        """J: channel_id lookup returns None when only NULL-channel (legacy) rows exist."""
        ctx = _seed_two_channels(db)
        topic_id = ctx["topic_id"]

        # Insert a NULL-channel legacy row for the same topic
        run_id = _seed_learning_run(db, topic_id, None)
        rec_id = _seed_recommendation(db, topic_id, run_id, None)
        db.commit()
        propose_application(db, recommendation_id=rec_id, intent=_make_intent())

        result = get_active_application_for_parameter(
            db,
            topic_id=topic_id,
            parameter_name=NARRATION_PACE_PARAMETER,
            channel_id=ctx["ch_a"],
        )
        assert result is None, "Channel-scoped lookup must NOT return legacy NULL rows"


# ── Tests K–N: resolve_speaking_rate_override() channel semantics ──────────────


class TestResolveSpeakingRateOverride:
    def test_K_resolve_channel_a_returns_a_override(self, db):
        """K: Resolve with ch_a returns Channel A's proposed speaking rate."""
        ctx = _seed_two_channels(db)
        intent_a = _make_intent(direction="increase", magnitude=0.1, current_rate=1.0)
        propose_application(db, recommendation_id=ctx["rec_a"], intent=intent_a)

        _app, rate = resolve_speaking_rate_override(
            db, topic_id=ctx["topic_id"], channel_id=ctx["ch_a"]
        )
        assert rate is not None
        assert abs(rate - intent_a.target_value) < 1e-6

    def test_L_resolve_channel_b_returns_none_when_only_a_proposed(self, db):
        """L: Resolve with ch_b returns (None, None) when only ch_a has an application."""
        ctx = _seed_two_channels(db)
        propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())

        app, rate = resolve_speaking_rate_override(
            db, topic_id=ctx["topic_id"], channel_id=ctx["ch_b"]
        )
        assert app is None
        assert rate is None

    def test_M_resolve_different_rates_per_channel(self, db):
        """M: Channels A and B can have independent speaking rate overrides."""
        ctx = _seed_two_channels(db)
        intent_a = _make_intent(direction="increase", magnitude=0.1, current_rate=1.0)
        intent_b = _make_intent(direction="decrease", magnitude=0.05, current_rate=1.0)
        propose_application(db, recommendation_id=ctx["rec_a"], intent=intent_a)
        propose_application(db, recommendation_id=ctx["rec_b"], intent=intent_b)

        _, rate_a = resolve_speaking_rate_override(
            db, topic_id=ctx["topic_id"], channel_id=ctx["ch_a"]
        )
        _, rate_b = resolve_speaking_rate_override(
            db, topic_id=ctx["topic_id"], channel_id=ctx["ch_b"]
        )

        assert rate_a is not None and rate_b is not None
        assert abs(rate_a - intent_a.target_value) < 1e-6
        assert abs(rate_b - intent_b.target_value) < 1e-6
        assert rate_a != rate_b

    def test_N_resolve_legacy_path_returns_null_channel_row(self, db):
        """N: Legacy (channel_id=None) resolve returns NULL-channel application."""
        topic_id = _seed_topic(db, "Legacy Topic N")
        run_id = _seed_learning_run(db, topic_id, None)
        rec_id = _seed_recommendation(db, topic_id, run_id, None)
        db.commit()

        intent = _make_intent()
        propose_application(db, recommendation_id=rec_id, intent=intent)

        app, rate = resolve_speaking_rate_override(db, topic_id=topic_id, channel_id=None)
        assert app is not None
        assert app.channel_id is None
        assert rate is not None


# ── Tests O–R: isolation when topic_id and parameter_name are identical ────────


class TestSameTopicLabelIsolation:
    def test_O_same_topic_id_different_channels_independent(self, db):
        """O: Same topic_id, same parameter — A and B have completely independent rows."""
        ctx = _seed_two_channels(db)
        app_a = propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())
        app_b = propose_application(db, recommendation_id=ctx["rec_b"], intent=_make_intent())

        assert app_a != app_b
        row_a = get_application(db, app_a)
        row_b = get_application(db, app_b)
        assert row_a.channel_id != row_b.channel_id
        assert row_a.channel_id == ctx["ch_a"]
        assert row_b.channel_id == ctx["ch_b"]

    def test_P_unique_constraint_still_blocks_dup_within_channel(self, db):
        """P: A second proposal on same (recommendation, topic, parameter) is still blocked."""
        ctx = _seed_two_channels(db)
        propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())

        with pytest.raises(DuplicateApplicationError):
            propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())

    def test_Q_three_channels_independent(self, db):
        """Q: Adding a third channel follows the same path without schema change."""
        ctx = _seed_two_channels(db)
        topic_id = ctx["topic_id"]

        cp_c = "cp-chan-c"
        _seed_cp_channel(db, cp_c, "Channel C")
        ch_c = _seed_intelligence_channel(db, cp_c)
        pub_c = _seed_publication(db, cp_c, key="pub-c")
        run_c = _seed_learning_run(db, topic_id, pub_c)
        rec_c = _seed_recommendation(db, topic_id, run_c, pub_c)
        db.commit()

        propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())
        propose_application(db, recommendation_id=ctx["rec_b"], intent=_make_intent())
        app_c = propose_application(db, recommendation_id=rec_c, intent=_make_intent())

        row_c = db.execute(
            "SELECT channel_id FROM recommendation_applications WHERE id = ?", (app_c,)
        ).fetchone()
        assert row_c["channel_id"] == ch_c

        res_a = get_active_application_for_parameter(
            db, topic_id=topic_id, parameter_name=NARRATION_PACE_PARAMETER, channel_id=ctx["ch_a"]
        )
        res_c = get_active_application_for_parameter(
            db, topic_id=topic_id, parameter_name=NARRATION_PACE_PARAMETER, channel_id=ch_c
        )
        assert res_a is not None and res_a.channel_id == ctx["ch_a"]
        assert res_c is not None and res_c.channel_id == ch_c
        assert res_a.id != res_c.id

    def test_R_fourth_channel_no_schema_change(self, db):
        """R: Fourth channel follows same path — SCHEMA_VERSION stays at 42."""
        from app.core.database import SCHEMA_VERSION

        ctx = _seed_two_channels(db)
        topic_id = ctx["topic_id"]

        for letter in ("c", "d"):
            cp = f"cp-chan-{letter}"
            _seed_cp_channel(db, cp, f"Channel {letter.upper()}")
            ch = _seed_intelligence_channel(db, cp)
            pub = _seed_publication(db, cp, key=f"pub-{letter}")
            run = _seed_learning_run(db, topic_id, pub)
            rec = _seed_recommendation(db, topic_id, run, pub)
            db.commit()

            app_id = propose_application(db, recommendation_id=rec, intent=_make_intent())
            row = db.execute(
                "SELECT channel_id FROM recommendation_applications WHERE id = ?", (app_id,)
            ).fetchone()
            assert row["channel_id"] == ch

        assert SCHEMA_VERSION == 51


# ── Tests S–T: experiment authority interaction ────────────────────────────────


class TestExperimentAuthorityInteraction:
    def test_S_channel_a_row_accessible_via_experiment_channel_derivation(self, db):
        """S: experiment.channel_id → same value as ch_a → scoped lookup finds Channel A row."""
        ctx = _seed_two_channels(db)
        propose_application(db, recommendation_id=ctx["rec_a"], intent=_make_intent())

        # Simulate the NarrationExecutor derivation: experiment gives channel_id = ch_a
        result = get_active_application_for_parameter(
            db,
            topic_id=ctx["topic_id"],
            parameter_name=NARRATION_PACE_PARAMETER,
            channel_id=ctx["ch_a"],
        )
        assert result is not None
        assert result.channel_id == ctx["ch_a"]
        assert result.status == APPLICATION_STATUS_PROPOSED

    def test_T_structural_columns_exist_for_derivation_chain(self, db):
        """T: All columns in the derivation chain exist in production schema."""
        # experiments.channel_id
        exp_cols = [r["name"] for r in db.execute("PRAGMA table_info(experiments)").fetchall()]
        assert "channel_id" in exp_cols

        # recommendation_applications.channel_id
        app_cols = [
            r["name"]
            for r in db.execute("PRAGMA table_info(recommendation_applications)").fetchall()
        ]
        assert "channel_id" in app_cols

        # publications.channel_id (UUID → cp_channels bridge)
        pub_cols = [r["name"] for r in db.execute("PRAGMA table_info(publications)").fetchall()]
        assert "channel_id" in pub_cols

        # channels.cp_channel_id (bridge from UUID to INTEGER)
        ch_cols = [r["name"] for r in db.execute("PRAGMA table_info(channels)").fetchall()]
        assert "cp_channel_id" in ch_cols
