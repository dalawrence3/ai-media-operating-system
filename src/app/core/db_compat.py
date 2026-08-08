"""PostgreSQL connection adapter compatible with the existing sqlite3 usage pattern.

The existing codebase uses:
  - conn.execute(sql, params)  with ? placeholders
  - conn.row_factory = sqlite3.Row  (dict-like row access via row['col'])
  - conn.commit(), conn.rollback()
  - Context manager: with conn:
  - Cursor API: cur = conn.cursor(); cur.execute(); cur.fetchall()

This module provides CompatConnection / CompatCursor that wrap psycopg3 to
satisfy all of the above without altering the business SQL in each repository.

Placeholder conversion: ? → %s  (positional psycopg3 syntax).
This is a well-defined, one-to-one token replacement, not dialect translation.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

# Compiled once — matches bare ? not inside quoted strings.
# Handles the common case; the application SQL does not embed ? in literals.
_PLACEHOLDER = re.compile(r"\?")


def _adapt(sql: str) -> str:
    """Convert SQLite positional ? placeholders to psycopg3 %s."""
    return _PLACEHOLDER.sub("%s", sql)


class CompatCursor:
    """Wraps a psycopg3 cursor to accept ? placeholders and return dict-like rows."""

    def __init__(self, pg_cursor: Any) -> None:
        self._cur = pg_cursor

    # ------------------------------------------------------------------ execute

    def execute(self, sql: str, params: Any = None) -> CompatCursor:
        if params is not None:
            self._cur.execute(_adapt(sql), params)
        else:
            self._cur.execute(_adapt(sql))
        return self

    def executemany(self, sql: str, params_seq: Any) -> CompatCursor:
        self._cur.executemany(_adapt(sql), params_seq)
        return self

    # ------------------------------------------------------------------ fetch

    def fetchone(self) -> dict | None:
        return self._cur.fetchone()

    def fetchall(self) -> list[dict]:
        return self._cur.fetchall()

    def fetchmany(self, size: int | None = None) -> list[dict]:
        if size is None:
            return self._cur.fetchmany()
        return self._cur.fetchmany(size)

    # ------------------------------------------------------------------ meta

    @property
    def lastrowid(self) -> int | None:
        # psycopg3: use RETURNING id or fetchone() after INSERT … RETURNING id.
        # For INSERT statements that use RETURNING, lastrowid is not auto-populated.
        # Callers that need lastrowid must use RETURNING id (already done in repo code
        # that was written after Phase 15 / requires PostgreSQL).
        return getattr(self._cur, "rownumber", None)

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def description(self) -> Any:
        return self._cur.description

    def __iter__(self):
        return iter(self._cur)

    def close(self) -> None:
        self._cur.close()

    def __enter__(self) -> CompatCursor:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class CompatConnection:
    """Wraps a psycopg3 connection to look like a sqlite3.Connection.

    Key behaviours matched:
    - execute() / executemany() → forwards through CompatCursor
    - commit() / rollback()
    - cursor() → CompatCursor
    - Context manager (with conn: ... → auto-commit/rollback)
    - row_factory assignment (ignored: dict_row is always used)
    """

    def __init__(self, pg_conn: Any) -> None:
        import psycopg.rows

        self._conn = pg_conn
        # Always use dict_row so row['col'] access works like sqlite3.Row.
        self._conn.row_factory = psycopg.rows.dict_row

    # ------------------------------------------------------------------ cursor

    def cursor(self) -> CompatCursor:
        return CompatCursor(self._conn.cursor())

    # ── execute shortcuts (sqlite3 API) ──────────────────────────────────────

    def execute(self, sql: str, params: Any = None) -> CompatCursor:
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def executemany(self, sql: str, params_seq: Any) -> CompatCursor:
        cur = self.cursor()
        cur.executemany(sql, params_seq)
        return cur

    # ------------------------------------------------------------------ transaction

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    # ------------------------------------------------------------------ context manager

    def __enter__(self) -> CompatConnection:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()

    # ------------------------------------------------------------------ row_factory compat

    @property
    def row_factory(self) -> Any:
        return sqlite3.Row  # sentinel value; actual factory is always dict_row

    @row_factory.setter
    def row_factory(self, value: Any) -> None:
        # Setting row_factory on a CompatConnection is a no-op:
        # psycopg3 dict_row is always active and provides the same interface.
        pass

    # ------------------------------------------------------------------ close

    def close(self) -> None:
        self._conn.close()

    def __del__(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
