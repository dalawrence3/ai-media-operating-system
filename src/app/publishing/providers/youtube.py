"""YouTube publishing provider adapter.

Credential design
-----------------
YouTube Data API v3 requires OAuth 2.0 (service accounts cannot upload videos).
Two operator-supplied configuration values are needed:

  YOUTUBE_CLIENT_SECRETS_PATH  — path to the OAuth client-secrets JSON file
                                  (downloaded from Google Cloud Console)
  YOUTUBE_CREDENTIALS_PATH     — path where the runtime stores the authorized
                                  token file (access_token + refresh_token)

Neither value is persisted in SQLite, review events, input hashes, or logs.
Only the path references are referenced (never the token values themselves).

The one-time browser-based OAuth authorization flow is deferred to a separate
`ace publish doctor --authorize` step (not implemented in this milestone).
For automated tests, inject a FakeYouTubeAPIClient at construction time.

OPERATOR DECISION REQUIRED (Phase 9 phase-gate):
  OAuth credential-file handling requires one of:
    A) Installed-app flow: run once, store token at YOUTUBE_CREDENTIALS_PATH
    B) Server-to-server via a proxy service (not standard for YouTube uploads)
  Recommendation: option A with a dedicated `ace publish doctor --authorize`
  command. This phase implements the adapter boundary; the authorization flow
  is a Phase 10 prerequisite for live publishing.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.publishing.protocol import (
    ProviderCapabilities,
    ProviderHealthReport,
    PublishResult,
    UploadPackage,
    UploadResult,
)

YOUTUBE_PROVIDER_NAME = "youtube"
YOUTUBE_PROVIDER_VERSION = "1.0.0"
YOUTUBE_API_VERSION = "v3"


# ── Injected client boundary ──────────────────────────────────────────────────

@runtime_checkable
class YouTubeAPIClient(Protocol):
    """Minimal API surface needed by the YouTube adapter.

    Production implementation uses google-api-python-client (not yet installed).
    Tests inject FakeYouTubeAPIClient.
    """

    def insert_video(
        self,
        snippet: dict,
        status: dict,
        file_path: str,
    ) -> dict: ...

    def update_video(self, video_id: str, snippet: dict, status: dict) -> dict: ...

    def get_video(self, video_id: str, parts: list[str]) -> dict: ...

    def health_check(self) -> bool: ...


class FakeYouTubeAPIClient:
    """Deterministic fake for tests — zero network calls."""

    def __init__(
        self,
        *,
        video_id: str = "yt_fake_vid_001",
        raise_on_insert: Exception | None = None,
    ) -> None:
        self._video_id = video_id
        self._raise_on_insert = raise_on_insert

    def insert_video(self, snippet: dict, status: dict, file_path: str) -> dict:
        if self._raise_on_insert is not None:
            raise self._raise_on_insert
        return {
            "kind": "youtube#video",
            "id": self._video_id,
            "snippet": snippet,
            "status": {**status, "uploadStatus": "uploaded"},
        }

    def update_video(self, video_id: str, snippet: dict, status: dict) -> dict:
        return {"kind": "youtube#video", "id": video_id, "status": status}

    def get_video(self, video_id: str, parts: list[str]) -> dict:
        return {
            "kind": "youtube#videoListResponse",
            "items": [{"id": video_id, "status": {"uploadStatus": "processed"}}],
        }

    def health_check(self) -> bool:
        return True


# ── YouTube adapter ───────────────────────────────────────────────────────────

class YouTubePublishingProvider:
    """YouTube publishing adapter implementing the PublishingProvider protocol.

    Accepts an injectable api_client for testability.
    Live usage requires google-api-python-client (installation pending approval).
    """

    def __init__(self, api_client: YouTubeAPIClient | None = None) -> None:
        self._client: YouTubeAPIClient = api_client or FakeYouTubeAPIClient()
        self._initialized = False

    @property
    def provider_name(self) -> str:
        return YOUTUBE_PROVIDER_NAME

    @property
    def provider_version(self) -> str:
        return YOUTUBE_PROVIDER_VERSION

    def initialize(self) -> None:
        self._initialized = True

    def validate_credentials(self) -> bool:
        try:
            return self._client.health_check()
        except Exception:
            return False

    def prepare_package(self, package: UploadPackage) -> UploadPackage:
        """Validate YouTube-specific constraints before upload."""
        if len(package.title) > 100:
            from app.publishing.errors import PublishingValidationError
            raise PublishingValidationError(
                f"YouTube title exceeds 100 chars ({len(package.title)})."
            )
        if len(package.tags) > 500:
            from app.publishing.errors import PublishingValidationError
            raise PublishingValidationError(
                f"YouTube tag count {len(package.tags)} exceeds 500."
            )
        return package

    def upload(self, package: UploadPackage) -> UploadResult:
        snippet = self._build_snippet(package)
        status = self._build_status(package)
        try:
            response = self._client.insert_video(
                snippet=snippet, status=status, file_path=package.file_path
            )
        except Exception as exc:
            from app.publishing.errors import ProviderUploadError
            raise ProviderUploadError(
                f"YouTube upload failed: {exc}"
            ) from exc
        video_id = response.get("id", "")
        url = f"https://www.youtube.com/watch?v={video_id}" if video_id else None
        return UploadResult(
            provider_video_id=video_id,
            provider_url=url,
            provider_response=response,
        )

    def publish(
        self,
        result: UploadResult,
        *,
        scheduled_at: str | None = None,
        visibility: str = "private",
    ) -> PublishResult:
        from datetime import UTC, datetime

        status_body: dict = {"privacyStatus": visibility}
        if scheduled_at:
            status_body["publishAt"] = scheduled_at
            pub_status = "scheduled"
            pub_at = None
        else:
            pub_status = "published"
            pub_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")

        try:
            response = self._client.update_video(
                video_id=result.provider_video_id,
                snippet={},
                status=status_body,
            )
        except Exception as exc:
            from app.publishing.errors import ProviderUploadError
            raise ProviderUploadError(
                f"YouTube publish step failed: {exc}"
            ) from exc

        return PublishResult(
            provider_video_id=result.provider_video_id,
            provider_url=result.provider_url,
            status=pub_status,
            published_at=pub_at,
            scheduled_at=scheduled_at,
            provider_response=response,
        )

    def health(self) -> ProviderHealthReport:
        try:
            ok = self._client.health_check()
            return ProviderHealthReport(
                ok=ok,
                provider=YOUTUBE_PROVIDER_NAME,
                provider_version=YOUTUBE_PROVIDER_VERSION,
                message="OK" if ok else "Credential check failed.",
            )
        except Exception as exc:
            return ProviderHealthReport(
                ok=False,
                provider=YOUTUBE_PROVIDER_NAME,
                provider_version=YOUTUBE_PROVIDER_VERSION,
                message=f"Health check error: {exc}",
            )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=YOUTUBE_PROVIDER_NAME,
            version=YOUTUBE_PROVIDER_VERSION,
            supports_scheduling=True,
            supports_playlists=True,
            supports_captions=True,
            supports_thumbnails=True,
            supports_status_polling=True,
        )

    def shutdown(self) -> None:
        self._initialized = False

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_snippet(self, package: UploadPackage) -> dict:
        snippet: dict = {
            "title": package.title,
            "description": package.description,
            "tags": package.tags,
            "defaultLanguage": package.language,
        }
        if package.category:
            snippet["categoryId"] = package.category
        return snippet

    def _build_status(self, package: UploadPackage) -> dict:
        status: dict = {
            "privacyStatus": package.visibility,
            "selfDeclaredMadeForKids": package.made_for_kids,
        }
        if package.scheduled_at:
            status["publishAt"] = package.scheduled_at
        return status
