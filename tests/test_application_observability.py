"""Tests for observability context, spans, and null context."""

from __future__ import annotations

import time

import pytest

from app.application.observability import (
    NullObservabilityContext,
    ObservabilityContext,
    Span,
)


class TestSpan:
    def test_span_creates_with_name(self):
        s = Span(name="test_span", started_at=time.monotonic(), workspace_id="ws-1")
        assert s.name == "test_span"
        assert s.workspace_id == "ws-1"
        assert s.finished_at is None

    def test_duration_ms_none_when_unfinished(self):
        s = Span(name="s", started_at=time.monotonic(), workspace_id="ws")
        assert s.duration_ms is None

    def test_duration_ms_computed_when_finished(self):
        t0 = time.monotonic()
        s = Span(name="s", started_at=t0, workspace_id="ws")
        s.finished_at = t0 + 0.1
        assert s.duration_ms is not None
        assert s.duration_ms >= 0

    def test_span_metadata_defaults_empty(self):
        s = Span(name="s", started_at=time.monotonic(), workspace_id="ws")
        assert s.metadata == {}

    def test_span_ok_when_no_error(self):
        s = Span(name="s", started_at=time.monotonic())
        assert s.ok is True

    def test_span_not_ok_when_error_set(self):
        s = Span(name="s", started_at=time.monotonic())
        s.error = "something failed"
        assert s.ok is False


class TestObservabilityContext:
    def test_start_and_finish_span(self):
        ctx = ObservabilityContext(workspace_id="ws-1")
        span = ctx.start_span("op.test")
        assert span.name == "op.test"
        ctx.finish_span(span)
        assert span.finished_at is not None

    def test_finish_with_error(self):
        ctx = ObservabilityContext(workspace_id="ws-1")
        span = ctx.start_span("op")
        ctx.finish_span(span, error="something went wrong")
        assert span.error == "something went wrong"

    def test_context_manager(self):
        ctx = ObservabilityContext(workspace_id="ws-1")
        with ctx.span("op.cm") as s:
            pass
        assert s.finished_at is not None
        assert s.error is None

    def test_context_manager_captures_exception(self):
        ctx = ObservabilityContext(workspace_id="ws-1")
        with pytest.raises(ValueError):
            with ctx.span("op.err") as s:
                raise ValueError("test")
        assert s.error is not None

    def test_summary_returns_dict(self):
        ctx = ObservabilityContext(workspace_id="ws-1")
        with ctx.span("a"):
            pass
        with ctx.span("b"):
            pass
        summary = ctx.summary()
        assert isinstance(summary, dict)
        assert summary["span_count"] == 2

    def test_summary_includes_span_names(self):
        ctx = ObservabilityContext(workspace_id="ws-1")
        with ctx.span("x"):
            pass
        names = [s["name"] for s in ctx.summary()["spans"]]
        assert "x" in names

    def test_correlation_id_propagated(self):
        ctx = ObservabilityContext(workspace_id="ws-1", correlation_id="corr-123")
        span = ctx.start_span("op")
        assert span.correlation_id == "corr-123"

    def test_error_count_increments(self):
        ctx = ObservabilityContext(workspace_id="ws")
        with pytest.raises(RuntimeError):
            with ctx.span("failing"):
                raise RuntimeError("boom")
        summary = ctx.summary()
        assert summary["error_count"] == 1


class TestNullObservabilityContext:
    def test_null_context_span_is_noop(self):
        ctx = NullObservabilityContext()
        with ctx.span("op") as s:
            pass
        assert s is not None

    def test_null_context_summary_empty(self):
        ctx = NullObservabilityContext()
        with ctx.span("op"):
            pass
        assert ctx.summary() == {}

    def test_null_context_no_extra_exception(self):
        ctx = NullObservabilityContext()
        with pytest.raises(RuntimeError):
            with ctx.span("op"):
                raise RuntimeError("boom")
