"""Tests for ClaudeProvider — all with a mocked client; no real API calls."""

from __future__ import annotations

from typing import Any
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


def _mock_client(
    text: str = '{"echo": "hi"}',
    model: str = "claude-sonnet-5",
    stop_reason: str = "end_turn",
) -> MagicMock:
    client = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(type="text", text=text)]
    msg.model = model
    msg.usage.input_tokens = 10
    msg.usage.output_tokens = 5
    msg.id = "msg_test123"
    msg.stop_reason = stop_reason
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


def test_temperature_omitted_when_unset() -> None:
    """Phase 7C.1 regression — reproduces the exact request shape that
    caused a real 400 invalid_request_error ('`temperature` is deprecated
    for this model.') against claude-sonnet-5 in cold_start.py's discovery
    call, which never set temperature and so inherited AIRequest's old
    hardcoded 0.3 default. temperature=None (the current default) must
    never appear in the SDK call kwargs at all."""
    client = _mock_client()
    p = _provider(client=client)
    req = _req(max_tokens=512)  # temperature left unset -> None
    assert req.temperature is None
    p.complete(req)
    call_kwargs = client.messages.create.call_args[1]
    assert "temperature" not in call_kwargs


def test_temperature_forwarded_when_a_caller_explicitly_wants_one() -> None:
    """A caller with a genuine reason to want a specific value (e.g.
    research/extractor.py's low-temperature claim extraction) must still
    get it forwarded unchanged."""
    client = _mock_client()
    p = _provider(client=client)
    req = _req(temperature=0.0)
    p.complete(req)
    call_kwargs = client.messages.create.call_args[1]
    assert call_kwargs["temperature"] == 0.0


def test_extended_thinking_block_before_text_is_handled() -> None:
    """Phase 7C.1 regression — reproduces the real response shape
    claude-sonnet-5 returns for a complex request (confirmed via a real
    cold-start call): content[0] is a ThinkingBlock (no .text attribute)
    and the real text is a later block. complete() must scan by block
    type, not assume content[0] is always the text block — the exact bug
    that broke cold_start.py's discovery call with
    'ThinkingBlock' object has no attribute 'text'."""
    client = MagicMock()
    msg = MagicMock()
    msg.content = [
        MagicMock(type="thinking", thinking="reasoning..."),
        MagicMock(type="text", text='{"echo": "hi"}'),
    ]
    msg.model = "claude-sonnet-5"
    msg.usage.input_tokens = 10
    msg.usage.output_tokens = 5
    msg.id = "msg_test123"
    msg.stop_reason = "end_turn"
    client.messages.create.return_value = msg
    p = _provider(client=client)
    resp = p.complete(_req())
    assert resp.raw_text == '{"echo": "hi"}'


def test_no_text_block_at_all_raises_invalid_structured_response() -> None:
    client = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(type="thinking", thinking="reasoning only, no answer")]
    msg.model = "claude-sonnet-5"
    msg.usage.input_tokens = 10
    msg.usage.output_tokens = 5
    msg.id = "msg_test123"
    msg.stop_reason = "end_turn"
    client.messages.create.return_value = msg
    p = _provider(client=client)
    with pytest.raises(InvalidStructuredResponseError):
        p.complete(_req())


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


# ── Bounded structured-response diagnostics (Phase 18H.B) ────────────────────


def test_malformed_json_carries_bounded_raw_text_excerpt() -> None:
    """A: malformed JSON — the exception now carries the raw text (bounded)
    instead of discarding it, without which this exact bug (a truncated
    cold-start response) went undiagnosed for a full audit cycle."""
    p = _provider(client=_mock_client("not json at all"))
    with pytest.raises(InvalidStructuredResponseError) as exc_info:
        p.complete(_req(response_schema=EchoOutput))
    assert exc_info.value.raw_text_excerpt == "not json at all"


def test_truncated_like_response_stop_reason_preserved() -> None:
    """B/C: a response whose JSON is cut off mid-object (simulating a
    max_tokens truncation) still fails parsing, but now the exception
    preserves both the excerpt and stop_reason='max_tokens' — the wrapper's
    own stop_reason value, not invented — so a future failure is
    diagnosable as truncation rather than a generic JSONDecodeError."""
    truncated = '{"probes": [{"query": "personal finance basics", "market_regi'
    p = _provider(client=_mock_client(truncated, stop_reason="max_tokens"))
    with pytest.raises(InvalidStructuredResponseError) as exc_info:
        p.complete(_req(response_schema=EchoOutput))
    assert exc_info.value.stop_reason == "max_tokens"
    assert exc_info.value.raw_text_excerpt == truncated


def test_schema_mismatch_also_carries_diagnostics() -> None:
    """Valid JSON, wrong shape — diagnostics are attached on the schema-
    validation failure path too, not only the JSON-decode path."""
    p = _provider(client=_mock_client('{"wrong": "field"}', stop_reason="end_turn"))
    with pytest.raises(InvalidStructuredResponseError) as exc_info:
        p.complete(_req(response_schema=EchoOutput))
    assert exc_info.value.raw_text_excerpt == '{"wrong": "field"}'
    assert exc_info.value.stop_reason == "end_turn"


