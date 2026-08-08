"""Bounded retry with exponential back-off.

The sleep callable is injectable so tests run deterministically without
real delays.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from app.ai.errors import RetryExhaustedError


def with_retry[T](
    fn: Callable[[], T],
    *,
    max_attempts: int,
    retryable: tuple[type[BaseException], ...],
    backoff_base: float = 1.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[T, int]:
    """Call *fn* up to *max_attempts* times, retrying on *retryable* exceptions.

    Returns ``(result, retry_count)`` where ``retry_count`` is the number of
    retries performed (0 = succeeded on the first attempt).

    Raises :exc:`RetryExhaustedError` when all attempts fail.
    Non-retryable exceptions propagate immediately without incrementing the
    retry counter.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")

    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return fn(), attempt  # attempt == number of retries so far
        except retryable as exc:
            last_exc = exc
            if attempt < max_attempts - 1:
                sleep_fn(backoff_base * (2**attempt))

    assert last_exc is not None
    raise RetryExhaustedError(attempts=max_attempts, last_error=last_exc)
