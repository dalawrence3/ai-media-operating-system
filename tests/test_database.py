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
    # Drop Phase 3/4/5/6 tables and columns to simulate a real v2 state
    for tbl in (
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
    # Remove v6 additions from topics (SQLite 3.35+ required for DROP COLUMN)
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


def test_schema_version_is_8(tmp_path: Path) -> None:
    from app.core.database import SCHEMA_VERSION, _get_version

    conn = open_db(tmp_path / "test.db")
    assert SCHEMA_VERSION == 8
    assert _get_version(conn) == 8
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
        "id", "source_content_id", "status", "input_hash",
        "total_chunk_count", "completed_chunk_count", "failed_chunk_count",
        "accepted_claim_count", "was_truncated",
        "prompt_name", "prompt_version", "model", "provider",
        "extraction_algo_version", "error_message",
        "superseded_at", "superseded_by_run_id",
        "started_at", "completed_at", "created_at",
    }
    assert required <= cols


def test_claim_extraction_run_calls_columns(db: sqlite3.Connection) -> None:
    cols = {
        row[1] for row in db.execute("PRAGMA table_info(claim_extraction_run_calls)").fetchall()
    }
    required = {
        "id", "claim_extraction_run_id", "ai_call_id", "chunk_index", "chunk_hash",
        "input_char_start", "input_char_end", "status", "retry_count",
        "accepted_claim_count", "error_message", "started_at", "completed_at", "created_at",
    }
    assert required <= cols


def test_claims_columns(db: sqlite3.Connection) -> None:
    cols = {row[1] for row in db.execute("PRAGMA table_info(claims)").fetchall()}
    required = {
        "id", "extraction_run_id", "chunk_index", "claim_text", "claim_type",
        "supporting_quote", "quote_support_status", "quote_start", "quote_end",
        "page_number", "requires_date_review", "created_at",
    }
    assert required <= cols
    assert "source_content_id" not in cols


def test_claims_no_source_content_id_column(db: sqlite3.Connection) -> None:
    cols = {row[1] for row in db.execute("PRAGMA table_info(claims)").fetchall()}
    assert "source_content_id" not in cols


def test_v8_indexes_exist(db: sqlite3.Connection) -> None:
    indexes = {
        r[0]
        for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
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
    """A v7 database gains claim tables on next open_db."""
    from app.core.database import SCHEMA_VERSION, _get_version, _set_version

    conn = open_db(tmp_path / "v7.db")
    _set_version(conn, 7)
    for tbl in ("claims", "claim_extraction_run_calls", "claim_extraction_runs"):
        conn.execute(f"DROP TABLE IF EXISTS {tbl}")
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
        "id", "source_id", "fetch_status", "extraction_status",
        "http_status", "canonical_url", "mime_type", "fetched_at",
        "raw_text", "retrieval_hash", "normalized_text_hash", "hash_algorithm",
        "word_count", "title", "author", "published_at",
        "domain_type", "extraction_method", "extraction_error", "suspected_truncation",
        "quality_score", "quality_factors_json", "quality_scorer_version", "created_at",
    }
    assert required <= cols


def test_source_contents_indexes_exist(db: sqlite3.Connection) -> None:
    indexes = {
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    assert "sc_source_id" in indexes
    assert "sc_normalized_text_hash" in indexes


def test_migration_v6_to_v7(tmp_path: Path) -> None:
    """A v6 database gains source_contents on next open_db."""
    from app.core.database import SCHEMA_VERSION, _get_version, _set_version

    conn = open_db(tmp_path / "v6.db")
    # Simulate v6 state: reset version and drop the v7 table
    _set_version(conn, 6)
    conn.execute("DROP TABLE IF EXISTS source_contents")
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
    cols = {
        row[1]
        for row in db.execute("PRAGMA table_info(topics)").fetchall()
    }
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
    # Simulate v5 state: reset version, remove v6 column
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (5)")
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
