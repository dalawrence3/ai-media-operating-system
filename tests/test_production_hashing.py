"""Tests for Phase 6 M6.1 production plan hashing functions."""

from __future__ import annotations

import hashlib
import json

from app.production.hashing import (
    compute_production_plan_input_hash,
    compute_script_body_hash,
)

# ---------------------------------------------------------------------------
# compute_script_body_hash
# ---------------------------------------------------------------------------


def test_script_body_hash_is_sha256_hex() -> None:
    body = '{"title":"T","sections":[]}'
    result = compute_script_body_hash(body)
    assert isinstance(result, str)
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)


def test_script_body_hash_matches_manual_computation() -> None:
    body = '{"title":"Test","sections":[]}'
    expected = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert compute_script_body_hash(body) == expected


def test_script_body_hash_is_stable() -> None:
    body = '{"title":"Stable","sections":[]}'
    assert compute_script_body_hash(body) == compute_script_body_hash(body)


def test_script_body_hash_changes_when_body_changes() -> None:
    hash_a = compute_script_body_hash('{"title":"A","sections":[]}')
    hash_b = compute_script_body_hash('{"title":"B","sections":[]}')
    assert hash_a != hash_b


def test_script_body_hash_is_sensitive_to_whitespace() -> None:
    hash_compact = compute_script_body_hash('{"title":"T"}')
    hash_spaced = compute_script_body_hash('{"title": "T"}')
    assert hash_compact != hash_spaced


# ---------------------------------------------------------------------------
# compute_production_plan_input_hash
# ---------------------------------------------------------------------------


def _base_kwargs() -> dict:
    return {
        "script_id": 1,
        "script_version": 1,
        "script_body_hash": "deadbeef" * 8,
        "plan_schema_version": "ProductionPlan-v1",
        "renderer_version": "production-renderer-v1",
        "duration_algorithm_version": "duration-150wpm-v1",
        "script_format": "short",
        "evidence_hash": "cafebabe" * 8,
        "requires_evidence_review": False,
    }


def test_input_hash_is_sha256_hex() -> None:
    result = compute_production_plan_input_hash(**_base_kwargs())
    assert isinstance(result, str)
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)


def test_input_hash_is_stable() -> None:
    kwargs = _base_kwargs()
    h1 = compute_production_plan_input_hash(**kwargs)
    h2 = compute_production_plan_input_hash(**kwargs)
    assert h1 == h2


def test_input_hash_changes_when_script_id_changes() -> None:
    kw1 = _base_kwargs()
    kw2 = {**kw1, "script_id": 99}
    assert compute_production_plan_input_hash(**kw1) != compute_production_plan_input_hash(**kw2)


def test_input_hash_changes_when_script_version_changes() -> None:
    kw1 = _base_kwargs()
    kw2 = {**kw1, "script_version": 2}
    assert compute_production_plan_input_hash(**kw1) != compute_production_plan_input_hash(**kw2)


def test_input_hash_changes_when_body_hash_changes() -> None:
    kw1 = _base_kwargs()
    kw2 = {**kw1, "script_body_hash": "00000000" * 8}
    assert compute_production_plan_input_hash(**kw1) != compute_production_plan_input_hash(**kw2)


def test_input_hash_changes_when_plan_schema_version_changes() -> None:
    kw1 = _base_kwargs()
    kw2 = {**kw1, "plan_schema_version": "ProductionPlan-v2"}
    assert compute_production_plan_input_hash(**kw1) != compute_production_plan_input_hash(**kw2)


def test_input_hash_changes_when_renderer_version_changes() -> None:
    kw1 = _base_kwargs()
    kw2 = {**kw1, "renderer_version": "production-renderer-v2"}
    assert compute_production_plan_input_hash(**kw1) != compute_production_plan_input_hash(**kw2)


def test_input_hash_changes_when_duration_version_changes() -> None:
    kw1 = _base_kwargs()
    kw2 = {**kw1, "duration_algorithm_version": "duration-160wpm-v1"}
    assert compute_production_plan_input_hash(**kw1) != compute_production_plan_input_hash(**kw2)


def test_input_hash_changes_when_format_changes() -> None:
    kw1 = _base_kwargs()
    kw2 = {**kw1, "script_format": "long_form"}
    assert compute_production_plan_input_hash(**kw1) != compute_production_plan_input_hash(**kw2)


def test_input_hash_changes_when_evidence_hash_changes() -> None:
    kw1 = _base_kwargs()
    kw2 = {**kw1, "evidence_hash": "11111111" * 8}
    assert compute_production_plan_input_hash(**kw1) != compute_production_plan_input_hash(**kw2)


def test_input_hash_changes_when_requires_evidence_review_changes() -> None:
    kw1 = _base_kwargs()
    kw2 = {**kw1, "requires_evidence_review": True}
    assert compute_production_plan_input_hash(**kw1) != compute_production_plan_input_hash(**kw2)


def test_input_hash_uses_sorted_canonical_json() -> None:
    """Verify hash matches manual SHA-256 of compact sorted JSON."""
    kwargs = _base_kwargs()
    expected_payload = json.dumps(
        {
            "script_id": kwargs["script_id"],
            "script_version": kwargs["script_version"],
            "script_body_hash": kwargs["script_body_hash"],
            "plan_schema_version": kwargs["plan_schema_version"],
            "renderer_version": kwargs["renderer_version"],
            "duration_algorithm_version": kwargs["duration_algorithm_version"],
            "script_format": kwargs["script_format"],
            "evidence_hash": kwargs["evidence_hash"],
            "requires_evidence_review": kwargs["requires_evidence_review"],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    expected = hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()
    assert compute_production_plan_input_hash(**kwargs) == expected
