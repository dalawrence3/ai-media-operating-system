"""Phase 6 M6.3B: Provider usage accounting abstraction.

UsageAccumulator is an in-memory accumulator for the current synthesis run.
It complements the persistent record_tts_call() DB function — that persists
every call; this provides lightweight in-process aggregation for metrics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class UsageRecord:
    """A single TTS synthesis usage record."""

    provider_name: str
    model_id: str
    characters_input: int
    characters_billed: int
    cost_usd: float
    duration_seconds: float | None
    recorded_at: datetime
    request_id: str | None = None
    segment_id: int | None = None
    run_id: int | None = None


class UsageAccumulator:
    """Accumulates UsageRecord objects in memory for the current run.

    All aggregation methods accept an optional provider_name filter.
    """

    def __init__(self) -> None:
        self._records: list[UsageRecord] = []

    def record(self, usage: UsageRecord) -> None:
        self._records.append(usage)

    def records(self, provider_name: str | None = None) -> list[UsageRecord]:
        if provider_name is None:
            return list(self._records)
        return [r for r in self._records if r.provider_name == provider_name]

    def total_cost_usd(self, provider_name: str | None = None) -> float:
        return sum(r.cost_usd for r in self.records(provider_name))

    def total_characters_billed(self, provider_name: str | None = None) -> int:
        return sum(r.characters_billed for r in self.records(provider_name))

    def total_characters_input(self, provider_name: str | None = None) -> int:
        return sum(r.characters_input for r in self.records(provider_name))

    def count(self, provider_name: str | None = None) -> int:
        return len(self.records(provider_name))

    def reset(self) -> None:
        self._records.clear()

    @staticmethod
    def make_record(
        *,
        provider_name: str,
        model_id: str,
        characters_input: int,
        characters_billed: int,
        cost_usd: float,
        duration_seconds: float | None = None,
        request_id: str | None = None,
        segment_id: int | None = None,
        run_id: int | None = None,
    ) -> UsageRecord:
        return UsageRecord(
            provider_name=provider_name,
            model_id=model_id,
            characters_input=characters_input,
            characters_billed=characters_billed,
            cost_usd=cost_usd,
            duration_seconds=duration_seconds,
            recorded_at=datetime.now(tz=UTC),
            request_id=request_id,
            segment_id=segment_id,
            run_id=run_id,
        )
