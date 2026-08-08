"""Deterministic input hash for render manifests.

The hash captures everything that would produce a different render:
- upstream manifest and run IDs / hashes
- compositor and schema versions
- resolution, fps, caption_burn_in
- per-scene audio hashes (order-sensitive)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class RenderHashInput:
    scene_manifest_id: int
    narration_run_id: int
    caption_run_id: int
    topic_id: int
    plan_id: int
    script_id: int
    scene_manifest_input_hash: str   # from ApprovedSceneManifest.input_hash
    narration_input_hash: str        # from ApprovedNarrationRun.input_hash
    render_schema_version: str
    compositor_version: str
    width: int
    height: int
    fps: int
    caption_burn_in: bool
    experiment_id: str | None
    # ordered list of (scene_index, segment_id, audio_sha256_or_none)
    scene_tuples: list[tuple[int, int, str | None]]


def compute_render_input_hash(inp: RenderHashInput) -> str:
    payload = {
        "scene_manifest_id": inp.scene_manifest_id,
        "narration_run_id": inp.narration_run_id,
        "caption_run_id": inp.caption_run_id,
        "topic_id": inp.topic_id,
        "plan_id": inp.plan_id,
        "script_id": inp.script_id,
        "scene_manifest_input_hash": inp.scene_manifest_input_hash,
        "narration_input_hash": inp.narration_input_hash,
        "render_schema_version": inp.render_schema_version,
        "compositor_version": inp.compositor_version,
        "width": inp.width,
        "height": inp.height,
        "fps": inp.fps,
        "caption_burn_in": inp.caption_burn_in,
        "experiment_id": inp.experiment_id,
        "scenes": [
            {"scene_index": si, "segment_id": seg, "audio_sha256": sha}
            for si, seg, sha in inp.scene_tuples
        ],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()
