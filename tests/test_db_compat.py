"""Tests for the PostgreSQL compatibility layer (db_compat.py) and schema v20 SQLite path.

These tests verify:
- db_compat CompatConnection/CompatCursor adapter contract
- ? → %s placeholder conversion
- row dict-access compatibility
- v20 auth/storage tables exist after open_db
- INSERT OR IGNORE → ON CONFLICT DO NOTHING fix
- get_db_connection() SQLite dispatch
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.core.database import SCHEMA_VERSION, open_db
from app.core.db_compat import CompatCursor, _adapt

# ── _adapt helper ──────────────────────────────────────────────────────────


def test_adapt_converts_single_placeholder():
    assert _adapt("SELECT * FROM t WHERE id = ?") == "SELECT * FROM t WHERE id = %s"


def test_adapt_converts_multiple_placeholders():
    sql = "INSERT INTO t (a, b, c) VALUES (?, ?, ?)"
    assert _adapt(sql) == "INSERT INTO t (a, b, c) VALUES (%s, %s, %s)"


def test_adapt_no_placeholders_unchanged():
    sql = "SELECT 1"
    assert _adapt(sql) == sql


def test_adapt_conflict_nothing():
    sql = "INSERT INTO t (id) VALUES (?) ON CONFLICT DO NOTHING"
    adapted = _adapt(sql)
    assert "%s" in adapted
    assert "?" not in adapted


# ── CompatCursor over mock psycopg cursor ──────────────────────────────────


def _make_mock_cursor(fetchone_val=None, fetchall_val=None):
    cur = MagicMock()
    cur.fetchone.return_value = fetchone_val
    cur.fetchall.return_value = fetchall_val or []
    cur.rowcount = 1
    cur.description = [("id",)]
    return cur


def test_compat_cursor_execute_converts_placeholder():
    mock_cur = _make_mock_cursor()
    cc = CompatCursor(mock_cur)
    cc.execute("SELECT ? + 1", (1,))
    mock_cur.execute.assert_called_once_with("SELECT %s + 1", (1,))


def test_compat_cursor_execute_no_params():
    mock_cur = _make_mock_cursor()
    cc = CompatCursor(mock_cur)
    cc.execute("SELECT 1")
    mock_cur.execute.assert_called_once_with("SELECT 1")


def test_compat_cursor_executemany_converts_placeholders():
    mock_cur = _make_mock_cursor()
    cc = CompatCursor(mock_cur)
    cc.executemany("INSERT INTO t (x) VALUES (?)", [(1,), (2,)])
    mock_cur.executemany.assert_called_once_with("INSERT INTO t (x) VALUES (%s)", [(1,), (2,)])


def test_compat_cursor_fetchone():
    mock_cur = _make_mock_cursor(fetchone_val={"id": 1})
    cc = CompatCursor(mock_cur)
    assert cc.fetchone() == {"id": 1}


def test_compat_cursor_fetchall():
    mock_cur = _make_mock_cursor(fetchall_val=[{"id": 1}, {"id": 2}])
    cc = CompatCursor(mock_cur)
    assert cc.fetchall() == [{"id": 1}, {"id": 2}]


def test_compat_cursor_rowcount():
    mock_cur = _make_mock_cursor()
    mock_cur.rowcount = 42
    cc = CompatCursor(mock_cur)
    assert cc.rowcount == 42


def test_compat_cursor_context_manager_calls_close():
    mock_cur = _make_mock_cursor()
    cc = CompatCursor(mock_cur)
    with cc:
        pass
    mock_cur.close.assert_called_once()


def test_compat_cursor_returns_self_from_execute():
    mock_cur = _make_mock_cursor()
    cc = CompatCursor(mock_cur)
    result = cc.execute("SELECT 1")
    assert result is cc


# ── SCHEMA_VERSION and v20 tables ─────────────────────────────────────────


def test_schema_version_is_20():
    assert SCHEMA_VERSION == 21


def test_v20_auth_tables_exist(tmp_path: Path):
    conn = open_db(tmp_path / "v20.db")
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "auth_users" in tables
    assert "auth_refresh_tokens" in tables
    assert "auth_workspace_roles" in tables
    assert "obj_storage_objects" in tables
    conn.close()


def test_auth_users_columns(tmp_path: Path):
    conn = open_db(tmp_path / "v20.db")
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(auth_users)").fetchall()}
    assert {
        "id",
        "email",
        "display_name",
        "password_hash",
        "is_active",
        "created_at",
        "updated_at",
        "last_login_at",
    } <= cols
    conn.close()


def test_auth_refresh_tokens_columns(tmp_path: Path):
    conn = open_db(tmp_path / "v20.db")
    cols = {
        row["name"] for row in conn.execute("PRAGMA table_info(auth_refresh_tokens)").fetchall()
    }
    assert {
        "id",
        "user_id",
        "token_hash",
        "issued_at",
        "expires_at",
        "revoked_at",
        "last_used_at",
        "device_hint",
        "ip_hint",
    } <= cols
    conn.close()


def test_auth_workspace_roles_columns(tmp_path: Path):
    conn = open_db(tmp_path / "v20.db")
    cols = {
        row["name"] for row in conn.execute("PRAGMA table_info(auth_workspace_roles)").fetchall()
    }
    assert {"id", "user_id", "workspace_id", "role", "granted_at", "granted_by"} <= cols
    conn.close()


def test_obj_storage_objects_columns(tmp_path: Path):
    conn = open_db(tmp_path / "v20.db")
    cols = {
        row["name"] for row in conn.execute("PRAGMA table_info(obj_storage_objects)").fetchall()
    }
    assert {
        "id",
        "workspace_id",
        "channel_id",
        "storage_backend",
        "bucket",
        "object_key",
        "sha256",
        "byte_size",
        "content_type",
        "source_entity_type",
        "source_entity_id",
        "created_at",
        "deleted_at",
    } <= cols
    conn.close()


def test_auth_workspace_roles_role_constraint(tmp_path: Path):
    conn = open_db(tmp_path / "v20.db")
    conn.execute(
        "INSERT INTO auth_users (email, password_hash) VALUES (?, ?)",
        ("user@example.com", "hash"),
    )
    conn.commit()
    user_id = conn.execute("SELECT id FROM auth_users").fetchone()["id"]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO auth_workspace_roles (user_id, workspace_id, role) VALUES (?, ?, ?)",
            (user_id, "ws-1", "INVALID_ROLE"),
        )
        conn.commit()
    conn.close()


def test_auth_users_email_unique(tmp_path: Path):
    conn = open_db(tmp_path / "v20.db")
    conn.execute(
        "INSERT INTO auth_users (email, password_hash) VALUES (?, ?)",
        ("dup@example.com", "hash1"),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO auth_users (email, password_hash) VALUES (?, ?)",
            ("dup@example.com", "hash2"),
        )
        conn.commit()
    conn.close()


def test_obj_storage_unique_constraint(tmp_path: Path):
    conn = open_db(tmp_path / "v20.db")
    conn.execute(
        "INSERT INTO obj_storage_objects "
        "(workspace_id, storage_backend, bucket, object_key, sha256, byte_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("ws-1", "local", "artifacts", "narration/1.wav", "abc123", 1024),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO obj_storage_objects "
            "(workspace_id, storage_backend, bucket, object_key, sha256, byte_size) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("ws-2", "local", "artifacts", "narration/1.wav", "def456", 512),
        )
        conn.commit()
    conn.close()


# ── on conflict do nothing ─────────────────────────────────────────────────


def test_on_conflict_do_nothing_sqlite(tmp_path: Path):
    """Verify ON CONFLICT DO NOTHING works in SQLite 3.24+."""
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT UNIQUE)")
    conn.execute("INSERT INTO t (id, val) VALUES (1, 'a')")
    conn.commit()
    # Should not raise — ON CONFLICT DO NOTHING is valid in SQLite 3.24+
    conn.execute("INSERT INTO t (id, val) VALUES (1, 'b') ON CONFLICT DO NOTHING")
    conn.commit()
    row = conn.execute("SELECT val FROM t WHERE id = 1").fetchone()
    assert row[0] == "a"  # original value preserved
    conn.close()


# ── get_db_connection SQLite dispatch ──────────────────────────────────────


def test_get_db_connection_returns_sqlite_without_url(tmp_path: Path):
    from app.core.database import get_db_connection

    conn = get_db_connection(db_path=tmp_path / "dispatch.db")
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


def test_get_db_connection_skips_postgres_when_no_url(tmp_path: Path, monkeypatch):
    from app.core.database import get_db_connection

    monkeypatch.delenv("ACE_DATABASE_URL", raising=False)
    conn = get_db_connection(db_path=tmp_path / "nourl.db")
    assert isinstance(conn, sqlite3.Connection)
    conn.close()
