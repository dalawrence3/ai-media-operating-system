"""Topic domain — workspace-scoped listing and creation.

Topics bridge the legacy engine layer (which identifies topics by integer PK)
and the modern control-plane layer (which identifies workspace-owned resources
by UUID). workspace_id is stored on the topics row (added in V21).

All functions accept a raw connection; callers own the lifecycle.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class TopicView(BaseModel, frozen=True):
    id: int
    title: str
    angle: str
    status: str
    workspace_id: str | None
    created_at: str
    updated_at: str


def _row_to_topic(row: Any) -> TopicView:
    return TopicView(
        id=row["id"],
        title=row["title"],
        angle=row["angle"] or "",
        status=row["status"],
        workspace_id=row["workspace_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_topics(conn: Any, workspace_id: str) -> list[TopicView]:
    """Return active topics owned by workspace_id, newest first."""
    rows = conn.execute(
        "SELECT id, title, angle, status, workspace_id, created_at, updated_at "
        "FROM topics WHERE workspace_id = ? AND status = 'active' "
        "ORDER BY id DESC",
        (workspace_id,),
    ).fetchall()
    return [_row_to_topic(r) for r in rows]


def get_topic(conn: Any, topic_id: int, workspace_id: str) -> TopicView | None:
    """Return a single topic, or None if not found / not in workspace."""
    row = conn.execute(
        "SELECT id, title, angle, status, workspace_id, created_at, updated_at "
        "FROM topics WHERE id = ? AND workspace_id = ?",
        (topic_id, workspace_id),
    ).fetchone()
    return _row_to_topic(row) if row else None


def create_topic(conn: Any, workspace_id: str, title: str, angle: str = "") -> TopicView:
    """Insert a new active topic for workspace_id and return the created view."""
    title = title.strip()
    if not title:
        raise ValueError("title must not be empty")
    if len(title) > 500:
        raise ValueError("title must be 500 characters or fewer")

    now = _now_iso()
    cur = conn.execute(
        "INSERT INTO topics (title, angle, status, workspace_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        (title, angle.strip(), "active", workspace_id, now, now),
    )
    conn.commit()
    topic_id = cur.lastrowid
    row = conn.execute(
        "SELECT id, title, angle, status, workspace_id, created_at, updated_at "
        "FROM topics WHERE id = ?",
        (topic_id,),
    ).fetchone()
    return _row_to_topic(row)
