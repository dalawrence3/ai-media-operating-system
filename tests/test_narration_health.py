"""Tests for Phase 6 M6.3B InMemoryProviderHealthCheck."""

from __future__ import annotations

from app.narration.fake import FAKE_PROVIDER_NAME
from app.narration.health import (
    InMemoryProviderHealthCheck,
    ProviderHealthCheck,
    ProviderHealthStatus,
)

# ── Protocol ──────────────────────────────────────────────────────────────────


def test_satisfies_protocol() -> None:
    assert isinstance(InMemoryProviderHealthCheck(), ProviderHealthCheck)


# ── Unknown provider → UNKNOWN status ────────────────────────────────────────


def test_untracked_provider_is_unknown() -> None:
    hc = InMemoryProviderHealthCheck()
    report = hc.check(FAKE_PROVIDER_NAME)
    assert report.status == ProviderHealthStatus.UNKNOWN


def test_unknown_report_has_no_message() -> None:
    hc = InMemoryProviderHealthCheck()
    report = hc.check(FAKE_PROVIDER_NAME)
    assert report.message is None


def test_unknown_report_zero_consecutive_failures() -> None:
    hc = InMemoryProviderHealthCheck()
    report = hc.check(FAKE_PROVIDER_NAME)
    assert report.consecutive_failures == 0


# ── record_success → HEALTHY ──────────────────────────────────────────────────


def test_after_success_status_is_healthy() -> None:
    hc = InMemoryProviderHealthCheck()
    hc.record_success(FAKE_PROVIDER_NAME)
    assert hc.check(FAKE_PROVIDER_NAME).status == ProviderHealthStatus.HEALTHY


def test_after_success_consecutive_failures_zero() -> None:
    hc = InMemoryProviderHealthCheck()
    hc.record_success(FAKE_PROVIDER_NAME)
    assert hc.check(FAKE_PROVIDER_NAME).consecutive_failures == 0


def test_success_resets_after_failure() -> None:
    hc = InMemoryProviderHealthCheck()
    hc.record_failure(FAKE_PROVIDER_NAME, "oops")
    hc.record_failure(FAKE_PROVIDER_NAME, "again")
    hc.record_success(FAKE_PROVIDER_NAME)
    assert hc.check(FAKE_PROVIDER_NAME).status == ProviderHealthStatus.HEALTHY
    assert hc.check(FAKE_PROVIDER_NAME).consecutive_failures == 0


# ── record_failure counts ─────────────────────────────────────────────────────


def test_one_failure_is_degraded() -> None:
    hc = InMemoryProviderHealthCheck()
    hc.record_failure(FAKE_PROVIDER_NAME, "err")
    report = hc.check(FAKE_PROVIDER_NAME)
    assert report.status in (
        ProviderHealthStatus.DEGRADED,
        ProviderHealthStatus.UNHEALTHY,
    )


def test_two_failures_is_degraded() -> None:
    hc = InMemoryProviderHealthCheck()
    hc.record_failure(FAKE_PROVIDER_NAME, "e1")
    hc.record_failure(FAKE_PROVIDER_NAME, "e2")
    assert hc.check(FAKE_PROVIDER_NAME).status == ProviderHealthStatus.DEGRADED


def test_five_failures_is_unhealthy() -> None:
    hc = InMemoryProviderHealthCheck()
    for i in range(5):
        hc.record_failure(FAKE_PROVIDER_NAME, f"err-{i}")
    assert hc.check(FAKE_PROVIDER_NAME).status == ProviderHealthStatus.UNHEALTHY


def test_failure_message_stored() -> None:
    hc = InMemoryProviderHealthCheck()
    hc.record_failure(FAKE_PROVIDER_NAME, "timeout")
    assert hc.check(FAKE_PROVIDER_NAME).message == "timeout"


def test_consecutive_failures_counted() -> None:
    hc = InMemoryProviderHealthCheck()
    hc.record_failure(FAKE_PROVIDER_NAME, "a")
    hc.record_failure(FAKE_PROVIDER_NAME, "b")
    assert hc.check(FAKE_PROVIDER_NAME).consecutive_failures == 2


def test_last_failure_at_set() -> None:
    hc = InMemoryProviderHealthCheck()
    hc.record_failure(FAKE_PROVIDER_NAME, "err")
    assert hc.check(FAKE_PROVIDER_NAME).last_failure_at is not None


# ── Provider isolation ────────────────────────────────────────────────────────


def test_failures_isolated_by_provider() -> None:
    hc = InMemoryProviderHealthCheck()
    for _ in range(5):
        hc.record_failure("other", "err")
    hc.record_success(FAKE_PROVIDER_NAME)
    assert hc.check(FAKE_PROVIDER_NAME).status == ProviderHealthStatus.HEALTHY


# ── check populates report fields ─────────────────────────────────────────────


def test_report_provider_name() -> None:
    hc = InMemoryProviderHealthCheck()
    report = hc.check(FAKE_PROVIDER_NAME)
    assert report.provider_name == FAKE_PROVIDER_NAME


def test_report_checked_at_is_set() -> None:
    hc = InMemoryProviderHealthCheck()
    report = hc.check(FAKE_PROVIDER_NAME)
    assert report.checked_at is not None


# ── reset ─────────────────────────────────────────────────────────────────────


def test_reset_specific_provider() -> None:
    hc = InMemoryProviderHealthCheck()
    hc.record_failure(FAKE_PROVIDER_NAME, "err")
    hc.reset(FAKE_PROVIDER_NAME)
    assert hc.check(FAKE_PROVIDER_NAME).status == ProviderHealthStatus.UNKNOWN


def test_reset_all_providers() -> None:
    hc = InMemoryProviderHealthCheck()
    hc.record_failure("a", "err")
    hc.record_failure("b", "err")
    hc.reset()
    assert hc.check("a").status == ProviderHealthStatus.UNKNOWN
    assert hc.check("b").status == ProviderHealthStatus.UNKNOWN
