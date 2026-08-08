"""Tests for database migration from Phase 1 schema to Phase 2."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core.database import _DDL_V1, SCHEMA_VERSION, _get_version, _set_version, open_db


def _open_v1(path: Path) -> sqlite3.Connection:
    """Create a Phase 1 database without Phase 2 tables."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_DDL_V1)
    _set_version(conn, 1)
    conn.commit()
    return conn


# ── Fresh initialisation ──────────────────────────────────────────────────────


def test_fresh_db_gets_current_version(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "test.db")
    assert _get_version(conn) == SCHEMA_VERSION
    conn.close()


def test_fresh_db_has_ai_calls_table(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "test.db")
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "ai_calls" in tables
    conn.close()


# ── Upgrade from Phase 1 ──────────────────────────────────────────────────────


def test_upgrade_from_v1_adds_ai_calls(tmp_path: Path) -> None:
    path = tmp_path / "v1.db"
    v1_conn = _open_v1(path)
    # Insert a Phase 1 row to verify data preservation.
    v1_conn.execute(
        "INSERT INTO topics (title, angle, status, created_at, updated_at) VALUES (?,?,?,?,?)",
        ("Preserved topic", "", "active", "2025-01-01T00:00:00", "2025-01-01T00:00:00"),
    )
    v1_conn.commit()
    v1_conn.close()

    # Re-open triggers migration.
    conn = open_db(path)
    assert _get_version(conn) == SCHEMA_VERSION
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "ai_calls" in tables

    # Existing data is preserved.
    topic = conn.execute("SELECT title FROM topics WHERE id=1").fetchone()
    assert topic["title"] == "Preserved topic"
    conn.close()


# ── Idempotency ───────────────────────────────────────────────────────────────


def test_open_twice_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "test.db"
    open_db(path).close()
    conn = open_db(path)
    assert _get_version(conn) == SCHEMA_VERSION
    conn.close()


# ── Unsupported future version ────────────────────────────────────────────────


def test_future_version_raises(tmp_path: Path) -> None:
    path = tmp_path / "future.db"
    conn = open_db(path)
    _set_version(conn, 999)
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="Unsupported schema version"):
        open_db(path)


# ── ai_calls foreign key ──────────────────────────────────────────────────────


def test_ai_calls_run_fk_enforced(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "test.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO ai_calls (provider,model,status,run_id,created_at) VALUES (?,?,?,?,?)",
            ("fake", "fake", "success", 9999, "2025-01-01T00:00:00"),
        )
        conn.commit()
    conn.close()
