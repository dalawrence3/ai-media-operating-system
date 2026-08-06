"""Fake publishing provider for tests and dry-run validation.

Makes zero network requests, zero uploads, zero API calls.
Returns deterministic pre-configured responses.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.publishing.protocol import (
    ProviderCapabilities,
    ProviderHealthReport,
    PublishResult,
    UploadPackage,
    UploadResult,
)

FAKE_PROVIDER_NAME = "fake"
FAKE_PROVIDER_VERSION = "1.0.0"


class FakePublishingProvider:
    """Injectable fake provider — use in all automated tests."""

    def __init__(
        self,
        *,
        provider_video_id: str = "fake_vid_001",
        simulate_upload_failure: bool = False,
        simulate_publish_failure: bool = False,
    ) -> None:
        self._video_id = provider_video_id
        self._upload_fail = simulate_upload_failure
        self._publish_fail = simulate_publish_failure
        self._initialized = False

    @property
    def provider_name(self) -> str:
        return FAKE_PROVIDER_NAME

    @property
    def provider_version(self) -> str:
        return FAKE_PROVIDER_VERSION

    def initialize(self) -> None:
        self._initialized = True

    def validate_credentials(self) -> bool:
        return True

    def prepare_package(self, package: UploadPackage) -> UploadPackage:
        return package

    def upload(self, package: UploadPackage) -> UploadResult:
        if self._upload_fail:
            from app.publishing.errors import ProviderUploadError
            raise ProviderUploadError("Simulated upload failure.")
        return UploadResult(
            provider_video_id=self._video_id,
            provider_url=f"https://fake.example/watch?v={self._video_id}",
            provider_response={"status": "uploaded", "id": self._video_id},
        )

    def publish(
        self,
        result: UploadResult,
        *,
        scheduled_at: str | None = None,
        visibility: str = "private",
    ) -> PublishResult:
        if self._publish_fail:
            from app.publishing.errors import ProviderUploadError
            raise ProviderUploadError("Simulated publish failure.")
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
        if scheduled_at:
            status = "scheduled"
            pub_at = None
        else:
            status = "published"
            pub_at = now
        return PublishResult(
            provider_video_id=result.provider_video_id,
            provider_url=result.provider_url,
            status=status,
            published_at=pub_at,
            scheduled_at=scheduled_at,
            provider_response={"status": status, "id": result.provider_video_id},
        )

    def health(self) -> ProviderHealthReport:
        return ProviderHealthReport(
            ok=True,
            provider=FAKE_PROVIDER_NAME,
            provider_version=FAKE_PROVIDER_VERSION,
            message="Fake provider is always healthy.",
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=FAKE_PROVIDER_NAME,
            version=FAKE_PROVIDER_VERSION,
            supports_scheduling=True,
            supports_playlists=True,
            supports_captions=True,
            supports_thumbnails=False,
            supports_status_polling=False,
        )

    def shutdown(self) -> None:
        self._initialized = False
