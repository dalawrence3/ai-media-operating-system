"""Tests for analytics metrics utility functions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.analytics.metrics import (
    LIFETIME_KEY,
    daily_key,
    monthly_key,
    parse_iso_datetime,
    period_keys_for_datetime,
    weekly_key,
)


class TestDailyKey:
    def test_returns_yyyy_mm_dd(self):
        dt = datetime(2026, 8, 6, 14, 30, tzinfo=UTC)
        assert daily_key(dt) == "2026-08-06"

    def test_first_of_month(self):
        dt = datetime(2026, 1, 1, tzinfo=UTC)
        assert daily_key(dt) == "2026-01-01"


class TestWeeklyKey:
    def test_returns_yyyy_www(self):
        dt = datetime(2026, 8, 6, tzinfo=UTC)
        key = weekly_key(dt)
        assert key.startswith("2026-W")

    def test_format_length(self):
        dt = datetime(2026, 8, 6, tzinfo=UTC)
        assert len(weekly_key(dt)) == len("2026-W32")


class TestMonthlyKey:
    def test_returns_yyyy_mm(self):
        dt = datetime(2026, 8, 6, tzinfo=UTC)
        assert monthly_key(dt) == "2026-08"

    def test_january(self):
        dt = datetime(2026, 1, 15, tzinfo=UTC)
        assert monthly_key(dt) == "2026-01"


class TestLifetimeKey:
    def test_is_all(self):
        assert LIFETIME_KEY == "all"


class TestParseIsoDatetime:
    def test_parses_utc_z(self):
        dt = parse_iso_datetime("2026-08-06T12:00:00Z")
        assert dt.year == 2026
        assert dt.month == 8

    def test_parses_offset(self):
        dt = parse_iso_datetime("2026-08-06T12:00:00+00:00")
        assert dt.year == 2026

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_iso_datetime("not-a-date")


class TestPeriodKeysForDatetime:
    def test_all_four_types(self):
        dt = datetime(2026, 8, 6, tzinfo=UTC)
        keys = period_keys_for_datetime(dt)
        assert set(keys.keys()) == {"daily", "weekly", "monthly", "lifetime"}

    def test_lifetime_is_all(self):
        dt = datetime(2026, 8, 6, tzinfo=UTC)
        assert period_keys_for_datetime(dt)["lifetime"] == "all"
