"""Metric utility functions — period key derivation, value coercion."""

from __future__ import annotations

from datetime import UTC, datetime


def daily_key(dt: datetime) -> str:
    """Return the ISO 8601 date string for a datetime's day: 'YYYY-MM-DD'."""
    return dt.strftime("%Y-%m-%d")


def weekly_key(dt: datetime) -> str:
    """Return the ISO 8601 week string: 'YYYY-Www'."""
    return dt.strftime("%G-W%V")


def monthly_key(dt: datetime) -> str:
    """Return the year-month string: 'YYYY-MM'."""
    return dt.strftime("%Y-%m")


LIFETIME_KEY = "all"


def parse_iso_datetime(s: str) -> datetime:
    """Parse an ISO 8601 UTC string to a timezone-aware datetime."""
    return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=UTC)


def period_keys_for_datetime(dt: datetime) -> dict[str, str]:
    """Return all period keys for a given datetime."""
    return {
        "daily": daily_key(dt),
        "weekly": weekly_key(dt),
        "monthly": monthly_key(dt),
        "lifetime": LIFETIME_KEY,
    }
