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


@pytest.fixture(autouse=True)
def _forbid_default_visual_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail closed if a test reaches VisualEngine's default (live) provider path.

    ``VisualEngine.__init__`` does
    ``providers if providers is not None else build_default_providers()``
    (app.visuals.engine) — a caller that omits ``providers=`` gets the real
    provider list, which includes WikimediaProvider. WikimediaProvider
    requires no API key and its ``available()`` unconditionally returns
    True, so it is the one provider guaranteed to fire for real regardless
    of test environment. That is exactly how a full production-cycle test
    (test_autonomy_production_cycle_18b.py, pre-existing, unrelated to
    Phase 18G) made a genuine HTTPS request to commons.wikimedia.org during
    an ordinary pytest run: the runtime-isolation fixture above guards the
    SQLite database, but nothing guarded outbound network calls.

    The patch target matters: engine.py imports the symbol with
    ``from app.visuals.providers import build_default_providers``, which
    binds it into app.visuals.engine's OWN module namespace at import time.
    Patching app.visuals.providers.registry.build_default_providers (or
    even app.visuals.providers.build_default_providers) would not reach
    that already-bound local name — VisualEngine looks the name up in its
    own module's globals at call time, so app.visuals.engine is the actual
    site that must be patched.

    A test that genuinely needs VisualEngine to resolve beats must pass an
    explicit ``providers=[...]`` list (real stub objects, or ``[]`` to
    exercise the guaranteed local-graphic fallback) — never rely on this
    default. This fixture does not silently substitute an empty list itself:
    a test that hits this path without opting in gets a loud, immediate
    failure explaining what to do, not quietly-wrong behavior.
    """
    import app.visuals.engine as _visual_engine_mod

    def _forbidden() -> None:
        raise RuntimeError(
            "VisualEngine attempted to construct its default (live) provider "
            "list via build_default_providers() during a test. Live/default "
            "visual providers are forbidden under pytest — pass an explicit "
            "providers=[...] list to VisualEngine (a stub provider, or [] to "
            "exercise the local-graphic fallback) instead of relying on the "
            "default."
        )

    monkeypatch.setattr(_visual_engine_mod, "build_default_providers", _forbidden)
