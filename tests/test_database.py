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


def test_unknown_schema_version_raises(tmp_path: Path) -> None:
    from app.core.database import _set_version

    conn = open_db(tmp_path / "test.db")
    _set_version(conn, 999)
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="Unsupported schema version"):
        open_db(tmp_path / "test.db")
