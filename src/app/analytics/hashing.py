"""Deterministic input hash for analytics snapshots.

Same inputs → same SHA-256 digest, always.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.analytics.constants import (
    ANALYTICS_ADAPTER_VERSION,
    ANALYTICS_ENGINE_VERSION,
    ANALYTICS_SCHEMA_VERSION,
)


@dataclass(frozen=True)
class AnalyticsHashInput:
    """All attribution fields that uniquely identify an analytics ingest."""

    # Provider identity
    provider: str
    provider_version: str

    # Full publication provenance
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

    # Experiment
    experiment_id: str | None

    # Coverage window
    period_start: str | None
    period_end: str | None

    # Provider response fingerprint (SHA-256 of raw provider JSON).
    # Same window + same fingerprint → same snapshot (idempotent).
    # Same window + different fingerprint → new snapshot (data changed).
    response_fingerprint: str

    # Versions (bumping any forces a new hash → new snapshot)
    engine_version: str = ANALYTICS_ENGINE_VERSION
    adapter_version: str = ANALYTICS_ADAPTER_VERSION
    analytics_schema_version: str = ANALYTICS_SCHEMA_VERSION


def compute_analytics_input_hash(inp: AnalyticsHashInput) -> str:
    """Return a stable SHA-256 hex digest over all analytics identity fields."""
    payload = json.dumps(
        {
            "provider": inp.provider,
            "provider_version": inp.provider_version,
            "publication_id": inp.publication_id,
            "publishing_plan_id": inp.publishing_plan_id,
            "publishing_job_id": inp.publishing_job_id,
            "render_manifest_id": inp.render_manifest_id,
            "scene_manifest_id": inp.scene_manifest_id,
            "production_plan_id": inp.production_plan_id,
            "script_id": inp.script_id,
            "topic_id": inp.topic_id,
            "narration_run_id": inp.narration_run_id,
            "caption_run_id": inp.caption_run_id,
            "experiment_id": inp.experiment_id,
            "period_start": inp.period_start,
            "period_end": inp.period_end,
            "response_fingerprint": inp.response_fingerprint,
            "engine_version": inp.engine_version,
            "adapter_version": inp.adapter_version,
            "analytics_schema_version": inp.analytics_schema_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()
