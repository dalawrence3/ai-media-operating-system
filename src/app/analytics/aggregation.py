"""Deterministic aggregation engine — daily, weekly, monthly, lifetime rollups.

No ML.  No forecasting.  No optimization.
Pure deterministic aggregation over stored normalized metrics.

Deduplication and correction semantics
--------------------------------------
Identical replay (same input_hash): returns existing snapshot, no new rows.

Correction (same provider/publication/period, different payload, different hash):
creates a new immutable snapshot.  In aggregation, for additive metrics, the most
recently ingested snapshot per base (period_start, period_end) pair wins.  Prior
snapshots are never deleted; they remain inspectable via their snapshot IDs.

Deduplication contract
-----------------------
Additive / monetary (AGG_SUM):
    Group metrics by (period_start, period_end).  Within each group, only the
    LAST ingested value is used (most recent snapshot = most authoritative).
    Sum the per-group representative values.

Gauge / ratio (AGG_LAST, calculation_method='latest_observation'):
    Return the globally most recently ingested value.  Phase 11 must not treat
    this as a mathematically recomputed aggregate; it is the latest provider
    observation.

Currency validation
-------------------
For monetary metrics (revenue_estimate), all snapshots contributing to an
aggregate must carry the same currency_code.  CurrencyMismatchError is raised
if they differ.  Missing currency on a monetary snapshot blocks ingest (enforced
by the orchestrator), not here — by the time aggregation runs, all snapshots
in the window either have a currency_code or have none (non-monetary).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field

from app.analytics.constants import (
    AGG_LAST,
    AGG_SUM,
    CALC_METHOD_LATEST_OBSERVATION,
    CALC_METHOD_SUM,
    METRIC_AGGREGATION_OP,
    MONETARY_METRICS,
    PERIOD_DAILY,
    PERIOD_LIFETIME,
    PERIOD_MONTHLY,
    PERIOD_TYPES,
    PERIOD_WEEKLY,
    calc_method_for,
)
from app.analytics.errors import CurrencyMismatchError, UnknownPeriodTypeError
from app.analytics.metrics import LIFETIME_KEY, daily_key, monthly_key, weekly_key
from app.analytics.models import AnalyticsAggregate, AnalyticsMetric
from app.analytics.repository import list_metrics_for_publication, upsert_aggregate


@dataclass(frozen=True)
class AggregationResult:
    """Outcome of a single aggregation run."""

    publication_id: int
    provider: str
    period_type: str
    period_key: str
    aggregates: list[AnalyticsAggregate]


@dataclass
class _ReductionResult:
    value: float
    source_snapshot_ids: list[int]
    calculation_method: str
    currency_code: str | None = field(default=None)


def _agg_hash(
    publication_id: int,
    provider: str,
    period_type: str,
    period_key: str,
    metric_values: dict[str, float],
) -> str:
    payload = json.dumps(
        {
            "publication_id": publication_id,
            "provider": provider,
            "period_type": period_type,
            "period_key": period_key,
            "metrics": {k: metric_values[k] for k in sorted(metric_values)},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _resolve_currency(
    metric_name: str,
    rows: list[AnalyticsMetric],
    snapshot_currency_map: dict[int, str | None],
) -> str | None:
    """Return the shared currency for monetary metrics; None for non-monetary.

    Raises CurrencyMismatchError if multiple distinct currencies are found.
    """
    if metric_name not in MONETARY_METRICS:
        return None
    currencies = {
        snapshot_currency_map.get(r.snapshot_id)
        for r in rows
        if snapshot_currency_map.get(r.snapshot_id) is not None
    }
    if len(currencies) > 1:
        raise CurrencyMismatchError(currencies)  # type: ignore[arg-type]
    return next(iter(currencies)) if currencies else None


def _reduce_metrics(
    metric_name: str,
    rows: list[AnalyticsMetric],
    snapshot_currency_map: dict[int, str | None],
) -> _ReductionResult:
    """Reduce a set of metric rows to a scalar + metadata.

    Additive / monetary (AGG_SUM):
        Deduplicate by (period_start, period_end) — last ingested wins per base
        period — then sum the deduplicated representatives.  Returns all
        contributing snapshot IDs.

    Gauge / ratio (AGG_LAST):
        Return the most recently ingested value (rows[-1]).
        Records calculation_method='latest_observation' so Phase 11 cannot
        confuse this with a mathematically recomputed aggregate.
    """
    if not rows:
        return _ReductionResult(
            value=0.0,
            source_snapshot_ids=[],
            calculation_method=CALC_METHOD_SUM,
        )

    op = METRIC_AGGREGATION_OP.get(metric_name, AGG_SUM)
    calc_method = calc_method_for(metric_name)
    currency = _resolve_currency(metric_name, rows, snapshot_currency_map)

    if op == AGG_LAST:
        last = rows[-1]
        return _ReductionResult(
            value=last.metric_value,
            source_snapshot_ids=[last.snapshot_id],
            calculation_method=CALC_METHOD_LATEST_OBSERVATION,
            currency_code=currency,
        )

    # AGG_SUM — deduplicate by (period_start, period_end)
    base_period_seen: dict[tuple[str | None, str | None], tuple[int, float]] = {}
    for row in rows:
        base_period_seen[(row.period_start, row.period_end)] = (
            row.snapshot_id,
            row.metric_value,
        )

    total = sum(v for _, v in base_period_seen.values())
    source_ids = [sid for sid, _ in base_period_seen.values()]
    return _ReductionResult(
        value=total,
        source_snapshot_ids=source_ids,
        calculation_method=calc_method,
        currency_code=currency,
    )


def _period_key_for(metric: AnalyticsMetric, period_type: str) -> str:
    """Derive the period key from a metric row's period_start."""
    if period_type == PERIOD_LIFETIME:
        return LIFETIME_KEY
    period_start = metric.period_start
    if period_start is None:
        return LIFETIME_KEY
    try:
        from app.analytics.metrics import parse_iso_datetime

        dt = parse_iso_datetime(period_start)
    except (ValueError, AttributeError):
        return LIFETIME_KEY
    if period_type == PERIOD_DAILY:
        return daily_key(dt)
    if period_type == PERIOD_WEEKLY:
        return weekly_key(dt)
    if period_type == PERIOD_MONTHLY:
        return monthly_key(dt)
    return LIFETIME_KEY


