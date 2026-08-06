"""Tests for publishing plan input hash computation."""

from __future__ import annotations

from app.publishing.hashing import PublishingHashInput, compute_publishing_input_hash


def _base_input(**overrides) -> PublishingHashInput:
    defaults = dict(
        render_manifest_id=1,
        output_sha256="abc123",
        provider="fake",
        provider_version="1.0.0",
        title="My Video",
        description="",
        tags=(),
        language="en",
        visibility="private",
        category=None,
        made_for_kids=False,
        captions_path=None,
        playlist_id=None,
        copyright_notice=None,
        licensing_notes=None,
        schedule_type="immediate",
        scheduled_at=None,
        timezone=None,
        experiment_id=None,
    )
    defaults.update(overrides)
    return PublishingHashInput(**defaults)


class TestPublishingInputHash:
    def test_deterministic(self):
        inp = _base_input()
        assert compute_publishing_input_hash(inp) == compute_publishing_input_hash(inp)

    def test_different_title_different_hash(self):
        h1 = compute_publishing_input_hash(_base_input(title="A"))
        h2 = compute_publishing_input_hash(_base_input(title="B"))
        assert h1 != h2

    def test_different_render_manifest_different_hash(self):
        h1 = compute_publishing_input_hash(_base_input(render_manifest_id=1))
        h2 = compute_publishing_input_hash(_base_input(render_manifest_id=2))
        assert h1 != h2

    def test_different_output_sha256_different_hash(self):
        h1 = compute_publishing_input_hash(_base_input(output_sha256="aaa"))
        h2 = compute_publishing_input_hash(_base_input(output_sha256="bbb"))
        assert h1 != h2

    def test_different_provider_different_hash(self):
        h1 = compute_publishing_input_hash(_base_input(provider="fake"))
        h2 = compute_publishing_input_hash(_base_input(provider="youtube"))
        assert h1 != h2

    def test_tags_order_independent(self):
        h1 = compute_publishing_input_hash(_base_input(tags=("a", "b", "c")))
        h2 = compute_publishing_input_hash(_base_input(tags=("c", "a", "b")))
        assert h1 == h2

    def test_different_visibility_different_hash(self):
        h1 = compute_publishing_input_hash(_base_input(visibility="private"))
        h2 = compute_publishing_input_hash(_base_input(visibility="public"))
        assert h1 != h2

    def test_different_schedule_different_hash(self):
        h1 = compute_publishing_input_hash(_base_input(schedule_type="immediate"))
        h2 = compute_publishing_input_hash(
            _base_input(schedule_type="scheduled", scheduled_at="2030-01-01T00:00:00")
        )
        assert h1 != h2

    def test_hash_is_sha256_hex(self):
        h = compute_publishing_input_hash(_base_input())
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_engine_version_sensitivity(self):
        inp = _base_input()
        h1 = compute_publishing_input_hash(inp)
        inp2 = PublishingHashInput(**{**inp.__dict__, "engine_version": "9.9.9"})
        h2 = compute_publishing_input_hash(inp2)
        assert h1 != h2
