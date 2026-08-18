"""Phase 10 analytics constants — metric names, period types, status values.

Canonical metric semantics
--------------------------
additive       : Sum across disjoint periods (views, impressions, shares,
                 subscribers_gained, subscribers_lost, revenue_estimate,
                 watch_time_seconds).  Within a single period_key, multiple
                 ingests of the same base period are deduplicated (last wins).

gauge          : Point-in-time cumulative total.  Only the most recent
                 authoritative value is meaningful (likes, dislikes, comments).
                 Never summed across snapshots.

ratio          : Provider-computed ratio or derived average.  Cannot be
                 independently re-aggregated without underlying numerators.
                 (ctr, average_view_duration)  Stored per-period; cross-period
                 aggregation is labeled as unweighted and must not be used as
                 if it were a weighted computation.

monetary       : Additive currency amount.  Aggregation requires all summed
                 snapshots to share the same currency.  Currency code is
                 preserved in raw_metrics_json; no float column exists for it.

One canonical unit per quantity
--------------------------------
- Durations: seconds (watch_time_seconds, average_view_duration).
  estimated_minutes_watched is not a canonical metric; YouTube adapters
  convert minutes to seconds before returning normalized output.

- Currency: numeric amount only (revenue_estimate, REAL column).
  Currency code lives in raw_metrics_json, not a canonical float metric.
  Aggregation must verify currency homogeneity before summing.

- Ratios: 0–1 scale (ctr as a fraction, not a percentage).
"""

from __future__ import annotations

ANALYTICS_ENGINE_VERSION = "1.0.0"
ANALYTICS_ADAPTER_VERSION = "1.0.0"
ANALYTICS_SCHEMA_VERSION = "1.0.0"

# ── Canonical metric names ────────────────────────────────────────────────────

METRIC_VIEWS = "views"
METRIC_ENGAGED_VIEWS = "engaged_views"
METRIC_WATCH_TIME_SECONDS = "watch_time_seconds"
METRIC_AVERAGE_VIEW_DURATION = "average_view_duration"
# Average percentage of each video that viewers watched (0–100 scale from YouTube API).
# Populated by the YouTube Analytics API basic user activity query.
METRIC_AVERAGE_VIEW_PERCENTAGE = "average_view_percentage"
# Thumbnail impressions and CTR — populated only via the YouTube Reporting API
# (bulk/scheduled). NOT available from the Analytics API targeted query.
METRIC_IMPRESSIONS = "impressions"
METRIC_CTR = "ctr"
METRIC_LIKES = "likes"
METRIC_DISLIKES = "dislikes"
METRIC_COMMENTS = "comments"
METRIC_SHARES = "shares"
METRIC_SUBSCRIBERS_GAINED = "subscribers_gained"
METRIC_SUBSCRIBERS_LOST = "subscribers_lost"
METRIC_REVENUE_ESTIMATE = "revenue_estimate"

CANONICAL_METRICS: frozenset[str] = frozenset(
    {
        METRIC_VIEWS,
        METRIC_ENGAGED_VIEWS,
        METRIC_WATCH_TIME_SECONDS,
        METRIC_AVERAGE_VIEW_DURATION,
        METRIC_AVERAGE_VIEW_PERCENTAGE,
        METRIC_IMPRESSIONS,
        METRIC_CTR,
        METRIC_LIKES,
        METRIC_DISLIKES,
        METRIC_COMMENTS,
        METRIC_SHARES,
        METRIC_SUBSCRIBERS_GAINED,
        METRIC_SUBSCRIBERS_LOST,
        METRIC_REVENUE_ESTIMATE,
    }
)

# ── Metric semantic kinds ─────────────────────────────────────────────────────

METRIC_KIND_ADDITIVE = "additive"  # sum across non-overlapping periods
METRIC_KIND_GAUGE = "gauge"  # cumulative total, take latest value
METRIC_KIND_RATIO = "ratio"  # provider-computed ratio, take latest
METRIC_KIND_MONETARY = "monetary"  # additive but currency-constrained

METRIC_KIND: dict[str, str] = {
    METRIC_VIEWS: METRIC_KIND_ADDITIVE,
    METRIC_ENGAGED_VIEWS: METRIC_KIND_ADDITIVE,
    METRIC_WATCH_TIME_SECONDS: METRIC_KIND_ADDITIVE,
    METRIC_IMPRESSIONS: METRIC_KIND_ADDITIVE,
    METRIC_SHARES: METRIC_KIND_ADDITIVE,
    METRIC_SUBSCRIBERS_GAINED: METRIC_KIND_ADDITIVE,
    METRIC_SUBSCRIBERS_LOST: METRIC_KIND_ADDITIVE,
    METRIC_REVENUE_ESTIMATE: METRIC_KIND_MONETARY,
    METRIC_LIKES: METRIC_KIND_GAUGE,
    METRIC_DISLIKES: METRIC_KIND_GAUGE,
    METRIC_COMMENTS: METRIC_KIND_GAUGE,
    METRIC_CTR: METRIC_KIND_RATIO,
    METRIC_AVERAGE_VIEW_DURATION: METRIC_KIND_RATIO,
    METRIC_AVERAGE_VIEW_PERCENTAGE: METRIC_KIND_RATIO,
}

