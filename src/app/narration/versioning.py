"""Phase 6 M6.3B: Provider versioning — tracks integration compatibility."""

from __future__ import annotations

from dataclasses import dataclass

from app.narration.constants import (
    NARRATION_ALGORITHM_VERSION,
    NARRATION_SCHEMA_VERSION,
    PROVIDER_INFRASTRUCTURE_VERSION,
)
from app.narration.errors import ProviderVersionIncompatibleError


@dataclass(frozen=True)
class ProviderVersion:
    """Version record for a provider integration.

    Binds a provider to the exact schema and algorithm versions it was built
    against.  Any mismatch signals that re-synthesis may be required.
    """

    provider_name: str
    integration_version: str
    schema_version: str
    algorithm_version: str
    infrastructure_version: str = PROVIDER_INFRASTRUCTURE_VERSION

    def is_compatible(
        self,
        *,
        schema_version: str = NARRATION_SCHEMA_VERSION,
        algorithm_version: str = NARRATION_ALGORITHM_VERSION,
    ) -> bool:
        return self.schema_version == schema_version and self.algorithm_version == algorithm_version

    def to_dict(self) -> dict[str, str]:
        return {
            "provider_name": self.provider_name,
            "integration_version": self.integration_version,
            "schema_version": self.schema_version,
            "algorithm_version": self.algorithm_version,
            "infrastructure_version": self.infrastructure_version,
        }


class ProviderVersionRegistry:
    """Stores and validates provider version records."""

    def __init__(self) -> None:
        self._versions: dict[str, ProviderVersion] = {}

    def register(self, version: ProviderVersion) -> None:
        self._versions[version.provider_name] = version

    def get(self, provider_name: str) -> ProviderVersion | None:
        return self._versions.get(provider_name)

    def all_versions(self) -> list[ProviderVersion]:
        return list(self._versions.values())

    def is_compatible(
        self,
        version: ProviderVersion,
        *,
        schema_version: str = NARRATION_SCHEMA_VERSION,
        algorithm_version: str = NARRATION_ALGORITHM_VERSION,
    ) -> bool:
        return version.is_compatible(
            schema_version=schema_version,
            algorithm_version=algorithm_version,
        )

    def require_compatible(
        self,
        provider_name: str,
        *,
        schema_version: str = NARRATION_SCHEMA_VERSION,
        algorithm_version: str = NARRATION_ALGORITHM_VERSION,
    ) -> ProviderVersion:
        version = self.get(provider_name)
        if version is None:
            raise ProviderVersionIncompatibleError(provider_name, "no version record registered")
        if not version.is_compatible(
            schema_version=schema_version,
            algorithm_version=algorithm_version,
        ):
            raise ProviderVersionIncompatibleError(
                provider_name,
                f"schema={version.schema_version!r} algorithm={version.algorithm_version!r}",
            )
        return version


_default_version_registry = ProviderVersionRegistry()


def get_default_version_registry() -> ProviderVersionRegistry:
    return _default_version_registry
