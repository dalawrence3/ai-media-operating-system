"""Structural separation of test runtimes from the live Media OS (Phase 18E).

Why this module exists
----------------------
During Phase 18C activation a Playwright test revoked the LIVE channel's
publishing authorization.  It could do that because the E2E suite ran against
the operational backend and the operational database: `playwright.config.ts`
set safety environment variables, but `scripts/start-backend.sh` sourced
`.env.local` *over* them, `reuseExistingServer` meant a live server already on
:8000 was simply adopted, and no configuration anywhere named a separate
database.  Every one of those is a way for a test to reach production state.

That was fixed at the time with a guard inside the one test that tripped.  A
guard per test is not a safety property — it is a list that has to stay
complete forever, maintained by whoever writes the next test.

So the invariant here is not "tests avoid dangerous endpoints".  It is:

    A process running in test mode and a process serving the live system
    cannot be pointed at the same database, and a process that is pointed at
    the wrong one refuses to start.

Both directions are enforced, because both directions are real failure modes:
a test run reaching the operational database is the incident that happened, and
the live daemons silently coming up against the throwaway E2E database is how
you would lose the operational state entirely.
"""

from __future__ import annotations

import os
from pathlib import Path

# Values of ACE_TEST_MODE that mean "this process exists to run tests".
TEST_MODES: frozenset[str] = frozenset({"e2e", "integration", "unit"})

# The database filename a test runtime is expected to use. Deliberately a
# distinct name, not merely a distinct directory: a path comparison can be
# defeated by a symlink or a relative path, a filename mismatch is obvious in
# any log line, and an operator glancing at `lsof` can tell instantly which
# database a process has open.
TEST_DB_FILENAME: str = "e2e-test.db"


class RuntimeIsolationError(RuntimeError):
    """Raised when a process is pointed at a database it must not touch."""


def test_mode() -> str | None:
    """The active test mode, or None for an ordinary runtime."""
    raw = os.environ.get("ACE_TEST_MODE", "").strip().lower()
    return raw if raw in TEST_MODES else None


def in_test_mode() -> bool:
    return test_mode() is not None


def operational_db_path() -> Path:
    """The live system's database path.

    Resolved the same way `Config` resolves its default, so the two cannot
    drift: if the default location changes, this follows it.
    """
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "ai-content-engine" / "content.db"


def _same_file(a: Path, b: Path) -> bool:
    """Whether two paths denote the same database file.

    Compares resolved paths so a symlink, a relative path or a `..` segment
    cannot smuggle the operational database past the check.  Falls back to a
    plain string comparison of the normalised paths when resolution fails
    (a path that does not exist yet still normalises fine).
    """
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return os.path.normpath(str(a)) == os.path.normpath(str(b))


def is_operational_db(db_path: Path) -> bool:
    return _same_file(Path(db_path), operational_db_path())


def is_test_db(db_path: Path) -> bool:
    return Path(db_path).name == TEST_DB_FILENAME


def assert_runtime_isolation(db_path: Path) -> None:
    """Refuse to run when the runtime and the database disagree about mode.

    Called from application startup and from the E2E backend launcher.  Raises
    rather than warns: a warning in a log nobody is reading is exactly how the
    original incident stayed invisible until it had already happened.
    """
    mode = test_mode()
    path = Path(db_path)

    if mode and is_operational_db(path):
        raise RuntimeIsolationError(
            f"REFUSING TO START: ACE_TEST_MODE={mode!r} but ACE_DB_PATH resolves to the "
            f"OPERATIONAL database ({path}).\n"
            f"A test runtime must never open the live Media OS database. Set "
            f"ACE_DB_PATH to a dedicated test database named {TEST_DB_FILENAME!r} "
            f"(the Playwright config and scripts/start-e2e-backend.sh already do this)."
        )

    if not mode and is_test_db(path):
        raise RuntimeIsolationError(
            f"REFUSING TO START: ACE_DB_PATH points at the E2E test database ({path}) "
            f"but ACE_TEST_MODE is not set.\n"
            f"This is the reverse of the usual mistake and is more dangerous: the live "
            f"daemons would come up against a throwaway database and appear to have lost "
            f"all operational state. Unset ACE_DB_PATH to use the operational database, "
            f"or set ACE_TEST_MODE if this really is a test runtime."
        )


# ── Live-effect operations ───────────────────────────────────────────────────
# Database isolation is the primary protection and does almost all the work: a
# test that mutates its own database cannot affect anything. These names cover
# the residue — operations whose effect escapes the database entirely (a real
# YouTube upload, a real visibility change) and so would be equally destructive
# against a test database. They are refused in test mode regardless of which
# database is open.

LIVE_EFFECT_OPERATIONS: frozenset[str] = frozenset(
    {
        "provider_upload",
        "provider_release_public",
        "provider_delete",
        "publishing_authorization_grant",
        "publishing_authorization_revoke",
    }
)

# Only the E2E mode is refused, and the distinction is not a convenience.
#
# E2E drives a REAL running server through REAL request flows; if a credential
# were present, an upload could genuinely happen, and no database rollback
# would undo it. That is the risk this list covers.
#
# Unit and integration tests inject fakes and call these functions directly to
# assert their own behaviour — `test_enabled_passes` exists precisely to check
# that check_live_publishing_gate() does NOT raise when the gate is open.
# Refusing there does not prevent an upload (there is no provider to upload
# to); it only makes the gate untestable, and an untestable gate is a worse
# safety outcome than a testable one. Those runs remain fully protected by
# database isolation, which is the primary mechanism.
ENFORCED_LIVE_EFFECT_MODES: frozenset[str] = frozenset({"e2e"})


def assert_live_effect_allowed(operation: str) -> None:
    """Refuse an operation with effects outside the database during an E2E run.

    Belt-and-braces on top of database isolation, not a substitute for it:
    this list can never be complete, which is exactly why it is not the
    mechanism the safety property rests on.
    """
    mode = test_mode()
    if mode in ENFORCED_LIVE_EFFECT_MODES and operation in LIVE_EFFECT_OPERATIONS:
        raise RuntimeIsolationError(
            f"Operation {operation!r} has effects outside the database and is refused "
            f"in test mode (ACE_TEST_MODE={mode!r})."
        )
