"""Extension registry for backend capabilities.

Registers command handlers, event handlers, review handlers, engine adapters,
and provider adapters. No arbitrary runtime package loading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.application.errors import ExtensionAlreadyRegisteredError, ExtensionNotFoundError


@dataclass
class ExtensionEntry:
    key: str
    capability: str
    version: str
    handler: Any
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


class ExtensionRegistry:
    """Thread-unsafe in-process extension registry.

    Suited for single-process use; Phase 15 may replace with a distributed
    capability registry if needed.
    """

    def __init__(self) -> None:
        self._entries: dict[str, ExtensionEntry] = {}

    def register(
        self,
        key: str,
        capability: str,
        handler: Any,
        *,
        version: str = "1.0.0",
        enabled: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> ExtensionEntry:
        if key in self._entries:
            raise ExtensionAlreadyRegisteredError(key)
        entry = ExtensionEntry(
            key=key,
            capability=capability,
            version=version,
            handler=handler,
            enabled=enabled,
            metadata=metadata or {},
        )
        self._entries[key] = entry
        return entry

    def get(self, key: str) -> ExtensionEntry:
        entry = self._entries.get(key)
        if entry is None:
            raise ExtensionNotFoundError(key)
        return entry

    def get_handler(self, key: str) -> Any:
        return self.get(key).handler

    def enable(self, key: str) -> None:
        self.get(key).enabled = True

    def disable(self, key: str) -> None:
        self.get(key).enabled = False

    def list_entries(
        self, capability: str | None = None, enabled_only: bool = False
    ) -> list[ExtensionEntry]:
        entries = list(self._entries.values())
        if capability is not None:
            entries = [e for e in entries if e.capability == capability]
        if enabled_only:
            entries = [e for e in entries if e.enabled]
        return entries

    def list_keys(self) -> list[str]:
        return sorted(self._entries.keys())

    def has(self, key: str) -> bool:
        return key in self._entries

    def unregister(self, key: str) -> None:
        if key not in self._entries:
            raise ExtensionNotFoundError(key)
        del self._entries[key]

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


# Module-level singleton — used by the composition root and tests.
_default_registry: ExtensionRegistry | None = None


def get_default_registry() -> ExtensionRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = ExtensionRegistry()
    return _default_registry


def reset_default_registry() -> None:
    """For use in tests only."""
    global _default_registry
    _default_registry = ExtensionRegistry()
