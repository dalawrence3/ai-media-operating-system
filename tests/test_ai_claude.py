"""Tests for ClaudeProvider — all with a mocked client; no real API calls."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.ai.claude import ClaudeProvider, _strip_json_fences
from app.ai.errors import (
    InvalidStructuredResponseError,
    MissingCredentialsError,
    RateLimitError,
    RetryExhaustedError,
    TransientProviderError,
)
from app.ai.provider import AIRequest
from app.ai.schemas import EchoOutput


def _mock_client(text: str = '{"echo": "hi"}', model: str = "claude-sonnet-5") -> MagicMock:
    client = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    msg.model = model
    msg.usage.input_tokens = 10
    msg.usage.output_tokens = 5
    msg.id = "msg_test123"
    msg.stop_reason = "end_turn"
    client.messages.create.return_value = msg
    return client


def _provider(client=None, **kwargs) -> ClaudeProvider:
    return ClaudeProvider("test-key", client=client or _mock_client(), **kwargs)


def _req(**kwargs) -> AIRequest:
    return AIRequest(system="sys", user="user text", model="claude-sonnet-5", **kwargs)


# ── Construction ──────────────────────────────────────────────────────────────


def test_empty_api_key_raises() -> None:
    with pytest.raises(MissingCredentialsError):
        ClaudeProvider("")


def test_name_is_claude() -> None:
    assert _provider().name == "claude"


# ── Request mapping ───────────────────────────────────────────────────────────


def test_request_fields_passed_to_sdk() -> None:
    client = _mock_client()
    p = _provider(client=client)
    req = _req(max_tokens=512, temperature=0.1)
    p.complete(req)
    call_kwargs = client.messages.create.call_args[1]
    assert call_kwargs["max_tokens"] == 512
    assert call_kwargs["temperature"] == 0.1
    assert call_kwargs["system"] == "sys"
    assert call_kwargs["messages"][0]["content"] == "user text"


# ── Successful response ───────────────────────────────────────────────────────


def test_complete_returns_ai_response() -> None:
    resp = _provider().complete(_req())
    assert resp.provider_name == "claude"
    assert resp.input_tokens == 10
    assert resp.output_tokens == 5
    assert resp.request_id == "msg_test123"
    assert resp.stop_reason == "end_turn"
    assert resp.retry_count == 0


def test_structured_output_parsed() -> None:
    p = _provider(client=_mock_client('{"echo": "hello"}'))
    resp = p.complete(_req(response_schema=EchoOutput))
    assert isinstance(resp.parsed, EchoOutput)
    assert resp.parsed.echo == "hello"


def test_malformed_json_raises() -> None:
    p = _provider(client=_mock_client("not json"))
    with pytest.raises(InvalidStructuredResponseError):
        p.complete(_req(response_schema=EchoOutput))


def test_schema_mismatch_raises() -> None:
    p = _provider(client=_mock_client('{"wrong": "field"}'))
    with pytest.raises(InvalidStructuredResponseError):
        p.complete(_req(response_schema=EchoOutput))


def test_json_fence_stripped_before_parse() -> None:
    """Markdown code-fenced JSON is accepted and parsed correctly."""
    fenced = '```json\n{"echo": "world"}\n```'
    p = _provider(client=_mock_client(fenced))
    resp = p.complete(_req(response_schema=EchoOutput))
    assert resp.parsed is not None
    assert resp.parsed.echo == "world"


def test_plain_json_fence_stripped() -> None:
    """Triple-backtick fence without 'json' tag is also stripped."""
    fenced = '```\n{"echo": "bare"}\n```'
    p = _provider(client=_mock_client(fenced))
    resp = p.complete(_req(response_schema=EchoOutput))
    assert resp.parsed is not None
    assert resp.parsed.echo == "bare"


def test_non_fenced_json_unaffected() -> None:
    """Plain JSON without fences is parsed as before."""
    p = _provider(client=_mock_client('{"echo": "plain"}'))
    resp = p.complete(_req(response_schema=EchoOutput))
    assert resp.parsed is not None
    assert resp.parsed.echo == "plain"


# ── Retry behaviour ───────────────────────────────────────────────────────────


def test_retry_on_rate_limit_then_succeed() -> None:
    client = _mock_client()
    call_count = 0

    def side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RateLimitError("rate limited")
        return client.messages.create.return_value

    client.messages.create.side_effect = side_effect
    no_sleep = MagicMock()
    p = ClaudeProvider("key", client=client, max_retries=3, sleep_fn=no_sleep)
    resp = p.complete(_req())
    assert resp.retry_count == 2
    assert no_sleep.call_count == 2


def test_retry_exhausted_raises() -> None:
    client = MagicMock()
    client.messages.create.side_effect = TransientProviderError("server error")
    no_sleep = MagicMock()
    p = ClaudeProvider("key", client=client, max_retries=2, sleep_fn=no_sleep)
    with pytest.raises(RetryExhaustedError) as exc_info:
        p.complete(_req())
    assert exc_info.value.attempts == 2


def test_non_retryable_error_not_retried() -> None:
    client = MagicMock()
    client.messages.create.side_effect = MissingCredentialsError("bad key")
    no_sleep = MagicMock()
    p = ClaudeProvider("key", client=client, max_retries=3, sleep_fn=no_sleep)
    with pytest.raises(MissingCredentialsError):
        p.complete(_req())
    # Only one call — no retries on non-retryable errors.
    assert client.messages.create.call_count == 1


# ── Error translation (anthropic SDK → app errors) ───────────────────────────


def test_anthropic_authentication_error_translated() -> None:
    import anthropic

    client = MagicMock()

    class _FakeAuthError(anthropic.AuthenticationError):
        def __init__(self):
            pass

    client.messages.create.side_effect = _FakeAuthError()
    p = ClaudeProvider("key", client=client, max_retries=1, sleep_fn=lambda _: None)
    with pytest.raises(MissingCredentialsError):
        p.complete(_req())


def test_anthropic_rate_limit_error_translated() -> None:
    import anthropic

    client = MagicMock()

    class _FakeRLError(anthropic.RateLimitError):
        def __init__(self):
            pass

    client.messages.create.side_effect = _FakeRLError()
    no_sleep = MagicMock()
    p = ClaudeProvider("key", client=client, max_retries=1, sleep_fn=no_sleep)
    with pytest.raises(RetryExhaustedError):
        p.complete(_req())


# ── Secret safety ─────────────────────────────────────────────────────────────


def test_api_key_not_in_exception_message() -> None:
    secret = "sk-ant-super-secret-key"
    client = MagicMock()
    client.messages.create.side_effect = TransientProviderError("server error")
    p = ClaudeProvider(secret, client=client, max_retries=1, sleep_fn=lambda _: None)
    try:
        p.complete(_req())
    except RetryExhaustedError as exc:
        assert secret not in str(exc)
        assert secret not in str(exc.last_error)


# ── _strip_json_fences unit tests ─────────────────────────────────────────────


def test_strip_json_fence_json_tagged() -> None:
    assert _strip_json_fences('```json\n{"k": 1}\n```') == '{"k": 1}'


def test_strip_json_fence_plain_backticks() -> None:
    assert _strip_json_fences('```\n{"k": 2}\n```') == '{"k": 2}'


def test_strip_json_fence_no_fence_unchanged() -> None:
    raw = '{"k": 3}'
    assert _strip_json_fences(raw) == raw


def test_strip_json_fence_with_surrounding_whitespace() -> None:
    assert _strip_json_fences('  ```json\n{"k": 4}\n```  ') == '{"k": 4}'


def test_strip_json_fence_ignores_non_fence_backticks() -> None:
    raw = "some text with `inline code`"
    assert _strip_json_fences(raw) == raw


def test_strip_json_fence_with_preamble_text() -> None:
    """Preamble text before the fence is ignored; JSON inside is extracted."""
    raw = 'Here is my response:\n```json\n{"k": 5}\n```'
    assert _strip_json_fences(raw) == '{"k": 5}'


def test_strip_json_fence_with_preamble_and_trailing_text() -> None:
    """Preamble before and trailing text after the fence — JSON still extracted."""
    raw = 'Sure!\n```json\n{"k": 6}\n```\nHope that helps.'
    assert _strip_json_fences(raw) == '{"k": 6}'
