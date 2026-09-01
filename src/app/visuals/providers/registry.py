"""Provider registry — cost-ordered fallback without engine branching."""

from __future__ import annotations

from app.visuals.providers.ai_image import AiImageProvider
from app.visuals.providers.base import ProviderCallLog, ProviderCapability, VisualProvider
from app.visuals.providers.pexels import PexelsProvider
from app.visuals.providers.pixabay import PixabayProvider
from app.visuals.providers.wikimedia import WikimediaProvider

__all__ = [
    "ProviderCapability",
    "ProviderCallLog",
    "VisualProvider",
    "build_default_providers",
    "providers_for",
]


def build_default_providers() -> list[VisualProvider]:
    """Every known provider, in preferred order, regardless of availability.

    Availability is checked at call time so a missing key downgrades one
    provider instead of failing the render.
    """
    return [
        PexelsProvider(),
        PixabayProvider(),
        WikimediaProvider(),
        AiImageProvider(),
    ]


def providers_for(
    providers: list[VisualProvider],
    media_type: str,
    *,
    max_cost_units: int | None = None,
) -> list[VisualProvider]:
    """Available providers serving *media_type*, cheapest and lowest tier first."""
    eligible = [
        provider
        for provider in providers
        if media_type in provider.capability.media_types
        and provider.available()
        and (max_cost_units is None or provider.capability.cost_units <= max_cost_units)
    ]
    return sorted(
        eligible,
        key=lambda p: (p.capability.cost_units, p.capability.tier, p.identity),
    )
