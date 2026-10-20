"""Anthropic Claude provider.

The ``anthropic`` SDK is imported at module level so its exception types are
available for the error-mapping table.  The SDK is always present because it
is a declared project dependency.

The *client* constructor parameter accepts a pre-built (or mocked) client
object so automated tests never make real network calls.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

import anthropic

from app.ai.errors import (
    InvalidStructuredResponseError,
    MissingCredentialsError,
    ProviderUnavailableError,
    RateLimitError,
    RequestTimeoutError,
    RetryExhaustedError,
    TransientProviderError,
)
from app.ai.provider import AIRequest, AIResponse
from app.ai.retry import with_retry

# Exceptions that warrant a retry attempt.
#
# Phase 7C.6 — two independent live recovery canaries, hours apart, both
# failed on a real anthropic.APITimeoutError. with_retry() and max_retries=3
# already existed and were already wired into complete() below, but
# RequestTimeoutError was never in this tuple, so a timeout propagated on
# the FIRST attempt with zero retries regardless of max_retries — the
# retry infrastructure was real but silently inert for the exact failure
# mode observed live. APIConnectionError (network-level, unambiguous) is
# reclassified to TransientProviderError below for the same reason: a
# genuine connection failure should retry, but ProviderUnavailableError
# also carries _translate's ambiguous catch-all fallback (which can be an
# unclassified 4xx, e.g. invalid_request) and must stay non-retryable —
# so ProviderUnavailableError itself is deliberately NOT added here.
_RETRYABLE = (RateLimitError, TransientProviderError, RequestTimeoutError)


def _translate(exc: anthropic.APIError) -> AIError:  # type: ignore[name-defined]
    """Map an Anthropic SDK exception to the application error hierarchy."""
    if isinstance(exc, anthropic.AuthenticationError):
        return MissingCredentialsError(f"Anthropic authentication failed: {type(exc).__name__}")
    if isinstance(exc, anthropic.RateLimitError):
        return RateLimitError(f"Anthropic rate limit: {type(exc).__name__}")
    if isinstance(exc, anthropic.APITimeoutError):
        return RequestTimeoutError(f"Anthropic request timed out: {type(exc).__name__}")
    if isinstance(exc, anthropic.APIConnectionError):
        # Unambiguous network-level failure (never a 4xx) — retryable,
        # unlike the generic ProviderUnavailableError fallback below.
        return TransientProviderError(f"Anthropic connection error: {type(exc).__name__}")
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500:
        return TransientProviderError(
            f"Anthropic server error {exc.status_code}: {type(exc).__name__}"
        )
    return ProviderUnavailableError(f"Anthropic API error: {type(exc).__name__}")


def _extract_text(content: list[Any]) -> str:
    """Find the actual text block in an Anthropic response's content list.

    Phase 7C.1 — content[0] is NOT always the text block: claude-sonnet-5
    (confirmed via a real cold-start call) auto-enables extended thinking
    for complex reasoning requests, putting a ThinkingBlock (no `.text`
    attribute) at content[0] and the real text at a LATER index. Scanning
    for `type == "text"` instead of assuming a fixed position is correct
    regardless of how many non-text blocks (thinking, redacted_thinking,
    ...) precede it, and for any provider response shape that never
    included one at all.
    """
    for block in content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise InvalidStructuredResponseError(
        "Claude response contains no text block "
        f"(block types: {[getattr(b, 'type', type(b).__name__) for b in content]})"
    )


# Re-export so callers can catch AIError without importing errors directly.
from app.ai.errors import AIError  # noqa: E402

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)

# Bounded excerpt length for InvalidStructuredResponseError.raw_text_excerpt —
# long enough to see whether a failure was truncation, prose, or malformed
# syntax; short enough to never become an unbounded log/DB dump.
_DIAGNOSTIC_EXCERPT_CHARS = 2000


def _strip_json_fences(text: str) -> str:
    """Extract JSON from inside a markdown code fence, if one is present.

    Handles ```json...``` and ```...``` wrappers that some models emit despite
    instructions to return plain JSON. Returns the original text unchanged when
    no fence is detected, so valid plain-JSON responses are unaffected.

    Uses search (not match) so preamble text before the fence is ignored.
    """
    m = _JSON_FENCE_RE.search(text)
    return m.group(1).strip() if m else text


class ClaudeProvider:
    """Provider implementation backed by the Anthropic Messages API."""

    name = "claude"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "claude-sonnet-5",
        timeout: float = 30.0,
        max_retries: int = 3,
        sleep_fn: Callable[[float], None] | None = None,
        client: Any = None,
    ) -> None:
        if not api_key:
            raise MissingCredentialsError(
                "ANTHROPIC_API_KEY is required for ClaudeProvider. "
                "Set the ACE_ANTHROPIC_API_KEY environment variable."
            )
        self._model = model
        self._max_retries = max_retries
        self._sleep_fn = sleep_fn or time.sleep
        # Accept an injected client (used in tests to avoid real API calls).
        self._client = (
            client if client is not None else anthropic.Anthropic(api_key=api_key, timeout=timeout)
        )

    def _call_api(self, request: AIRequest) -> Any:
        """Issue one (non-retried) API call and translate SDK exceptions."""
        kwargs: dict[str, Any] = {
            "model": request.model or self._model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
        }
        # Phase 7C.1 — only forward temperature when a caller explicitly
        # asked for one. Some current models (confirmed: claude-sonnet-5)
        # reject the parameter outright as deprecated; omitting it entirely
        # when unset is always safe (it just means "use the model's own
        # default"), and every caller that genuinely wants a specific value
        # still gets it forwarded unchanged.
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        try:
            return self._client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            raise _translate(exc) from exc

    def complete(self, request: AIRequest) -> AIResponse:
        t0 = time.monotonic()

        try:
            raw_response, retry_count = with_retry(
                lambda: self._call_api(request),
                max_attempts=self._max_retries,
                retryable=_RETRYABLE,
                backoff_base=1.0,
                sleep_fn=self._sleep_fn,
            )
        except RetryExhaustedError:
            raise

        duration_ms = max(1, int((time.monotonic() - t0) * 1000))
        raw_text = _extract_text(raw_response.content)

        parsed = None
        if request.response_schema is not None:
            try:
                data = json.loads(_strip_json_fences(raw_text))
            except json.JSONDecodeError as exc:
                raise InvalidStructuredResponseError(
                    f"Claude response is not valid JSON: {type(exc).__name__}",
                    raw_text_excerpt=raw_text[:_DIAGNOSTIC_EXCERPT_CHARS],
                    stop_reason=raw_response.stop_reason,
                ) from exc
            try:
                parsed = request.response_schema.model_validate(data)
            except Exception as exc:
                raise InvalidStructuredResponseError(
                    f"Claude response failed schema validation: {type(exc).__name__}",
                    raw_text_excerpt=raw_text[:_DIAGNOSTIC_EXCERPT_CHARS],
                    stop_reason=raw_response.stop_reason,
                ) from exc

        return AIResponse(
            raw_text=raw_text,
            provider_name=self.name,
            model=raw_response.model,
            input_tokens=raw_response.usage.input_tokens,
            output_tokens=raw_response.usage.output_tokens,
            duration_ms=duration_ms,
            retry_count=retry_count,
            parsed=parsed,
            request_id=raw_response.id,
            stop_reason=raw_response.stop_reason,
        )
