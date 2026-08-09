"""Tests for analytics provider adapters."""

from __future__ import annotations

import os

import pytest

from app.analytics.constants import CANONICAL_METRICS, METRIC_VIEWS, METRIC_WATCH_TIME_SECONDS
from app.analytics.errors import ProviderAdapterError
from app.analytics.protocol import AnalyticsProvider, ProviderMetrics
from app.analytics.providers.fake import FakeAnalyticsProvider
from app.analytics.providers.youtube import YouTubeAnalyticsProvider


class TestFakeAnalyticsProvider:
    def test_implements_protocol(self):
        assert isinstance(FakeAnalyticsProvider(), AnalyticsProvider)

    def test_provider_name(self):
        assert FakeAnalyticsProvider().provider_name == "fake"

    def test_validate_credentials_always_true(self):
        assert FakeAnalyticsProvider().validate_credentials() is True

    def test_fetch_metrics_returns_provider_metrics(self):
        p = FakeAnalyticsProvider()
        result = p.fetch_metrics("vid123")
        assert isinstance(result, ProviderMetrics)
        assert result.provider_video_id == "vid123"

    def test_fetch_metrics_passes_period(self):
        p = FakeAnalyticsProvider()
        result = p.fetch_metrics("v1", period_start="2026-01-01", period_end="2026-01-31")
        assert result.period_start == "2026-01-01"
        assert result.period_end == "2026-01-31"

    def test_normalize_returns_canonical_names(self):
        p = FakeAnalyticsProvider()
        raw = p.fetch_metrics("vid")
        normalized = p.normalize(raw)
        for name in normalized:
            assert name in CANONICAL_METRICS, f"{name!r} is not a canonical metric"

    def test_normalize_returns_floats(self):
        p = FakeAnalyticsProvider()
        raw = p.fetch_metrics("vid")
        normalized = p.normalize(raw)
        for v in normalized.values():
            assert isinstance(v, float)

    def test_health_ok(self):
        report = FakeAnalyticsProvider().health()
        assert report.ok is True
        assert report.provider == "fake"

    def test_capabilities(self):
        caps = FakeAnalyticsProvider().capabilities()
        assert caps.name == "fake"
        assert caps.supports_period_queries is True

    def test_custom_metrics(self):
        p = FakeAnalyticsProvider(metrics={METRIC_VIEWS: 9999.0})
        raw = p.fetch_metrics("v")
        normalized = p.normalize(raw)
        assert normalized[METRIC_VIEWS] == 9999.0

    def test_initialize_and_shutdown_noop(self):
        p = FakeAnalyticsProvider()
        p.initialize()
        p.shutdown()


