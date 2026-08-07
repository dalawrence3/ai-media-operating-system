"""Strategy profile management (versioned operational intent per channel)."""

from __future__ import annotations

import uuid
from typing import Any

from app.control_plane import repository as repo
from app.control_plane.models import StrategyProfile, StrategyProfileDraft


def create_strategy_profile(
    conn: Any,
    *,
    channel_id: str,
    config: dict[str, Any],
    actor: str,
) -> StrategyProfile:
    existing = repo.list_strategy_profiles_by_channel(conn, channel_id)
    version = (max(p.version for p in existing) + 1) if existing else 1
    draft = StrategyProfileDraft(
        id=str(uuid.uuid4()),
        channel_id=channel_id,
        version=version,
        config=config,
        actor=actor,
    )
    return repo.create_strategy_profile(conn, draft)


def get_active_strategy(conn: Any, channel_id: str) -> StrategyProfile | None:
    return repo.get_active_strategy_for_channel(conn, channel_id)
