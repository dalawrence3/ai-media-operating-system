"""Tests for Phase 6 M6.3B UsageAccumulator."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.narration.accounting import UsageAccumulator, UsageRecord


def _record(
    provider_name: str = "fake",
    model_id: str = "fake/FAKE",
    characters_input: int = 100,
    characters_billed: int = 100,
    cost_usd: float = 0.0,
    duration_seconds: float | None = 1.0,
    request_id: str | None = "req-1",
    segment_id: int | None = None,
    run_id: int | None = None,
) -> UsageRecord:
    return UsageAccumulator.make_record(
        provider_name=provider_name,
        model_id=model_id,
        characters_input=characters_input,
        characters_billed=characters_billed,
        cost_usd=cost_usd,
        duration_seconds=duration_seconds,
        request_id=request_id,
        segment_id=segment_id,
        run_id=run_id,
    )


# ── UsageRecord ───────────────────────────────────────────────────────────────


def test_make_record_populates_fields() -> None:
    r = _record(provider_name="fake", characters_billed=50, cost_usd=0.01)
    assert r.provider_name == "fake"
    assert r.characters_billed == 50
    assert r.cost_usd == 0.01


def test_make_record_recorded_at_is_set() -> None:
    r = _record()
    assert r.recorded_at is not None


def test_record_is_frozen() -> None:
    r = _record()
    with pytest.raises(FrozenInstanceError):
        r.cost_usd = 99.0  # type: ignore[misc]


# ── UsageAccumulator.record / records ─────────────────────────────────────────


def test_empty_accumulator_has_no_records() -> None:
    acc = UsageAccumulator()
    assert acc.records() == []


def test_record_appends() -> None:
    acc = UsageAccumulator()
    acc.record(_record())
    assert len(acc.records()) == 1


def test_records_returns_all_by_default() -> None:
    acc = UsageAccumulator()
    acc.record(_record(provider_name="a"))
    acc.record(_record(provider_name="b"))
    assert len(acc.records()) == 2


def test_records_filtered_by_provider() -> None:
    acc = UsageAccumulator()
    acc.record(_record(provider_name="fake"))
    acc.record(_record(provider_name="other"))
    assert len(acc.records(provider_name="fake")) == 1
    assert acc.records(provider_name="fake")[0].provider_name == "fake"


def test_records_filter_no_match_returns_empty() -> None:
    acc = UsageAccumulator()
    acc.record(_record(provider_name="fake"))
    assert acc.records(provider_name="missing") == []


# ── Aggregations ──────────────────────────────────────────────────────────────


def test_total_cost_usd_sums_all() -> None:
    acc = UsageAccumulator()
    acc.record(_record(cost_usd=1.0))
    acc.record(_record(cost_usd=2.5))
    assert acc.total_cost_usd() == 3.5


def test_total_cost_usd_filtered() -> None:
    acc = UsageAccumulator()
    acc.record(_record(provider_name="a", cost_usd=1.0))
    acc.record(_record(provider_name="b", cost_usd=5.0))
    assert acc.total_cost_usd(provider_name="a") == 1.0


def test_total_characters_billed_sums() -> None:
    acc = UsageAccumulator()
    acc.record(_record(characters_billed=100))
    acc.record(_record(characters_billed=200))
    assert acc.total_characters_billed() == 300


def test_total_characters_input_sums() -> None:
    acc = UsageAccumulator()
    acc.record(_record(characters_input=150))
    assert acc.total_characters_input() == 150


def test_count_all() -> None:
    acc = UsageAccumulator()
    for _ in range(5):
        acc.record(_record())
    assert acc.count() == 5


def test_count_filtered() -> None:
    acc = UsageAccumulator()
    acc.record(_record(provider_name="a"))
    acc.record(_record(provider_name="a"))
    acc.record(_record(provider_name="b"))
    assert acc.count(provider_name="a") == 2


# ── reset ─────────────────────────────────────────────────────────────────────


def test_reset_clears_all_records() -> None:
    acc = UsageAccumulator()
    acc.record(_record())
    acc.reset()
    assert acc.records() == []
    assert acc.count() == 0
    assert acc.total_cost_usd() == 0.0
