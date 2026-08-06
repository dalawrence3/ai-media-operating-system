"""Tests for Phase 6 M6.3B InMemoryProviderBenchmark."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.narration.benchmark import (
    BenchmarkSample,
    InMemoryProviderBenchmark,
    ProviderBenchmark,
)
from app.narration.fake import FAKE_MODEL_NAME, FAKE_PROVIDER_NAME


def _sample(
    *,
    provider_name: str = FAKE_PROVIDER_NAME,
    model_id: str = FAKE_MODEL_NAME,
    latency_ms: int = 10,
    characters: int = 100,
    success: bool = True,
) -> BenchmarkSample:
    return InMemoryProviderBenchmark.make_sample(
        provider_name=provider_name,
        model_id=model_id,
        latency_ms=latency_ms,
        characters=characters,
        success=success,
    )


# ── BenchmarkSample ───────────────────────────────────────────────────────────


def test_make_sample_populates_fields() -> None:
    s = _sample(latency_ms=42, success=False)
    assert s.latency_ms == 42
    assert not s.success
    assert s.provider_name == FAKE_PROVIDER_NAME


def test_sample_sampled_at_is_set() -> None:
    s = _sample()
    assert s.sampled_at is not None


def test_sample_is_frozen() -> None:
    s = _sample()
    with pytest.raises(FrozenInstanceError):
        s.latency_ms = 999  # type: ignore[misc]


# ── InMemoryProviderBenchmark — protocol ──────────────────────────────────────


def test_satisfies_protocol() -> None:
    assert isinstance(InMemoryProviderBenchmark(), ProviderBenchmark)


# ── No samples ────────────────────────────────────────────────────────────────


def test_no_result_for_unrecorded_provider() -> None:
    bench = InMemoryProviderBenchmark()
    assert bench.get_result(FAKE_PROVIDER_NAME) is None


# ── Single sample ─────────────────────────────────────────────────────────────


def test_single_sample_result_count() -> None:
    bench = InMemoryProviderBenchmark()
    bench.record_sample(_sample(latency_ms=50))
    result = bench.get_result(FAKE_PROVIDER_NAME)
    assert result is not None
    assert result.sample_count == 1


def test_single_success_rate_is_one() -> None:
    bench = InMemoryProviderBenchmark()
    bench.record_sample(_sample(success=True))
    assert bench.get_result(FAKE_PROVIDER_NAME).success_rate == 1.0


def test_single_failure_rate_is_zero() -> None:
    bench = InMemoryProviderBenchmark()
    bench.record_sample(_sample(success=False))
    assert bench.get_result(FAKE_PROVIDER_NAME).success_rate == 0.0


def test_mean_latency_single_sample() -> None:
    bench = InMemoryProviderBenchmark()
    bench.record_sample(_sample(latency_ms=80))
    result = bench.get_result(FAKE_PROVIDER_NAME)
    assert result.mean_latency_ms == pytest.approx(80.0)


# ── Multiple samples ──────────────────────────────────────────────────────────


def test_success_rate_mixed() -> None:
    bench = InMemoryProviderBenchmark()
    bench.record_sample(_sample(success=True))
    bench.record_sample(_sample(success=True))
    bench.record_sample(_sample(success=False))
    result = bench.get_result(FAKE_PROVIDER_NAME)
    assert result.success_rate == pytest.approx(2 / 3)


def test_mean_latency_multiple() -> None:
    bench = InMemoryProviderBenchmark()
    for ms in [10, 20, 30]:
        bench.record_sample(_sample(latency_ms=ms))
    result = bench.get_result(FAKE_PROVIDER_NAME)
    assert result.mean_latency_ms == pytest.approx(20.0)


def test_p50_latency() -> None:
    bench = InMemoryProviderBenchmark()
    for ms in [10, 20, 30, 40, 50]:
        bench.record_sample(_sample(latency_ms=ms))
    result = bench.get_result(FAKE_PROVIDER_NAME)
    assert result.p50_latency_ms <= result.p95_latency_ms


def test_p95_latency_at_least_mean() -> None:
    bench = InMemoryProviderBenchmark()
    for ms in range(1, 21):
        bench.record_sample(_sample(latency_ms=ms * 10))
    result = bench.get_result(FAKE_PROVIDER_NAME)
    assert result.p95_latency_ms >= result.mean_latency_ms


# ── Provider isolation ────────────────────────────────────────────────────────


def test_result_isolated_by_provider_name() -> None:
    bench = InMemoryProviderBenchmark()
    bench.record_sample(_sample(provider_name="fake", latency_ms=10))
    bench.record_sample(_sample(provider_name="other", latency_ms=999))
    result = bench.get_result("fake")
    assert result.sample_count == 1
    assert bench.get_result("other").sample_count == 1


def test_samples_filtered_by_provider() -> None:
    bench = InMemoryProviderBenchmark()
    bench.record_sample(_sample(provider_name="a"))
    bench.record_sample(_sample(provider_name="b"))
    assert len(bench.samples(provider_name="a")) == 1


# ── reset ─────────────────────────────────────────────────────────────────────


def test_reset_clears_all_samples() -> None:
    bench = InMemoryProviderBenchmark()
    bench.record_sample(_sample())
    bench.reset()
    assert bench.get_result(FAKE_PROVIDER_NAME) is None
    assert bench.samples() == []
