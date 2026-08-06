"""Phase 8 rendering engine domain exceptions."""

from __future__ import annotations


class RenderEngineError(Exception):
    """Base for all rendering engine errors."""


class NoApprovedManifestError(RenderEngineError):
    """No approved scene manifest exists for the requested topic/plan."""


class RenderManifestNotFoundError(RenderEngineError):
    """Render manifest row not found."""


class RenderJobNotFoundError(RenderEngineError):
    """Render job row not found."""


class IllegalRenderTransitionError(RenderEngineError):
    """Attempt to move a render manifest to a disallowed status."""


class IllegalRenderJobTransitionError(RenderEngineError):
    """Attempt to move a render job to a disallowed status."""


class RenderManifestAlreadyExistsError(RenderEngineError):
    """A render manifest with the same input_hash already exists."""


class RenderBuildError(RenderEngineError):
    """Failed to assemble the render manifest from upstream artifacts."""


class RenderBackendError(RenderEngineError):
    """The render backend (e.g. FFmpeg) failed during execution."""


class RenderValidationError(RenderEngineError):
    """FFprobe validation of the output video failed."""


class FFmpegNotFoundError(RenderEngineError):
    """ffmpeg binary not found on PATH."""


class FFprobeNotFoundError(RenderEngineError):
    """ffprobe binary not found on PATH."""
