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
_RETRYABLE = (RateLimitError, TransientProviderError)


def _translate(exc: anthropic.APIError) -> AIError:  # type: ignore[name-defined]
    """Map an Anthropic SDK exception to the application error hierarchy."""
    if isinstance(exc, anthropic.AuthenticationError):
        return MissingCredentialsError(f"Anthropic authentication failed: {type(exc).__name__}")
    if isinstance(exc, anthropic.RateLimitError):
        return RateLimitError(f"Anthropic rate limit: {type(exc).__name__}")
    if isinstance(exc, anthropic.APITimeoutError):
        return RequestTimeoutError(f"Anthropic request timed out: {type(exc).__name__}")
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderUnavailableError(f"Anthropic connection error: {type(exc).__name__}")
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500:
        return TransientProviderError(
            f"Anthropic server error {exc.status_code}: {type(exc).__name__}"
        )
    return ProviderUnavailableError(f"Anthropic API error: {type(exc).__name__}")


# Re-export so callers can catch AIError without importing errors directly.
from app.ai.errors import AIError  # noqa: E402

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)


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
        try:
            return self._client.messages.create(
                model=request.model or self._model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
                temperature=request.temperature,
            )
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
        raw_text: str = raw_response.content[0].text

        parsed = None
        if request.response_schema is not None:
            try:
                data = json.loads(_strip_json_fences(raw_text))
            except json.JSONDecodeError as exc:
                raise InvalidStructuredResponseError(
                    f"Claude response is not valid JSON: {type(exc).__name__}"
                ) from exc
            try:
                parsed = request.response_schema.model_validate(data)
            except Exception as exc:
                raise InvalidStructuredResponseError(
                    f"Claude response failed schema validation: {type(exc).__name__}"
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
