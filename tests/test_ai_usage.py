"""Tests for AI usage recording."""

from __future__ import annotations

import sqlite3

import pytest

from app.ai.fake import (
    FakeProvider,  # noqa: F401 — FakeProvider unused directly but kept for context
)
from app.ai.pricing import PricingRegistry
from app.ai.provider import AIRequest, AIResponse
from app.ai.usage import record_ai_call


def _req(**kwargs) -> AIRequest:
    return AIRequest(
        system="sys",
        user="user",
        model="fake",
        prompt_name="demo-echo",
        prompt_version="1",
        **kwargs,
    )


def _resp(**kwargs) -> AIResponse:
    defaults = dict(
        raw_text='{"echo":"hi"}',
        provider_name="fake",
        model="fake",
        input_tokens=10,
        output_tokens=5,
        duration_ms=20,
        retry_count=0,
        stop_reason="end_turn",
    )
    return AIResponse(**{**defaults, **kwargs})


def test_success_row_written(db: sqlite3.Connection) -> None:
    call_id = record_ai_call(db, _req(), _resp(), status="success")
    row = db.execute("SELECT * FROM ai_calls WHERE id=?", (call_id,)).fetchone()
    assert row is not None
    assert row["status"] == "success"
    assert row["provider"] == "fake"
    assert row["model"] == "fake"
    assert row["prompt_name"] == "demo-echo"
    assert row["prompt_version"] == "1"
    assert row["input_tokens"] == 10
    assert row["output_tokens"] == 5
    assert row["retry_count"] == 0
    assert row["error_message"] is None


def test_failed_row_has_error_fields(db: sqlite3.Connection) -> None:
    call_id = record_ai_call(
        db,
        _req(),
        None,
        status="failed",
        error_category="TransientProviderError",
        error_message="server blew up",
    )
    row = db.execute("SELECT * FROM ai_calls WHERE id=?", (call_id,)).fetchone()
    assert row["status"] == "failed"
    assert row["error_category"] == "TransientProviderError"
    assert row["error_message"] == "server blew up"
    assert row["input_tokens"] is None
    assert row["output_tokens"] is None


def test_cost_estimated_for_known_model(db: sqlite3.Connection) -> None:
    resp = _resp(
        provider_name="claude", model="claude-sonnet-5", input_tokens=1000, output_tokens=500
    )
    call_id = record_ai_call(db, _req(), resp, status="success")
    row = db.execute("SELECT estimated_cost_usd FROM ai_calls WHERE id=?", (call_id,)).fetchone()
    assert row["estimated_cost_usd"] is not None
    assert row["estimated_cost_usd"] > 0


def test_cost_zero_for_fake_model(db: sqlite3.Connection) -> None:
    call_id = record_ai_call(db, _req(), _resp(), status="success")
    row = db.execute("SELECT estimated_cost_usd FROM ai_calls WHERE id=?", (call_id,)).fetchone()
    assert row["estimated_cost_usd"] == 0.0


def test_run_id_stored(db: sqlite3.Connection) -> None:
    # Create a topic and run to get a valid run_id.
    conn = db
    conn.execute(
        "INSERT INTO topics (title, angle, status, created_at, updated_at) VALUES (?,?,?,?,?)",
        ("t", "", "active", "2025-01-01T00:00:00", "2025-01-01T00:00:00"),
    )
    conn.execute(
        "INSERT INTO runs (topic_id, status, created_at) VALUES (?,?,?)",
        (1, "pending", "2025-01-01T00:00:00"),
    )
    conn.commit()
    call_id = record_ai_call(db, _req(), _resp(), status="success", run_id=1)
    row = db.execute("SELECT run_id FROM ai_calls WHERE id=?", (call_id,)).fetchone()
    assert row["run_id"] == 1


def test_custom_pricing_used(db: sqlite3.Connection) -> None:
    custom = PricingRegistry(
        pricing={
            "registry_version": "t",
            "effective_date": "2025-01-01",
            "models": {"my-model": {"input_per_mtok": 100.0, "output_per_mtok": 200.0}},
        }
    )
    resp = _resp(model="my-model", input_tokens=1_000_000, output_tokens=1_000_000)
    call_id = record_ai_call(db, _req(), resp, status="success", pricing=custom)
    row = db.execute("SELECT estimated_cost_usd FROM ai_calls WHERE id=?", (call_id,)).fetchone()
    assert row["estimated_cost_usd"] == pytest.approx(300.0)


def test_duration_stored(db: sqlite3.Connection) -> None:
    call_id = record_ai_call(db, _req(), _resp(duration_ms=42), status="success")
    row = db.execute("SELECT duration_ms FROM ai_calls WHERE id=?", (call_id,)).fetchone()
    assert row["duration_ms"] == 42


def test_unknown_production_model_cost_is_null(db: sqlite3.Connection) -> None:
    """Unknown production model must record NULL cost, not silently report $0."""
    resp = _resp(model="gpt-999-unknown", input_tokens=100, output_tokens=50)
    call_id = record_ai_call(db, _req(), resp, status="success")
    row = db.execute(
        "SELECT status, estimated_cost_usd FROM ai_calls WHERE id=?", (call_id,)
    ).fetchone()
    assert row["status"] == "success"
    assert row["estimated_cost_usd"] is None  # not 0.0 — cost is unknown, not free


def test_fake_model_cost_is_explicitly_zero_not_null(db: sqlite3.Connection) -> None:
    """Fake provider cost must be 0.0 (free), distinct from NULL (unknown)."""
    call_id = record_ai_call(db, _req(), _resp(model="fake"), status="success")
    row = db.execute("SELECT estimated_cost_usd FROM ai_calls WHERE id=?", (call_id,)).fetchone()
    assert row["estimated_cost_usd"] == 0.0  # exactly zero, not NULL
