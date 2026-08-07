"""Deterministic hashing for Phase 12 Control Plane entities."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class WorkspaceHashInput:
    name: str
    slug: str
    actor: str


def compute_workspace_hash(inp: WorkspaceHashInput) -> str:
    return _sha256_hex(_stable_json({"actor": inp.actor, "name": inp.name, "slug": inp.slug}))


@dataclass(frozen=True)
class CredentialProfileHashInput:
    workspace_id: str
    display_name: str
    credential_type: str
    external_ref: str
    actor: str


def compute_credential_hash(inp: CredentialProfileHashInput) -> str:
    return _sha256_hex(
        _stable_json(
            {
                "actor": inp.actor,
                "credential_type": inp.credential_type,
                "display_name": inp.display_name,
                "external_ref": inp.external_ref,
                "workspace_id": inp.workspace_id,
            }
        )
    )


@dataclass(frozen=True)
class EventHashInput:
    event_type: str
    workspace_id: str
    actor: str
    payload: dict[str, Any]
    correlation_id: str | None


def compute_event_hash(inp: EventHashInput) -> str:
    return _sha256_hex(
        _stable_json(
            {
                "actor": inp.actor,
                "correlation_id": inp.correlation_id,
                "event_type": inp.event_type,
                "payload": inp.payload,
                "workspace_id": inp.workspace_id,
            }
        )
    )


@dataclass(frozen=True)
class WorkflowHashInput:
    workspace_id: str
    name: str
    trigger_event_type: str
    conditions: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    actor: str


def compute_workflow_hash(inp: WorkflowHashInput) -> str:
    return _sha256_hex(
        _stable_json(
            {
                "actions": inp.actions,
                "actor": inp.actor,
                "conditions": inp.conditions,
                "name": inp.name,
                "trigger_event_type": inp.trigger_event_type,
                "workspace_id": inp.workspace_id,
            }
        )
    )


@dataclass(frozen=True)
class ExperimentHashInput:
    workspace_id: str
    channel_id: str
    name: str
    hypothesis: str
    primary_metric: str
    actor: str


def compute_experiment_hash(inp: ExperimentHashInput) -> str:
    return _sha256_hex(
        _stable_json(
            {
                "actor": inp.actor,
                "channel_id": inp.channel_id,
                "hypothesis": inp.hypothesis,
                "name": inp.name,
                "primary_metric": inp.primary_metric,
                "workspace_id": inp.workspace_id,
            }
        )
    )


@dataclass(frozen=True)
class OperationHashInput:
    operation_type: str
    workspace_id: str
    actor: str
    input_data: dict[str, Any] | None


def compute_operation_idempotency_key(inp: OperationHashInput) -> str:
    return _sha256_hex(
        _stable_json(
            {
                "actor": inp.actor,
                "input_data": inp.input_data,
                "operation_type": inp.operation_type,
                "workspace_id": inp.workspace_id,
            }
        )
    )


@dataclass(frozen=True)
class ChannelHashInput:
    workspace_id: str
    name: str
    slug: str
    actor: str


def compute_channel_hash(inp: ChannelHashInput) -> str:
    return _sha256_hex(
        _stable_json(
            {
                "actor": inp.actor,
                "name": inp.name,
                "slug": inp.slug,
                "workspace_id": inp.workspace_id,
            }
        )
    )