def test_diagnostic_excerpt_is_bounded_not_unbounded() -> None:
    """D: an oversized response never produces an unbounded diagnostic —
    capped at _DIAGNOSTIC_EXCERPT_CHARS (2000), matching the "bounded, never
    an unlimited provider-output dump" requirement."""
    from app.ai.claude import _DIAGNOSTIC_EXCERPT_CHARS

    oversized = "x" * 10_000  # not valid JSON either way
    p = _provider(client=_mock_client(oversized))
    with pytest.raises(InvalidStructuredResponseError) as exc_info:
        p.complete(_req(response_schema=EchoOutput))
    assert exc_info.value.raw_text_excerpt is not None
    assert len(exc_info.value.raw_text_excerpt) == _DIAGNOSTIC_EXCERPT_CHARS


def test_prose_without_fence_still_raises_no_tolerant_fallback() -> None:
    """Confirms Part 10: no tolerant prose-extraction fallback was added —
    a response that is neither clean JSON nor fenced JSON still fails
    closed, it does not get silently coerced into something parseable."""
    prose_no_fence = 'Sure, here you go: {"echo": "hi"} — hope that helps!'
    p = _provider(client=_mock_client(prose_no_fence))
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


# ── Phase 7C.6 — timeout/connection retry hardening ──────────────────────────
#
# Root cause: with_retry() and max_retries=3 already existed and were
# already wired into complete(), but RequestTimeoutError (the translation
# of a real anthropic.APITimeoutError) was never in _RETRYABLE, so a
# timeout propagated on the first attempt with zero retries — reproduced
# twice live, hours apart. These tests exercise the REAL SDK exception ->
# _translate() -> with_retry() path (not the app-error-direct-raise style
# above) so the fix is proven at the exact boundary that failed live.


def _httpx_request() -> Any:
    import httpx

    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def test_a_timeout_then_success_is_one_logical_call_with_one_retry() -> None:
    """§5.A — first call times out, second succeeds: one logical recovery,
    discovery continues, exactly one retry recorded."""
    import anthropic

    client = _mock_client()
    good_response = client.messages.create.return_value
    call_count = 0

    def side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise anthropic.APITimeoutError(request=_httpx_request())
        return good_response

    client.messages.create.side_effect = side_effect
    no_sleep = MagicMock()
    p = ClaudeProvider("key", client=client, max_retries=3, sleep_fn=no_sleep)

    resp = p.complete(_req())

    assert resp.retry_count == 1
    assert client.messages.create.call_count == 2


def test_b_both_calls_timeout_is_a_clean_final_failure() -> None:
    """§5.B — both attempts time out: RetryExhaustedError, no malformed
    state, no unbounded retry loop (bounded by max_retries)."""
    import anthropic

    client = MagicMock()
    client.messages.create.side_effect = anthropic.APITimeoutError(request=_httpx_request())
    no_sleep = MagicMock()
    p = ClaudeProvider("key", client=client, max_retries=2, sleep_fn=no_sleep)

    with pytest.raises(RetryExhaustedError) as exc_info:
        p.complete(_req())

    assert exc_info.value.attempts == 2
    assert client.messages.create.call_count == 2


def test_c_invalid_request_error_is_never_retried() -> None:
    """§5.C — a real 400 invalid_request_error must not retry."""
    import anthropic
    import httpx

    request = _httpx_request()
    response = httpx.Response(
        400, request=request, json={"error": {"type": "invalid_request_error"}}
    )
    client = MagicMock()
    client.messages.create.side_effect = anthropic.APIStatusError(
        "invalid request", response=response, body={"type": "invalid_request_error"}
    )
    no_sleep = MagicMock()
    p = ClaudeProvider("key", client=client, max_retries=3, sleep_fn=no_sleep)

    from app.ai.errors import ProviderUnavailableError

    with pytest.raises(ProviderUnavailableError):
        p.complete(_req())

    assert client.messages.create.call_count == 1
    assert no_sleep.call_count == 0


def test_d_successful_first_call_makes_exactly_one_provider_call() -> None:
    """§5.D — the ordinary happy path is untouched by the retry fix."""
    client = _mock_client()
    p = ClaudeProvider("key", client=client, max_retries=3, sleep_fn=MagicMock())

    resp = p.complete(_req())

    assert resp.retry_count == 0
    assert client.messages.create.call_count == 1


def test_connection_error_is_retried_and_reclassified_as_transient() -> None:
    """A genuine (unambiguous) network-level connection failure retries,
    unlike the generic ProviderUnavailableError fallback bucket which may
    contain an unclassified 4xx and must stay non-retryable."""
    import anthropic

    client = _mock_client()
    good_response = client.messages.create.return_value
    call_count = 0

    def side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise anthropic.APIConnectionError(request=_httpx_request())
        return good_response

    client.messages.create.side_effect = side_effect
    p = ClaudeProvider("key", client=client, max_retries=3, sleep_fn=MagicMock())

    resp = p.complete(_req())

    assert resp.retry_count == 1
    assert client.messages.create.call_count == 2


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
