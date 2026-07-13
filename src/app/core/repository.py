"""Repository layer: typed CRUD operations over the SQLite connection."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.core.models import Run, RunStatus, Script, ScriptStatus, Source, Topic, TopicStatus

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _row_to_topic(row: sqlite3.Row) -> Topic:
    return Topic(
        id=row["id"],
        title=row["title"],
        angle=row["angle"],
        status=TopicStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_source(row: sqlite3.Row) -> Source:
    from app.core.models import SourceKind

    return Source(
        id=row["id"],
        topic_id=row["topic_id"],
        kind=SourceKind(row["kind"]),
        reference=row["reference"],
        notes=row["notes"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_script(row: sqlite3.Row) -> Script:
    return Script(
        id=row["id"],
        topic_id=row["topic_id"],
        version=row["version"],
        body=row["body"],
        status=ScriptStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_run(row: sqlite3.Row) -> Run:
    return Run(
        id=row["id"],
        topic_id=row["topic_id"],
        script_id=row["script_id"],
        status=RunStatus(row["status"]),
        started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
        finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
        error=row["error"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------


def create_topic(conn: sqlite3.Connection, topic: Topic) -> Topic:
    now = _now()
    cur = conn.execute(
        "INSERT INTO topics (title, angle, status, created_at, updated_at) VALUES (?,?,?,?,?)",
        (topic.title, topic.angle, topic.status.value, now, now),
    )
    conn.commit()
    logger.debug("Created topic id=%d title=%r", cur.lastrowid, topic.title)
    return get_topic(conn, cur.lastrowid)  # type: ignore[arg-type]


def get_topic(conn: sqlite3.Connection, topic_id: int) -> Topic | None:
    row = conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
    return _row_to_topic(row) if row else None


def list_topics(conn: sqlite3.Connection, *, status: TopicStatus | None = None) -> list[Topic]:
    if status is not None:
        rows = conn.execute(
            "SELECT * FROM topics WHERE status = ? ORDER BY id", (status.value,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM topics ORDER BY id").fetchall()
    return [_row_to_topic(r) for r in rows]


def update_topic(conn: sqlite3.Connection, topic: Topic) -> Topic:
    if topic.id is None:
        raise ValueError("Cannot update a Topic without an id")
    now = _now()
    conn.execute(
        "UPDATE topics SET title=?, angle=?, status=?, updated_at=? WHERE id=?",
        (topic.title, topic.angle, topic.status.value, now, topic.id),
    )
    conn.commit()
    logger.debug("Updated topic id=%d", topic.id)
    return get_topic(conn, topic.id)  # type: ignore[return-value]


def delete_topic(conn: sqlite3.Connection, topic_id: int) -> bool:
    cur = conn.execute("DELETE FROM topics WHERE id = ?", (topic_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    if deleted:
        logger.debug("Deleted topic id=%d (cascade)", topic_id)
    return deleted


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def create_source(conn: sqlite3.Connection, source: Source) -> Source:
    now = _now()
    cur = conn.execute(
        "INSERT INTO sources (topic_id, kind, reference, notes, created_at) VALUES (?,?,?,?,?)",
        (source.topic_id, source.kind.value, source.reference, source.notes, now),
    )
    conn.commit()
    logger.debug("Created source id=%d topic_id=%d", cur.lastrowid, source.topic_id)
    return get_source(conn, cur.lastrowid)  # type: ignore[arg-type]


def get_source(conn: sqlite3.Connection, source_id: int) -> Source | None:
    row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    return _row_to_source(row) if row else None


def list_sources(conn: sqlite3.Connection, topic_id: int) -> list[Source]:
    rows = conn.execute(
        "SELECT * FROM sources WHERE topic_id = ? ORDER BY id", (topic_id,)
    ).fetchall()
    return [_row_to_source(r) for r in rows]


def delete_source(conn: sqlite3.Connection, source_id: int) -> bool:
    cur = conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Scripts
# ---------------------------------------------------------------------------


def create_script(conn: sqlite3.Connection, script: Script) -> Script:
    now = _now()
    cur = conn.execute(
        "INSERT INTO scripts (topic_id, version, body, status, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?)",
        (script.topic_id, script.version, script.body, script.status.value, now, now),
    )
    conn.commit()
    logger.debug(
        "Created script id=%d topic_id=%d v%d", cur.lastrowid, script.topic_id, script.version
    )
    return get_script(conn, cur.lastrowid)  # type: ignore[arg-type]


def get_script(conn: sqlite3.Connection, script_id: int) -> Script | None:
    row = conn.execute("SELECT * FROM scripts WHERE id = ?", (script_id,)).fetchone()
    return _row_to_script(row) if row else None


def list_scripts(conn: sqlite3.Connection, topic_id: int) -> list[Script]:
    rows = conn.execute(
        "SELECT * FROM scripts WHERE topic_id = ? ORDER BY version", (topic_id,)
    ).fetchall()
    return [_row_to_script(r) for r in rows]


def update_script_status(
    conn: sqlite3.Connection, script_id: int, status: ScriptStatus
) -> Script | None:
    now = _now()
    conn.execute(
        "UPDATE scripts SET status=?, updated_at=? WHERE id=?",
        (status.value, now, script_id),
    )
    conn.commit()
    return get_script(conn, script_id)


def next_script_version(conn: sqlite3.Connection, topic_id: int) -> int:
    row = conn.execute(
        "SELECT MAX(version) FROM scripts WHERE topic_id = ?", (topic_id,)
    ).fetchone()
    return (row[0] or 0) + 1


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def create_run(conn: sqlite3.Connection, run: Run) -> Run:
    now = _now()
    cur = conn.execute(
        "INSERT INTO runs (topic_id, script_id, status, created_at) VALUES (?,?,?,?)",
        (run.topic_id, run.script_id, run.status.value, now),
    )
    conn.commit()
    logger.debug("Created run id=%d topic_id=%d", cur.lastrowid, run.topic_id)
    return get_run(conn, cur.lastrowid)  # type: ignore[arg-type]


def get_run(conn: sqlite3.Connection, run_id: int) -> Run | None:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return _row_to_run(row) if row else None


def list_runs(conn: sqlite3.Connection, topic_id: int) -> list[Run]:
    rows = conn.execute("SELECT * FROM runs WHERE topic_id = ? ORDER BY id", (topic_id,)).fetchall()
    return [_row_to_run(r) for r in rows]


def update_run_status(
    conn: sqlite3.Connection,
    run_id: int,
    status: RunStatus,
    *,
    error: str | None = None,
) -> Run | None:
    started_at: str | None = None
    finished_at: str | None = None
    now = _now()
    if status == RunStatus.running:
        started_at = now
        conn.execute(
            "UPDATE runs SET status=?, started_at=? WHERE id=?",
            (status.value, started_at, run_id),
        )
    elif status in (RunStatus.completed, RunStatus.failed):
        finished_at = now
        conn.execute(
            "UPDATE runs SET status=?, finished_at=?, error=? WHERE id=?",
            (status.value, finished_at, error, run_id),
        )
    else:
        conn.execute("UPDATE runs SET status=? WHERE id=?", (status.value, run_id))
    conn.commit()
    logger.debug("Updated run id=%d status=%s", run_id, status.value)
    return get_run(conn, run_id)
