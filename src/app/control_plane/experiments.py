"""Experiment lifecycle management (immutable once activated)."""

from __future__ import annotations

import uuid
from typing import Any

from app.control_plane import repository as repo
from app.control_plane.constants import (
    EXPERIMENT_IMMUTABLE_STATUSES,
    EXPERIMENT_STATUS_ACTIVE,
)
from app.control_plane.errors import (
    ExperimentAlreadyActiveError,
    ExperimentNotActiveError,
)
from app.control_plane.models import (
    Experiment,
    ExperimentAssignment,
    ExperimentDraft,
    ExperimentVariant,
    ExperimentVariantDraft,
)


def create_experiment(
    conn: Any,
    *,
    workspace_id: str,
    channel_id: str,
    name: str,
    hypothesis: str,
    primary_metric: str,
    actor: str,
) -> Experiment:
    draft = ExperimentDraft(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        channel_id=channel_id,
        name=name,
        hypothesis=hypothesis,
        primary_metric=primary_metric,
        actor=actor,
        status="draft",
    )
    return repo.create_experiment(conn, draft)


def add_variant(
    conn: Any,
    *,
    experiment_id: str,
    name: str,
    variant_type: str,
    description: str | None = None,
    config: dict[str, Any] | None = None,
) -> ExperimentVariant:
    experiment = repo.get_experiment(conn, experiment_id)
    if experiment.status in EXPERIMENT_IMMUTABLE_STATUSES:
        raise ExperimentAlreadyActiveError(experiment_id)
    draft = ExperimentVariantDraft(
        id=str(uuid.uuid4()),
        experiment_id=experiment_id,
        name=name,
        variant_type=variant_type,
        description=description,
        config=config,
    )
    return repo.create_experiment_variant(conn, draft)


def activate_experiment(conn: Any, experiment_id: str) -> Experiment:
    experiment = repo.get_experiment(conn, experiment_id)
    if experiment.status in EXPERIMENT_IMMUTABLE_STATUSES:
        raise ExperimentAlreadyActiveError(experiment_id)
    return repo.activate_experiment(conn, experiment_id)


def conclude_experiment(conn: Any, experiment_id: str) -> Experiment:
    experiment = repo.get_experiment(conn, experiment_id)
    if experiment.status != EXPERIMENT_STATUS_ACTIVE:
        raise ExperimentNotActiveError(f"Experiment {experiment_id} must be active to conclude")
    return repo.conclude_experiment(conn, experiment_id)


def cancel_experiment(conn: Any, experiment_id: str) -> Experiment:
    experiment = repo.get_experiment(conn, experiment_id)
    if experiment.status in (
        "concluded",
        "cancelled",
    ):
        raise ExperimentAlreadyActiveError(experiment_id)
    return repo.cancel_experiment(conn, experiment_id)


def assign_unit(
    conn: Any,
    experiment_id: str,
    unit_id: str,
) -> ExperimentAssignment:
    experiment = repo.get_experiment(conn, experiment_id)
    if experiment.status != EXPERIMENT_STATUS_ACTIVE:
        raise ExperimentNotActiveError(f"Experiment {experiment_id} must be active for assignments")
    variants = repo.list_variants_by_experiment(conn, experiment_id)
    if not variants:
        raise ValueError(f"Experiment {experiment_id} has no variants")

    unit_hash = hash(unit_id) % len(variants)
    target_variant = variants[unit_hash]

    return repo.get_or_create_assignment(
        conn,
        assignment_id=str(uuid.uuid4()),
        experiment_id=experiment_id,
        variant_id=target_variant.id,
        unit_id=unit_id,
    )
