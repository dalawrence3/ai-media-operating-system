"""Phase 10 analytics domain errors."""

from __future__ import annotations


class AnalyticsError(Exception):
    """Base class for all analytics errors."""


class SnapshotNotFoundError(AnalyticsError):
    """Raised when an analytics snapshot cannot be found by ID."""

    def __init__(self, snapshot_id: int) -> None:
        super().__init__(f"Analytics snapshot {snapshot_id} not found")
        self.snapshot_id = snapshot_id


class MetricNotFoundError(AnalyticsError):
    """Raised when an analytics metric cannot be found by ID."""

    def __init__(self, metric_id: int) -> None:
        super().__init__(f"Analytics metric {metric_id} not found")
        self.metric_id = metric_id


class AggregateNotFoundError(AnalyticsError):
    """Raised when an analytics aggregate cannot be found."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"Analytics aggregate not found: {detail}")


class ReviewEventNotFoundError(AnalyticsError):
    """Raised when an analytics review event cannot be found by ID."""

    def __init__(self, event_id: int) -> None:
        super().__init__(f"Analytics review event {event_id} not found")
        self.event_id = event_id


class DuplicateSnapshotError(AnalyticsError):
    """Raised when a snapshot with the same input_hash already exists."""

    def __init__(self, input_hash: str) -> None:
        super().__init__(f"Snapshot with hash {input_hash!r} already exists")
        self.input_hash = input_hash


class UnknownMetricError(AnalyticsError):
    """Raised when a provider emits a metric with an unknown canonical name."""

    def __init__(self, metric_name: str) -> None:
        super().__init__(f"Unknown canonical metric: {metric_name!r}")
        self.metric_name = metric_name


class UnknownPeriodTypeError(AnalyticsError):
    """Raised when an aggregation is requested for an unsupported period type."""

    def __init__(self, period_type: str) -> None:
        super().__init__(f"Unknown period type: {period_type!r}")
        self.period_type = period_type


class ProviderAdapterError(AnalyticsError):
    """Raised when an analytics provider adapter fails."""


class NormalizationError(AnalyticsError):
    """Raised when provider metrics cannot be normalized to canonical form."""


class ReviewNotesRequiredError(AnalyticsError):
    """Raised when notes are required but not provided for a review event."""

    def __init__(self, severity: str) -> None:
        super().__init__(f"Notes are required for severity {severity!r}")
        self.severity = severity


class PublicationIneligibleError(AnalyticsError):
    """Raised when a publication is not eligible for analytics ingestion.

    A publication is ineligible if it does not exist, has a failed or deleted
    status, or has a null provider_video_id.
    """

    def __init__(self, publication_id: int, reason: str) -> None:
        super().__init__(f"Publication {publication_id} is ineligible for analytics: {reason}")
        self.publication_id = publication_id
        self.reason = reason


class CurrencyMismatchError(AnalyticsError):
    """Raised when revenue aggregation spans snapshots with different currencies.

    Revenue amounts can only be summed within a single currency.
    """

    def __init__(self, currencies: set[str]) -> None:
        sorted_currencies = sorted(currencies)
        super().__init__(f"Cannot aggregate revenue across mixed currencies: {sorted_currencies}")
        self.currencies = currencies


class MissingCurrencyError(AnalyticsError):
    """Raised when monetary metrics are present but no currency code is supplied.

    All ingests that include revenue_estimate or other monetary metrics must
    provide an ISO 4217 currency code so storage and aggregation are unambiguous.
    """

    def __init__(self, monetary_metrics: set[str]) -> None:
        super().__init__(
            f"Monetary metrics {sorted(monetary_metrics)} require a currency_code "
            "(ISO 4217, e.g. 'USD'). Pass currency_code to the ingest call."
        )
        self.monetary_metrics = monetary_metrics
