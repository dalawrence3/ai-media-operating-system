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

# Supported media types for pre-upload file validation
SUPPORTED_VIDEO_EXTENSIONS = frozenset(
    {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv", ".flv", ".3gp", ".ts"}
)


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


class RealYouTubeAPIClient:
    """Live YouTube Data API v3 client using an OAuth access token.

    Uses google-api-python-client (already in pyproject.toml dependencies).
    Resumable upload is used for all video files via MediaFileUpload(resumable=True).

    Token management: the caller is responsible for ensuring the access token
    is valid and has youtube.upload scope before constructing this client.
    Use upload_gate.build_authenticated_youtube_provider() as the safe factory,
    which refreshes the token before construction.

    Credential construction: when refresh_token and client secrets are supplied,
    the google-auth library can auto-refresh mid-upload if the access token
    expires during a long chunked upload. Provide them via build_authenticated_youtube_provider()
    rather than constructing this client directly.

    Idempotency: YouTube videos.insert is NOT idempotent. A network failure
    after partial upload may result in an orphaned video on the channel.
    The caller must handle duplicate-upload risk (e.g. by checking for existing
    publications before retrying). This is documented but cannot be avoided
    within YouTube API semantics.
    """

    def __init__(
        self,
        access_token: str,
        *,
        refresh_token: str | None = None,
        token_uri: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        try:
            from google.oauth2.credentials import Credentials  # type: ignore[import]
            from googleapiclient.discovery import build  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "google-api-python-client is required for live YouTube uploads. "
                "Run: pip install google-api-python-client"
            ) from exc
        credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri=token_uri,
            client_id=client_id,
            client_secret=client_secret,
        )
        self._service = build("youtube", "v3", credentials=credentials)

    def insert_video(self, snippet: dict, status: dict, file_path: str) -> dict:
        try:
            from googleapiclient.http import MediaFileUpload  # type: ignore[import]
        except ImportError as exc:
            raise ImportError("google-api-python-client is required.") from exc

        import mimetypes

        mime_type, _ = mimetypes.guess_type(file_path)
        media = MediaFileUpload(
            file_path,
            mimetype=mime_type or "video/*",
            resumable=True,
        )
        request = self._service.videos().insert(
            part="snippet,status",
            body={"snippet": snippet, "status": status},
            media_body=media,
        )
        response = None
        while response is None:
            _, response = request.next_chunk()
        return response  # type: ignore[return-value]

    def update_video(self, video_id: str, snippet: dict, status: dict) -> dict:
        body: dict = {"id": video_id, "status": status}
        if snippet:
            body["snippet"] = snippet
        parts = "status,snippet" if snippet else "status"
        return self._service.videos().update(part=parts, body=body).execute()

    def get_video(self, video_id: str, parts: list[str]) -> dict:
        return self._service.videos().list(part=",".join(parts), id=video_id).execute()

    def health_check(self) -> bool:
        result = self._service.channels().list(part="id", mine=True).execute()
        return bool(result.get("items"))


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

            raise PublishingValidationError(f"YouTube tag count {len(package.tags)} exceeds 500.")
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

            raise ProviderUploadError(f"YouTube upload failed: {exc}") from exc
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

        # Hard-lock: mirror the upload() check so the publish step cannot
        # change an already-uploaded private video to a different privacy status.
        if visibility != "private":
            from app.publishing.errors import PublishingValidationError

            raise PublishingValidationError(
                f"YouTube publish step is locked to 'private'. "
                f"Requested visibility {visibility!r} is not permitted."
            )

        # videos.insert already set privacyStatus=private (and publishAt when scheduled_at
        # is supplied via _build_status).  videos.update requires the 'youtube' or
        # 'youtube.force-ssl' scope — scopes we intentionally do not hold and do not need
        # for a private-only upload workflow.  Skip the update entirely.
        if scheduled_at:
            return PublishResult(
                provider_video_id=result.provider_video_id,
                provider_url=result.provider_url,
                status="scheduled",
                published_at=None,
                scheduled_at=scheduled_at,
                provider_response=result.provider_response,
            )
        return PublishResult(
            provider_video_id=result.provider_video_id,
            provider_url=result.provider_url,
            status="published",
            published_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
            scheduled_at=None,
            provider_response=result.provider_response,
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
        # Hard-lock: this milestone only supports private uploads.
        # Public and unlisted are not yet permitted; reject to prevent accidents.
        if package.visibility != "private":
            from app.publishing.errors import PublishingValidationError

            raise PublishingValidationError(
                f"YouTube uploads are currently locked to 'private'. "
                f"Requested visibility {package.visibility!r} is not permitted. "
                "Only private uploads are supported in this milestone."
            )
        status: dict = {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": package.made_for_kids,
        }
        if package.scheduled_at:
            status["publishAt"] = package.scheduled_at
        return status


# ── Account-bound provider factory ───────────────────────────────────────────


def build_youtube_provider_for_account(
    conn: object,
    *,
    account_id: str,
    workspace_id: str,
    channel_id: str,
    oauth_client: object,
    token_store: object | None = None,
) -> YouTubePublishingProvider:
    """Resolve credentials, run pre-flight checks, and return a live provider.

    Gate sequence (fail closed):
      1. ACE_PUBLISHING_LIVE_ENABLED must be True.
      2. Account must be connected with an active credential profile.
      3. Token must be readable and refreshed if expired.
      4. youtube.upload scope must be in the stored token's granted scopes.
      5. Live channel identity must match the registered external_account_id.

    On any failure, raises a specific error from app.publishing.errors.
    No upload is attempted; the returned provider is safe to pass to
    start_publishing_job().
    """
    from app.publishing.upload_gate import build_authenticated_youtube_provider

    return build_authenticated_youtube_provider(
        conn,
        account_id=account_id,
        workspace_id=workspace_id,
        channel_id=channel_id,
        oauth_client=oauth_client,
        token_store=token_store,
    )
