"""Tests for rendering engine constants."""

from app.media.constants import (
    BACKEND_FFMPEG,
    COMPOSITOR_VERSION,
    DEFAULT_AUDIO_CODEC,
    DEFAULT_CRF,
    DEFAULT_FPS,
    DEFAULT_RESOLUTION,
    DEFAULT_VIDEO_CODEC,
    EVENT_RENDER_APPROVED,
    EVENT_RENDER_REJECTED,
    FFMPEG_BACKEND_VERSION,
    FFPROBE_VALIDATOR_VERSION,
    MAX_DURATION_DEVIATION_FRACTION,
    RENDER_JOB_STATUS_COMPLETED,
    RENDER_JOB_STATUS_FAILED,
    RENDER_JOB_STATUS_PENDING,
    RENDER_JOB_STATUS_RENDERING,
    RENDER_JOB_STATUSES,
    RENDER_JOB_VALID_TRANSITIONS,
    RENDER_REASON_CODES,
    RENDER_REVIEW_EVENT_TYPES,
    RENDER_SCHEMA_VERSION,
    RENDER_STATUS_APPROVED,
    RENDER_STATUS_DRAFT,
    RENDER_STATUS_REJECTED,
    RENDER_STATUS_SUPERSEDED,
    RENDER_STATUSES,
    RENDER_VALID_TRANSITIONS,
    RESOLUTION_LANDSCAPE_HD,
    RESOLUTION_PORTRAIT_720,
    RESOLUTION_SHORTS,
)


def test_schema_version_format():
    assert RENDER_SCHEMA_VERSION.startswith("Render-v")


def test_version_strings_non_empty():
    assert COMPOSITOR_VERSION
    assert FFMPEG_BACKEND_VERSION
    assert FFPROBE_VALIDATOR_VERSION


def test_default_resolution_is_shorts():
    assert DEFAULT_RESOLUTION == RESOLUTION_SHORTS
    assert DEFAULT_RESOLUTION == (1080, 1920)


def test_resolution_presets():
    assert RESOLUTION_SHORTS == (1080, 1920)
    assert RESOLUTION_LANDSCAPE_HD == (1920, 1080)
    assert RESOLUTION_PORTRAIT_720 == (720, 1280)


def test_default_fps():
    assert DEFAULT_FPS == 30


def test_default_codecs():
    assert DEFAULT_VIDEO_CODEC == "libx264"
    assert DEFAULT_AUDIO_CODEC == "aac"


def test_render_statuses_complete():
    assert RENDER_STATUS_DRAFT in RENDER_STATUSES
    assert RENDER_STATUS_APPROVED in RENDER_STATUSES
    assert RENDER_STATUS_REJECTED in RENDER_STATUSES
    assert RENDER_STATUS_SUPERSEDED in RENDER_STATUSES
    assert len(RENDER_STATUSES) == 4


def test_render_job_statuses_complete():
    assert RENDER_JOB_STATUS_PENDING in RENDER_JOB_STATUSES
    assert RENDER_JOB_STATUS_RENDERING in RENDER_JOB_STATUSES
    assert RENDER_JOB_STATUS_COMPLETED in RENDER_JOB_STATUSES
    assert RENDER_JOB_STATUS_FAILED in RENDER_JOB_STATUSES
    assert len(RENDER_JOB_STATUSES) == 4


def test_valid_render_transitions():
    # draft → approved ✓
    assert RENDER_STATUS_APPROVED in RENDER_VALID_TRANSITIONS[RENDER_STATUS_DRAFT]
    # draft → rejected ✓
    assert RENDER_STATUS_REJECTED in RENDER_VALID_TRANSITIONS[RENDER_STATUS_DRAFT]
    # approved → superseded ✓
    assert RENDER_STATUS_SUPERSEDED in RENDER_VALID_TRANSITIONS[RENDER_STATUS_APPROVED]
    # rejected → anything ✗
    assert len(RENDER_VALID_TRANSITIONS[RENDER_STATUS_REJECTED]) == 0
    # superseded → anything ✗
    assert len(RENDER_VALID_TRANSITIONS[RENDER_STATUS_SUPERSEDED]) == 0


def test_valid_job_transitions():
    # pending → rendering ✓
    assert RENDER_JOB_STATUS_RENDERING in RENDER_JOB_VALID_TRANSITIONS[RENDER_JOB_STATUS_PENDING]
    # rendering → completed ✓
    assert RENDER_JOB_STATUS_COMPLETED in RENDER_JOB_VALID_TRANSITIONS[RENDER_JOB_STATUS_RENDERING]
    # rendering → failed ✓
    assert RENDER_JOB_STATUS_FAILED in RENDER_JOB_VALID_TRANSITIONS[RENDER_JOB_STATUS_RENDERING]
    # completed → anything ✗
    assert len(RENDER_JOB_VALID_TRANSITIONS[RENDER_JOB_STATUS_COMPLETED]) == 0


def test_review_event_types():
    assert EVENT_RENDER_APPROVED in RENDER_REVIEW_EVENT_TYPES
    assert EVENT_RENDER_REJECTED in RENDER_REVIEW_EVENT_TYPES
    assert len(RENDER_REVIEW_EVENT_TYPES) == 2


def test_reason_codes_non_empty():
    assert len(RENDER_REASON_CODES) >= 5
    assert "audio_sync" in RENDER_REASON_CODES
    assert "visual_quality" in RENDER_REASON_CODES
    assert "other" in RENDER_REASON_CODES


def test_backend_name():
    assert BACKEND_FFMPEG == "ffmpeg"


def test_max_duration_deviation():
    assert 0 < MAX_DURATION_DEVIATION_FRACTION < 0.5


def test_default_crf_range():
    assert 0 <= DEFAULT_CRF <= 51
