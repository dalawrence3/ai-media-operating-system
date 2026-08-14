"""Deterministic hashing for Phase 6 M6.1 production plan generation.

Changing any input to compute_production_plan_input_hash() — including script
content, algorithm version constants, or generation settings — produces a new
hash, which causes create_production_plan() to create a new plan row rather
than returning the existing one.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _canonical_json(obj: Any) -> str:
    """Compact, sorted-key JSON suitable for hashing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_script_body_hash(body_json: str) -> str:
    """SHA-256 of the exact canonical body_json string (UTF-8).

    This is independent of all algorithm versions and generation settings;
    it changes only when the script content itself changes.
    """
    return hashlib.sha256(body_json.encode("utf-8")).hexdigest()


def compute_production_plan_input_hash(
    *,
    script_id: int,
    script_version: int,
    script_body_hash: str,
    plan_schema_version: str,
    renderer_version: str,
    duration_algorithm_version: str,
    script_format: str,
    evidence_hash: str,
    requires_evidence_review: bool,
    max_segment_duration_s: int | None = None,
) -> str:
    """SHA-256 of compact sorted JSON covering all behaviour-affecting inputs.

    Changing any version constant or any script attribute produces a new hash.
    max_segment_duration_s is included so split and unsplit plans have distinct hashes.
    """
    payload = {
        "script_id": script_id,
        "script_version": script_version,
        "script_body_hash": script_body_hash,
        "plan_schema_version": plan_schema_version,
        "renderer_version": renderer_version,
        "duration_algorithm_version": duration_algorithm_version,
        "script_format": script_format,
        "evidence_hash": evidence_hash,
        "requires_evidence_review": requires_evidence_review,
        "max_segment_duration_s": max_segment_duration_s,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
