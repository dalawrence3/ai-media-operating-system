"""In-process event bus: durable, idempotent, append-only, replay-safe."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from app.control_plane import repository as repo
from app.control_plane.constants import (
    MAX_DELIVERY_ATTEMPTS,
    PROCESSING_STATUS_COMPLETED,
    PROCESSING_STATUS_DEAD_LETTERED,
    PROCESSING_STATUS_FAILED,
    PROCESSING_STATUS_PROCESSING,
)
from app.control_plane.models import ControlEvent, EventProcessing

HandlerFn = Callable[[Any, ControlEvent], None]

_HANDLERS: dict[str, list[tuple[str, HandlerFn]]] = {}


def register_handler(event_type: str, handler_key: str, fn: HandlerFn) -> None:
    _HANDLERS.setdefault(event_type, [])
    _HANDLERS[event_type].append((handler_key, fn))


def dispatch_event(conn: Any, event: ControlEvent) -> list[EventProcessing]:
    handlers = _HANDLERS.get(event.event_type, [])
    results: list[EventProcessing] = []
    for handler_key, fn in handlers:
        row = conn.execute(
            "SELECT * FROM cp_event_processing WHERE event_id = ? AND handler_key = ?",
            (event.id, handler_key),
        ).fetchone()

        if row and row["status"] in ("completed", "dead_lettered"):
            continue

        if row:
            proc = repo._row_to_event_processing(row)
        else:
            proc = repo.create_event_processing(conn, str(uuid.uuid4()), event.id, handler_key)

        proc = repo.update_event_processing_status(conn, proc.id, PROCESSING_STATUS_PROCESSING)
        try:
            fn(conn, event)
            proc = repo.update_event_processing_status(conn, proc.id, PROCESSING_STATUS_COMPLETED)
        except Exception as exc:
            row2 = conn.execute(
                "SELECT attempt_count FROM cp_event_processing WHERE id = ?", (proc.id,)
            ).fetchone()
            attempts = row2["attempt_count"] if row2 else 1
            terminal = (
                PROCESSING_STATUS_DEAD_LETTERED
                if attempts >= MAX_DELIVERY_ATTEMPTS
                else PROCESSING_STATUS_FAILED
            )
            proc = repo.update_event_processing_status(
                conn, proc.id, terminal, error_message=str(exc)
            )
        results.append(proc)
    return results


def clear_handlers() -> None:
    _HANDLERS.clear()
