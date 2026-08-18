"""Phase 10 analytics orchestrator — coordinates ingest, normalization, and storage.

The orchestrator is the only entry point for analytics workflows.
It depends only on the AnalyticsProvider protocol.

Publication eligibility
-----------------------
Every ingest call validates the target publication before fetching any data:

1. Publication exists in the database.
2. publication.status is in PUBLICATION_ELIGIBLE_STATUSES.
3. publication.provider_video_id is not NULL or empty.
4. publication.provider matches the analytics provider name.
5. Render manifest referenced by render_manifest_id exists.
6. Render manifest status is 'approved'.
7. Render manifest is not superseded (superseded_at IS NULL).

A failed or superseded render must never produce analytics — it implies the
content that was published does not correspond to the canonical approved render.

Currency requirement
--------------------
If the provider returns any monetary metrics (revenue_estimate), the caller
must supply an ISO 4217 currency_code (e.g. 'USD').  Ingestion raises
MissingCurrencyError if monetary metrics are present but no code is given.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime

from app.analytics.aggregation import aggregate_all_periods
from app.analytics.constants import (
    ANALYTICS_ADAPTER_VERSION,
    MONETARY_METRICS,
    PUBLICATION_ELIGIBLE_STATUSES,
)
from app.analytics.errors import (
    DuplicateSnapshotError,
    MissingCurrencyError,
    PublicationIneligibleError,
)
from app.analytics.hashing import AnalyticsHashInput, compute_analytics_input_hash
from app.analytics.models import (
    AnalyticsMetric,
    AnalyticsReviewEvent,
    AnalyticsSnapshot,
)
from app.analytics.normalization import validate_canonical_metrics
from app.analytics.protocol import AnalyticsProvider, ProviderMetrics
from app.analytics.repository import (
    create_metric,
    create_review_event,
    create_snapshot,
    get_snapshot_by_hash,
    list_metrics_for_snapshot,
    list_review_events,
    list_snapshots,
)
from app.analytics.validation import (
    validate_ingest_draft,
    validate_review_notes,
    validate_review_severity,
)


def _review_hash(snapshot_id: int, severity: str, reviewer: str) -> str:
    payload = json.dumps(
        {"snapshot_id": snapshot_id, "severity": severity, "reviewer": reviewer},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _validate_publication_eligibility(
    conn: sqlite3.Connection,
    publication_id: int,
    render_manifest_id: int,
    provider_name: str,
) -> None:
    """Verify the full publication chain is eligible for analytics ingestion.

    Raises PublicationIneligibleError for any of:
    - Publication not found
    - publication.status not in PUBLICATION_ELIGIBLE_STATUSES
    - publication.provider_video_id is NULL or empty
    - publication.provider does not match the analytics provider
    - Render manifest not found
    - Render manifest status is not 'approved'
    - Render manifest is superseded
    """
    pub = conn.execute(
        "SELECT status, provider_video_id, provider FROM publications WHERE id = ?",
        (publication_id,),
    ).fetchone()

    if pub is None:
        raise PublicationIneligibleError(publication_id, "publication not found in database")

    status = pub["status"] or ""
    if status not in PUBLICATION_ELIGIBLE_STATUSES:
        raise PublicationIneligibleError(
            publication_id,
            f"status {status!r} is not eligible (expected one of "
            f"{sorted(PUBLICATION_ELIGIBLE_STATUSES)})",
        )

    provider_video_id = pub["provider_video_id"]
    if not provider_video_id:
        raise PublicationIneligibleError(publication_id, "provider_video_id is null or empty")

    pub_provider = pub["provider"] or ""
    if pub_provider != provider_name:
        raise PublicationIneligibleError(
            publication_id,
            f"publication provider {pub_provider!r} does not match "
            f"analytics provider {provider_name!r}",
        )

    render = conn.execute(
        "SELECT status, superseded_at FROM render_manifests WHERE id = ?",
        (render_manifest_id,),
    ).fetchone()

    if render is None:
        raise PublicationIneligibleError(
            publication_id,
            f"render_manifest {render_manifest_id} not found",
        )

    render_status = render["status"] or ""
    if render_status != "approved":
        raise PublicationIneligibleError(
            publication_id,
            f"render_manifest {render_manifest_id} status {render_status!r} is not 'approved'",
        )

    if render["superseded_at"] is not None:
        raise PublicationIneligibleError(
            publication_id,
            f"render_manifest {render_manifest_id} has been superseded",
        )


class AnalyticsOrchestrator:
    """Coordinates analytics ingest, normalization, storage, and review."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        provider: AnalyticsProvider,
    ) -> None:
        self._conn = conn
        self._provider = provider

    # ── Ingest ────────────────────────────────────────────────────────────────

    def ingest(
        self,
        *,
        provider_video_id: str,
        publication_id: int,
        publishing_plan_id: int,
        publishing_job_id: int,
        render_manifest_id: int,
        scene_manifest_id: int,
        production_plan_id: int,
        script_id: int,
        topic_id: int,
        narration_run_id: int,
        caption_run_id: int,
        experiment_id: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        is_period_complete: bool = False,
        currency_code: str | None = None,
        adapter_version: str = ANALYTICS_ADAPTER_VERSION,
    ) -> tuple[AnalyticsSnapshot, list[AnalyticsMetric]]:
        """Fetch provider metrics, normalize, persist, and return snapshot + metrics.

        Idempotent: if the same input_hash already exists, returns the existing snapshot.
        Publication eligibility is validated before fetching any provider data.

        Args:
            currency_code: ISO 4217 code (e.g. 'USD').  Required when the
                provider returns monetary metrics (revenue_estimate).
            is_period_complete: True if the provider has confirmed this period
                is complete and will not be revised.  Default False (provisional).
        """
        validate_ingest_draft(
            self._provider.provider_name,
            publication_id,
            publishing_plan_id,
            publishing_job_id,
            topic_id,
        )

        _validate_publication_eligibility(
            self._conn,
            publication_id,
            render_manifest_id,
            self._provider.provider_name,
        )

        raw: ProviderMetrics = self._provider.fetch_metrics(
            provider_video_id,
            period_start=period_start,
            period_end=period_end,
        )
        normalized: dict[str, float] = self._provider.normalize(raw)
        validate_canonical_metrics(normalized)

        # Currency validation: required for monetary metrics
        monetary_present = MONETARY_METRICS & set(normalized)
        if monetary_present and not currency_code:
            raise MissingCurrencyError(monetary_present)

        response_fingerprint = hashlib.sha256(
            json.dumps(raw.raw, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        observation_state = "data" if raw.raw else "no_data"
        observed_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")

        hash_input = AnalyticsHashInput(
            provider=self._provider.provider_name,
            provider_version=self._provider.provider_version,
            publication_id=publication_id,
            publishing_plan_id=publishing_plan_id,
            publishing_job_id=publishing_job_id,
            render_manifest_id=render_manifest_id,
            scene_manifest_id=scene_manifest_id,
            production_plan_id=production_plan_id,
            script_id=script_id,
            topic_id=topic_id,
            narration_run_id=narration_run_id,
            caption_run_id=caption_run_id,
            experiment_id=experiment_id,
            period_start=period_start,
            period_end=period_end,
            response_fingerprint=response_fingerprint,
            adapter_version=adapter_version,
        )
        input_hash = compute_analytics_input_hash(hash_input)

        existing = get_snapshot_by_hash(self._conn, input_hash)
        if existing is not None:
            metrics = list_metrics_for_snapshot(self._conn, existing.id)
            return existing, metrics

        try:
            snapshot = create_snapshot(
                self._conn,
                publication_id=publication_id,
                publishing_plan_id=publishing_plan_id,
                publishing_job_id=publishing_job_id,
                render_manifest_id=render_manifest_id,
                scene_manifest_id=scene_manifest_id,
                production_plan_id=production_plan_id,
                script_id=script_id,
                topic_id=topic_id,
                narration_run_id=narration_run_id,
                caption_run_id=caption_run_id,
                experiment_id=experiment_id,
                provider=self._provider.provider_name,
                provider_version=self._provider.provider_version,
                adapter_version=adapter_version,
                raw_metrics=raw.raw,
                input_hash=input_hash,
                period_start=period_start,
                period_end=period_end,
                is_period_complete=is_period_complete,
                currency_code=currency_code,
                observed_at=observed_at,
                response_fingerprint=response_fingerprint,
                observation_state=observation_state,
            )
        except DuplicateSnapshotError:
            existing = get_snapshot_by_hash(self._conn, input_hash)
            assert existing is not None
            metrics = list_metrics_for_snapshot(self._conn, existing.id)
            return existing, metrics

        metrics: list[AnalyticsMetric] = []
        if observation_state == "data":
            for metric_name, metric_value in normalized.items():
                m = create_metric(
                    self._conn,
                    snapshot_id=snapshot.id,
                    publication_id=publication_id,
                    topic_id=topic_id,
                    provider=self._provider.provider_name,
                    metric_name=metric_name,
                    metric_value=metric_value,
                    period_start=period_start,
                    period_end=period_end,
                    input_hash=input_hash,
                )
                metrics.append(m)

        return snapshot, metrics

    # ── Aggregation ───────────────────────────────────────────────────────────

    def aggregate(
        self,
        *,
        publication_id: int,
        topic_id: int,
    ) -> None:
        """Recompute all period rollups for a publication."""
        aggregate_all_periods(
            self._conn,
            publication_id=publication_id,
            topic_id=topic_id,
            provider=self._provider.provider_name,
        )

    # ── Review events ─────────────────────────────────────────────────────────

    def record_review(
        self,
        *,
        snapshot_id: int,
        severity: str,
        notes: str = "",
        reviewer: str = "",
    ) -> AnalyticsReviewEvent:
        """Append a review event to a snapshot."""
        validate_review_severity(severity)
        validate_review_notes(severity, notes)
        event_hash = _review_hash(snapshot_id, severity, reviewer)
        return create_review_event(
            self._conn,
            snapshot_id=snapshot_id,
            severity=severity,
            notes=notes,
            reviewer=reviewer,
            input_hash=event_hash,
        )

    # ── Queries ───────────────────────────────────────────────────────────────

    def list_snapshots(
        self,
        *,
        publication_id: int | None = None,
        topic_id: int | None = None,
        limit: int = 50,
    ) -> list[AnalyticsSnapshot]:
        return list_snapshots(
            self._conn,
            publication_id=publication_id,
            topic_id=topic_id,
            provider=self._provider.provider_name,
            limit=limit,
        )

    def list_review_events(
        self,
        snapshot_id: int | None = None,
    ) -> list[AnalyticsReviewEvent]:
        return list_review_events(self._conn, snapshot_id=snapshot_id)
