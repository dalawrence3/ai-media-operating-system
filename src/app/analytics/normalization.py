"""Provider metric normalization — maps raw provider data to canonical names.

The normalization boundary is enforced here and in adapters only.
No code outside this module or adapter files may interpret provider field names.
"""

from __future__ import annotations

from app.analytics.constants import CANONICAL_METRICS
from app.analytics.errors import NormalizationError, UnknownMetricError


def validate_canonical_metrics(normalized: dict[str, float]) -> None:
    """Raise UnknownMetricError if any key is not a canonical metric name."""
    for name in normalized:
        if name not in CANONICAL_METRICS:
            raise UnknownMetricError(name)


def safe_float(value: object, field: str) -> float | None:
    """Coerce a value to float; return None if absent or not numeric."""
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise NormalizationError(
            f"Cannot convert field {field!r} value {value!r} to float"
        ) from exc


def merge_normalized(
    *dicts: dict[str, float],
) -> dict[str, float]:
    """Merge multiple normalized metric dicts, last writer wins per key.

    All inputs must already contain only canonical metric names.
    """
    result: dict[str, float] = {}
    for d in dicts:
        result.update(d)
    return result


def filter_none_metrics(metrics: dict[str, float | None]) -> dict[str, float]:
    """Drop metric entries where the value is None."""
    return {k: v for k, v in metrics.items() if v is not None}
