"""Test-infrastructure safety net — default live visual providers are forbidden.

A full backend pytest run once made a genuine HTTPS request to
commons.wikimedia.org (see the Phase 18G investigation report): nothing in
the shared test fixtures guarded against VisualEngine falling back to its
real, network-backed provider list. tests/conftest.py's
`_forbid_default_visual_providers` autouse fixture closes that gap by
patching the exact symbol VisualEngine looks up (app.visuals.engine's own
module-level `build_default_providers`, bound there at import time via
`from app.visuals.providers import build_default_providers`) to raise
instead of construct real providers. These tests prove the guard actually
holds, in both directions, without ever touching the network.
"""

from __future__ import annotations

import pytest

from app.visuals.engine import VisualEngine, VisualEngineConfig
from app.visuals.providers.base import ProviderCapability


class _NoopStubProvider:
    """The minimal shape VisualEngine needs from a provider — no network."""

    identity = "noop-stub"
    capability = ProviderCapability(media_types=(), tier=1, cost_units=0)

    def available(self) -> bool:
        return True

    def search(self, query, *, media_type, limit=10, orientation=None):
        return []

    def download(self, candidate, cache_dir):
        return None


class TestDefaultVisualProvidersAreForbidden:
    def test_omitting_providers_fails_closed_before_any_network_access(self, tmp_path):
        """The exact defect path: VisualEngine(config) with providers
        omitted must raise immediately, not silently reach
        build_default_providers() -> WikimediaProvider -> a live request."""
        config = VisualEngineConfig(
            cache_dir=tmp_path / "cache",
            graphics_dir=tmp_path / "graphics",
        )
        with pytest.raises(RuntimeError, match="Live/default visual providers are forbidden"):
            VisualEngine(config)

    def test_explicit_providers_list_still_works_normally(self, tmp_path):
        """The guard must not block the sanctioned path: an explicit
        providers= list, however short, is never routed through
        build_default_providers()."""
        config = VisualEngineConfig(
            cache_dir=tmp_path / "cache",
            graphics_dir=tmp_path / "graphics",
        )
        engine = VisualEngine(config, providers=[_NoopStubProvider()])
        assert engine is not None

    def test_explicit_empty_providers_list_is_not_the_forbidden_default(self, tmp_path):
        """providers=[] is a deliberate, explicit opt-in (exercising the
        guaranteed local-graphic fallback) and must be distinguished from
        omitting the argument entirely — `is not None`, not truthiness."""
        config = VisualEngineConfig(
            cache_dir=tmp_path / "cache",
            graphics_dir=tmp_path / "graphics",
        )
        engine = VisualEngine(config, providers=[])
        assert engine is not None
