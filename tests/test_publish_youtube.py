"""Tests for the YouTube publishing adapter — fake API client, zero network calls."""

from __future__ import annotations

import pytest

from app.publishing.errors import ProviderUploadError
from app.publishing.protocol import UploadPackage, UploadResult
from app.publishing.providers.youtube import (
    YOUTUBE_PROVIDER_NAME,
    YOUTUBE_PROVIDER_VERSION,
    FakeYouTubeAPIClient,
    YouTubePublishingProvider,
)


def _package(**kw) -> UploadPackage:
    defaults = dict(
        plan_id=1,
        file_path="/tmp/video.mp4",
        file_sha256="abc123",
        title="My Test Video",
        description="A test.",
        tags=["test", "video"],
        language="en",
        category="22",
        visibility="private",
        made_for_kids=False,
        scheduled_at=None,
        captions_path=None,
        playlist_id=None,
    )
    defaults.update(kw)
    return UploadPackage(**defaults)


def _provider(*, video_id: str = "yt_fake_vid_001", raise_on_insert: Exception | None = None):
    client = FakeYouTubeAPIClient(video_id=video_id, raise_on_insert=raise_on_insert)
    return YouTubePublishingProvider(api_client=client)


class TestYouTubeProviderIdentity:
    def test_provider_name(self):
        p = _provider()
        assert p.provider_name == YOUTUBE_PROVIDER_NAME

    def test_provider_version(self):
        p = _provider()
        assert p.provider_version == YOUTUBE_PROVIDER_VERSION

    def test_initialize(self):
        p = _provider()
        p.initialize()  # must not raise


class TestYouTubeCredentials:
    def test_validate_credentials_with_fake_client(self):
        p = _provider()
        assert p.validate_credentials() is True


class TestYouTubeUpload:
    def test_upload_returns_result(self):
        p = _provider()
        result = p.upload(_package())
        assert result.provider_video_id == "yt_fake_vid_001"
        assert "yt_fake_vid_001" in result.provider_url
        assert isinstance(result.provider_response, dict)

    def test_upload_maps_title_to_snippet(self):
        FakeYouTubeAPIClient()
        received_snippet = {}

        class CapturingClient(FakeYouTubeAPIClient):
            def insert_video(self, snippet, status, file_path):
                received_snippet.update(snippet)
                return super().insert_video(snippet, status, file_path)

        p = YouTubePublishingProvider(api_client=CapturingClient())
        p.upload(_package(title="Exact Title"))
        assert received_snippet.get("title") == "Exact Title"

    def test_upload_failure_raises_provider_error(self):
        p = _provider(raise_on_insert=RuntimeError("API error"))
        with pytest.raises(ProviderUploadError, match="API error"):
            p.upload(_package())

    def test_prepare_package_passes_through(self):
        p = _provider()
        pkg = _package()
        result = p.prepare_package(pkg)
        assert result is pkg

    def test_prepare_package_title_too_long_raises(self):
        from app.publishing.errors import PublishingValidationError

        p = _provider()
        with pytest.raises(PublishingValidationError, match="100"):
            p.prepare_package(_package(title="x" * 101))


class TestYouTubePublish:
    def test_publish_immediate_returns_published(self):
        p = _provider()
        upload = UploadResult(
            provider_video_id="yt_fake_vid_001",
            provider_url="https://www.youtube.com/watch?v=yt_fake_vid_001",
        )
        result = p.publish(upload, visibility="private")
        assert result.status == "published"
        assert result.published_at is not None
        assert result.scheduled_at is None

    def test_publish_with_schedule_returns_scheduled(self):
        p = _provider()
        upload = UploadResult(
            provider_video_id="yt_fake_vid_001",
            provider_url="https://www.youtube.com/watch?v=yt_fake_vid_001",
        )
        result = p.publish(upload, scheduled_at="2099-01-01T12:00:00Z", visibility="public")
        assert result.status == "scheduled"
        assert result.scheduled_at == "2099-01-01T12:00:00Z"
        assert result.published_at is None

    def test_publish_sets_visibility(self):
        received_status: dict = {}

        class CapturingClient(FakeYouTubeAPIClient):
            def update_video(self, video_id, snippet, status):
                received_status.update(status)
                return super().update_video(video_id, snippet, status)

        p = YouTubePublishingProvider(api_client=CapturingClient())
        upload = UploadResult(provider_video_id="vid1", provider_url=None)
        p.publish(upload, visibility="public")
        assert received_status.get("privacyStatus") == "public"


class TestYouTubeHealth:
    def test_health_ok_with_fake_client(self):
        p = _provider()
        report = p.health()
        assert report.ok is True
        assert report.provider == YOUTUBE_PROVIDER_NAME

    def test_health_fail_when_client_raises(self):
        class BrokenClient(FakeYouTubeAPIClient):
            def health_check(self) -> bool:
                raise RuntimeError("no network")

        p = YouTubePublishingProvider(api_client=BrokenClient())
        report = p.health()
        assert report.ok is False
        assert "no network" in report.message


class TestYouTubeCapabilities:
    def test_capabilities(self):
        p = _provider()
        caps = p.capabilities()
        assert caps.name == YOUTUBE_PROVIDER_NAME
        assert caps.supports_scheduling is True
        assert caps.supports_thumbnails is True
