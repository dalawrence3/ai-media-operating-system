"""Tests for Phase 6 M6.2 narration hashing functions."""

from __future__ import annotations

import hashlib
import json

from app.narration.hashing import (
    compute_narration_run_input_hash,
    compute_narration_segment_input_hash,
    compute_narration_text_hash,
    compute_settings_hash,
)

# ── compute_narration_text_hash ───────────────────────────────────────────────


def test_text_hash_is_hex_sha256() -> None:
    h = compute_narration_text_hash("hello world")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_text_hash_is_sha256_of_utf8() -> None:
    text = "hello world"
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert compute_narration_text_hash(text) == expected


def test_text_hash_deterministic() -> None:
    text = "Same text produces same hash."
    assert compute_narration_text_hash(text) == compute_narration_text_hash(text)


def test_text_hash_different_texts() -> None:
    assert compute_narration_text_hash("abc") != compute_narration_text_hash("ABC")


def test_text_hash_empty_string() -> None:
    h = compute_narration_text_hash("")
    expected = hashlib.sha256(b"").hexdigest()
    assert h == expected


# ── compute_settings_hash ─────────────────────────────────────────────────────


def test_settings_hash_is_sha256() -> None:
    h = compute_settings_hash("{}")
    expected = hashlib.sha256(b"{}").hexdigest()
    assert h == expected


def test_settings_hash_deterministic() -> None:
    s = '{"key": "value"}'
    assert compute_settings_hash(s) == compute_settings_hash(s)


def test_settings_hash_sensitive_to_content() -> None:
    assert compute_settings_hash("{}") != compute_settings_hash('{"a": 1}')


# ── compute_narration_segment_input_hash ──────────────────────────────────────


def _segment_hash(**overrides):
    defaults = dict(
        plan_id=1,
        plan_input_hash="a" * 64,
        segment_id=2,
        narration_text_hash="b" * 64,
        provider="fake",
        model="fake/FAKE",
        voice_id="fake-voice",
        voice_profile_id=1,
        voice_profile_version=1,
        language="en-US",
        speaking_rate=1.0,
        style=None,
        stability=None,
        similarity_boost=None,
        settings_json_hash="c" * 64,
        output_format="wav",
        sample_rate_hz=22050,
        narration_schema_version="Narration-v1",
        narration_algorithm_version="narration-segment-v1",
    )
    defaults.update(overrides)
    return compute_narration_segment_input_hash(**defaults)


def test_segment_hash_is_hex_sha256() -> None:
    h = _segment_hash()
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_segment_hash_deterministic() -> None:
    assert _segment_hash() == _segment_hash()


def test_segment_hash_sensitive_to_plan_id() -> None:
    assert _segment_hash(plan_id=1) != _segment_hash(plan_id=2)


def test_segment_hash_sensitive_to_segment_id() -> None:
    assert _segment_hash(segment_id=1) != _segment_hash(segment_id=2)


def test_segment_hash_sensitive_to_text_hash() -> None:
    h1 = _segment_hash(narration_text_hash="a" * 64)
    h2 = _segment_hash(narration_text_hash="b" * 64)
    assert h1 != h2


def test_segment_hash_sensitive_to_voice_id() -> None:
    assert _segment_hash(voice_id="v1") != _segment_hash(voice_id="v2")


def test_segment_hash_sensitive_to_provider() -> None:
    assert _segment_hash(provider="fake") != _segment_hash(provider="openai")


def test_segment_hash_sensitive_to_speaking_rate() -> None:
    assert _segment_hash(speaking_rate=1.0) != _segment_hash(speaking_rate=1.2)


def test_segment_hash_sensitive_to_schema_version() -> None:
    assert _segment_hash(narration_schema_version="Narration-v1") != _segment_hash(
        narration_schema_version="Narration-v2"
    )


def test_segment_hash_manual_sha256() -> None:
    """Manual verification: hash must equal SHA-256 of compact sorted JSON."""
    kwargs = dict(
        plan_id=1,
        plan_input_hash="a" * 64,
        segment_id=2,
        narration_text_hash="b" * 64,
        provider="fake",
        model="fake/FAKE",
        voice_id="v",
        voice_profile_id=1,
        voice_profile_version=1,
        language="en-US",
        speaking_rate=1.0,
        style=None,
        stability=None,
        similarity_boost=None,
        settings_json_hash="c" * 64,
        output_format="wav",
        sample_rate_hz=22050,
        narration_schema_version="Narration-v1",
        narration_algorithm_version="narration-segment-v1",
    )
    expected_payload = json.dumps(kwargs, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    expected = hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()
    assert compute_narration_segment_input_hash(**kwargs) == expected


# ── compute_narration_run_input_hash ─────────────────────────────────────────


def _run_hash(**overrides):
    defaults = dict(
        plan_id=1,
        plan_input_hash="a" * 64,
        voice_profile_id=1,
        voice_profile_version=1,
        language="en-US",
        speaking_rate=1.0,
        style=None,
        stability=None,
        similarity_boost=None,
        settings_json_hash="c" * 64,
        output_format="wav",
        sample_rate_hz=22050,
        narration_schema_version="Narration-v1",
        narration_algorithm_version="narration-segment-v1",
    )
    defaults.update(overrides)
    return compute_narration_run_input_hash(**defaults)


def test_run_hash_is_hex_sha256() -> None:
    h = _run_hash()
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_run_hash_deterministic() -> None:
    assert _run_hash() == _run_hash()


def test_run_hash_sensitive_to_plan_id() -> None:
    assert _run_hash(plan_id=1) != _run_hash(plan_id=2)


def test_run_hash_sensitive_to_voice_profile_version() -> None:
    assert _run_hash(voice_profile_version=1) != _run_hash(voice_profile_version=2)


def test_run_hash_differs_from_segment_hash() -> None:
    run_h = _run_hash()
    seg_h = _segment_hash()
    assert run_h != seg_h
