"""Tests for the pricing registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai.errors import UnknownModelPricingError
from app.ai.pricing import PricingRegistry


def test_known_model_nonzero_cost() -> None:
    pr = PricingRegistry()
    cost = pr.estimate_cost("claude-sonnet-5", input_tokens=1000, output_tokens=500)
    assert cost > 0


def test_zero_tokens_zero_cost() -> None:
    pr = PricingRegistry()
    cost = pr.estimate_cost("claude-sonnet-5", input_tokens=0, output_tokens=0)
    assert cost == 0.0


def test_fake_model_is_free() -> None:
    pr = PricingRegistry()
    cost = pr.estimate_cost("fake", input_tokens=10_000, output_tokens=10_000)
    assert cost == 0.0


def test_fake_prefix_model_is_free() -> None:
    pr = PricingRegistry()
    cost = pr.estimate_cost(
        "fake/FAKE_PROVIDER_NOT_PRODUCTION", input_tokens=100, output_tokens=100
    )
    assert cost == 0.0


def test_is_fake_exact_match() -> None:
    pr = PricingRegistry()
    assert pr.is_fake("fake") is True


def test_is_fake_prefix_match() -> None:
    pr = PricingRegistry()
    assert pr.is_fake("fake/FAKE_PROVIDER_NOT_PRODUCTION") is True


def test_is_fake_production_model() -> None:
    pr = PricingRegistry()
    assert pr.is_fake("claude-sonnet-5") is False


def test_unknown_production_model_raises() -> None:
    pr = PricingRegistry()
    with pytest.raises(UnknownModelPricingError) as exc_info:
        pr.estimate_cost("unknown-model-xyz", input_tokens=1000, output_tokens=500)
    assert exc_info.value.model == "unknown-model-xyz"


def test_unknown_model_error_message_contains_model_name() -> None:
    pr = PricingRegistry()
    with pytest.raises(UnknownModelPricingError, match="unknown-model-xyz"):
        pr.estimate_cost("unknown-model-xyz", input_tokens=0, output_tokens=0)


def test_version_and_effective_date_accessible() -> None:
    pr = PricingRegistry()
    assert pr.version != ""
    assert pr.effective_date != ""


def test_custom_pricing_override() -> None:
    custom = {
        "registry_version": "test",
        "effective_date": "2025-01-01",
        "models": {
            "my-model": {"input_per_mtok": 10.00, "output_per_mtok": 20.00},
        },
    }
    pr = PricingRegistry(pricing=custom)
    cost = pr.estimate_cost("my-model", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(30.0)
    assert pr.version == "test"


def test_pricing_file_override(tmp_path: Path) -> None:
    pricing = {
        "registry_version": "file-test",
        "effective_date": "2025-06-01",
        "models": {"test-model": {"input_per_mtok": 1.0, "output_per_mtok": 2.0}},
    }
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(pricing))

    import os

    old = os.environ.pop("ACE_AI_PRICING_FILE", None)
    try:
        os.environ["ACE_AI_PRICING_FILE"] = str(p)
        pr = PricingRegistry()
        assert pr.version == "file-test"
    finally:
        if old is not None:
            os.environ["ACE_AI_PRICING_FILE"] = old
        else:
            os.environ.pop("ACE_AI_PRICING_FILE", None)
