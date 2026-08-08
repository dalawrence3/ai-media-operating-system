"""Tests for AIRequest, AIResponse, and the AIProvider protocol."""

from __future__ import annotations

from app.ai.fake import FakeProvider
from app.ai.provider import AIProvider, AIRequest, AIResponse


def _make_request(**kwargs) -> AIRequest:
    defaults = dict(system="sys", user="usr", model="fake")
    return AIRequest(**{**defaults, **kwargs})


def test_ai_request_defaults() -> None:
    r = _make_request()
    assert r.temperature == 0.3
    assert r.max_tokens == 1024
    assert r.response_schema is None
    assert r.prompt_name == ""
    assert r.prompt_version == ""
    assert r.metadata == {}


def test_ai_response_fields() -> None:
    resp = AIResponse(
        raw_text='{"echo":"hi"}',
        provider_name="fake",
        model="fake/test",
        input_tokens=5,
        output_tokens=3,
        duration_ms=10,
        retry_count=0,
    )
    assert resp.parsed is None
    assert resp.request_id is None
    assert resp.stop_reason is None


def test_fake_provider_satisfies_protocol() -> None:
    provider = FakeProvider()
    assert isinstance(provider, AIProvider)


def test_fake_provider_name() -> None:
    assert FakeProvider().name == "fake"
