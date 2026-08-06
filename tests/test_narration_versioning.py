"""Tests for Phase 6 M6.3B ProviderVersion and ProviderVersionRegistry."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.narration.constants import (
    NARRATION_ALGORITHM_VERSION,
    NARRATION_SCHEMA_VERSION,
)
from app.narration.errors import ProviderVersionIncompatibleError
from app.narration.fake import FAKE_PROVIDER_NAME, FAKE_PROVIDER_VERSION
from app.narration.versioning import (
    ProviderVersion,
    ProviderVersionRegistry,
    get_default_version_registry,
)


def _version(
    *,
    provider_name: str = "test",
    integration_version: str = "1.0.0",
    schema_version: str = NARRATION_SCHEMA_VERSION,
    algorithm_version: str = NARRATION_ALGORITHM_VERSION,
) -> ProviderVersion:
    return ProviderVersion(
        provider_name=provider_name,
        integration_version=integration_version,
        schema_version=schema_version,
        algorithm_version=algorithm_version,
    )


# ── ProviderVersion — fields ──────────────────────────────────────────────────


def test_version_fields_stored() -> None:
    v = _version(provider_name="fake", integration_version="2.0.0")
    assert v.provider_name == "fake"
    assert v.integration_version == "2.0.0"


def test_version_schema_and_algorithm_stored() -> None:
    v = _version()
    assert v.schema_version == NARRATION_SCHEMA_VERSION
    assert v.algorithm_version == NARRATION_ALGORITHM_VERSION


def test_version_is_frozen() -> None:
    v = _version()
    with pytest.raises(FrozenInstanceError):
        v.integration_version = "99.0"  # type: ignore[misc]


def test_version_to_dict_has_required_keys() -> None:
    v = _version()
    d = v.to_dict()
    assert "provider_name" in d
    assert "integration_version" in d
    assert "schema_version" in d
    assert "algorithm_version" in d
    assert "infrastructure_version" in d


# ── ProviderVersion — is_compatible ──────────────────────────────────────────


def test_compatible_with_current_versions() -> None:
    v = _version()
    assert v.is_compatible()


def test_incompatible_schema_version() -> None:
    v = _version(schema_version="Narration-v0")
    assert not v.is_compatible()


def test_incompatible_algorithm_version() -> None:
    v = _version(algorithm_version="narration-segment-v0")
    assert not v.is_compatible()


def test_compatible_explicit_current_versions() -> None:
    v = _version()
    assert v.is_compatible(
        schema_version=NARRATION_SCHEMA_VERSION,
        algorithm_version=NARRATION_ALGORITHM_VERSION,
    )


# ── FAKE_PROVIDER_VERSION ─────────────────────────────────────────────────────


def test_fake_version_provider_name() -> None:
    assert FAKE_PROVIDER_VERSION.provider_name == FAKE_PROVIDER_NAME


def test_fake_version_is_compatible() -> None:
    assert FAKE_PROVIDER_VERSION.is_compatible()


# ── ProviderVersionRegistry ───────────────────────────────────────────────────


def test_register_and_get() -> None:
    registry = ProviderVersionRegistry()
    v = _version(provider_name="fake")
    registry.register(v)
    assert registry.get("fake") is v


def test_get_unregistered_returns_none() -> None:
    registry = ProviderVersionRegistry()
    assert registry.get("ghost") is None


def test_all_versions_empty_initially() -> None:
    registry = ProviderVersionRegistry()
    assert registry.all_versions() == []


def test_all_versions_after_register() -> None:
    registry = ProviderVersionRegistry()
    registry.register(_version(provider_name="a"))
    registry.register(_version(provider_name="b"))
    names = {v.provider_name for v in registry.all_versions()}
    assert names == {"a", "b"}


def test_is_compatible_delegates_to_version() -> None:
    registry = ProviderVersionRegistry()
    v = _version()
    assert registry.is_compatible(v)


def test_is_compatible_false_for_old_schema() -> None:
    registry = ProviderVersionRegistry()
    v = _version(schema_version="old")
    assert not registry.is_compatible(v)


def test_require_compatible_returns_version() -> None:
    registry = ProviderVersionRegistry()
    v = _version(provider_name="fake")
    registry.register(v)
    result = registry.require_compatible("fake")
    assert result is v


def test_require_compatible_unregistered_raises() -> None:
    registry = ProviderVersionRegistry()
    with pytest.raises(ProviderVersionIncompatibleError, match="ghost"):
        registry.require_compatible("ghost")


def test_require_compatible_old_schema_raises() -> None:
    registry = ProviderVersionRegistry()
    v = _version(provider_name="fake", schema_version="old")
    registry.register(v)
    with pytest.raises(ProviderVersionIncompatibleError):
        registry.require_compatible("fake")


def test_incompatible_error_stores_provider_name() -> None:
    registry = ProviderVersionRegistry()
    with pytest.raises(ProviderVersionIncompatibleError) as exc_info:
        registry.require_compatible("missing")
    assert exc_info.value.provider_name == "missing"


# ── get_default_version_registry singleton ────────────────────────────────────


def test_default_version_registry_is_singleton() -> None:
    r1 = get_default_version_registry()
    r2 = get_default_version_registry()
    assert r1 is r2
