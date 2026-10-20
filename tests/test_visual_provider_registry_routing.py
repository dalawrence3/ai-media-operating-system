"""Phase 2 — source-suitability routing mechanism (§4).

`providers_for(preferred_identities=...)` is a pure ordering mechanism: it
must never change which providers are eligible, only their order among the
eligible set, and it must be a no-op when no preference is given (the
pre-existing behaviour). This module contains no domain->provider mapping —
see providers/registry.py's docstring for why that decision is deliberately
left to a caller with a real signal, not made here.
"""

from __future__ import annotations

from app.visuals.constants import MEDIA_PHOTO, MEDIA_VIDEO
from app.visuals.providers.base import ProviderCapability
from app.visuals.providers.registry import providers_for


class _StubProvider:
    def __init__(self, identity: str, *, cost_units: int = 1, tier: int = 1, media=(MEDIA_VIDEO,)):
        self.identity = identity
        self.capability = ProviderCapability(
            media_types=media, tier=tier, cost_units=cost_units, supports_orientation=True
        )

    def available(self) -> bool:
        return True


class TestProviderRoutingMechanism:
    def test_no_preference_keeps_the_ordinary_cost_tier_order(self):
        providers = [
            _StubProvider("expensive", cost_units=3),
            _StubProvider("cheap", cost_units=0),
        ]
        ordered = providers_for(providers, MEDIA_VIDEO)
        assert [p.identity for p in ordered] == ["cheap", "expensive"]

    def test_preferred_identity_sorts_ahead_of_cheaper_alternatives(self):
        providers = [
            _StubProvider("cheap", cost_units=0),
            _StubProvider("archival", cost_units=1),
        ]
        ordered = providers_for(providers, MEDIA_VIDEO, preferred_identities=("archival",))
        assert [p.identity for p in ordered] == ["archival", "cheap"]

    def test_preference_never_makes_an_ineligible_provider_eligible(self):
        """Ordering only — a preferred provider that does not support this
        media type, or is unavailable, must still be excluded entirely."""
        providers = [_StubProvider("photo_only", media=(MEDIA_PHOTO,))]
        ordered = providers_for(providers, MEDIA_VIDEO, preferred_identities=("photo_only",))
        assert ordered == []

    def test_multiple_preferences_are_applied_in_order(self):
        providers = [
            _StubProvider("c", cost_units=0),
            _StubProvider("a", cost_units=2),
            _StubProvider("b", cost_units=1),
        ]
        ordered = providers_for(providers, MEDIA_VIDEO, preferred_identities=("a", "b"))
        assert [p.identity for p in ordered] == ["a", "b", "c"]

    def test_unpreferred_providers_keep_their_relative_cost_tier_order(self):
        providers = [
            _StubProvider("preferred", cost_units=5),
            _StubProvider("mid", cost_units=1),
            _StubProvider("cheap", cost_units=0),
        ]
        ordered = providers_for(providers, MEDIA_VIDEO, preferred_identities=("preferred",))
        assert [p.identity for p in ordered] == ["preferred", "cheap", "mid"]
