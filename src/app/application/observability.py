"""In-process observability: timing spans, error counters, correlation tracking.

No external vendor. No persistence. Phase 14 may surface this via the facade.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Span:
    name: str
    started_at: float
    workspace_id: str | None = None
    correlation_id: str | None = None
    finished_at: float | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at) * 1000.0

    @property
    def ok(self) -> bool:
        return self.error is None


class ObservabilityContext:
    """Collects spans for a single request/command lifetime."""

    def __init__(
        self,
        workspace_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self._workspace_id = workspace_id
        self._correlation_id = correlation_id
        self._spans: list[Span] = []
        self._error_counts: dict[str, int] = {}

    def start_span(
        self,
        name: str,
        *,
        workspace_id: str | None = None,
        correlation_id: str | None = None,
        **metadata: Any,
    ) -> Span:
        span = Span(
            name=name,
            started_at=time.monotonic(),
            workspace_id=workspace_id or self._workspace_id,
            correlation_id=correlation_id or self._correlation_id,
            metadata=metadata,
        )
        self._spans.append(span)
        return span

    def finish_span(self, span: Span, *, error: str | None = None) -> None:
        span.finished_at = time.monotonic()
        span.error = error
        if error:
            self._error_counts[span.name] = self._error_counts.get(span.name, 0) + 1

    @contextmanager
    def span(
        self,
        name: str,
        **metadata: Any,
    ) -> Generator[Span]:
        s = self.start_span(name, **metadata)
        try:
            yield s
        except Exception as exc:
            self.finish_span(s, error=str(exc))
            raise
        else:
            self.finish_span(s)

    def summary(self) -> dict[str, Any]:
        completed = [s for s in self._spans if s.finished_at is not None]
        return {
            "span_count": len(self._spans),
            "completed_count": len(completed),
            "error_count": sum(1 for s in completed if not s.ok),
            "total_duration_ms": sum(
                s.duration_ms for s in completed if s.duration_ms is not None
            ),
            "spans": [
                {
                    "name": s.name,
                    "duration_ms": s.duration_ms,
                    "ok": s.ok,
                    "error": s.error,
                    "metadata": s.metadata,
                }
                for s in completed
            ],
            "error_counts_by_span": dict(self._error_counts),
        }

    def spans(self) -> list[Span]:
        return list(self._spans)

    def clear(self) -> None:
        self._spans.clear()
        self._error_counts.clear()


# Null context for use when no observability is desired.
class NullObservabilityContext(ObservabilityContext):
    """Drops all spans silently — for production paths where overhead matters."""

    def start_span(self, name: str, **kw: Any) -> Span:
        return Span(name=name, started_at=0.0)

    def finish_span(self, span: Span, *, error: str | None = None) -> None:
        pass

    def summary(self) -> dict[str, Any]:
        return {}
