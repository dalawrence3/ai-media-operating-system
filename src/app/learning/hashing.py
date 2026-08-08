"""Phase 11 — deterministic SHA-256 hashing for recommendations.

Every recommendation and review event carries an input_hash derived
from the fields that produced it.  Identical inputs always produce the
same hash.  Any field change forces a new row.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.learning.errors import HashInputError


@dataclass(frozen=True)
class LearningRunHashInput:
    """Canonical fields that uniquely identify a learning run."""

    topic_id: int
    publication_id: int | None  # None = topic-level run
    engine_version: str
    schema_version: str
    algorithm_version: str
    snapshot_ids_sorted: list[int]  # all contributing snapshot IDs, sorted


@dataclass(frozen=True)
class RecommendationHashInput:
    """Canonical fields that uniquely identify a recommendation."""

    learning_run_id: int
    topic_id: int
    publication_id: int | None
    domain: str
    subsystem: str
    measure: str
    engine_version: str
    schema_version: str
    algorithm_version: str
    evidence_classification: str
    recommendation_strength: str
    # Snapshot IDs contributing to the evidence (sorted for determinism)
    evidence_snapshot_ids_sorted: list[int]


@dataclass(frozen=True)
class ReviewEventHashInput:
    """Canonical fields that uniquely identify a review event."""

    recommendation_id: int
    topic_id: int
    event_type: str
    reviewer: str


def _stable_json(obj: object) -> str:
    """Compact, sorted-key JSON with no whitespace."""
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError) as exc:
        raise HashInputError(f"Non-serializable value in hash input: {exc}") from exc


def compute_learning_run_hash(inp: LearningRunHashInput) -> str:
    """Return a hex SHA-256 hash for the learning run input."""
    payload = _stable_json(
        {
            "topic_id": inp.topic_id,
            "publication_id": inp.publication_id,
            "engine_version": inp.engine_version,
            "schema_version": inp.schema_version,
            "algorithm_version": inp.algorithm_version,
            "snapshot_ids_sorted": sorted(inp.snapshot_ids_sorted),
        }
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def compute_recommendation_hash(inp: RecommendationHashInput) -> str:
    """Return a hex SHA-256 hash for the recommendation input."""
    payload = _stable_json(
        {
            "learning_run_id": inp.learning_run_id,
            "topic_id": inp.topic_id,
            "publication_id": inp.publication_id,
            "domain": inp.domain,
            "subsystem": inp.subsystem,
            "measure": inp.measure,
            "engine_version": inp.engine_version,
            "schema_version": inp.schema_version,
            "algorithm_version": inp.algorithm_version,
            "evidence_classification": inp.evidence_classification,
            "recommendation_strength": inp.recommendation_strength,
            "evidence_snapshot_ids_sorted": sorted(inp.evidence_snapshot_ids_sorted),
        }
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def compute_review_event_hash(inp: ReviewEventHashInput) -> str:
    """Return a hex SHA-256 hash for the review event input."""
    payload = _stable_json(
        {
            "recommendation_id": inp.recommendation_id,
            "topic_id": inp.topic_id,
            "event_type": inp.event_type,
            "reviewer": inp.reviewer,
        }
    )
    return hashlib.sha256(payload.encode()).hexdigest()
