"""Phase 8 rendering engine constants."""

from __future__ import annotations

# ── Version strings ───────────────────────────────────────────────────────────

RENDER_SCHEMA_VERSION = "Render-v1"
COMPOSITOR_VERSION = "compositor-1.0.0"
FFMPEG_BACKEND_VERSION = "ffmpeg-1.0.0"
FFPROBE_VALIDATOR_VERSION = "ffprobe-1.0.0"

# ── Resolution presets ────────────────────────────────────────────────────────

# (width, height)
RESOLUTION_SHORTS = (1080, 1920)  # YouTube Shorts / TikTok portrait
RESOLUTION_LANDSCAPE_HD = (1920, 1080)
RESOLUTION_PORTRAIT_720 = (720, 1280)

DEFAULT_RESOLUTION = RESOLUTION_SHORTS
DEFAULT_FPS = 30
DEFAULT_VIDEO_CODEC = "libx264"
DEFAULT_AUDIO_CODEC = "aac"
DEFAULT_CRF = 23
DEFAULT_FFMPEG_PRESET = "medium"
DEFAULT_AUDIO_BITRATE = "128k"
DEFAULT_AUDIO_SAMPLE_RATE = 44100

# Placeholder slide background colour (dark navy — visible, non-distracting)
PLACEHOLDER_BG_COLOR = "0x1a1a2e"

# ── Render manifest lifecycle ─────────────────────────────────────────────────

RENDER_STATUS_DRAFT = "draft"
RENDER_STATUS_APPROVED = "approved"
RENDER_STATUS_REJECTED = "rejected"
RENDER_STATUS_SUPERSEDED = "superseded"

RENDER_STATUSES = (
    RENDER_STATUS_DRAFT,
    RENDER_STATUS_APPROVED,
    RENDER_STATUS_REJECTED,
    RENDER_STATUS_SUPERSEDED,
)

# Valid transitions: draft → approved | rejected; approved → superseded
RENDER_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    RENDER_STATUS_DRAFT: frozenset({RENDER_STATUS_APPROVED, RENDER_STATUS_REJECTED}),
    RENDER_STATUS_APPROVED: frozenset({RENDER_STATUS_SUPERSEDED}),
    RENDER_STATUS_REJECTED: frozenset(),
    RENDER_STATUS_SUPERSEDED: frozenset(),
}

# ── Render job lifecycle ──────────────────────────────────────────────────────

RENDER_JOB_STATUS_PENDING = "pending"
RENDER_JOB_STATUS_RENDERING = "rendering"
RENDER_JOB_STATUS_COMPLETED = "completed"
RENDER_JOB_STATUS_FAILED = "failed"

RENDER_JOB_STATUSES = (
    RENDER_JOB_STATUS_PENDING,
    RENDER_JOB_STATUS_RENDERING,
    RENDER_JOB_STATUS_COMPLETED,
    RENDER_JOB_STATUS_FAILED,
)

RENDER_JOB_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    RENDER_JOB_STATUS_PENDING: frozenset({RENDER_JOB_STATUS_RENDERING}),
    RENDER_JOB_STATUS_RENDERING: frozenset({RENDER_JOB_STATUS_COMPLETED, RENDER_JOB_STATUS_FAILED}),
    RENDER_JOB_STATUS_COMPLETED: frozenset(),
    RENDER_JOB_STATUS_FAILED: frozenset(),
}

# ── Review event types ────────────────────────────────────────────────────────

EVENT_RENDER_APPROVED = "render_approved"
EVENT_RENDER_REJECTED = "render_rejected"

RENDER_REVIEW_EVENT_TYPES = (EVENT_RENDER_APPROVED, EVENT_RENDER_REJECTED)

# ── Review reason codes ───────────────────────────────────────────────────────

REASON_AUDIO_SYNC = "audio_sync"
REASON_VISUAL_QUALITY = "visual_quality"
REASON_CAPTION_ALIGNMENT = "caption_alignment"
REASON_DURATION_MISMATCH = "duration_mismatch"
REASON_ENCODING_ERROR = "encoding_error"
REASON_VALIDATION_FAILURE = "validation_failure"
REASON_OTHER = "other"

RENDER_REASON_CODES = (
    REASON_AUDIO_SYNC,
    REASON_VISUAL_QUALITY,
    REASON_CAPTION_ALIGNMENT,
    REASON_DURATION_MISMATCH,
    REASON_ENCODING_ERROR,
    REASON_VALIDATION_FAILURE,
    REASON_OTHER,
)

# ── Backend names ─────────────────────────────────────────────────────────────

BACKEND_FFMPEG = "ffmpeg"

# ── Validation thresholds ─────────────────────────────────────────────────────

# Tolerated fractional deviation between planned and actual video duration
MAX_DURATION_DEVIATION_FRACTION = 0.05

# Minimum acceptable bitrate (kbps) for a completed render
MIN_VIDEO_BITRATE_KBPS = 500