def _build_snapshot_currency_map(
    conn: sqlite3.Connection,
    publication_id: int,
) -> dict[int, str | None]:
    """Return {snapshot_id: currency_code} for all snapshots of this publication."""
    rows = conn.execute(
        "SELECT id, currency_code FROM analytics_snapshots WHERE publication_id = ?",
        (publication_id,),
    ).fetchall()
    return {row["id"]: row["currency_code"] for row in rows}


def aggregate_publication(
    conn: sqlite3.Connection,
    *,
    publication_id: int,
    topic_id: int,
    provider: str,
    period_type: str,
) -> AggregationResult:
    """Compute deterministic rollups for one publication × period_type."""
    if period_type not in PERIOD_TYPES:
        raise UnknownPeriodTypeError(period_type)

    all_metrics = list_metrics_for_publication(conn, publication_id)
    provider_metrics = [m for m in all_metrics if m.provider == provider]

    snapshot_currency_map = _build_snapshot_currency_map(conn, publication_id)

    # Group by (period_key, metric_name) — insertion order preserved
    groups: dict[tuple[str, str], list[AnalyticsMetric]] = {}
    for m in provider_metrics:
        key = _period_key_for(m, period_type)
        bucket = (key, m.metric_name)
        groups.setdefault(bucket, []).append(m)

    results: list[AnalyticsAggregate] = []
    for (period_key, metric_name), rows in groups.items():
        reduction = _reduce_metrics(metric_name, rows, snapshot_currency_map)
        agg_hash = _agg_hash(
            publication_id,
            provider,
            period_type,
            period_key,
            {metric_name: reduction.value},
        )
        agg = upsert_aggregate(
            conn,
            publication_id=publication_id,
            topic_id=topic_id,
            provider=provider,
            period_type=period_type,
            period_key=period_key,
            metric_name=metric_name,
            metric_value=reduction.value,
            snapshot_count=len(rows),
            calculation_method=reduction.calculation_method,
            source_snapshot_ids=reduction.source_snapshot_ids,
            input_hash=agg_hash,
            currency_code=reduction.currency_code,
        )
        results.append(agg)

    return AggregationResult(
        publication_id=publication_id,
        provider=provider,
        period_type=period_type,
        period_key=LIFETIME_KEY if period_type == PERIOD_LIFETIME else "",
        aggregates=results,
    )


def aggregate_all_periods(
    conn: sqlite3.Connection,
    *,
    publication_id: int,
    topic_id: int,
    provider: str,
) -> list[AggregationResult]:
    """Run all four period rollups for a publication."""
    return [
        aggregate_publication(
            conn,
            publication_id=publication_id,
            topic_id=topic_id,
            provider=provider,
            period_type=pt,
        )
        for pt in [PERIOD_DAILY, PERIOD_WEEKLY, PERIOD_MONTHLY, PERIOD_LIFETIME]
    ]