class TestYouTubeAnalyticsProvider:
    def test_implements_protocol(self):
        assert isinstance(YouTubeAnalyticsProvider(), AnalyticsProvider)

    def test_provider_name(self):
        assert YouTubeAnalyticsProvider().provider_name == "youtube"

    def test_validate_credentials_false_without_env(self):
        env_key = "YOUTUBE_API_KEY"
        old = os.environ.pop(env_key, None)
        try:
            assert YouTubeAnalyticsProvider().validate_credentials() is False
        finally:
            if old is not None:
                os.environ[env_key] = old

    def test_validate_credentials_true_with_env(self):
        os.environ["YOUTUBE_API_KEY"] = "test-key"
        try:
            assert YouTubeAnalyticsProvider().validate_credentials() is True
        finally:
            del os.environ["YOUTUBE_API_KEY"]

    def test_fetch_metrics_raises_without_credentials(self):
        env_key = "YOUTUBE_API_KEY"
        old = os.environ.pop(env_key, None)
        try:
            with pytest.raises(ProviderAdapterError):
                YouTubeAnalyticsProvider().fetch_metrics("vid123")
        finally:
            if old is not None:
                os.environ[env_key] = old

    def test_normalize_maps_views(self):
        p = YouTubeAnalyticsProvider()
        raw = ProviderMetrics(provider_video_id="v", raw={"views": 500})
        normalized = p.normalize(raw)
        assert normalized[METRIC_VIEWS] == 500.0

    def test_normalize_converts_minutes_to_seconds(self):
        p = YouTubeAnalyticsProvider()
        raw = ProviderMetrics(provider_video_id="v", raw={"estimatedMinutesWatched": 10})
        normalized = p.normalize(raw)
        assert METRIC_WATCH_TIME_SECONDS in normalized
        assert normalized[METRIC_WATCH_TIME_SECONDS] == pytest.approx(600.0)

    def test_normalize_all_keys_canonical(self):
        p = YouTubeAnalyticsProvider()
        raw = ProviderMetrics(
            provider_video_id="v",
            raw={
                "views": 100,
                "estimatedMinutesWatched": 5,
                "averageViewDuration": 90,
                "impressions": 500,
                "impressionClickThroughRate": 0.04,
                "likes": 20,
                "comments": 3,
                "shares": 1,
                "subscribersGained": 2,
                "subscribersLost": 0,
                "estimatedRevenue": 1.50,
            },
        )
        normalized = p.normalize(raw)
        for key in normalized:
            assert key in CANONICAL_METRICS, f"{key!r} leaked outside adapter"

    def test_normalize_ignores_unknown_youtube_fields(self):
        p = YouTubeAnalyticsProvider()
        raw = ProviderMetrics(provider_video_id="v", raw={"unknownYtField": 999, "views": 100})
        normalized = p.normalize(raw)
        assert METRIC_VIEWS in normalized
        assert "unknownYtField" not in normalized

    def test_health_ok_with_credentials(self):
        os.environ["YOUTUBE_API_KEY"] = "key"
        try:
            report = YouTubeAnalyticsProvider().health()
            assert report.ok is True
        finally:
            del os.environ["YOUTUBE_API_KEY"]

    def test_health_fail_without_credentials(self):
        env_key = "YOUTUBE_API_KEY"
        old = os.environ.pop(env_key, None)
        try:
            report = YouTubeAnalyticsProvider().health()
            assert report.ok is False
        finally:
            if old is not None:
                os.environ[env_key] = old

    def test_capabilities(self):
        caps = YouTubeAnalyticsProvider().capabilities()
        assert caps.name == "youtube"
        assert caps.supports_revenue is True

    def test_initialize_and_shutdown_noop(self):
        p = YouTubeAnalyticsProvider()
        p.initialize()
        p.shutdown()


class TestProviderIsolation:
    def test_youtube_field_names_only_in_adapter(self):
        """No youtube-specific field name may appear outside the youtube adapter."""
        import importlib
        import inspect

        yt_field_names = {
            "estimatedMinutesWatched",
            "impressionClickThroughRate",
            "subscribersGained",
            "subscribersLost",
            "estimatedRevenue",
            "averageViewDuration",
        }
        modules_to_check = [
            "app.analytics.constants",
            "app.analytics.normalization",
            "app.analytics.repository",
            "app.analytics.aggregation",
            "app.analytics.orchestrator",
            "app.analytics.hashing",
            "app.analytics.models",
            "app.analytics.validation",
            "app.analytics.metrics",
        ]
        for mod_name in modules_to_check:
            mod = importlib.import_module(mod_name)
            source = inspect.getsource(mod)
            for field in yt_field_names:
                assert field not in source, (
                    f"YouTube field {field!r} found in {mod_name}. "
                    "Provider-specific names must stay in adapters."
                )

    def test_no_provider_check_outside_adapters(self):
        """No analytics code outside adapters may check provider == 'youtube'."""
        import importlib
        import inspect

        modules_to_check = [
            "app.analytics.constants",
            "app.analytics.normalization",
            "app.analytics.repository",
            "app.analytics.aggregation",
            "app.analytics.orchestrator",
            "app.analytics.hashing",
            "app.analytics.models",
            "app.analytics.validation",
            "app.analytics.metrics",
        ]
        for mod_name in modules_to_check:
            mod = importlib.import_module(mod_name)
            source = inspect.getsource(mod)
            assert '"youtube"' not in source or mod_name.endswith("providers"), (
                f'provider == "youtube" check found in {mod_name}. Must stay in adapters only.'
            )