# ── Aggregation period types ──────────────────────────────────────────────────

PERIOD_DAILY = "daily"
PERIOD_WEEKLY = "weekly"
PERIOD_MONTHLY = "monthly"
PERIOD_LIFETIME = "lifetime"

PERIOD_TYPES: frozenset[str] = frozenset(
    {
        PERIOD_DAILY,
        PERIOD_WEEKLY,
        PERIOD_MONTHLY,
        PERIOD_LIFETIME,
    }
)

# ── Review event severity ─────────────────────────────────────────────────────

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"
SEVERITY_CRITICAL = "critical"
SEVERITY_OTHER = "other"

REVIEW_SEVERITIES: frozenset[str] = frozenset(
    {
        SEVERITY_INFO,
        SEVERITY_WARNING,
        SEVERITY_ERROR,
        SEVERITY_CRITICAL,
        SEVERITY_OTHER,
    }
)

SEVERITY_REQUIRES_NOTES: frozenset[str] = frozenset({SEVERITY_OTHER})

# ── Aggregation operations ────────────────────────────────────────────────────

AGG_SUM = "sum"
AGG_LAST = "last"

# How each metric reduces within a period bucket.
#
# additive metrics: SUM across distinct period_start/end pairs, AGG_LAST
#   for multiple ingests of the same base period.
#
# gauge metrics: AGG_LAST — the most recent provider snapshot is authoritative.
#   Multiple ingests of the same period are disambiguated by insertion order.
#
# ratio metrics: AGG_LAST — provider-computed; cross-period aggregation of ratios
#   produces an unweighted mean that is NOT equivalent to the weighted ratio.
#   Phase 11 may re-derive these from underlying numerators if stored.
#
# monetary metrics: treated as additive within the same currency.
#   CurrencyMismatchError is raised if currencies differ across summed snapshots.

METRIC_AGGREGATION_OP: dict[str, str] = {
    METRIC_VIEWS: AGG_SUM,
    METRIC_ENGAGED_VIEWS: AGG_SUM,
    METRIC_WATCH_TIME_SECONDS: AGG_SUM,
    METRIC_IMPRESSIONS: AGG_SUM,
    METRIC_SHARES: AGG_SUM,
    METRIC_SUBSCRIBERS_GAINED: AGG_SUM,
    METRIC_SUBSCRIBERS_LOST: AGG_SUM,
    METRIC_REVENUE_ESTIMATE: AGG_SUM,
    METRIC_LIKES: AGG_LAST,
    METRIC_DISLIKES: AGG_LAST,
    METRIC_COMMENTS: AGG_LAST,
    METRIC_CTR: AGG_LAST,
    METRIC_AVERAGE_VIEW_DURATION: AGG_LAST,
    METRIC_AVERAGE_VIEW_PERCENTAGE: AGG_LAST,
}

# Publication eligibility: statuses that permit analytics ingestion
PUBLICATION_ELIGIBLE_STATUSES: frozenset[str] = frozenset(
    {
        "uploaded",
        "scheduled",
        "published",
    }
)

# ── Aggregate calculation methods ─────────────────────────────────────────────

CALC_METHOD_SUM = "sum"
CALC_METHOD_LATEST_OBSERVATION = "latest_observation"

# Maps METRIC_AGGREGATION_OP values to the readable calculation method label
# stored in analytics_aggregates.calculation_method.
_AGG_OP_TO_CALC_METHOD: dict[str, str] = {
    AGG_SUM: CALC_METHOD_SUM,
    AGG_LAST: CALC_METHOD_LATEST_OBSERVATION,
}


def calc_method_for(metric_name: str) -> str:
    """Return the calculation method label for a canonical metric name."""
    op = METRIC_AGGREGATION_OP.get(metric_name, AGG_SUM)
    return _AGG_OP_TO_CALC_METHOD.get(op, CALC_METHOD_SUM)


# ── Monetary metrics ──────────────────────────────────────────────────────────

# Metric names that carry a monetary value and require an authoritative
# currency code on the snapshot.  Ingestion blocks when these metrics are
# present but no currency_code is supplied.
MONETARY_METRICS: frozenset[str] = frozenset({METRIC_REVENUE_ESTIMATE})
