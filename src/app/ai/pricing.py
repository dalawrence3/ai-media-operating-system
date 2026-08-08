"""Pricing registry for estimating AI call costs.

Prices are estimates only.  They carry an explicit version and effective date
so callers know how stale the data is.  Never treat these as authoritative
billing figures — always verify with the provider's current pricing page.

Override the built-in registry by setting ACE_AI_PRICING_FILE to a JSON file
with the same structure as _BUILTIN_PRICING.

Cost semantics
--------------
``estimate_cost`` returns one of three outcomes:
  - ``0.0``   — model is the built-in fake provider (no real API call made).
  - ``> 0.0`` — calculated cost for a known production model.
  - raises ``UnknownModelPricingError`` — model is not fake and not in the
                registry; callers must not assume the cost is free.
"""

from __future__ import annotations

import json
import os
from typing import Any

from app.ai.errors import UnknownModelPricingError

_BUILTIN_PRICING: dict[str, Any] = {
    "registry_version": "2025-07",
    "effective_date": "2025-07-01",
    "note": (
        "Estimates only. Verify current pricing at https://www.anthropic.com/pricing. "
        "Input/output costs are USD per million tokens."
    ),
    "models": {
        "claude-sonnet-5": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
        "claude-opus-4-8": {"input_per_mtok": 15.00, "output_per_mtok": 75.00},
        "claude-haiku-4-5-20251001": {"input_per_mtok": 0.80, "output_per_mtok": 4.00},
        # Fake provider: explicitly free — makes no real API call.
        "fake": {"input_per_mtok": 0.00, "output_per_mtok": 0.00},
    },
}

_ZERO_RATE: dict[str, float] = {"input_per_mtok": 0.00, "output_per_mtok": 0.00}

# Any model whose name starts with this prefix is treated as the fake provider.
_FAKE_PREFIX = "fake"


class PricingRegistry:
    """Load and query a provider pricing table."""

    def __init__(self, pricing: dict[str, Any] | None = None) -> None:
        if pricing is not None:
            self._data = pricing
        else:
            path_env = os.environ.get("ACE_AI_PRICING_FILE", "")
            if path_env:
                with open(path_env) as fh:
                    self._data = json.load(fh)
            else:
                self._data = _BUILTIN_PRICING

    @property
    def version(self) -> str:
        return str(self._data.get("registry_version", "unknown"))

    @property
    def effective_date(self) -> str:
        return str(self._data.get("effective_date", "unknown"))

    def is_fake(self, model: str) -> bool:
        """Return True if the model is a fake/test provider (no real API call)."""
        return model == _FAKE_PREFIX or model.startswith(_FAKE_PREFIX + "/")

    def estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Return estimated USD cost in dollars.

        Returns ``0.0`` for fake-provider models.
        Raises ``UnknownModelPricingError`` for production models not in the registry.
        """
        models: dict[str, Any] = self._data.get("models", {})

        if self.is_fake(model):
            # Use the explicit fake entry if present; otherwise it is free by definition.
            rates = models.get(_FAKE_PREFIX, _ZERO_RATE)
            return (
                input_tokens * rates["input_per_mtok"] / 1_000_000
                + output_tokens * rates["output_per_mtok"] / 1_000_000
            )

        rates = models.get(model)
        if rates is None:
            raise UnknownModelPricingError(model)

        return (
            input_tokens * rates["input_per_mtok"] / 1_000_000
            + output_tokens * rates["output_per_mtok"] / 1_000_000
        )
