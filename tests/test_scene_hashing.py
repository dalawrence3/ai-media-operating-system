"""Tests for Phase 7 scene manifest hashing."""

from app.scenes.hashing import ManifestHashInput, compute_manifest_input_hash


def _make_input(**kwargs) -> ManifestHashInput:
    defaults = dict(
        caption_run_id=1,
        narration_run_id=2,
        plan_id=3,
        script_id=4,
        topic_id=5,
        caption_input_hash="abc",
        narration_input_hash="def",
        manifest_schema_version="1.0",
        planner_version="1.0",
        experiment_id=None,
        segment_tuples=[(10, "hash1", 5000), (11, "hash2", 3000)],
    )
    defaults.update(kwargs)
    return ManifestHashInput(**defaults)


def test_hash_is_deterministic():
    h1 = compute_manifest_input_hash(_make_input())
    h2 = compute_manifest_input_hash(_make_input())
    assert h1 == h2


def test_hash_is_hex_64_chars():
    h = compute_manifest_input_hash(_make_input())
    assert len(h) == 64
    int(h, 16)  # must be valid hex


def test_different_caption_run_ids_produce_different_hashes():
    h1 = compute_manifest_input_hash(_make_input(caption_run_id=1))
    h2 = compute_manifest_input_hash(_make_input(caption_run_id=2))
    assert h1 != h2


def test_different_narration_run_ids_produce_different_hashes():
    h1 = compute_manifest_input_hash(_make_input(narration_run_id=10))
    h2 = compute_manifest_input_hash(_make_input(narration_run_id=20))
    assert h1 != h2


def test_different_planner_versions_produce_different_hashes():
    h1 = compute_manifest_input_hash(_make_input(planner_version="1.0"))
    h2 = compute_manifest_input_hash(_make_input(planner_version="2.0"))
    assert h1 != h2


def test_different_segment_tuples_produce_different_hashes():
    h1 = compute_manifest_input_hash(_make_input(segment_tuples=[(1, "a", 1000)]))
    h2 = compute_manifest_input_hash(_make_input(segment_tuples=[(1, "b", 1000)]))
    assert h1 != h2


def test_segment_order_matters():
    h1 = compute_manifest_input_hash(
        _make_input(segment_tuples=[(1, "a", 1000), (2, "b", 2000)])
    )
    h2 = compute_manifest_input_hash(
        _make_input(segment_tuples=[(2, "b", 2000), (1, "a", 1000)])
    )
    assert h1 != h2


def test_experiment_id_affects_hash():
    h1 = compute_manifest_input_hash(_make_input(experiment_id=None))
    h2 = compute_manifest_input_hash(_make_input(experiment_id="exp-1"))
    assert h1 != h2


def test_empty_segment_list_still_produces_hash():
    h = compute_manifest_input_hash(_make_input(segment_tuples=[]))
    assert len(h) == 64
