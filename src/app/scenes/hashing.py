"""Deterministic input hash for scene manifests.

The hash captures everything that would produce a different manifest:
- upstream run IDs and hashes
- planner and schema versions
- narration segment hashes (order-sensitive)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ManifestHashInput:
    caption_run_id: int
    narration_run_id: int
    plan_id: int
    script_id: int
    topic_id: int
    caption_input_hash: str
    narration_input_hash: str
    manifest_schema_version: str
    planner_version: str
    experiment_id: str | None
    # ordered list of (segment_id, narration_text_hash, duration_ms)
    segment_tuples: list[tuple[int, str, int]]


def compute_manifest_input_hash(inp: ManifestHashInput) -> str:
    payload = {
        "caption_run_id": inp.caption_run_id,
        "narration_run_id": inp.narration_run_id,
        "plan_id": inp.plan_id,
        "script_id": inp.script_id,
        "topic_id": inp.topic_id,
        "caption_input_hash": inp.caption_input_hash,
        "narration_input_hash": inp.narration_input_hash,
        "manifest_schema_version": inp.manifest_schema_version,
        "planner_version": inp.planner_version,
        "experiment_id": inp.experiment_id,
        "segments": [
            {"segment_id": s, "narration_text_hash": h, "duration_ms": d}
            for s, h, d in inp.segment_tuples
        ],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()
