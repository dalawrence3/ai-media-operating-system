"""Deterministic input hash for publishing plans.

Every field that changes publishing behavior participates in the hash.
Identical inputs for the same approved render → same plan (idempotent).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.publishing.constants import PUBLISHING_ENGINE_VERSION, PUBLISHING_METADATA_VERSION


@dataclass(frozen=True)
class PublishingHashInput:
    """All behavior-affecting fields for a publishing plan."""

    # Render provenance
    render_manifest_id: int
    output_sha256: str

    # Provider identity
    provider: str
    provider_version: str

    # Metadata
    title: str
    description: str
    tags: tuple[str, ...]
    language: str
    visibility: str
    category: str | None
    made_for_kids: bool
    captions_path: str | None
    playlist_id: str | None
    copyright_notice: str | None
    licensing_notes: str | None

    # Scheduling
    schedule_type: str
    scheduled_at: str | None
    timezone: str | None

    # Experiment
    experiment_id: str | None

    # Versions (bump = new hash = new plan)
    engine_version: str = PUBLISHING_ENGINE_VERSION
    metadata_version: str = PUBLISHING_METADATA_VERSION


def compute_publishing_input_hash(inp: PublishingHashInput) -> str:
    """Return a stable SHA-256 hex digest over all publishing identity fields."""
    payload = json.dumps(
        {
            "render_manifest_id": inp.render_manifest_id,
            "output_sha256": inp.output_sha256,
            "provider": inp.provider,
            "provider_version": inp.provider_version,
            "title": inp.title,
            "description": inp.description,
            "tags": sorted(inp.tags),
            "language": inp.language,
            "visibility": inp.visibility,
            "category": inp.category,
            "made_for_kids": inp.made_for_kids,
            "captions_path": inp.captions_path,
            "playlist_id": inp.playlist_id,
            "copyright_notice": inp.copyright_notice,
            "licensing_notes": inp.licensing_notes,
            "schedule_type": inp.schedule_type,
            "scheduled_at": inp.scheduled_at,
            "timezone": inp.timezone,
            "experiment_id": inp.experiment_id,
            "engine_version": inp.engine_version,
            "metadata_version": inp.metadata_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()
