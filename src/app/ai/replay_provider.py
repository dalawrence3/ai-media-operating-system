"""Replay provider for replaying a confirmed real-AI eligibility assessment.

This provider replays the score, fit_label, and rationale from a prior real
Anthropic call, preserving its source for audit purposes.  It differs from
FakeProvider in two ways:
  1. provider_name = "replay" — distinct from "fake", so disposition tracking
     records "replay_prior_real_call" instead of "fake_provider_test".
  2. source — caller must supply the originating call reference (e.g.
     "phase_16c4_confirmed_2026-08-22") so the DB record is traceable.

Use this when:
  - A real Anthropic call already confirmed eligibility in a prior phase.
  - Rerunning the call would waste tokens and risk a different stochastic result.
  - The caller has auditable evidence that the prior result is still valid.

NEVER use ReplayEligibilityProvider when:
  - The channel profile has changed since the original call.
  - The opportunity topic or summary has been materially revised.
  - The prior call used a FakeProvider itself.
"""

from __future__ import annotations

import json
import time

from app.ai.errors import InvalidStructuredResponseError
from app.ai.provider import AIRequest, AIResponse


class ReplayEligibilityProvider:
    """Replays a confirmed prior real-AI semantic fit result.

    Parameters
    ----------
    score:
        The semantic fit score from the original call (0.0–1.0).
    fit_label:
        The fit_label string from the original call.
    rationale:
        The rationale text from the original call.
    source:
        Traceable reference to the original call, e.g.
        "phase_16c4_confirmed_2026-08-22".  Stored in the rationale suffix
        so the DB record is auditable.
    """

    provider_name = "replay"

    def __init__(
        self,
        *,
        score: float,
        fit_label: str,
        rationale: str,
        source: str,
    ) -> None:
        self._score = score
        self._fit_label = fit_label
        self._rationale = rationale
        self._source = source
        self._output = json.dumps(
            {
                "score": self._score,
                "fit_label": self._fit_label,
                "rationale": f"{self._rationale} [replay:{self._source}]",
            }
        )

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
                    f"[ReplayEligibilityProvider] output is not valid JSON: {exc}"
                ) from exc
            try:
                parsed = request.response_schema.model_validate(data)
            except Exception as exc:
                raise InvalidStructuredResponseError(
                    f"[ReplayEligibilityProvider] output failed schema validation: {exc}"
                ) from exc

        return AIResponse(
            raw_text=raw,
            provider_name=self.provider_name,
            model=f"replay/{self._source}",
            input_tokens=0,
            output_tokens=0,
            duration_ms=duration_ms,
            retry_count=0,
            parsed=parsed,
            request_id=None,
            stop_reason="end_turn",
        )
