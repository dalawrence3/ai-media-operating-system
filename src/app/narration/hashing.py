"""Deterministic hashing for Phase 6 M6.2 narration generation.

Two hash functions:
  compute_narration_text_hash   — SHA-256 of the exact UTF-8 narration text.
  compute_narration_segment_input_hash — SHA-256 of compact sorted JSON of all
      output-affecting fields for one segment synthesis attempt.
  compute_narration_run_input_hash — SHA-256 of compact sorted JSON of all
      run-level output-affecting fields.
  compute_settings_hash         — SHA-256 of canonical settings JSON string.
"""

from __future__ import annotations

import hashlib
import json


def compute_narration_text_hash(text: str) -> str:
    """SHA-256 of the exact UTF-8 narration text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_settings_hash(settings_json: str) -> str:
    """SHA-256 of a canonical settings JSON string (for inclusion in hash payloads)."""
    return hashlib.sha256(settings_json.encode("utf-8")).hexdigest()


def compute_narration_segment_input_hash(
    *,
    plan_id: int,
    plan_input_hash: str,
    segment_id: int,
    narration_text_hash: str,
    provider: str,
    model: str,
    voice_id: str,
    voice_profile_id: int,
    voice_profile_version: int,
    language: str,
    speaking_rate: float,
    style: str | None,
    stability: float | None,
    similarity_boost: float | None,
    settings_json_hash: str,
    output_format: str,
    sample_rate_hz: int,
    narration_schema_version: str,
    narration_algorithm_version: str,
) -> str:
    """SHA-256 of compact sorted JSON of all output-affecting segment fields.

    Changing any field produces a new hash, triggering a new synthesis attempt.
    """
    payload: dict = {
        "plan_id": plan_id,
        "plan_input_hash": plan_input_hash,
        "segment_id": segment_id,
        "narration_text_hash": narration_text_hash,
        "provider": provider,
        "model": model,
        "voice_id": voice_id,
        "voice_profile_id": voice_profile_id,
        "voice_profile_version": voice_profile_version,
        "language": language,
        "speaking_rate": speaking_rate,
        "style": style,
        "stability": stability,
        "similarity_boost": similarity_boost,
        "settings_json_hash": settings_json_hash,
        "output_format": output_format,
        "sample_rate_hz": sample_rate_hz,
        "narration_schema_version": narration_schema_version,
        "narration_algorithm_version": narration_algorithm_version,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "utf-8"
        )
    ).hexdigest()


def compute_narration_run_input_hash(
    *,
    plan_id: int,
    plan_input_hash: str,
    voice_profile_id: int,
    voice_profile_version: int,
    language: str,
    speaking_rate: float,
    style: str | None,
    stability: float | None,
    similarity_boost: float | None,
    settings_json_hash: str,
    output_format: str,
    sample_rate_hz: int,
    narration_schema_version: str,
    narration_algorithm_version: str,
) -> str:
    """SHA-256 of compact sorted JSON of all run-level output-affecting fields.

    Changing voice profile settings or algorithms produces a new hash and a new run.
    """
    payload: dict = {
        "plan_id": plan_id,
        "plan_input_hash": plan_input_hash,
        "voice_profile_id": voice_profile_id,
        "voice_profile_version": voice_profile_version,
        "language": language,
        "speaking_rate": speaking_rate,
        "style": style,
        "stability": stability,
        "similarity_boost": similarity_boost,
        "settings_json_hash": settings_json_hash,
        "output_format": output_format,
        "sample_rate_hz": sample_rate_hz,
        "narration_schema_version": narration_schema_version,
        "narration_algorithm_version": narration_algorithm_version,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "utf-8"
        )
    ).hexdigest()
