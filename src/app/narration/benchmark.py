"""Phase 6 M6.3B: Provider benchmark abstraction."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class BenchmarkSample:
    """A single latency/success observation from a synthesis call."""

    provider_name: str
    model_id: str
    latency_ms: int
    characters: int
    success: bool
    sampled_at: datetime


@dataclass(frozen=True)
class BenchmarkResult:
    """Aggregated benchmark statistics for a provider."""

    provider_name: str
    model_id: str
    sample_count: int
    success_rate: float
    mean_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float


@runtime_checkable
class ProviderBenchmark(Protocol):
    """Records synthesis observations and surfaces aggregated statistics."""

    def record_sample(self, sample: BenchmarkSample) -> None: ...

    def get_result(self, provider_name: str) -> BenchmarkResult | None: ...


class InMemoryProviderBenchmark:
    """In-process benchmark accumulator backed by a list of BenchmarkSamples."""

    def __init__(self) -> None:
        self._samples: list[BenchmarkSample] = []

    def record_sample(self, sample: BenchmarkSample) -> None:
        self._samples.append(sample)

    def get_result(self, provider_name: str) -> BenchmarkResult | None:
        samples = [s for s in self._samples if s.provider_name == provider_name]
        if not samples:
            return None

        n = len(samples)
        success_count = sum(1 for s in samples if s.success)
        latencies = [s.latency_ms for s in samples]
        sorted_latencies = sorted(latencies)

        mean_lat = statistics.mean(latencies)
        p50 = _percentile(sorted_latencies, 50)
        p95 = _percentile(sorted_latencies, 95)

        model_id = samples[-1].model_id

        return BenchmarkResult(
            provider_name=provider_name,
            model_id=model_id,
            sample_count=n,
            success_rate=success_count / n,
            mean_latency_ms=mean_lat,
            p50_latency_ms=p50,
            p95_latency_ms=p95,
        )

    def samples(self, provider_name: str | None = None) -> list[BenchmarkSample]:
        if provider_name is None:
            return list(self._samples)
        return [s for s in self._samples if s.provider_name == provider_name]

    def reset(self) -> None:
        self._samples.clear()

    @staticmethod
    def make_sample(
        *,
        provider_name: str,
        model_id: str,
        latency_ms: int,
        characters: int,
        success: bool,
    ) -> BenchmarkSample:
        return BenchmarkSample(
            provider_name=provider_name,
            model_id=model_id,
            latency_ms=latency_ms,
            characters=characters,
            success=success,
            sampled_at=datetime.now(tz=UTC),
        )


def _percentile(sorted_values: list[int | float], pct: int) -> float:
    if not sorted_values:
        return 0.0
    idx = int(len(sorted_values) * pct / 100)
    idx = min(idx, len(sorted_values) - 1)
    return float(sorted_values[idx])
