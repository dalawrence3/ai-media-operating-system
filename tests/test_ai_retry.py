"""Tests for bounded retry logic."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.ai.errors import RetryExhaustedError
from app.ai.retry import with_retry

_RETRYABLE = (ValueError,)


def test_success_first_attempt() -> None:
    result, retries = with_retry(lambda: 42, max_attempts=3, retryable=_RETRYABLE)
    assert result == 42
    assert retries == 0


def test_success_on_second_attempt() -> None:
    calls = [0]

    def fn():
        calls[0] += 1
        if calls[0] < 2:
            raise ValueError("transient")
        return "ok"

    no_sleep = MagicMock()
    result, retries = with_retry(fn, max_attempts=3, retryable=_RETRYABLE, sleep_fn=no_sleep)
    assert result == "ok"
    assert retries == 1
    assert no_sleep.call_count == 1


def test_retry_exhausted_raises() -> None:
    no_sleep = MagicMock()
    with pytest.raises(RetryExhaustedError) as exc_info:
        with_retry(
            lambda: (_ for _ in ()).throw(ValueError("always fails")),
            max_attempts=3,
            retryable=_RETRYABLE,
            sleep_fn=no_sleep,
        )
    assert exc_info.value.attempts == 3


def test_non_retryable_propagates_immediately() -> None:
    no_sleep = MagicMock()
    with pytest.raises(TypeError):
        with_retry(
            lambda: (_ for _ in ()).throw(TypeError("not retryable")),
            max_attempts=5,
            retryable=_RETRYABLE,
            sleep_fn=no_sleep,
        )
    assert no_sleep.call_count == 0


def test_exponential_backoff_delays() -> None:
    calls = [0]

    def fn():
        calls[0] += 1
        raise ValueError("fail")

    sleep_calls: list[float] = []
    with pytest.raises(RetryExhaustedError):
        with_retry(
            fn,
            max_attempts=4,
            retryable=_RETRYABLE,
            backoff_base=2.0,
            sleep_fn=sleep_calls.append,
        )
    # 3 sleeps for 4 attempts (no sleep after the last failure).
    assert sleep_calls == [2.0, 4.0, 8.0]


def test_max_attempts_one_never_sleeps() -> None:
    no_sleep = MagicMock()
    with pytest.raises(RetryExhaustedError):
        with_retry(
            lambda: (_ for _ in ()).throw(ValueError("fail")),
            max_attempts=1,
            retryable=_RETRYABLE,
            sleep_fn=no_sleep,
        )
    no_sleep.assert_not_called()


def test_max_attempts_zero_raises_value_error() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        with_retry(lambda: None, max_attempts=0, retryable=_RETRYABLE)
