"""Tests for FakeProvider determinism and structured output handling."""

from __future__ import annotations

import pytest

from app.ai.errors import InvalidStructuredResponseError
from app.ai.fake import _NOT_PRODUCTION, FakeProvider
from app.ai.provider import AIRequest
from app.ai.schemas import EchoOutput


def _req(**kwargs) -> AIRequest:
    return AIRequest(system="s", user="u", model="fake", **kwargs)


def test_returns_configured_output() -> None:
    p = FakeProvider(output='{"echo": "hello"}')
    resp = p.complete(_req())
    assert resp.raw_text == '{"echo": "hello"}'


def test_is_deterministic() -> None:
    p = FakeProvider(output='{"echo": "x"}')
    r1 = p.complete(_req())
    r2 = p.complete(_req())
    assert r1.raw_text == r2.raw_text


def test_clearly_not_production() -> None:
    p = FakeProvider()
    resp = p.complete(_req())
    assert _NOT_PRODUCTION in resp.model
    assert resp.provider_name == "fake"


def test_valid_schema_is_parsed() -> None:
    p = FakeProvider(output='{"echo": "world"}')
    resp = p.complete(_req(response_schema=EchoOutput))
    assert isinstance(resp.parsed, EchoOutput)
    assert resp.parsed.echo == "world"


def test_malformed_json_raises() -> None:
    p = FakeProvider(output="not json")
    with pytest.raises(InvalidStructuredResponseError, match="not valid JSON"):
        p.complete(_req(response_schema=EchoOutput))


def test_schema_invalid_raises() -> None:
    p = FakeProvider(output='{"wrong_field": "value"}')
    with pytest.raises(InvalidStructuredResponseError, match="schema validation"):
        p.complete(_req(response_schema=EchoOutput))


def test_no_schema_returns_raw() -> None:
    p = FakeProvider(output='{"anything": true}')
    resp = p.complete(_req())
    assert resp.parsed is None
    assert resp.raw_text == '{"anything": true}'


def test_token_counts_are_positive() -> None:
    p = FakeProvider(output='{"echo": "test"}')
    resp = p.complete(_req())
    assert resp.input_tokens >= 1
    assert resp.output_tokens >= 1


def test_retry_count_is_zero() -> None:
    p = FakeProvider()
    resp = p.complete(_req())
    assert resp.retry_count == 0
