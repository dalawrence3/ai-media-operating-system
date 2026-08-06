"""Tests for render manifest input hashing."""

from app.media.hashing import RenderHashInput, compute_render_input_hash


def _make_inp(**overrides) -> RenderHashInput:
    defaults = dict(
        scene_manifest_id=1,
        narration_run_id=2,
        caption_run_id=3,
        topic_id=4,
        plan_id=5,
        script_id=6,
        scene_manifest_input_hash="abc123",
        narration_input_hash="def456",
        render_schema_version="Render-v1",
        compositor_version="compositor-1.0.0",
        width=1080,
        height=1920,
        fps=30,
        caption_burn_in=False,
        experiment_id=None,
        scene_tuples=[(0, 10, "sha_a"), (1, 11, "sha_b")],
    )
    defaults.update(overrides)
    return RenderHashInput(**defaults)


def test_hash_returns_64_hex_chars():
    h = compute_render_input_hash(_make_inp())
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_is_deterministic():
    inp = _make_inp()
    assert compute_render_input_hash(inp) == compute_render_input_hash(inp)


def test_different_manifest_ids_produce_different_hashes():
    h1 = compute_render_input_hash(_make_inp(scene_manifest_id=1))
    h2 = compute_render_input_hash(_make_inp(scene_manifest_id=2))
    assert h1 != h2


def test_different_narration_run_produces_different_hash():
    h1 = compute_render_input_hash(_make_inp(narration_run_id=1))
    h2 = compute_render_input_hash(_make_inp(narration_run_id=99))
    assert h1 != h2


def test_different_caption_run_produces_different_hash():
    h1 = compute_render_input_hash(_make_inp(caption_run_id=3))
    h2 = compute_render_input_hash(_make_inp(caption_run_id=99))
    assert h1 != h2


def test_different_resolution_produces_different_hash():
    h1 = compute_render_input_hash(_make_inp(width=1080, height=1920))
    h2 = compute_render_input_hash(_make_inp(width=720, height=1280))
    assert h1 != h2


def test_caption_burn_in_changes_hash():
    h1 = compute_render_input_hash(_make_inp(caption_burn_in=False))
    h2 = compute_render_input_hash(_make_inp(caption_burn_in=True))
    assert h1 != h2


def test_scene_order_is_sensitive():
    scenes_a = [(0, 10, "sha_a"), (1, 11, "sha_b")]
    scenes_b = [(0, 11, "sha_b"), (1, 10, "sha_a")]
    h1 = compute_render_input_hash(_make_inp(scene_tuples=scenes_a))
    h2 = compute_render_input_hash(_make_inp(scene_tuples=scenes_b))
    assert h1 != h2


def test_none_audio_sha_is_stable():
    scenes_with_none = [(0, 10, None), (1, 11, None)]
    h = compute_render_input_hash(_make_inp(scene_tuples=scenes_with_none))
    assert len(h) == 64


def test_experiment_id_changes_hash():
    h1 = compute_render_input_hash(_make_inp(experiment_id=None))
    h2 = compute_render_input_hash(_make_inp(experiment_id="exp-abc"))
    assert h1 != h2


def test_compositor_version_changes_hash():
    h1 = compute_render_input_hash(_make_inp(compositor_version="compositor-1.0.0"))
    h2 = compute_render_input_hash(_make_inp(compositor_version="compositor-2.0.0"))
    assert h1 != h2


def test_different_scene_manifest_hashes_produce_different_hashes():
    h1 = compute_render_input_hash(_make_inp(scene_manifest_input_hash="aaa"))
    h2 = compute_render_input_hash(_make_inp(scene_manifest_input_hash="bbb"))
    assert h1 != h2


def test_fps_change_produces_different_hash():
    h1 = compute_render_input_hash(_make_inp(fps=30))
    h2 = compute_render_input_hash(_make_inp(fps=60))
    assert h1 != h2
