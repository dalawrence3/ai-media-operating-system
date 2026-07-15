"""Deterministic fake provider for tests and offline development.

This provider NEVER contacts any external API.  It is clearly identified as
non-production in every response it returns.  Do not use it to generate
content that will be published.
"""

from __future__ import annotations

import json
import time

from app.ai.errors import InvalidStructuredResponseError
from app.ai.provider import AIRequest, AIResponse

_NOT_PRODUCTION = "FAKE_PROVIDER_NOT_PRODUCTION"


class FakeProvider:
    """Returns a pre-configured JSON string as though it came from an LLM.

    Instantiate with *output* set to a valid JSON string for the expected
    response schema.  The default satisfies :class:`~app.ai.schemas.EchoOutput`.
    """

    name = "fake"

    def __init__(self, output: str = '{"echo": "test"}') -> None:
        self._output = output

    def complete(self, request: AIRequest) -> AIResponse:
        t0 = time.monotonic()
        raw = self._output
        duration_ms = max(1, int((time.monotonic() - t0) * 1000))

        parsed = None
        if request.response_schema is not None:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise InvalidStructuredResponseError(
                    f"[FakeProvider] output is not valid JSON: {exc}"
                ) from exc
            try:
                parsed = request.response_schema.model_validate(data)
            except Exception as exc:
                raise InvalidStructuredResponseError(
                    f"[FakeProvider] output failed schema validation: {exc}"
                ) from exc

        return AIResponse(
            raw_text=raw,
            provider_name=self.name,
            model=f"fake/{_NOT_PRODUCTION}",
            input_tokens=max(1, len(request.user.split())),
            output_tokens=max(1, len(raw.split())),
            duration_ms=duration_ms,
            retry_count=0,
            parsed=parsed,
            request_id=None,
            stop_reason="end_turn",
        )
