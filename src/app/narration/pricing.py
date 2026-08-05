"""Phase 6 M6.2: TTS pricing registry."""

from __future__ import annotations

from app.narration.errors import UnknownTTSModelPricingError
from app.narration.fake import FAKE_MODEL_NAME

# Price per 1,000 characters billed, in USD.
# The fake provider is always 0.0 (no cost).
_PRICING: dict[str, float] = {
    FAKE_MODEL_NAME: 0.0,
}


class TTSPricingRegistry:
    """Looks up TTS pricing by model identifier."""

    def __init__(self, pricing: dict[str, float] | None = None) -> None:
        self._pricing: dict[str, float] = pricing if pricing is not None else dict(_PRICING)

    def price_per_1k_chars(self, model: str) -> float:
        if model not in self._pricing:
            raise UnknownTTSModelPricingError(model)
        return self._pricing[model]

    def estimate_cost(self, model: str, characters_billed: int) -> float:
        rate = self.price_per_1k_chars(model)
        return rate * characters_billed / 1000.0

    def register(self, model: str, price_per_1k_chars: float) -> None:
        self._pricing[model] = price_per_1k_chars


_default_registry = TTSPricingRegistry()


def get_default_registry() -> TTSPricingRegistry:
    return _default_registry
