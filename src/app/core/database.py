"""SQLite connection management and schema initialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

# Increment when the schema changes; add a migration branch in _migrate().
SCHEMA_VERSION = 2

# Phase 1 DDL — topics, sources, scripts, runs.
_DDL_V1 = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT    NOT NULL,
    angle      TEXT    NOT NULL DEFAULT '',
    status     TEXT    NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active', 'archived')),
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE TABLE IF NOT EXISTS sources (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id   INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL CHECK (kind IN ('url', 'file', 'note')),
    reference  TEXT    NOT NULL,
    notes      TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE TABLE IF NOT EXISTS scripts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id   INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    version    INTEGER NOT NULL DEFAULT 1,
    body       TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'draft'
                       CHECK (status IN ('draft', 'approved', 'rejected')),
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    UNIQUE (topic_id, version)
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id    INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    script_id   INTEGER REFERENCES scripts(id) ON DELETE SET NULL,
    status      TEXT    NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    started_at  TEXT,
    finished_at TEXT,
    error       TEXT,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);
"""

# Phase 2 DDL — ai_calls.
_DDL_V2 = """
CREATE TABLE IF NOT EXISTS ai_calls (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    provider            TEXT    NOT NULL,
    model               TEXT    NOT NULL,
    prompt_name         TEXT    NOT NULL DEFAULT '',
    prompt_version      TEXT    NOT NULL DEFAULT '',
    input_tokens        INTEGER,
    output_tokens       INTEGER,
    estimated_cost_usd  REAL,
    duration_ms         INTEGER,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    status              TEXT    NOT NULL CHECK (status IN ('success', 'failed')),
    error_category      TEXT,
    error_message       TEXT,
    run_id              INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    completed_at        TEXT
);
"""


def _get_version(conn: sqlite3.Connection) -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if not exists:
        return 0
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return row[0] if row else 0


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


def _migrate(conn: sqlite3.Connection) -> None:
    current = _get_version(conn)
    if current == SCHEMA_VERSION:
        return

    if current == 0:
        logger.info("Initialising schema at version %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V1)
        conn.executescript(_DDL_V2)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Schema ready at version %d", SCHEMA_VERSION)

    elif current == 1:
        logger.info("Migrating schema from version 1 to %d", SCHEMA_VERSION)
        conn.executescript(_DDL_V2)
        _set_version(conn, SCHEMA_VERSION)
        logger.info("Migration complete")

    else:
        raise RuntimeError(
            f"Unsupported schema version {current}; expected <= {SCHEMA_VERSION}. "
            "Manual migration required."
        )


def open_db(path: Path) -> sqlite3.Connection:
    """Open (or create) the SQLite database, enforce FK constraints, and run migrations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate(conn)
    conn.commit()
    logger.debug("Database open: %s", path)
    return conn
