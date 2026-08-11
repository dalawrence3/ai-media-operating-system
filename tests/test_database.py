"""Tests for database initialisation and schema versioning."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core.database import SCHEMA_VERSION, _get_version, open_db


def test_open_db_creates_file(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "test.db"
    conn = open_db(path)
    assert path.exists()
    conn.close()


def test_schema_version_set_after_init(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "test.db")
    assert _get_version(conn) == SCHEMA_VERSION
    conn.close()


def test_open_db_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "test.db"
    conn1 = open_db(path)
    conn1.close()
    conn2 = open_db(path)
    assert _get_version(conn2) == SCHEMA_VERSION
    conn2.close()


def test_foreign_keys_enforced(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO sources (topic_id, kind, reference) VALUES (999, 'url', 'http://x')"
        )
        db.commit()


def test_all_expected_tables_exist(db: sqlite3.Connection) -> None:
    tables = {
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert {"topics", "sources", "scripts", "runs", "schema_version"} <= tables


def test_phase3_tables_exist(db: sqlite3.Connection) -> None:
    tables = {
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    expected = {
        "channels",
        "channel_monetization_strategies",
        "channel_profile_versions",
        "channel_capacity_policies",
        "channel_operating_mode_events",
    }
    assert expected <= tables


def test_migration_from_v2_applies_latest(tmp_path) -> None:
    """A database at schema version 2 must migrate to SCHEMA_VERSION on next open_db."""
    from app.core.database import SCHEMA_VERSION, _get_version

    # Manually build a v2 database
    conn = open_db(tmp_path / "v2.db")
    # open_db already migrates to SCHEMA_VERSION; reset it to 2 to simulate a pre-v3 DB
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (2)")
    # Drop Phase 3–10 tables and columns to simulate a real v2 state
    for tbl in (
        "narration_review_events",
        "tts_calls",
        "narration_segment_assets",
        "narration_runs",
        "voice_profiles",
        "production_plan_review_events",
        "production_segment_citations",
        "production_segments",
        "production_plans",
        "script_citations",
        "script_generation_runs",
        "opportunity_state_events",
        "opportunity_source_evidence",
        "opportunity_observations",
        "opportunities",
        "discovery_runs",
        "opportunity_scores",
        "scoring_policies",
        "channel_operating_mode_events",
        "channel_capacity_policies",
        "channel_profile_versions",
        "channel_monetization_strategies",
        "channels",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {tbl}")
    # Remove v9 script columns (SQLite 3.35+ required for DROP COLUMN)
    for col in ("body_json", "format", "approved_at", "superseded_at"):
        conn.execute(f"ALTER TABLE scripts DROP COLUMN {col}")
    # Remove v6 additions from topics
    conn.execute("DROP INDEX IF EXISTS uq_topics_promoted_opportunity")
    conn.execute("ALTER TABLE topics DROP COLUMN promoted_opportunity_id")
    conn.commit()
    conn.close()

    # Re-open — must migrate to latest SCHEMA_VERSION
    conn2 = open_db(tmp_path / "v2.db")
    assert _get_version(conn2) == SCHEMA_VERSION
    tables = {
        r[0]
        for r in conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert "channels" in tables
    assert "channel_profile_versions" in tables
    conn2.close()


def test_schema_version_is_14(tmp_path: Path) -> None:
    from app.core.database import SCHEMA_VERSION, _get_version

    conn = open_db(tmp_path / "test.db")
    assert SCHEMA_VERSION == 20
    assert _get_version(conn) == 20
    conn.close()


def test_claim_extraction_tables_exist(db: sqlite3.Connection) -> None:
    tables = {
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert "claim_extraction_runs" in tables
    assert "claim_extraction_run_calls" in tables
    assert "claims" in tables


def test_claim_extraction_runs_columns(db: sqlite3.Connection) -> None:
    cols = {row[1] for row in db.execute("PRAGMA table_info(claim_extraction_runs)").fetchall()}
    required = {
        "id",
        "source_content_id",
        "status",
        "input_hash",
        "total_chunk_count",
        "completed_chunk_count",
        "failed_chunk_count",
        "accepted_claim_count",
        "was_truncated",
        "prompt_name",
        "prompt_version",
        "model",
        "provider",
        "extraction_algo_version",
        "error_message",
        "superseded_at",
        "superseded_by_run_id",
        "started_at",
        "completed_at",
        "created_at",
    }
    assert required <= cols


def test_claim_extraction_run_calls_columns(db: sqlite3.Connection) -> None:
    cols = {
        row[1] for row in db.execute("PRAGMA table_info(claim_extraction_run_calls)").fetchall()
    }
    required = {
        "id",
        "claim_extraction_run_id",
        "ai_call_id",
        "chunk_index",
        "chunk_hash",
        "input_char_start",
        "input_char_end",
        "status",
        "retry_count",
        "accepted_claim_count",
        "error_message",
        "started_at",
        "completed_at",
        "created_at",
    }
    assert required <= cols


def test_claims_columns(db: sqlite3.Connection) -> None:
    cols = {row[1] for row in db.execute("PRAGMA table_info(claims)").fetchall()}
    required = {
        "id",
        "extraction_run_id",
        "chunk_index",
        "claim_text",
        "claim_type",
        "supporting_quote",
        "quote_support_status",
        "quote_start",
        "quote_end",
        "page_number",
        "requires_date_review",
        "created_at",
    }
    assert required <= cols
    assert "source_content_id" not in cols


def test_claims_no_source_content_id_column(db: sqlite3.Connection) -> None:
    cols = {row[1] for row in db.execute("PRAGMA table_info(claims)").fetchall()}
    assert "source_content_id" not in cols


def test_v8_indexes_exist(db: sqlite3.Connection) -> None:
    indexes = {
        r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert "idx_cer_source_content" in indexes
    assert "idx_cer_input_hash" in indexes
    assert "idx_cerc_run" in indexes
    assert "idx_cerc_ai_call" in indexes
    assert "idx_claims_run" in indexes


def test_run_calls_unique_constraint(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(
        "INSERT INTO claim_extraction_runs"
        " (source_content_id, status, input_hash, total_chunk_count,"
        " model, provider, extraction_algo_version, started_at)"
        " VALUES (1, 'running', 'abc', 1, 'm', 'p', 'v', '2024-01-01T00:00:00')"
    )
    db.execute(
        "INSERT INTO claim_extraction_run_calls"
        " (claim_extraction_run_id, chunk_index, chunk_hash,"
        " input_char_start, input_char_end, status, started_at)"
        " VALUES (1, 0, 'h', 0, 100, 'running', '2024-01-01T00:00:00')"
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO claim_extraction_run_calls"
            " (claim_extraction_run_id, chunk_index, chunk_hash,"
            " input_char_start, input_char_end, status, started_at)"
            " VALUES (1, 0, 'h2', 0, 100, 'running', '2024-01-01T00:00:00')"
        )
        db.commit()


def test_migration_v7_to_v8(tmp_path: Path) -> None:
    """A v7 database gains claim tables and v9/v10 tables on next open_db."""
    from app.core.database import SCHEMA_VERSION, _get_version, _set_version

    conn = open_db(tmp_path / "v7.db")
    _set_version(conn, 7)
    for tbl in (
        "narration_review_events",
        "tts_calls",
        "narration_segment_assets",
        "narration_runs",
        "voice_profiles",
        "production_plan_review_events",
        "production_segment_citations",
        "production_segments",
        "production_plans",
        "script_citations",
        "script_generation_runs",
        "claims",
        "claim_extraction_run_calls",
        "claim_extraction_runs",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {tbl}")
    for col in ("body_json", "format", "approved_at", "superseded_at"):
        conn.execute(f"ALTER TABLE scripts DROP COLUMN {col}")
    conn.commit()
    conn.close()

    conn2 = open_db(tmp_path / "v7.db")
    assert _get_version(conn2) == SCHEMA_VERSION
    tables = {
        r[0]
        for r in conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert "claim_extraction_runs" in tables
    assert "claim_extraction_run_calls" in tables
    assert "claims" in tables
    assert "script_generation_runs" in tables
    conn2.close()


def test_source_contents_table_exists(db: sqlite3.Connection) -> None:
    tables = {
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert "source_contents" in tables


def test_source_contents_columns(db: sqlite3.Connection) -> None:
    cols = {row[1] for row in db.execute("PRAGMA table_info(source_contents)").fetchall()}
    required = {
        "id",
        "source_id",
        "fetch_status",
        "extraction_status",
        "http_status",
        "canonical_url",
        "mime_type",
        "fetched_at",
        "raw_text",
        "retrieval_hash",
        "normalized_text_hash",
        "hash_algorithm",
        "word_count",
        "title",
        "author",
        "published_at",
        "domain_type",
        "extraction_method",
        "extraction_error",
        "suspected_truncation",
        "quality_score",
        "quality_factors_json",
        "quality_scorer_version",
        "created_at",
    }
    assert required <= cols


def test_source_contents_indexes_exist(db: sqlite3.Connection) -> None:
    indexes = {
        r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert "sc_source_id" in indexes
    assert "sc_normalized_text_hash" in indexes


def test_migration_v6_to_v7(tmp_path: Path) -> None:
    """A v6 database gains source_contents and v9/v10 tables on next open_db."""
    from app.core.database import SCHEMA_VERSION, _get_version, _set_version

    conn = open_db(tmp_path / "v6.db")
    # Simulate v6 state: reset version and drop v7+ tables/columns
    _set_version(conn, 6)
    for tbl in (
        "narration_review_events",
        "tts_calls",
        "narration_segment_assets",
        "narration_runs",
        "voice_profiles",
        "production_plan_review_events",
        "production_segment_citations",
        "production_segments",
        "production_plans",
        "script_citations",
        "script_generation_runs",
        "claims",
        "claim_extraction_run_calls",
        "claim_extraction_runs",
        "source_contents",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {tbl}")
    for col in ("body_json", "format", "approved_at", "superseded_at"):
        conn.execute(f"ALTER TABLE scripts DROP COLUMN {col}")
    conn.commit()
    conn.close()

    conn2 = open_db(tmp_path / "v6.db")
    assert _get_version(conn2) == SCHEMA_VERSION
    tables = {
        r[0]
        for r in conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert "source_contents" in tables
    conn2.close()


def test_topics_promoted_opportunity_id_column_exists(db) -> None:
    cols = {row[1] for row in db.execute("PRAGMA table_info(topics)").fetchall()}
    assert "promoted_opportunity_id" in cols


def test_topics_promoted_opportunity_unique_index_enforced(db) -> None:
    import sqlite3

    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(
        "INSERT INTO topics (title, angle, status, promoted_opportunity_id, created_at, updated_at)"
        " VALUES ('a', '', 'active', 1, '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO topics"
            " (title, angle, status, promoted_opportunity_id, created_at, updated_at)"
            " VALUES ('b', '', 'active', 1, '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
        )
        db.commit()


def test_migration_v5_to_v6_preserves_existing_topics(tmp_path: Path) -> None:
    """Topics created before v6 survive migration with promoted_opportunity_id = NULL."""
    from app.core.database import SCHEMA_VERSION, _get_version

    conn = open_db(tmp_path / "v5.db")
    # Simulate v5 state: reset version, remove v6+ tables/columns
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (5)")
    for tbl in (
        "production_plan_review_events",
        "production_segment_citations",
        "production_segments",
        "production_plans",
        "script_citations",
        "script_generation_runs",
        "claims",
        "claim_extraction_run_calls",
        "claim_extraction_runs",
        "source_contents",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {tbl}")
    for col in ("body_json", "format", "approved_at", "superseded_at"):
        conn.execute(f"ALTER TABLE scripts DROP COLUMN {col}")
    conn.execute("DROP INDEX IF EXISTS uq_topics_promoted_opportunity")
    conn.execute("ALTER TABLE topics DROP COLUMN promoted_opportunity_id")
    # Insert a manual topic in this simulated v5 state
    conn.execute(
        "INSERT INTO topics (title, angle, status, created_at, updated_at)"
        " VALUES ('Legacy Topic', 'some angle', 'active',"
        " '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    conn2 = open_db(tmp_path / "v5.db")
    assert _get_version(conn2) == SCHEMA_VERSION
    row = conn2.execute("SELECT * FROM topics WHERE title = 'Legacy Topic'").fetchone()
    assert row is not None
    assert row["promoted_opportunity_id"] is None
    conn2.close()


def test_unknown_schema_version_raises(tmp_path: Path) -> None:
    from app.core.database import _set_version

    conn = open_db(tmp_path / "test.db")
    _set_version(conn, 999)
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="Unsupported schema version"):
        open_db(tmp_path / "test.db")


# ---------------------------------------------------------------------------
# Phase 9 (Script generation) schema tests
# ---------------------------------------------------------------------------


def test_migration_v8_to_v9(tmp_path: Path) -> None:
    """A v8 database gains script_generation_runs, script_citations, and v9/v10 tables."""
    from app.core.database import SCHEMA_VERSION, _get_version, _set_version

    conn = open_db(tmp_path / "v8.db")
    _set_version(conn, 8)
    for tbl in (
        "production_plan_review_events",
        "production_segment_citations",
        "production_segments",
        "production_plans",
        "script_citations",
        "script_generation_runs",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {tbl}")
    for col in ("body_json", "format", "approved_at", "superseded_at"):
        conn.execute(f"ALTER TABLE scripts DROP COLUMN {col}")
    conn.commit()
    conn.close()

    conn2 = open_db(tmp_path / "v8.db")
    assert _get_version(conn2) == SCHEMA_VERSION
    tables = {
        r[0]
        for r in conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert "script_generation_runs" in tables
    assert "script_citations" in tables
    cols = {row[1] for row in conn2.execute("PRAGMA table_info(scripts)").fetchall()}
    assert {"body_json", "format", "approved_at", "superseded_at"} <= cols
    conn2.close()


def test_v9_scripts_columns(db: sqlite3.Connection) -> None:
    cols = {row[1] for row in db.execute("PRAGMA table_info(scripts)").fetchall()}
    assert {"body_json", "format", "approved_at", "superseded_at"} <= cols


def test_v9_script_generation_runs_columns(db: sqlite3.Connection) -> None:
    cols = {row[1] for row in db.execute("PRAGMA table_info(script_generation_runs)").fetchall()}
    required = {
        "id",
        "topic_id",
        "script_id",
        "status",
        "input_hash",
        "evidence_hash",
        "prompt_hash",
        "prompt_name",
        "prompt_version",
        "model",
        "temperature",
        "max_tokens",
        "tone",
        "audience",
        "target_duration_s",
        "computed_word_count",
        "computed_duration_s",
        "warnings_json",
        "requires_evidence_review",
        "ai_call_id",
        "error_message",
        "superseded_at",
        "superseded_by_run_id",
        "started_at",
        "completed_at",
        "created_at",
    }
    assert required <= cols


def test_v9_script_citations_columns(db: sqlite3.Connection) -> None:
    cols = {row[1] for row in db.execute("PRAGMA table_info(script_citations)").fetchall()}
    assert {"id", "script_id", "claim_id", "section_index", "citation_order", "created_at"} <= cols


def test_v9_no_partial_unique_approval_index(db: sqlite3.Connection) -> None:
    indexes = {
        r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert not any("approved" in n for n in indexes)


def test_v9_scripts_format_default(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(
        "INSERT INTO scripts (topic_id, version, body, status, created_at, updated_at)"
        " VALUES (1, 1, 'body', 'draft', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    db.commit()
    row = db.execute("SELECT format FROM scripts WHERE version=1").fetchone()
    assert row["format"] == "short"


def test_v9_multiple_approved_scripts_per_topic_allowed(db: sqlite3.Connection) -> None:
    """No DB-level constraint prevents multiple approved scripts per topic (invariant 21)."""
    db.execute("PRAGMA foreign_keys=OFF")
    for v in (1, 2):
        db.execute(
            "INSERT INTO scripts (topic_id, version, body, status, created_at, updated_at)"
            f" VALUES (1, {v}, 'body', 'approved', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
        )
    db.commit()
    count = db.execute(
        "SELECT COUNT(*) FROM scripts WHERE topic_id=1 AND status='approved'"
    ).fetchone()[0]
    assert count == 2


def test_v9_indexes_exist(db: sqlite3.Connection) -> None:
    indexes = {
        r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert "idx_sgr_topic" in indexes
    assert "idx_sgr_input_hash" in indexes
    assert "idx_sgr_script" in indexes
    assert "idx_sc_script" in indexes


# ---------------------------------------------------------------------------
# Phase 10 (Production Plan) schema tests
# ---------------------------------------------------------------------------


def test_v10_production_tables_exist(db: sqlite3.Connection) -> None:
    tables = {
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert "production_plans" in tables
    assert "production_segments" in tables
    assert "production_segment_citations" in tables
    assert "production_plan_review_events" in tables


def test_v10_production_plans_columns(db: sqlite3.Connection) -> None:
    cols = {row[1] for row in db.execute("PRAGMA table_info(production_plans)").fetchall()}
    required = {
        "id",
        "topic_id",
        "script_id",
        "script_version",
        "input_hash",
        "script_body_hash",
        "plan_schema_version",
        "renderer_version",
        "duration_algorithm_version",
        "title",
        "format",
        "total_estimated_duration_s",
        "total_word_count",
        "warnings_json",
        "requires_evidence_review",
        "evidence_hash",
        "generation_run_id",
        "experiment_id",
        "status",
        "approved_at",
        "superseded_at",
        "rejected_at",
        "created_at",
        "updated_at",
    }
    assert required <= cols
    # Speculative columns must NOT exist
    for banned in (
        "visual_intent",
        "shot_type",
        "music_direction",
        "transition",
        "asset_path",
        "narration_path",
        "caption",
    ):
        assert banned not in cols, f"speculative column {banned!r} must not exist"


def test_v10_production_segments_columns(db: sqlite3.Connection) -> None:
    cols = {row[1] for row in db.execute("PRAGMA table_info(production_segments)").fetchall()}
    required = {
        "id",
        "plan_id",
        "segment_index",
        "section_index",
        "section_type",
        "narration_text",
        "estimated_duration_s",
        "estimated_word_count",
        "created_at",
    }
    assert required <= cols
    for banned in (
        "visual_intent",
        "shot_type",
        "music_direction",
        "transition",
        "asset_path",
        "narration_path",
        "caption",
        "citation_ids_json",
    ):
        assert banned not in cols, f"speculative column {banned!r} must not exist"


def test_v10_production_segment_citations_columns(db: sqlite3.Connection) -> None:
    cols = {
        row[1] for row in db.execute("PRAGMA table_info(production_segment_citations)").fetchall()
    }
    assert {"id", "segment_id", "claim_id", "citation_order", "created_at"} <= cols


def test_v10_production_plan_review_events_columns(db: sqlite3.Connection) -> None:
    cols = {
        row[1] for row in db.execute("PRAGMA table_info(production_plan_review_events)").fetchall()
    }
    required = {
        "id",
        "plan_id",
        "topic_id",
        "script_id",
        "evidence_hash",
        "model",
        "prompt_hash",
        "experiment_id",
        "decision",
        "reason_code",
        "notes",
        "actor",
        "created_at",
    }
    assert required <= cols


def test_v10_indexes_exist(db: sqlite3.Connection) -> None:
    indexes = {
        r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert "idx_pp_one_active_normal" in indexes
    assert "idx_pp_one_active_experiment" in indexes
    assert "idx_pp_topic_created" in indexes
    assert "idx_pp_script" in indexes
    assert "idx_ps_plan" in indexes
    assert "idx_psc_segment" in indexes
    assert "idx_psc_claim" in indexes
    assert "idx_ppre_plan" in indexes
    assert "idx_ppre_model_prompt" in indexes
    assert "idx_ppre_experiment" in indexes


def test_v10_unique_script_id_input_hash_enforced(db: sqlite3.Connection) -> None:
    """UNIQUE(script_id, input_hash) prevents duplicate plans with same hash."""
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(
        "INSERT INTO production_plans"
        " (topic_id, script_id, script_version, input_hash, script_body_hash,"
        "  plan_schema_version, renderer_version, duration_algorithm_version,"
        "  created_at, updated_at)"
        " VALUES (1, 1, 1, 'hash-abc', 'body-hash', 'v1', 'rv1', 'dv1',"
        "  '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO production_plans"
            " (topic_id, script_id, script_version, input_hash, script_body_hash,"
            "  plan_schema_version, renderer_version, duration_algorithm_version,"
            "  created_at, updated_at)"
            " VALUES (1, 1, 2, 'hash-abc', 'body-hash2', 'v1', 'rv1', 'dv1',"
            "  '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
        )
        db.commit()


def test_v10_partial_unique_index_blocks_two_active_normal_plans(
    db: sqlite3.Connection,
) -> None:
    """idx_pp_one_active_normal: at most one approved non-experiment plan per topic."""
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(
        "INSERT INTO production_plans"
        " (topic_id, script_id, script_version, input_hash, script_body_hash,"
        "  plan_schema_version, renderer_version, duration_algorithm_version,"
        "  status, approved_at, created_at, updated_at)"
        " VALUES (1, 1, 1, 'hash-1', 'bh1', 'v1', 'rv1', 'dv1',"
        "  'approved', '2024-01-01T00:00:00', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO production_plans"
            " (topic_id, script_id, script_version, input_hash, script_body_hash,"
            "  plan_schema_version, renderer_version, duration_algorithm_version,"
            "  status, approved_at, created_at, updated_at)"
            " VALUES (1, 2, 2, 'hash-2', 'bh2', 'v1', 'rv1', 'dv1',"
            "  'approved', '2024-01-01T00:00:00', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
        )
        db.commit()


def test_v10_partial_unique_index_allows_superseded_plus_new_active(
    db: sqlite3.Connection,
) -> None:
    """A superseded plan plus a new active approved plan for the same topic is allowed."""
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(
        "INSERT INTO production_plans"
        " (topic_id, script_id, script_version, input_hash, script_body_hash,"
        "  plan_schema_version, renderer_version, duration_algorithm_version,"
        "  status, approved_at, superseded_at, created_at, updated_at)"
        " VALUES (1, 1, 1, 'hash-1', 'bh1', 'v1', 'rv1', 'dv1',"
        "  'approved', '2024-01-01T00:00:00', '2024-01-02T00:00:00',"
        "  '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    db.execute(
        "INSERT INTO production_plans"
        " (topic_id, script_id, script_version, input_hash, script_body_hash,"
        "  plan_schema_version, renderer_version, duration_algorithm_version,"
        "  status, approved_at, created_at, updated_at)"
        " VALUES (1, 2, 2, 'hash-2', 'bh2', 'v1', 'rv1', 'dv1',"
        "  'approved', '2024-01-02T00:00:00', '2024-01-02T00:00:00', '2024-01-02T00:00:00')"
    )
    db.commit()
    count = db.execute(
        "SELECT COUNT(*) FROM production_plans WHERE topic_id=1 AND status='approved'"
    ).fetchone()[0]
    assert count == 2


def test_v10_experiment_plans_allowed_alongside_normal_plan(
    db: sqlite3.Connection,
) -> None:
    """A normal plan and an experiment plan may both be active for the same topic."""
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(
        "INSERT INTO production_plans"
        " (topic_id, script_id, script_version, input_hash, script_body_hash,"
        "  plan_schema_version, renderer_version, duration_algorithm_version,"
        "  status, approved_at, created_at, updated_at)"
        " VALUES (1, 1, 1, 'hash-normal', 'bh1', 'v1', 'rv1', 'dv1',"
        "  'approved', '2024-01-01T00:00:00', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    db.execute(
        "INSERT INTO production_plans"
        " (topic_id, script_id, script_version, input_hash, script_body_hash,"
        "  plan_schema_version, renderer_version, duration_algorithm_version,"
        "  experiment_id, status, approved_at, created_at, updated_at)"
        " VALUES (1, 2, 2, 'hash-exp', 'bh2', 'v1', 'rv1', 'dv1',"
        "  'hook-style-v1-arm-a', 'approved', '2024-01-01T00:00:00',"
        "  '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    db.commit()


def test_v10_segment_citations_unique_constraints(db: sqlite3.Connection) -> None:
    """UNIQUE(segment_id, claim_id) and UNIQUE(segment_id, citation_order) enforced."""
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(
        "INSERT INTO production_plans"
        " (topic_id, script_id, script_version, input_hash, script_body_hash,"
        "  plan_schema_version, renderer_version, duration_algorithm_version,"
        "  created_at, updated_at)"
        " VALUES (1, 1, 1, 'h', 'bh', 'v1', 'rv1', 'dv1',"
        "  '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    db.execute(
        "INSERT INTO production_segments"
        " (plan_id, segment_index, section_index, section_type, narration_text,"
        "  created_at)"
        " VALUES (1, 0, 0, 'hook', 'Hello world.', '2024-01-01T00:00:00')"
    )
    db.commit()
    db.execute(
        "INSERT INTO production_segment_citations"
        " (segment_id, claim_id, citation_order, created_at)"
        " VALUES (1, 42, 0, '2024-01-01T00:00:00')"
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO production_segment_citations"
            " (segment_id, claim_id, citation_order, created_at)"
            " VALUES (1, 42, 1, '2024-01-01T00:00:00')"
        )
        db.commit()


def test_v10_review_event_decision_check(db: sqlite3.Connection) -> None:
    """decision CHECK constraint rejects values outside ('approved','rejected')."""
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(
        "INSERT INTO production_plans"
        " (topic_id, script_id, script_version, input_hash, script_body_hash,"
        "  plan_schema_version, renderer_version, duration_algorithm_version,"
        "  created_at, updated_at)"
        " VALUES (1, 1, 1, 'h', 'bh', 'v1', 'rv1', 'dv1',"
        "  '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO production_plan_review_events"
            " (plan_id, topic_id, script_id, evidence_hash, decision, created_at)"
            " VALUES (1, 1, 1, 'eh', 'pending', '2024-01-01T00:00:00')"
        )
        db.commit()


def test_migration_v9_to_v10(tmp_path: Path) -> None:
    """A v9 database gains all production and narration tables on next open_db."""
    from app.core.database import SCHEMA_VERSION, _get_version, _set_version

    conn = open_db(tmp_path / "v9.db")
    _set_version(conn, 9)
    for tbl in (
        "narration_review_events",
        "tts_calls",
        "narration_segment_assets",
        "narration_runs",
        "voice_profiles",
        "production_plan_review_events",
        "production_segment_citations",
        "production_segments",
        "production_plans",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {tbl}")
    conn.commit()
    conn.close()

    conn2 = open_db(tmp_path / "v9.db")
    assert _get_version(conn2) == SCHEMA_VERSION
    assert SCHEMA_VERSION == 20
    tables = {
        r[0]
        for r in conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert "production_plans" in tables
    assert "production_segments" in tables
    assert "production_segment_citations" in tables
    assert "production_plan_review_events" in tables
    assert "narration_runs" in tables
    assert "voice_profiles" in tables
    conn2.close()


def test_v12_caption_tables_exist(db: sqlite3.Connection) -> None:
    tables = {
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert "caption_runs" in tables
    assert "caption_cues" in tables
    assert "caption_review_events" in tables


def test_v12_caption_runs_columns(db: sqlite3.Connection) -> None:
    cols = {row[1] for row in db.execute("PRAGMA table_info(caption_runs)").fetchall()}
    required = {
        "id",
        "narration_run_id",
        "plan_id",
        "script_id",
        "topic_id",
        "experiment_id",
        "input_hash",
        "caption_schema_version",
        "segmentation_version",
        "timing_algorithm_version",
        "style_version",
        "exporter_version",
        "language",
        "status",
        "total_cue_count",
        "total_duration_ms",
        "failure_reason",
        "srt_path",
        "vtt_path",
        "json_path",
        "srt_sha256",
        "vtt_sha256",
        "json_sha256",
        "approved_at",
        "rejected_at",
        "superseded_at",
        "superseded_by_run_id",
        "created_at",
        "updated_at",
    }
    assert required <= cols, f"Missing columns: {required - cols}"


def test_v12_caption_cues_columns(db: sqlite3.Connection) -> None:
    cols = {row[1] for row in db.execute("PRAGMA table_info(caption_cues)").fetchall()}
    required = {
        "id",
        "run_id",
        "segment_id",
        "narration_asset_id",
        "narration_text_hash",
        "audio_sha256",
        "cue_index",
        "segment_cue_index",
        "text",
        "start_ms",
        "end_ms",
        "line_count",
        "char_count",
        "timing_source",
        "warnings_json",
        "superseded_at",
        "created_at",
    }
    assert required <= cols, f"Missing columns: {required - cols}"


def test_v12_caption_review_events_columns(db: sqlite3.Connection) -> None:
    cols = {row[1] for row in db.execute("PRAGMA table_info(caption_review_events)").fetchall()}
    required = {
        "id",
        "run_id",
        "cue_id",
        "segment_id",
        "narration_asset_id",
        "narration_run_id",
        "plan_id",
        "script_id",
        "topic_id",
        "voice_profile_id",
        "provider",
        "model",
        "voice_id",
        "experiment_id",
        "caption_schema_version",
        "segmentation_version",
        "timing_algorithm_version",
        "style_version",
        "exporter_version",
        "event_type",
        "reason_code",
        "severity",
        "expected_correction",
        "notes",
        "actor",
        "created_at",
    }
    assert required <= cols, f"Missing columns: {required - cols}"


def test_v12_caption_runs_unique_input_hash(db: sqlite3.Connection) -> None:
    """UNIQUE(narration_run_id, input_hash) is enforced on caption_runs."""
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(
        "INSERT INTO caption_runs"
        " (narration_run_id, plan_id, script_id, topic_id, input_hash,"
        "  caption_schema_version, segmentation_version, timing_algorithm_version,"
        "  style_version, exporter_version, created_at, updated_at)"
        " VALUES (1, 1, 1, 1, 'hash-abc',"
        "  'Caption-v1', 'seg-v1', 'timing-v1', 'style-v1', 'exp-v1',"
        "  '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    db.commit()
    import pytest as _pytest

    with _pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO caption_runs"
            " (narration_run_id, plan_id, script_id, topic_id, input_hash,"
            "  caption_schema_version, segmentation_version, timing_algorithm_version,"
            "  style_version, exporter_version, created_at, updated_at)"
            " VALUES (1, 1, 1, 1, 'hash-abc',"
            "  'Caption-v1', 'seg-v1', 'timing-v1', 'style-v1', 'exp-v1',"
            "  '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
        )
        db.commit()


def test_v12_caption_cues_unique_cue_index_per_run(db: sqlite3.Connection) -> None:
    """UNIQUE(run_id, cue_index) is enforced on caption_cues."""
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(
        "INSERT INTO caption_runs"
        " (id, narration_run_id, plan_id, script_id, topic_id, input_hash,"
        "  caption_schema_version, segmentation_version, timing_algorithm_version,"
        "  style_version, exporter_version, created_at, updated_at)"
        " VALUES (1, 1, 1, 1, 1, 'h1',"
        "  'Caption-v1', 'sv1', 'tv1', 'stv1', 'ev1',"
        "  '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    db.execute(
        "INSERT INTO caption_cues"
        " (run_id, segment_id, narration_asset_id, narration_text_hash, audio_sha256,"
        "  cue_index, segment_cue_index, text, start_ms, end_ms,"
        "  line_count, char_count, timing_source, created_at)"
        " VALUES (1, 1, 1, 'th1', 'ah1', 0, 0, 'Hello', 0, 2000, 1, 5, 'estimated',"
        "  '2024-01-01T00:00:00')"
    )
    db.commit()
    import pytest as _pytest

    with _pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO caption_cues"
            " (run_id, segment_id, narration_asset_id, narration_text_hash, audio_sha256,"
            "  cue_index, segment_cue_index, text, start_ms, end_ms,"
            "  line_count, char_count, timing_source, created_at)"
            " VALUES (1, 1, 1, 'th1', 'ah1', 0, 1, 'World', 2000, 4000, 1, 5, 'estimated',"
            "  '2024-01-01T00:00:00')"
        )
        db.commit()


def test_v12_caption_cues_event_type_check(db: sqlite3.Connection) -> None:
    """event_type CHECK constraint rejects unknown event types on caption_review_events."""
    db.execute("PRAGMA foreign_keys=OFF")
    import pytest as _pytest

    with _pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO caption_review_events"
            " (run_id, narration_run_id, plan_id, script_id, topic_id, voice_profile_id,"
            "  provider, model, voice_id,"
            "  caption_schema_version, segmentation_version, timing_algorithm_version,"
            "  style_version, exporter_version, event_type, created_at)"
            " VALUES (1, 1, 1, 1, 1, 1,"
            "  'mock', 'm1', 'v1',"
            "  'Caption-v1', 'sv1', 'tv1', 'stv1', 'ev1', 'unknown_event',"
            "  '2024-01-01T00:00:00')"
        )
        db.commit()


def test_migration_v11_to_v12(tmp_path: Path) -> None:
    """A v11 database gains caption tables on next open_db."""
    from app.core.database import SCHEMA_VERSION, _get_version, _set_version

    conn = open_db(tmp_path / "v11.db")
    for tbl in ("caption_review_events", "caption_cues", "caption_runs"):
        conn.execute(f"DROP TABLE IF EXISTS {tbl}")
    _set_version(conn, 11)
    conn.commit()
    conn.close()

    conn2 = open_db(tmp_path / "v11.db")
    assert _get_version(conn2) == SCHEMA_VERSION
    assert SCHEMA_VERSION == 20
    tables = {
        r[0]
        for r in conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert "caption_runs" in tables
    assert "caption_cues" in tables
    assert "caption_review_events" in tables
    conn2.close()


# ── V14 Render manifest tests ─────────────────────────────────────────────────


def test_v14_render_tables_exist(db: sqlite3.Connection) -> None:
    tables = {
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert "render_manifests" in tables
    assert "render_jobs" in tables
    assert "render_review_events" in tables
    assert "render_manifest_scenes" in tables
    assert "resolved_assets" in tables


def test_v14_render_manifests_columns(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute("PRAGMA table_info(render_manifests)").fetchall()}
    expected = {
        "id",
        "scene_manifest_id",
        "narration_run_id",
        "caption_run_id",
        "topic_id",
        "plan_id",
        "script_id",
        "experiment_id",
        "input_hash",
        "render_schema_version",
        "compositor_version",
        "total_scene_count",
        "total_duration_ms",
        "width",
        "height",
        "fps",
        "caption_burn_in",
        "status",
        "approved_at",
        "rejected_at",
        "superseded_at",
        "superseded_by_id",
        "created_at",
        "updated_at",
    }
    assert expected.issubset(cols)


def test_v14_render_jobs_columns(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute("PRAGMA table_info(render_jobs)").fetchall()}
    expected = {
        "id",
        "render_manifest_id",
        "backend",
        "backend_version",
        "output_path",
        "output_sha256",
        "duration_s",
        "file_size_bytes",
        "render_time_s",
        "width",
        "height",
        "fps",
        "video_codec",
        "audio_codec",
        "crf",
        "audio_bitrate",
        "caption_burn_in",
        "ffmpeg_cmd_json",
        "status",
        "error_message",
        "validated",
        "validation_metadata_json",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
    }
    assert expected.issubset(cols)


def test_v14_render_review_events_columns(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute("PRAGMA table_info(render_review_events)").fetchall()}
    expected = {
        "id",
        "render_manifest_id",
        "render_job_id",
        "topic_id",
        "plan_id",
        "script_id",
        "scene_manifest_id",
        "experiment_id",
        "render_schema_version",
        "event_type",
        "reason_code",
        "severity",
        "expected_correction",
        "notes",
        "actor",
        "created_at",
    }
    assert expected.issubset(cols)


def test_v14_render_manifest_scenes_columns(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute("PRAGMA table_info(render_manifest_scenes)").fetchall()}
    expected = {
        "id",
        "render_manifest_id",
        "scene_index",
        "scene_id",
        "segment_id",
        "audio_path",
        "audio_sha256",
        "start_ms",
        "end_ms",
        "duration_ms",
        "shot_type",
        "camera_movement",
        "visual_objective",
        "caption_cue_ids_json",
        "primary_asset_id",
        "has_placeholder",
        "created_at",
    }
    assert expected.issubset(cols)


def test_v14_resolved_assets_columns(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute("PRAGMA table_info(resolved_assets)").fetchall()}
    expected = {
        "id",
        "planned_asset_id",
        "scene_id",
        "segment_id",
        "render_manifest_id",
        "local_path",
        "sha256",
        "license_status",
        "commercial_use_verified",
        "warnings_json",
        "superseded_at",
        "superseded_by_id",
        "created_at",
        "updated_at",
    }
    assert expected.issubset(cols)


def test_v14_render_manifest_unique_input_hash(db: sqlite3.Connection) -> None:
    """render_manifests.input_hash has a UNIQUE constraint."""
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(
        "INSERT INTO render_manifests"
        " (scene_manifest_id, narration_run_id, caption_run_id, topic_id, plan_id, script_id,"
        "  input_hash, render_schema_version, compositor_version, status)"
        " VALUES (1,1,1,1,1,1,'unique_hash','Render-v1','comp-1.0.0','draft')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO render_manifests"
            " (scene_manifest_id, narration_run_id, caption_run_id, topic_id, plan_id, script_id,"
            "  input_hash, render_schema_version, compositor_version, status)"
            " VALUES (1,1,1,1,1,1,'unique_hash','Render-v1','comp-1.0.0','draft')"
        )


def test_v14_render_status_check(db: sqlite3.Connection) -> None:
    """render_manifests.status CHECK prevents invalid values."""
    db.execute("PRAGMA foreign_keys=OFF")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO render_manifests"
            " (scene_manifest_id, narration_run_id, caption_run_id, topic_id, plan_id, script_id,"
            "  input_hash, render_schema_version, compositor_version, status)"
            " VALUES (1,1,1,1,1,1,'h2','Render-v1','comp-1.0.0','invalid_status')"
        )
        db.commit()


def test_v14_render_job_status_check(db: sqlite3.Connection) -> None:
    """render_jobs.status CHECK prevents invalid values."""
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(
        "INSERT INTO render_manifests"
        " (id, scene_manifest_id, narration_run_id, caption_run_id, topic_id, plan_id, script_id,"
        "  input_hash, render_schema_version, compositor_version, status)"
        " VALUES (99,1,1,1,1,1,1,'rh99','Render-v1','comp-1.0.0','draft')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO render_jobs"
            " (render_manifest_id, backend, backend_version, width, height, fps, status)"
            " VALUES (99, 'ffmpeg', 'ffmpeg-1.0.0', 1080, 1920, 30, 'bad_status')"
        )
        db.commit()


def test_v14_render_review_event_type_check(db: sqlite3.Connection) -> None:
    """render_review_events.event_type CHECK prevents unknown types."""
    db.execute("PRAGMA foreign_keys=OFF")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO render_review_events"
            " (render_manifest_id, topic_id, plan_id, script_id, scene_manifest_id,"
            "  render_schema_version, event_type)"
            " VALUES (1,1,1,1,1,'Render-v1','unknown_event')"
        )
        db.commit()


def test_migration_v13_to_v14(tmp_path: Path) -> None:
    """A v13 database gains render tables on next open_db."""
    from app.core.database import SCHEMA_VERSION, _get_version, _set_version

    conn = open_db(tmp_path / "v13.db")
    for tbl in (
        "resolved_assets",
        "render_manifest_scenes",
        "render_review_events",
        "render_jobs",
        "render_manifests",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {tbl}")
    _set_version(conn, 13)
    conn.commit()
    conn.close()

    conn2 = open_db(tmp_path / "v13.db")
    assert _get_version(conn2) == SCHEMA_VERSION
    assert SCHEMA_VERSION == 20
    tables = {
        r[0]
        for r in conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert "render_manifests" in tables
    assert "render_jobs" in tables
    assert "render_review_events" in tables
    assert "render_manifest_scenes" in tables
    assert "resolved_assets" in tables
    conn2.close()


# ---------------------------------------------------------------------------
# SQLite cross-thread safety regression tests (FastAPI thread-pool teardown)
# ---------------------------------------------------------------------------


def test_open_db_allows_cross_thread_use(tmp_path: Path) -> None:
    """Regression: open_db must use check_same_thread=False.

    FastAPI runs sync route handlers in a threadpool via anyio.  Generator
    dependency teardown (conn.close) can execute in a different thread than
    where the connection was opened.  Without check_same_thread=False the
    teardown raises sqlite3.ProgrammingError.
    """
    import threading

    conn = open_db(tmp_path / "threading.db")
    errors: list[Exception] = []

    def use_from_another_thread() -> None:
        try:
            conn.execute("SELECT 1").fetchone()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=use_from_another_thread)
    t.start()
    t.join()
    conn.close()

    assert not errors, f"Cross-thread DB use raised: {errors[0]}"


def test_open_db_close_from_another_thread(tmp_path: Path) -> None:
    """Regression: conn.close() from a different thread must not raise.

    This mirrors the FastAPI generator dependency teardown pattern where the
    finally-block executes in the async event loop thread, not the thread
    that originally called get_db().
    """
    import threading

    conn = open_db(tmp_path / "threading_close.db")
    errors: list[Exception] = []

    def close_from_another_thread() -> None:
        try:
            conn.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=close_from_another_thread)
    t.start()
    t.join()

    assert not errors, f"Cross-thread conn.close() raised: {errors[0]}"
