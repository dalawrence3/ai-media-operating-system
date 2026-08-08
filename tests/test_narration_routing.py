"""Tests for Phase 6 M6.3B ProviderRouter."""

from __future__ import annotations

import pytest

from app.narration.errors import ProviderSelectionError
from app.narration.fake import (
    FAKE_MODEL_NAME,
    FAKE_PROVIDER_NAME,
    FAKE_VOICE_ID,
)
from app.narration.protocol import TTSRequest
from app.narration.registry import ProviderRegistry, get_default_provider_registry
from app.narration.routing import (
    DefaultProviderRouter,
    ProviderRouter,
    RoutingRequest,
    RoutingResult,
)
from app.narration.selection import ProviderSelectionCriteria


def _request() -> TTSRequest:
    return TTSRequest(
        text="Hello world",
        provider=FAKE_PROVIDER_NAME,
        model=FAKE_MODEL_NAME,
        voice_id=FAKE_VOICE_ID,
        language="en-US",
        speaking_rate=1.0,
        output_format="wav",
        sample_rate_hz=22050,
    )


def _router(registry: ProviderRegistry | None = None) -> DefaultProviderRouter:
    reg = registry or get_default_provider_registry()
    return DefaultProviderRouter(reg)


# ── RoutingRequest ─────────────────────────────────────────────────────────────


def test_routing_request_stores_tts_request() -> None:
    req = _request()
    rr = RoutingRequest(tts_request=req)
    assert rr.tts_request is req


def test_routing_request_default_attempt_zero() -> None:
    rr = RoutingRequest(tts_request=_request())
    assert rr.attempt == 0


def test_routing_request_default_criteria_is_empty() -> None:
    rr = RoutingRequest(tts_request=_request())
    assert rr.criteria.preferred_provider is None


# ── DefaultProviderRouter — protocol ─────────────────────────────────────────


def test_router_satisfies_protocol() -> None:
    assert isinstance(_router(), ProviderRouter)


# ── DefaultProviderRouter — routing ──────────────────────────────────────────


def test_route_returns_routing_result() -> None:
    result = _router().route(RoutingRequest(tts_request=_request()))
    assert isinstance(result, RoutingResult)


def test_route_result_provider_name() -> None:
    result = _router().route(RoutingRequest(tts_request=_request()))
    assert result.provider_name == FAKE_PROVIDER_NAME


def test_route_result_provider_is_tts_provider() -> None:
    from app.narration.protocol import TTSProvider

    result = _router().route(RoutingRequest(tts_request=_request()))
    assert isinstance(result.provider, TTSProvider)


def test_route_result_metadata_matches_provider() -> None:
    result = _router().route(RoutingRequest(tts_request=_request()))
    assert result.metadata.provider_name == result.provider_name


def test_route_with_explicit_preferred_provider() -> None:
    criteria = ProviderSelectionCriteria(preferred_provider=FAKE_PROVIDER_NAME)
    result = _router().route(RoutingRequest(tts_request=_request(), criteria=criteria))
    assert result.provider_name == FAKE_PROVIDER_NAME


# ── DefaultProviderRouter — no match ─────────────────────────────────────────


def test_route_no_match_raises_selection_error() -> None:
    criteria = ProviderSelectionCriteria(required_output_format="flac")
    with pytest.raises(ProviderSelectionError):
        _router().route(RoutingRequest(tts_request=_request(), criteria=criteria))


# ── Empty registry ────────────────────────────────────────────────────────────


def test_route_empty_registry_raises() -> None:
    router = DefaultProviderRouter(ProviderRegistry())
    with pytest.raises(ProviderSelectionError):
        router.route(RoutingRequest(tts_request=_request()))


# ── Custom selector injection ─────────────────────────────────────────────────


def test_router_uses_injected_selector() -> None:
    from app.narration.selection import DefaultProviderSelector

    selector = DefaultProviderSelector()
    router = DefaultProviderRouter(get_default_provider_registry(), selector=selector)
    result = router.route(RoutingRequest(tts_request=_request()))
    assert result.provider_name == FAKE_PROVIDER_NAME
