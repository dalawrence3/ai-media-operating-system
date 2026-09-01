"""Shared pytest fixtures.

Phase 18E adds session-level runtime isolation. The E2E suite was not the only
way a test run could reach the live Media OS database: `ACE_DB_PATH=` (set but
empty) falls through `Config`'s `raw if raw else _default_db_path()` to the
OPERATIONAL database, and the CLI tests read `get_config().db_path` directly.
A backend test run under that environment opens — and migrates — live state.

The guard below makes that impossible rather than merely unlikely, in the same
way and for the same reason as the E2E guard: by refusing to run at all.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

# ── Runtime isolation, established at IMPORT time ───────────────────────────
# Before any test module is imported and before any fixture runs, because a
# module-level open_db() in a test file would otherwise execute first and slip
# past a fixture-based guard.
#
# ACE_DB_PATH set-but-EMPTY is the specific trap: Config does
# `Path(raw) if raw else _default_db_path()`, so an empty value looks
# deliberate and silently resolves to the OPERATIONAL database. That is how a
# pytest run migrated the live Media OS database during Phase 18E.
os.environ["ACE_TEST_MODE"] = "unit"
if not os.environ.get("ACE_DB_PATH", "").strip():
    os.environ["ACE_DB_PATH"] = str(
        Path(tempfile.mkdtemp(prefix="ace_pytest_")) / "session-test.db"
    )

import pytest  # noqa: E402

from app.core.database import open_db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _runtime_isolation() -> None:
    """Assert the import-time isolation actually holds for the whole session.

    The environment was set at import time above; this verifies the resulting
    config agrees, so a test that manipulates ACE_DB_PATH cannot leave the
    session pointed at the live database.

    `ACE_TEST_MODE=unit` also makes `open_db` itself refuse the operational
    database — see app.core.database.open_db and app.core.runtime_mode.
    """
    from app.core.config import get_config, reset_config
    from app.core.runtime_mode import assert_runtime_isolation, is_operational_db

    reset_config()
    db_path = get_config().db_path

    assert_runtime_isolation(db_path)
    assert not is_operational_db(db_path), (
        f"Refusing to run the test suite against the operational database ({db_path})."
    )


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    """Return an in-memory-backed connection with the schema applied."""
    return open_db(tmp_path / "test.db")
