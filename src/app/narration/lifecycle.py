"""Phase 6 M6.3B: Provider lifecycle abstraction."""

from __future__ import annotations

import enum
from typing import Protocol, runtime_checkable


class ProviderLifecycleState(enum.Enum):
    """Observable lifecycle state of a TTS provider instance."""

    CREATED = "created"
    INITIALIZING = "initializing"
    READY = "ready"
    DEGRADED = "degraded"
    SHUTDOWN = "shutdown"


@runtime_checkable
class ProviderLifecycle(Protocol):
    """Protocol for providers that expose lifecycle management.

    initialize() is called once before synthesis.  shutdown() is called when
    the provider is no longer needed.  Both are no-ops for local providers.
    Synthesis may proceed in any state — the lifecycle is observable, not gating.
    """

    def initialize(self) -> None: ...

    def shutdown(self) -> None: ...

    @property
    def lifecycle_state(self) -> ProviderLifecycleState: ...
