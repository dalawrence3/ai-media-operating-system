"""PublishingProvider Protocol — provider-neutral publishing interface.

Generic orchestration code depends only on this protocol.
No provider names, API endpoints, or credential types may appear here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class UploadPackage:
    """Everything a provider needs to upload and publish a video."""

    plan_id: int
    file_path: str
    file_sha256: str
    title: str
    description: str
    tags: list[str]
    language: str
    category: str | None
    visibility: str
    made_for_kids: bool
    scheduled_at: str | None
    captions_path: str | None
    playlist_id: str | None
    provider_metadata: dict = field(default_factory=dict)


@dataclass
class UploadResult:
    """Outcome of a successful upload operation (bytes transferred, not yet live)."""

    provider_video_id: str
    provider_url: str | None
    provider_response: dict = field(default_factory=dict)


@dataclass
class PublishResult:
    """Outcome of a publish (set-visibility / go-live) operation."""

    provider_video_id: str
    provider_url: str | None
    status: str                        # 'published' | 'scheduled' | 'failed'
    published_at: str | None = None
    scheduled_at: str | None = None
    provider_response: dict = field(default_factory=dict)


@dataclass
class ProviderHealthReport:
    """Provider readiness status."""

    ok: bool
    provider: str
    provider_version: str
    message: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class ProviderCapabilities:
    """Static capabilities for a provider."""

    name: str
    version: str
    supports_scheduling: bool = False
    supports_playlists: bool = False
    supports_captions: bool = False
    supports_thumbnails: bool = False
    supports_status_polling: bool = False


@runtime_checkable
class PublishingProvider(Protocol):
    """Provider-neutral interface for video publishing adapters."""

    @property
    def provider_name(self) -> str: ...

    @property
    def provider_version(self) -> str: ...

    def initialize(self) -> None:
        """One-time initialization (credential loading, session setup)."""
        ...

    def validate_credentials(self) -> bool:
        """Return True if credentials are configured and valid."""
        ...

    def prepare_package(self, package: UploadPackage) -> UploadPackage:
        """Validate and enrich the package with provider-specific metadata."""
        ...

    def upload(self, package: UploadPackage) -> UploadResult:
        """Transfer the video file to the provider. Does not make it live."""
        ...

    def publish(
        self,
        result: UploadResult,
        *,
        scheduled_at: str | None = None,
        visibility: str = "private",
    ) -> PublishResult:
        """Set visibility and/or schedule. Makes the upload live (or scheduled)."""
        ...

    def health(self) -> ProviderHealthReport:
        """Check provider connectivity and credential validity."""
        ...

    def capabilities(self) -> ProviderCapabilities:
        """Return static provider capability declarations."""
        ...

    def shutdown(self) -> None:
        """Release resources (sessions, file handles, etc.)."""
        ...
