"""Tests for publishing validation and scheduler."""

from __future__ import annotations

import pytest

from app.publishing.errors import PublishingValidationError
from app.publishing.models import PublishingMetadataDraft, PublishingScheduleDraft
from app.publishing.scheduler import is_scheduled_time_due, validate_schedule
from app.publishing.validation import (
    validate_approved_render_for_publishing,
    validate_publishing_metadata,
)


def _make_approved_render(**overrides):
    from datetime import UTC, datetime

    from app.media.models import ApprovedRender

    base = dict(
        render_manifest_id=1,
        render_job_id=1,
        scene_manifest_id=1,
        narration_run_id=1,
        caption_run_id=1,
        topic_id=1,
        plan_id=1,
        script_id=1,
        experiment_id=None,
        output_path="/tmp/out.mp4",
        output_sha256="abc123",
        duration_s=30.0,
        width=1080,
        height=1920,
        fps=30,
        video_codec="h264",
        audio_codec="aac",
        approved_at=datetime.now(UTC),
    )
    base.update(overrides)
    return ApprovedRender(**base)


class TestApprovedRenderValidation:
    def test_valid_render_passes(self):
        validate_approved_render_for_publishing(_make_approved_render())

    def test_missing_output_path_raises(self):
        with pytest.raises(PublishingValidationError, match="output_path"):
            validate_approved_render_for_publishing(_make_approved_render(output_path=""))

    def test_missing_output_sha256_raises(self):
        with pytest.raises(PublishingValidationError, match="output_sha256"):
            validate_approved_render_for_publishing(_make_approved_render(output_sha256=""))

    def test_zero_duration_raises(self):
        with pytest.raises(PublishingValidationError, match="duration"):
            validate_approved_render_for_publishing(_make_approved_render(duration_s=0.0))

    def test_negative_duration_raises(self):
        with pytest.raises(PublishingValidationError, match="duration"):
            validate_approved_render_for_publishing(_make_approved_render(duration_s=-1.0))

    def test_zero_width_raises(self):
        with pytest.raises(PublishingValidationError, match="dimensions"):
            validate_approved_render_for_publishing(_make_approved_render(width=0))


class TestMetadataValidation:
    def test_valid_metadata_passes(self):
        validate_publishing_metadata(PublishingMetadataDraft(title="My Video"))

    def test_empty_title_raises(self):
        with pytest.raises(PublishingValidationError, match="title"):
            validate_publishing_metadata(PublishingMetadataDraft(title=""))

    def test_whitespace_title_raises(self):
        with pytest.raises(PublishingValidationError, match="title"):
            validate_publishing_metadata(PublishingMetadataDraft(title="   "))

    def test_title_too_long_raises(self):
        with pytest.raises(PublishingValidationError, match="100"):
            validate_publishing_metadata(PublishingMetadataDraft(title="x" * 101))

    def test_description_too_long_raises(self):
        with pytest.raises(PublishingValidationError, match="5000"):
            validate_publishing_metadata(
                PublishingMetadataDraft(title="T", description="x" * 5001)
            )

    def test_invalid_visibility_raises(self):
        with pytest.raises(PublishingValidationError, match="visibility"):
            validate_publishing_metadata(
                PublishingMetadataDraft(title="T", visibility="secret")
            )

    def test_all_visibility_values_valid(self):
        for v in ("private", "unlisted", "public"):
            validate_publishing_metadata(PublishingMetadataDraft(title="T", visibility=v))


class TestScheduleValidation:
    def test_immediate_valid(self):
        validate_schedule(PublishingScheduleDraft(schedule_type="immediate"))

    def test_manual_valid(self):
        validate_schedule(PublishingScheduleDraft(schedule_type="manual"))

    def test_invalid_schedule_type_raises(self):
        with pytest.raises(PublishingValidationError, match="schedule_type"):
            validate_schedule(PublishingScheduleDraft(schedule_type="cron"))

    def test_scheduled_without_time_raises(self):
        with pytest.raises(PublishingValidationError, match="scheduled_at"):
            validate_schedule(PublishingScheduleDraft(schedule_type="scheduled"))

    def test_scheduled_in_past_raises(self):
        with pytest.raises(PublishingValidationError, match="future"):
            validate_schedule(
                PublishingScheduleDraft(
                    schedule_type="scheduled", scheduled_at="2000-01-01T00:00:00+00:00"
                )
            )

    def test_scheduled_in_future_valid(self):
        validate_schedule(
            PublishingScheduleDraft(
                schedule_type="scheduled",
                scheduled_at="2099-01-01T00:00:00+00:00",
                timezone="America/New_York",
            )
        )

    def test_invalid_timezone_raises(self):
        with pytest.raises(PublishingValidationError, match="timezone"):
            validate_schedule(
                PublishingScheduleDraft(schedule_type="immediate", timezone="UTC+5")
            )

    def test_is_scheduled_time_due_immediate(self):
        assert is_scheduled_time_due(PublishingScheduleDraft(schedule_type="immediate"))

    def test_is_scheduled_time_due_future(self):
        assert not is_scheduled_time_due(
            PublishingScheduleDraft(
                schedule_type="scheduled", scheduled_at="2099-01-01T00:00:00+00:00"
            )
        )
