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
    preferred_identities: tuple[str, ...] = (),
) -> list[VisualProvider]:
    """Available providers serving *media_type*, cheapest and lowest tier first.

    `preferred_identities` (Phase 2, §4 — source suitability) is a pure
    ordering mechanism, not a policy: named providers are moved ahead of the
    normal cost/tier sort, but the set of eligible providers and their
    relative order otherwise is unchanged. Empty by default, which is
    identical to the pre-existing behaviour.

    Deliberately does NOT decide here which providers suit which content
    domain — no domain/subject mapping lives in this registry (the module
    docstring's whole point). A caller with a genuine, general
    domain-suitability signal passes the providers it prefers; this function
    only guarantees they sort first among the otherwise-eligible set.
    """
    eligible = [
        provider
        for provider in providers
        if media_type in provider.capability.media_types
        and provider.available()
        and (max_cost_units is None or provider.capability.cost_units <= max_cost_units)
    ]
    preferred_rank = {identity: i for i, identity in enumerate(preferred_identities)}
    return sorted(
        eligible,
        key=lambda p: (
            preferred_rank.get(p.identity, len(preferred_rank)),
            p.capability.cost_units,
            p.capability.tier,
            p.identity,
        ),
    )
