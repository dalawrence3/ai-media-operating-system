"""Tests for analytics snapshot input hash computation."""

from __future__ import annotations

from app.analytics.hashing import AnalyticsHashInput, compute_analytics_input_hash


def _base_input(**overrides) -> AnalyticsHashInput:
    defaults = dict(
        provider="fake",
        provider_version="1.0.0",
        publication_id=1,
        publishing_plan_id=1,
        publishing_job_id=1,
        render_manifest_id=1,
        scene_manifest_id=1,
        production_plan_id=1,
        script_id=1,
        topic_id=1,
        narration_run_id=1,
        caption_run_id=1,
        experiment_id=None,
        period_start=None,
        period_end=None,
    )
    defaults.update(overrides)
    return AnalyticsHashInput(**defaults)


class TestAnalyticsInputHash:
    def test_deterministic(self):
        inp = _base_input()
        assert compute_analytics_input_hash(inp) == compute_analytics_input_hash(inp)

    def test_returns_64_char_hex(self):
        h = compute_analytics_input_hash(_base_input())
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_provider_different_hash(self):
        h1 = compute_analytics_input_hash(_base_input(provider="fake"))
        h2 = compute_analytics_input_hash(_base_input(provider="youtube"))
        assert h1 != h2

    def test_different_publication_id_different_hash(self):
        h1 = compute_analytics_input_hash(_base_input(publication_id=1))
        h2 = compute_analytics_input_hash(_base_input(publication_id=2))
        assert h1 != h2

    def test_different_topic_id_different_hash(self):
        h1 = compute_analytics_input_hash(_base_input(topic_id=1))
        h2 = compute_analytics_input_hash(_base_input(topic_id=99))
        assert h1 != h2

    def test_different_period_start_different_hash(self):
        h1 = compute_analytics_input_hash(_base_input(period_start="2026-01-01"))
        h2 = compute_analytics_input_hash(_base_input(period_start="2026-02-01"))
        assert h1 != h2

    def test_different_experiment_different_hash(self):
        h1 = compute_analytics_input_hash(_base_input(experiment_id=None))
        h2 = compute_analytics_input_hash(_base_input(experiment_id="exp-42"))
        assert h1 != h2

    def test_different_adapter_version_different_hash(self):
        h1 = compute_analytics_input_hash(_base_input(adapter_version="1.0.0"))
        h2 = compute_analytics_input_hash(_base_input(adapter_version="2.0.0"))
        assert h1 != h2

    def test_different_render_manifest_different_hash(self):
        h1 = compute_analytics_input_hash(_base_input(render_manifest_id=1))
        h2 = compute_analytics_input_hash(_base_input(render_manifest_id=2))
        assert h1 != h2

    def test_same_inputs_multiple_calls(self):
        inp = _base_input(publication_id=5, experiment_id="abc")
        results = {compute_analytics_input_hash(inp) for _ in range(10)}
        assert len(results) == 1
