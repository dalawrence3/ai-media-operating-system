"""Phase 13D-A — Exploration run, probe, and contract models.

No LLM calls. No YouTube calls. Persistence contracts only.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ExplorationProbeType(StrEnum):
    CHANNEL_BOOTSTRAP = "channel_bootstrap"
    MARKET_REGION = "market_region"
    ADJACENT_TOPIC = "adjacent_topic"
    VELOCITY_FOLLOWUP = "velocity_followup"
    VALIDATION = "validation"


class ExplorationProbeStatus(StrEnum):
    CANDIDATE = "candidate"
    SELECTED = "selected"
    DEFERRED = "deferred"
    REJECTED = "rejected"
    DISPATCHED = "dispatched"


class ExplorationRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class SemanticFitEvaluatorType(StrEnum):
    LEXICAL = "lexical"
    LLM = "llm"


class ProbeEvidenceType(StrEnum):
    OBSERVATION = "observation"
    VELOCITY = "velocity"
    JOB = "job"


# ---------------------------------------------------------------------------
# Collection policy
# ---------------------------------------------------------------------------

_VALID_ORDERS: frozenset[str] = frozenset({"relevance", "date", "viewCount", "rating"})


class CollectionPolicy(BaseModel):
    """Structured collection policy snapshot stored on each probe.

    V1 defaults to max_pages=1 for inexpensive market exploration.
    The architecture never hard-codes this — it is always a policy.
    """

    model_config = ConfigDict(extra="forbid")

    max_pages: int = Field(default=1, ge=1, le=10)
    max_results: int = Field(default=25, ge=1, le=50)
    order: str = Field(default="relevance")
    region_code: str | None = None
    language_code: str | None = None
    published_after: str | None = None
    # expected_max_search_calls must be >= max_pages
    # (one search.list call per page is the minimum for the current collector)
    expected_max_search_calls: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _validate_expected_calls(self) -> CollectionPolicy:
        if self.expected_max_search_calls < self.max_pages:
            raise ValueError(
                f"expected_max_search_calls ({self.expected_max_search_calls}) "
                f"must be >= max_pages ({self.max_pages})"
            )
        if self.order not in _VALID_ORDERS:
            raise ValueError(f"order must be one of {sorted(_VALID_ORDERS)}, got {self.order!r}")
        return self


# ---------------------------------------------------------------------------
# Priority components contract
# ---------------------------------------------------------------------------


class PriorityComponents(BaseModel):
    """Normalized priority components for a probe.

    All values are in [0.0, 1.0] or None (not yet evaluated).
    Raw metrics (e.g. raw view count) must NOT appear here directly —
    they must be normalised to [0, 1] before assignment.

    Final priority_score is computed by the planner (Phase 13D-D)
    as a weighted sum of populated components.
    """

    model_config = ConfigDict(extra="forbid")

    niche_fit: float | None = Field(default=None, ge=0.0, le=1.0)
    novelty: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_strength: float | None = Field(default=None, ge=0.0, le=1.0)
    velocity_trigger: float | None = Field(default=None, ge=0.0, le=1.0)
    corroboration: float | None = Field(default=None, ge=0.0, le=1.0)
    depth_factor: float | None = Field(default=None, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Semantic niche-fit contract
# ---------------------------------------------------------------------------


class SemanticNicheFit(BaseModel):
    """Typed result of a semantic niche-fit evaluation.

    Phase 13D-D fills this. Phase 13D-A defines the contract only.
    V1 lexical evaluator uses Jaccard similarity; later versions may use LLM.
    """

    model_config = ConfigDict(extra="forbid")

    eligible: bool
    fit_score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=500)
    evaluator_type: str  # SemanticFitEvaluatorType value
    evaluator_version: str  # e.g. "v1"


# ---------------------------------------------------------------------------
# Persisted domain models (mirror DB rows)
# ---------------------------------------------------------------------------

PLANNER_VERSION = "v1"


class ExplorationRun(BaseModel):
    """One invocation of the Market Exploration Planner.

    Persisted to market_exploration_runs. Channel-specific — NOT global.
    """

    id: int
    workspace_id: str | None = None
    channel_id: int | None = None

    planner_version: str = PLANNER_VERSION
    prompt_version: str | None = None
    provider: str | None = None
    model: str | None = None

    max_depth: int = 3
    max_probes: int = 10
    search_budget: int = 20
    policy_json: str = "{}"

    input_hash: str

    status: str = "pending"

    candidate_count: int = 0
    selected_count: int = 0
    deferred_count: int = 0
    rejected_count: int = 0
    dispatched_count: int = 0

    error_message: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str

    def policy(self) -> dict[str, Any]:
        return json.loads(self.policy_json)


class ExplorationProbe(BaseModel):
    """A market research question the planner considered.

    Persisted to market_exploration_probes. Represents a planner decision
    even when the probe was never dispatched. Channel-specific — NOT global.
    """

    id: int
    exploration_run_id: int
    workspace_id: str | None = None
    channel_id: int | None = None

    query_text: str
    normalized_query: str

    probe_type: str

    parent_probe_id: int | None = None
    parent_job_id: int | None = None
    exploration_depth: int = 0

    region_code: str | None = None
    language_code: str | None = None

    collection_policy_json: str = "{}"

    status: str = "candidate"

    priority_score: float | None = None
    priority_components_json: str | None = None

    niche_fit_score: float | None = None
    semantic_fit_status: str | None = None

    market_region_label: str | None = None  # LLM-generated descriptive label (Phase 13E.1)
    decision_reason: str | None = None
    corroboration_count: int = 0

    dispatched_job_id: int | None = None

    planner_version: str = PLANNER_VERSION
    input_hash: str

    decided_at: str | None = None
    dispatched_at: str | None = None
    created_at: str

    def collection_policy(self) -> CollectionPolicy:
        data = json.loads(self.collection_policy_json)
        if not data:
            return CollectionPolicy()
        return CollectionPolicy(**data)

    def priority_components(self) -> PriorityComponents | None:
        if self.priority_components_json is None:
            return None
        data = json.loads(self.priority_components_json)
        return PriorityComponents(**data)


class ProbeEvidence(BaseModel):
    """Provenance link from a probe to the market evidence that supports it."""

    id: int
    probe_id: int
    evidence_type: str
    observation_id: int | None = None
    velocity_id: int | None = None
    job_id: int | None = None
    evidence_notes: str | None = None
    created_at: str


# ---------------------------------------------------------------------------
# Input hash helpers
# ---------------------------------------------------------------------------


def make_run_input_hash(
    *,
    channel_id: int | None,
    workspace_id: str | None,
    planner_version: str,
    max_depth: int,
    max_probes: int,
    search_budget: int,
    created_at: str,
) -> str:
    """Deterministic run identity hash."""
    payload: dict[str, Any] = {
        "channel_id": channel_id,
        "workspace_id": workspace_id,
        "planner_version": planner_version,
        "max_depth": max_depth,
        "max_probes": max_probes,
        "search_budget": search_budget,
        "created_at": created_at,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def make_probe_input_hash(
    *,
    normalized_query: str,
    probe_type: str,
    region_code: str | None,
    language_code: str | None,
    max_pages: int,
    max_results: int,
    order: str,
    published_after: str | None,
    planner_version: str,
) -> str:
    """Deterministic probe identity hash.

    Captures the effective market question: same query + same collection
    policy + same planner version → same hash.
    """
    payload: dict[str, Any] = {
        "normalized_query": normalized_query,
        "probe_type": probe_type,
        "region_code": region_code,
        "language_code": language_code,
        "max_pages": max_pages,
        "max_results": max_results,
        "order": order,
        "published_after": published_after,
        "planner_version": planner_version,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()
