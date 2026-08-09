"""Tests for Phase 6 M6.3B ProviderMetadata."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.narration.capabilities import ProviderCapabilities, ProviderFeatureFlags
from app.narration.constants import PROVIDER_LANGUAGE_WILDCARD
from app.narration.fake import FAKE_METADATA, FAKE_MODEL_NAME, FAKE_PROVIDER_NAME
from app.narration.metadata import ProviderMetadata


def _make_caps() -> ProviderCapabilities:
    return ProviderCapabilities(
        supported_output_formats=frozenset({"wav"}),
        supported_languages=frozenset({PROVIDER_LANGUAGE_WILDCARD}),
        supported_sample_rates_hz=frozenset({22050}),
        min_speaking_rate=0.5,
        max_speaking_rate=2.0,
        max_characters_per_request=10_000,
        feature_flags=ProviderFeatureFlags(),
    )


def _make_meta(*, sdk_name=None, sdk_version=None, api_version=None) -> ProviderMetadata:
    caps = _make_caps()
    return ProviderMetadata(
        provider_name="test-provider",
        provider_version="1.0.0",
        model_id="test/model",
        api_version=api_version,
        sdk_name=sdk_name,
        sdk_version=sdk_version,
        capabilities=caps,
        feature_flags=ProviderFeatureFlags(),
    )


# ── Basic field access ────────────────────────────────────────────────────────


def test_provider_name_stored() -> None:
    meta = _make_meta()
    assert meta.provider_name == "test-provider"


def test_model_id_stored() -> None:
    meta = _make_meta()
    assert meta.model_id == "test/model"


def test_provider_version_stored() -> None:
    meta = _make_meta()
    assert meta.provider_version == "1.0.0"


def test_api_version_none_by_default() -> None:
    meta = _make_meta()
    assert meta.api_version is None


def test_api_version_stored() -> None:
    meta = _make_meta(api_version="v1")
    assert meta.api_version == "v1"


def test_sdk_name_none_for_local() -> None:
    meta = _make_meta()
    assert meta.sdk_name is None


def test_sdk_version_none_for_local() -> None:
    meta = _make_meta()
    assert meta.sdk_version is None


# ── is_local ──────────────────────────────────────────────────────────────────


def test_is_local_true_when_no_sdk() -> None:
    meta = _make_meta(sdk_name=None)
    assert meta.is_local()


def test_is_local_false_when_sdk_present() -> None:
    meta = _make_meta(sdk_name="some-sdk", sdk_version="2.0.0")
    assert not meta.is_local()


# ── to_reproducibility_dict ───────────────────────────────────────────────────


def test_reproducibility_dict_has_required_keys() -> None:
    meta = _make_meta()
    d = meta.to_reproducibility_dict()
    required = {
        "provider_name",
        "provider_version",
        "model_id",
        "api_version",
        "sdk_name",
        "sdk_version",
        "capabilities",
    }
    assert required.issubset(d.keys())


def test_reproducibility_dict_capabilities_is_dict() -> None:
    meta = _make_meta()
    d = meta.to_reproducibility_dict()
    assert isinstance(d["capabilities"], dict)


def test_reproducibility_dict_provider_name_matches() -> None:
    meta = _make_meta()
    assert meta.to_reproducibility_dict()["provider_name"] == "test-provider"


def test_reproducibility_dict_none_fields_preserved() -> None:
    meta = _make_meta(api_version=None, sdk_name=None, sdk_version=None)
    d = meta.to_reproducibility_dict()
    assert d["api_version"] is None
    assert d["sdk_name"] is None
    assert d["sdk_version"] is None


# ── Frozen (immutability) ─────────────────────────────────────────────────────


def test_metadata_is_frozen() -> None:
    meta = _make_meta()
    with pytest.raises(FrozenInstanceError):
        meta.provider_name = "other"  # type: ignore[misc]


# ── FAKE_METADATA ─────────────────────────────────────────────────────────────


def test_fake_metadata_provider_name() -> None:
    assert FAKE_METADATA.provider_name == FAKE_PROVIDER_NAME


def test_fake_metadata_model_id() -> None:
    assert FAKE_METADATA.model_id == FAKE_MODEL_NAME


def test_fake_metadata_is_local() -> None:
    assert FAKE_METADATA.is_local()


def test_fake_metadata_reproducibility_dict_is_self_contained() -> None:
    d = FAKE_METADATA.to_reproducibility_dict()
    assert d["provider_name"] == FAKE_PROVIDER_NAME
    assert d["model_id"] == FAKE_MODEL_NAME
    assert d["sdk_name"] is None
