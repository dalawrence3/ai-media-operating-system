"""Channel-aware visual asset memory.

Answers, deterministically and per channel:

  Has this exact asset been used before?  On which channel?  In which
  render/scene/beat?  How recently?  For how long?  How often in this video?

Reuse across *different* channels is not penalised here — that is a policy
decision, and different channels may legitimately draw on the same stock
library.  Reuse on the *same* channel decays with age rather than being
banned outright, so a genuinely apt asset can return once it is no longer
recent.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.visuals.constants import CHANNEL_REUSE_DECAY_DAYS


@dataclass(frozen=True)
class AssetUsageStats:
    asset_key: str
    total_uses: int
    channel_uses: int
    last_used_at: str | None
    total_duration_ms: int
    reuse_weight: float  # 0..1, recency-weighted, channel-scoped


def _table_exists(conn: sqlite3.Connection) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='visual_asset_usage'"
        ).fetchone()
    )


def record_asset_usage(
    conn: sqlite3.Connection,
    *,
    asset_key: str,
    provider: str,
    provider_asset_id: str,
    media_type: str,
    duration_ms: int,
    channel_key: str | None = None,
    workspace_id: str | None = None,
    topic_id: int | None = None,
    experiment_id: str | None = None,
    scene_manifest_id: int | None = None,
    render_manifest_id: int | None = None,
    publication_id: int | None = None,
    beat_index: int | None = None,
    scene_index: int | None = None,
) -> None:
    """Append one immutable usage row.  Never updates or deletes history."""
    if not _table_exists(conn):
        return
    conn.execute(
        """
        INSERT INTO visual_asset_usage (
            asset_key, provider, provider_asset_id, media_type, channel_key,
            workspace_id, topic_id, experiment_id, scene_manifest_id,
            render_manifest_id, publication_id, beat_index, scene_index, duration_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            asset_key,
            provider,
            provider_asset_id,
            media_type,
            channel_key,
            workspace_id,
            topic_id,
            experiment_id,
            scene_manifest_id,
            render_manifest_id,
            publication_id,
            beat_index,
            scene_index,
            int(duration_ms),
        ),
    )


def clear_manifest_usage(conn: sqlite3.Connection, scene_manifest_id: int) -> None:
    """Drop usage rows for one scene manifest.

    Called before re-recording a re-planned manifest so a repeatedly rendered
    manifest does not inflate its own reuse penalties on the next run.
    """
    if not _table_exists(conn):
        return
    conn.execute("DELETE FROM visual_asset_usage WHERE scene_manifest_id = ?", (scene_manifest_id,))


def _recency_weight(last_used_at: str | None, decay_days: int) -> float:
    if not last_used_at:
        return 0.0
    try:
        stamp = datetime.fromisoformat(last_used_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.5
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    age = datetime.now(UTC) - stamp
    if age <= timedelta(0):
        return 1.0
    if age >= timedelta(days=decay_days):
        return 0.0
    return round(1.0 - age / timedelta(days=decay_days), 4)


def get_asset_usage(
    conn: sqlite3.Connection,
    asset_key: str,
    *,
    channel_key: str | None = None,
    decay_days: int = CHANNEL_REUSE_DECAY_DAYS,
    exclude_scene_manifest_id: int | None = None,
) -> AssetUsageStats:
    """Usage history for one asset, scoped to *channel_key* where given."""
    if not _table_exists(conn):
        return AssetUsageStats(asset_key, 0, 0, None, 0, 0.0)

    params: list[object] = [asset_key]
    where = "asset_key = ?"
    if exclude_scene_manifest_id is not None:
        where += " AND (scene_manifest_id IS NULL OR scene_manifest_id != ?)"
        params.append(exclude_scene_manifest_id)

    total_row = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(duration_ms), 0) AS ms "
        f"FROM visual_asset_usage WHERE {where}",
        params,
    ).fetchone()

    channel_where = where
    channel_params = list(params)
    if channel_key is None:
        channel_where += " AND channel_key IS NULL"
    else:
        channel_where += " AND channel_key = ?"
        channel_params.append(channel_key)

    channel_row = conn.execute(
        "SELECT COUNT(*) AS n, MAX(used_at) AS last_used "
        f"FROM visual_asset_usage WHERE {channel_where}",
        channel_params,
    ).fetchone()

    channel_uses = int(channel_row["n"] or 0)
    last_used = channel_row["last_used"]
    weight = _recency_weight(last_used, decay_days) if channel_uses else 0.0
    # Repeated use on the channel compounds beyond the recency of the last use.
    if channel_uses > 1:
        weight = min(1.0, weight + 0.15 * (channel_uses - 1))

    return AssetUsageStats(
        asset_key=asset_key,
        total_uses=int(total_row["n"] or 0),
        channel_uses=channel_uses,
        last_used_at=last_used,
        total_duration_ms=int(total_row["ms"] or 0),
        reuse_weight=round(weight, 4),
    )


def channel_reuse_weights(
    conn: sqlite3.Connection,
    asset_keys: list[str],
    *,
    channel_key: str | None = None,
    decay_days: int = CHANNEL_REUSE_DECAY_DAYS,
    exclude_scene_manifest_id: int | None = None,
) -> dict[str, float]:
    """Batch form of ``get_asset_usage`` returning only the reuse weights."""
    weights: dict[str, float] = {}
    for key in set(asset_keys):
        stats = get_asset_usage(
            conn,
            key,
            channel_key=channel_key,
            decay_days=decay_days,
            exclude_scene_manifest_id=exclude_scene_manifest_id,
        )
        if stats.reuse_weight:
            weights[key] = stats.reuse_weight
    return weights


def asset_history(
    conn: sqlite3.Connection,
    asset_key: str,
    *,
    limit: int = 50,
) -> list[sqlite3.Row]:
    """Full lineage rows for one asset, most recent first."""
    if not _table_exists(conn):
        return []
    return list(
        conn.execute(
            """
            SELECT * FROM visual_asset_usage
            WHERE asset_key = ?
            ORDER BY used_at DESC, id DESC
            LIMIT ?
            """,
            (asset_key, limit),
        ).fetchall()
    )
