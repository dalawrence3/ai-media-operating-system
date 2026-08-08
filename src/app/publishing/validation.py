"""Pre-publish validation — approved render and metadata checks."""

from __future__ import annotations

from app.media.models import ApprovedRender
from app.publishing.errors import PublishingValidationError
from app.publishing.models import PublishingMetadataDraft

# Safety floor: renders shorter than 1s are likely broken
_MIN_DURATION_S = 1.0

# YouTube API limits (also reasonable for other platforms)
_MAX_TITLE_LEN = 100
_MAX_DESCRIPTION_LEN = 5000
_MAX_TAG_COUNT = 500
_MAX_TAG_LEN = 500


def validate_approved_render_for_publishing(approved_render: ApprovedRender) -> None:
    """Raise if the approved render is not safe to publish."""
    if not approved_render.output_path:
        raise PublishingValidationError("ApprovedRender has no output_path.")
    if not approved_render.output_sha256:
        raise PublishingValidationError("ApprovedRender has no output_sha256.")
    if approved_render.duration_s < _MIN_DURATION_S:
        raise PublishingValidationError(
            f"ApprovedRender duration {approved_render.duration_s}s is below minimum "
            f"{_MIN_DURATION_S}s — likely a broken render."
        )
    if approved_render.width <= 0 or approved_render.height <= 0:
        raise PublishingValidationError(
            f"ApprovedRender has invalid dimensions "
            f"{approved_render.width}x{approved_render.height}."
        )


def validate_publishing_metadata(metadata: PublishingMetadataDraft) -> None:
    """Raise if the metadata fails validation rules."""
    if not metadata.title or not metadata.title.strip():
        raise PublishingValidationError("Publishing title must not be empty.")
    if len(metadata.title) > _MAX_TITLE_LEN:
        raise PublishingValidationError(
            f"Title exceeds {_MAX_TITLE_LEN} chars ({len(metadata.title)})."
        )
    if len(metadata.description) > _MAX_DESCRIPTION_LEN:
        raise PublishingValidationError(
            f"Description exceeds {_MAX_DESCRIPTION_LEN} chars ({len(metadata.description)})."
        )
    if len(metadata.tags) > _MAX_TAG_COUNT:
        raise PublishingValidationError(
            f"Tag count {len(metadata.tags)} exceeds maximum {_MAX_TAG_COUNT}."
        )
    for tag in metadata.tags:
        if len(tag) > _MAX_TAG_LEN:
            raise PublishingValidationError(f"Tag {tag!r} exceeds {_MAX_TAG_LEN} chars.")
    from app.publishing.constants import VISIBILITY_OPTIONS

    if metadata.visibility not in VISIBILITY_OPTIONS:
        raise PublishingValidationError(
            f"Invalid visibility {metadata.visibility!r}. "
            f"Must be one of: {sorted(VISIBILITY_OPTIONS)}."
        )
