"""Multi-channel identity bridge — Phase 16B.1.

Maps control-plane UUID channel identity (cp_channels.id) to intelligence
domain integer channel identity (channels.id). This is the ONLY authoritative
lookup path. Callers must not fall back to "first channel" or any other
heuristic when the mapping is absent — missing mappings must fail loudly.

Architecture (Option A):
  channels.cp_channel_id TEXT UNIQUE → cp_channels.id UUID

Isolation contract:
  One cp_channel maps to at most one intelligence channel (UNIQUE index).
  One intelligence channel maps to at most one cp_channel (FK column).
  Missing mapping → ChannelNotBootstrappedError, never None-fallback.
"""

from __future__ import annotations

import sqlite3

from app.intelligence.models import Channel, MaturityStage, OperatingMode, Platform


class ChannelNotBootstrappedError(Exception):
    """Raised when an intelligence channel does not exist for a cp_channel_id.

    This is a hard stop — the operator must bootstrap the channel before
    intelligence pipeline operations can proceed.
    """

    def __init__(self, cp_channel_id: str) -> None:
        self.cp_channel_id = cp_channel_id
        super().__init__(
            f"No intelligence channel is bootstrapped for cp_channel_id={cp_channel_id!r}. "
            "Run the channel bootstrap operation before proceeding."
        )


def get_intelligence_channel_id(conn: sqlite3.Connection, cp_channel_id: str) -> int | None:
    """Return the integer intelligence channel id for the given cp_channel_id, or None."""
    row = conn.execute(
        "SELECT id FROM channels WHERE cp_channel_id = ?", (cp_channel_id,)
    ).fetchone()
    return int(row["id"]) if row else None


def require_intelligence_channel_id(conn: sqlite3.Connection, cp_channel_id: str) -> int:
    """Return the integer intelligence channel id or raise ChannelNotBootstrappedError.

    Use this everywhere a missing mapping must be a hard failure.
    """
    channel_id = get_intelligence_channel_id(conn, cp_channel_id)
    if channel_id is None:
        raise ChannelNotBootstrappedError(cp_channel_id)
    return channel_id


def get_cp_channel_id_for_intelligence_channel(
    conn: sqlite3.Connection, channel_id: int
) -> str | None:
    """Return the cp_channel_id (UUID) for the given intelligence channel id, or None."""
    row = conn.execute("SELECT cp_channel_id FROM channels WHERE id = ?", (channel_id,)).fetchone()
    return str(row["cp_channel_id"]) if row and row["cp_channel_id"] else None


def bootstrap_intelligence_channel(
    conn: sqlite3.Connection,
    cp_channel_id: str,
    *,
    channel_name: str,
    platform_channel_id: str | None = None,
    platform: Platform = Platform.youtube,
    operating_mode: OperatingMode = OperatingMode.manual,
) -> Channel:
    """Create or return the intelligence channel linked to cp_channel_id.

    Idempotent — if a channel already exists for this cp_channel_id, returns it
    without modification. Fails loudly if cp_channel_id does not exist in
    cp_channels (FK constraint) or if another channel already claims this
    cp_channel_id (UNIQUE constraint).
    """
    existing_id = get_intelligence_channel_id(conn, cp_channel_id)
    if existing_id is not None:
        from app.intelligence.repository import get_channel

        ch = get_channel(conn, existing_id)
        assert ch is not None, f"channels row {existing_id} disappeared after lookup"
        return ch

    from app.intelligence.repository import create_channel

    draft = Channel(
        channel_name=channel_name,
        platform=platform,
        platform_channel_id=platform_channel_id,
        cp_channel_id=cp_channel_id,
        operating_mode=operating_mode,
        current_maturity_stage=MaturityStage.validation,
    )
    return create_channel(conn, draft)
