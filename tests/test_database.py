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
    # Drop Phase 3/4 tables to simulate a real v2 state
    for tbl in (
        "opportunity_state_events",
        "opportunity_source_evidence",
        "opportunity_observations",
        "opportunities",
        "discovery_runs",
        "channel_operating_mode_events",
        "channel_capacity_policies",
        "channel_profile_versions",
        "channel_monetization_strategies",
        "channels",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {tbl}")
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


def test_unknown_schema_version_raises(tmp_path: Path) -> None:
    from app.core.database import _set_version

    conn = open_db(tmp_path / "test.db")
    _set_version(conn, 999)
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="Unsupported schema version"):
        open_db(tmp_path / "test.db")
