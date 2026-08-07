"""Application contract versioning.

Versions follow <phase>.<minor>.<patch>.
Phase 13 establishes the 13.0.0 baseline. Future phases increment the major.
"""

from __future__ import annotations

from app.application.errors import ContractVersionError

APPLICATION_CONTRACT_VERSION = "13.0.0"

# Versions this server can serve. Older clients negotiating a lower version
# receive the minimum supported version.
_SUPPORTED_VERSIONS: tuple[str, ...] = ("13.0.0",)
_MIN_SUPPORTED = "13.0.0"


def current_version() -> str:
    return APPLICATION_CONTRACT_VERSION


def is_compatible(requested: str) -> bool:
    """True if requested version can be served by this application."""
    try:
        r_major, r_minor, _ = (int(p) for p in requested.split("."))
        s_major, s_minor, _ = (int(p) for p in APPLICATION_CONTRACT_VERSION.split("."))
    except (ValueError, AttributeError):
        return False
    # Same major, requested minor <= supported minor.
    return r_major == s_major and r_minor <= s_minor


def assert_compatible(requested: str) -> None:
    if not is_compatible(requested):
        raise ContractVersionError(requested, APPLICATION_CONTRACT_VERSION)


def negotiate_version(requested: str) -> str:
    """Return the best version to serve for a client request.

    Returns APPLICATION_CONTRACT_VERSION when compatible, raises otherwise.
    """
    assert_compatible(requested)
    return APPLICATION_CONTRACT_VERSION
