"""Tests for Phase 6 M6.3B ProviderConfig and ProviderConfigRegistry."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.narration.config import ProviderConfig, ProviderConfigRegistry
from app.narration.errors import ProviderConfigError
from app.narration.fake import FAKE_MODEL_NAME, FAKE_PROVIDER_CONFIG, FAKE_PROVIDER_NAME


def _config(**overrides) -> ProviderConfig:
    defaults = dict(
        provider_name="test",
        model_id="test/model",
        voice_id="voice-1",
        language="en-US",
        speaking_rate=1.0,
        output_format="wav",
        sample_rate_hz=22050,
    )
    defaults.update(overrides)
    return ProviderConfig(**defaults)


# ── Validation in __post_init__ ───────────────────────────────────────────────


def test_empty_provider_name_raises() -> None:
    with pytest.raises(ProviderConfigError, match="provider_name"):
        _config(provider_name="")


def test_empty_model_id_raises() -> None:
    with pytest.raises(ProviderConfigError, match="model_id"):
        _config(model_id="")


def test_zero_speaking_rate_raises() -> None:
    with pytest.raises(ProviderConfigError, match="speaking_rate"):
        _config(speaking_rate=0.0)


def test_negative_speaking_rate_raises() -> None:
    with pytest.raises(ProviderConfigError, match="speaking_rate"):
        _config(speaking_rate=-0.1)


def test_zero_sample_rate_raises() -> None:
    with pytest.raises(ProviderConfigError, match="sample_rate_hz"):
        _config(sample_rate_hz=0)


def test_valid_config_created() -> None:
    config = _config()
    assert config.provider_name == "test"
    assert config.model_id == "test/model"


def test_optional_fields_default_to_none() -> None:
    config = _config()
    assert config.style is None
    assert config.stability is None
    assert config.similarity_boost is None


def test_to_dict_has_required_keys() -> None:
    d = _config().to_dict()
    assert "provider_name" in d
    assert "model_id" in d
    assert "voice_id" in d
    assert "speaking_rate" in d


def test_config_is_frozen() -> None:
    config = _config()
    with pytest.raises(FrozenInstanceError):
        config.provider_name = "other"  # type: ignore[misc]


# ── FAKE_PROVIDER_CONFIG ──────────────────────────────────────────────────────


def test_fake_provider_config_name() -> None:
    assert FAKE_PROVIDER_CONFIG.provider_name == FAKE_PROVIDER_NAME


def test_fake_provider_config_model_id() -> None:
    assert FAKE_PROVIDER_CONFIG.model_id == FAKE_MODEL_NAME


def test_fake_provider_config_speaking_rate_positive() -> None:
    assert FAKE_PROVIDER_CONFIG.speaking_rate > 0.0


def test_fake_provider_config_sample_rate_positive() -> None:
    assert FAKE_PROVIDER_CONFIG.sample_rate_hz > 0


# ── ProviderConfigRegistry ────────────────────────────────────────────────────


def test_register_and_get() -> None:
    registry = ProviderConfigRegistry()
    config = _config()
    registry.register("my-config", config)
    assert registry.get("my-config") is config


def test_get_unknown_name_raises() -> None:
    registry = ProviderConfigRegistry()
    with pytest.raises(ProviderConfigError, match="no-such"):
        registry.get("no-such")


def test_names_empty_initially() -> None:
    registry = ProviderConfigRegistry()
    assert registry.names() == []


def test_names_after_register() -> None:
    registry = ProviderConfigRegistry()
    registry.register("a", _config())
    registry.register("b", _config())
    assert sorted(registry.names()) == ["a", "b"]


def test_is_registered_true_after_register() -> None:
    registry = ProviderConfigRegistry()
    registry.register("key", _config())
    assert registry.is_registered("key")


def test_is_registered_false_before_register() -> None:
    registry = ProviderConfigRegistry()
    assert not registry.is_registered("key")


def test_register_overwrites_existing_name() -> None:
    registry = ProviderConfigRegistry()
    registry.register("key", _config(provider_name="first"))
    registry.register("key", _config(provider_name="second"))
    assert registry.get("key").provider_name == "second"
