"""Deterministic metadata builder for publishing plans.

Builds metadata from persisted upstream inputs and explicit operator configuration.
No LLM calls. No network requests. Fully reproducible.
"""

from __future__ import annotations

from app.media.models import ApprovedRender
from app.publishing.models import PublishingMetadataDraft


def build_metadata_draft(
    approved_render: ApprovedRender,
    *,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    language: str = "en",
    category: str | None = None,
    visibility: str = "private",
    made_for_kids: bool = False,
    playlist_id: str | None = None,
    thumbnail_path: str | None = None,
    captions_path: str | None = None,
    copyright_notice: str | None = None,
    licensing_notes: str | None = None,
    publication_notes: str | None = None,
) -> PublishingMetadataDraft:
    """Build a metadata draft from an approved render and explicit configuration."""
    return PublishingMetadataDraft(
        title=title,
        description=description,
        tags=list(tags or []),
        language=language,
        category=category,
        visibility=visibility,
        made_for_kids=made_for_kids,
        playlist_id=playlist_id,
        thumbnail_path=thumbnail_path,
        captions_path=captions_path,
        copyright_notice=copyright_notice,
        licensing_notes=licensing_notes,
        publication_notes=publication_notes,
    )
