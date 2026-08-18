"""Phase 10 analytics domain models.

Dataclasses = mutable pre-persistence drafts.
Pydantic BaseModels = frozen DB row projections.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from pydantic import BaseModel

# ── Pre-persistence drafts (mutable) ─────────────────────────────────────────


@dataclass
class AnalyticsIngestDraft:
    """Everything needed to create an analytics snapshot."""

    publication_id: int
    publishing_plan_id: int
    publishing_job_id: int
    render_manifest_id: int
    scene_manifest_id: int
    production_plan_id: int
    script_id: int
    topic_id: int
    narration_run_id: int
    caption_run_id: int

    provider: str
    provider_version: str
    adapter_version: str

    raw_metrics: dict[str, object] = field(default_factory=dict)

    experiment_id: str | None = None
    period_start: str | None = None
    period_end: str | None = None


@dataclass
class ReviewEventDraft:
    """Human review event for an analytics snapshot."""

    snapshot_id: int
    severity: str
    notes: str = ""
    reviewer: str = ""


# ── DB row projections (frozen Pydantic) ──────────────────────────────────────


class AnalyticsSnapshot(BaseModel):
    """Frozen projection of an analytics_snapshots row."""

    model_config = {"frozen": True}

    id: int
    publication_id: int
    publishing_plan_id: int
    publishing_job_id: int
    render_manifest_id: int
    scene_manifest_id: int
    production_plan_id: int
    script_id: int
    topic_id: int
    narration_run_id: int
    caption_run_id: int
    experiment_id: str | None

    provider: str
    provider_version: str
    adapter_version: str
    engine_version: str
    analytics_schema_version: str
    db_schema_version: int

    input_hash: str
    raw_metrics_json: str

    period_start: str | None
    period_end: str | None

    # Reporting completeness: 0 = provisional, 1 = provider-confirmed final.
    is_period_complete: int

    # ISO 4217 currency code.  Required when monetary metrics (revenue_estimate) are present.
    # NULL for non-monetary snapshots.
    currency_code: str | None

    ingested_at: str
    created_at: str

    # v24 observation model — NULL on legacy snapshots created before schema v24.
    observed_at: str | None = None
    response_fingerprint: str | None = None
    observation_state: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> AnalyticsSnapshot:
        return cls(**dict(row))

    @property
    def raw_metrics(self) -> dict[str, object]:
        try:
            return json.loads(self.raw_metrics_json)
        except (json.JSONDecodeError, TypeError):
            return {}


class AnalyticsMetric(BaseModel):
    """Frozen projection of an analytics_metrics row (immutable)."""

    model_config = {"frozen": True}

    id: int
    snapshot_id: int
    publication_id: int
    topic_id: int
    provider: str

    metric_name: str
    metric_value: float
    period_start: str | None
    period_end: str | None

    input_hash: str
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> AnalyticsMetric:
        return cls(**dict(row))


class AnalyticsAggregate(BaseModel):
    """Frozen projection of an analytics_aggregates row."""

    model_config = {"frozen": True}

    id: int
    publication_id: int
    topic_id: int
    provider: str

    period_type: str
    period_key: str

    metric_name: str
    metric_value: float
    snapshot_count: int

    # How the metric_value was produced.
    #   'sum'                — additive/monetary: summed across disjoint base periods.
    #   'latest_observation' — gauge/ratio: most recently ingested value.
    # Phase 11 must distinguish these: a latest_observation is NOT a recomputed aggregate.
    calculation_method: str

    # ISO 4217 currency code for monetary aggregates.  NULL for non-monetary.
    currency_code: str | None

    # JSON array of analytics_snapshots IDs that contributed to this aggregate.
    # Enables reproducible lineage tracing.
    source_snapshot_ids_json: str

    input_hash: str
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> AnalyticsAggregate:
        return cls(**dict(row))

    @property
    def source_snapshot_ids(self) -> list[int]:
        try:
            return json.loads(self.source_snapshot_ids_json)
        except (json.JSONDecodeError, TypeError):
            return []


class AnalyticsReviewEvent(BaseModel):
    """Frozen projection of an analytics_review_events row (append-only)."""

    model_config = {"frozen": True}

    id: int
    snapshot_id: int
    severity: str
    notes: str
    reviewer: str
    input_hash: str
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> AnalyticsReviewEvent:
        return cls(**dict(row))


class AnalyticsHandoff(BaseModel):
    """Frozen bundle passed from Phase 10 (analytics) to Phase 11 (optimization).

    Phase 11 reads this model; it must never write back to analytics tables.
    All fields are read-only snapshots — append-only contract is enforced
    by refusing any UPDATE or DELETE on the underlying tables.
    """

    model_config = {"frozen": True}

    # Primary anchor
    publication_id: int
    topic_id: int
    provider: str

    # Full attribution chain
    publishing_plan_id: int
    publishing_job_id: int
    render_manifest_id: int
    scene_manifest_id: int
    production_plan_id: int
    script_id: int
    narration_run_id: int
    caption_run_id: int
    experiment_id: str | None

    # Analytics payload
    snapshots: list[AnalyticsSnapshot]
    metrics: list[AnalyticsMetric]
    aggregates: list[AnalyticsAggregate]
    review_events: list[AnalyticsReviewEvent]

    # Currency context for monetary metrics.  None when no monetary metrics are present.
    currency_code: str | None

    # Metadata
    engine_version: str
    analytics_schema_version: str
    handoff_created_at: str
