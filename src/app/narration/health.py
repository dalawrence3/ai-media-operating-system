"""Phase 6 M6.3B: Provider health abstraction."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


class ProviderHealthStatus(enum.Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProviderHealthReport:
    """Point-in-time health snapshot for a provider."""

    provider_name: str
    status: ProviderHealthStatus
    checked_at: datetime
    message: str | None
    consecutive_failures: int
    last_failure_at: datetime | None


@runtime_checkable
class ProviderHealthCheck(Protocol):
    """Tracks synthesis success/failure to derive a health signal."""

    def record_success(self, provider_name: str) -> None: ...

    def record_failure(self, provider_name: str, error: str) -> None: ...

    def check(self, provider_name: str) -> ProviderHealthReport: ...


class InMemoryProviderHealthCheck:
    """In-memory health tracker.

    Thresholds (unhealthy at 5+ consecutive failures, degraded at 2+).
    """

    DEGRADED_THRESHOLD: int = 1
    UNHEALTHY_THRESHOLD: int = 5

    def __init__(self) -> None:
        self._consecutive_failures: dict[str, int] = {}
        self._last_failure_at: dict[str, datetime] = {}
        self._last_error: dict[str, str] = {}

    def record_success(self, provider_name: str) -> None:
        self._consecutive_failures[provider_name] = 0

    def record_failure(self, provider_name: str, error: str) -> None:
        self._consecutive_failures[provider_name] = (
            self._consecutive_failures.get(provider_name, 0) + 1
        )
        self._last_failure_at[provider_name] = datetime.now(tz=UTC)
        self._last_error[provider_name] = error

    def check(self, provider_name: str) -> ProviderHealthReport:
        failures = self._consecutive_failures.get(provider_name, 0)
        last_failure = self._last_failure_at.get(provider_name)
        last_error = self._last_error.get(provider_name)

        if failures >= self.UNHEALTHY_THRESHOLD:
            status = ProviderHealthStatus.UNHEALTHY
        elif failures >= self.DEGRADED_THRESHOLD:
            status = ProviderHealthStatus.DEGRADED
        elif failures == 0 and provider_name in self._consecutive_failures:
            status = ProviderHealthStatus.HEALTHY
        else:
            status = ProviderHealthStatus.UNKNOWN

        return ProviderHealthReport(
            provider_name=provider_name,
            status=status,
            checked_at=datetime.now(tz=UTC),
            message=last_error if failures > 0 else None,
            consecutive_failures=failures,
            last_failure_at=last_failure,
        )

    def reset(self, provider_name: str | None = None) -> None:
        if provider_name is None:
            self._consecutive_failures.clear()
            self._last_failure_at.clear()
            self._last_error.clear()
        else:
            self._consecutive_failures.pop(provider_name, None)
            self._last_failure_at.pop(provider_name, None)
            self._last_error.pop(provider_name, None)
