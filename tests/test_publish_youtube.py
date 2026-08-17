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
        result = p.publish(upload, scheduled_at="2099-01-01T12:00:00Z", visibility="private")
        assert result.status == "scheduled"
        assert result.scheduled_at == "2099-01-01T12:00:00Z"
        assert result.published_at is None

    def test_publish_does_not_call_update_video_for_private(self):
        """publish() must NOT call update_video for a private upload.

        videos.insert already sets privacyStatus=private.  videos.update requires
        youtube or youtube.force-ssl scopes — broader than youtube.upload — and is
        unnecessary for the private-only workflow.
        """

        class BombClient(FakeYouTubeAPIClient):
            def update_video(self, video_id, snippet, status):
                raise AssertionError("update_video must not be called for a private publish")

        p = YouTubePublishingProvider(api_client=BombClient())
        upload = UploadResult(provider_video_id="vid1", provider_url=None)
        result = p.publish(upload, visibility="private")  # must not raise
        assert result.status == "published"

    def test_publish_rejects_public_visibility(self):
        """publish() must hard-lock to 'private' — cannot be used to make a video public."""
        from app.publishing.errors import PublishingValidationError

        p = _provider()
        upload = UploadResult(provider_video_id="vid1", provider_url=None)
        with pytest.raises(PublishingValidationError, match="locked to 'private'"):
            p.publish(upload, visibility="public")


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


# ---------------------------------------------------------------------------
# Regression tests: private upload succeeds without videos.update
#
# Root cause guarded here: publish() previously called update_video() for every
# upload, including private ones where videos.insert already set
# privacyStatus=private.  videos.update requires youtube or youtube.force-ssl
# scopes — broader than youtube.upload — causing a 403 on the update even
# though the insert succeeded and the video was already correctly private.
# ---------------------------------------------------------------------------


class TestPrivateUploadNoRedundantUpdate:
    def test_private_publish_succeeds_when_update_video_would_403(self):
        """Simulates the live failure: update_video raises 403; publish() must succeed
        without calling it.  The test uses a client that unconditionally raises on
        update_video to prove the code path never reaches that call."""

        class Scope403Client(FakeYouTubeAPIClient):
            def update_video(self, video_id, snippet, status):
                raise RuntimeError(
                    'HttpError 403 "Request had insufficient authentication scopes."'
                )

        p = YouTubePublishingProvider(api_client=Scope403Client())
        upload = UploadResult(
            provider_video_id="kQH88nXdiRY",
            provider_url="https://www.youtube.com/watch?v=kQH88nXdiRY",
            provider_response={"id": "kQH88nXdiRY", "status": {"uploadStatus": "uploaded"}},
        )
        result = p.publish(upload, visibility="private")
        assert result.status == "published"
        assert result.provider_video_id == "kQH88nXdiRY"
        assert result.published_at is not None

    def test_no_redundant_status_update_for_immediate_private(self):
        """update_video must not be called for an immediate private publish.

        Records all update_video calls; asserts the list is empty after publish().
        """
        update_calls: list[str] = []

        class SpyClient(FakeYouTubeAPIClient):
            def update_video(self, video_id, snippet, status):
                update_calls.append(video_id)
                return super().update_video(video_id, snippet, status)

        p = YouTubePublishingProvider(api_client=SpyClient())
        upload = UploadResult(provider_video_id="vid_priv_001", provider_url=None)
        p.publish(upload, visibility="private")
        assert update_calls == [], (
            f"update_video was called {len(update_calls)} time(s) — "
            "it must not be called for a private immediate publish"
        )

    def test_private_only_safety_lock_still_enforced(self):
        """Removing the videos.update call must not weaken the privacy hard-lock.

        publish(visibility='public') must still raise PublishingValidationError.
        """
        from app.publishing.errors import PublishingValidationError

        p = _provider()
        upload = UploadResult(provider_video_id="vid1", provider_url=None)
        with pytest.raises(PublishingValidationError, match="locked to 'private'"):
            p.publish(upload, visibility="public")
        with pytest.raises(PublishingValidationError, match="locked to 'private'"):
            p.publish(upload, visibility="unlisted")

    def test_true_upload_failure_still_fails(self):
        """A failure in videos.insert must still propagate as ProviderUploadError.

        Verifies that removing the update_video call did not accidentally suppress
        real upload errors.
        """
        from app.publishing.errors import ProviderUploadError

        p = _provider(raise_on_insert=RuntimeError("network timeout"))
        with pytest.raises(ProviderUploadError, match="network timeout"):
            p.upload(_package())
