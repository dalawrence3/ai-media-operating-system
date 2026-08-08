"""Tests for Phase 6 M6.2 TTS pricing registry."""

from __future__ import annotations

import pytest

from app.narration.errors import UnknownTTSModelPricingError
from app.narration.fake import FAKE_MODEL_NAME
from app.narration.pricing import TTSPricingRegistry, get_default_registry


def test_fake_model_price_is_zero() -> None:
    registry = TTSPricingRegistry()
    assert registry.price_per_1k_chars(FAKE_MODEL_NAME) == 0.0


def test_estimate_cost_fake_is_zero() -> None:
    registry = TTSPricingRegistry()
    assert registry.estimate_cost(FAKE_MODEL_NAME, 1000) == 0.0


def test_estimate_cost_scales_linearly() -> None:
    registry = TTSPricingRegistry({"model-x": 10.0})
    assert registry.estimate_cost("model-x", 1000) == pytest.approx(10.0)
    assert registry.estimate_cost("model-x", 500) == pytest.approx(5.0)


def test_unknown_model_raises() -> None:
    registry = TTSPricingRegistry()
    with pytest.raises(UnknownTTSModelPricingError) as exc_info:
        registry.price_per_1k_chars("nonexistent/model")
    assert "nonexistent/model" in str(exc_info.value)


def test_register_new_model() -> None:
    registry = TTSPricingRegistry()
    registry.register("new/model", 5.0)
    assert registry.price_per_1k_chars("new/model") == 5.0


def test_register_overrides_existing() -> None:
    registry = TTSPricingRegistry()
    registry.register(FAKE_MODEL_NAME, 99.0)
    assert registry.price_per_1k_chars(FAKE_MODEL_NAME) == 99.0


def test_custom_pricing_dict() -> None:
    registry = TTSPricingRegistry({"custom/model": 3.5})
    assert registry.price_per_1k_chars("custom/model") == 3.5
    with pytest.raises(UnknownTTSModelPricingError):
        registry.price_per_1k_chars(FAKE_MODEL_NAME)


def test_default_registry_is_singleton() -> None:
    r1 = get_default_registry()
    r2 = get_default_registry()
    assert r1 is r2


def test_estimate_cost_zero_chars() -> None:
    registry = TTSPricingRegistry()
    assert registry.estimate_cost(FAKE_MODEL_NAME, 0) == 0.0


def test_unknown_model_error_stores_model() -> None:
    registry = TTSPricingRegistry()
    with pytest.raises(UnknownTTSModelPricingError) as exc_info:
        registry.price_per_1k_chars("bad/model")
    assert exc_info.value.model == "bad/model"
