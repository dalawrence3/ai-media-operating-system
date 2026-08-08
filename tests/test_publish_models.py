"""Tests for publishing model construction and properties."""

from __future__ import annotations

import pytest

from app.publishing.models import (
    PublishingJob,
    PublishingMetadataDraft,
    PublishingPlan,
    PublishingScheduleDraft,
)


def _plan_row(**overrides) -> dict:
    defaults = {
        "id": 1,
        "render_manifest_id": 1,
        "render_job_id": None,
        "topic_id": 1,
        "production_plan_id": 1,
        "script_id": 1,
        "scene_manifest_id": 1,
        "narration_run_id": 1,
        "caption_run_id": 1,
        "experiment_id": None,
        "input_hash": "abc123",
        "publishing_engine_version": "1.0.0",
        "metadata_version": "1.0.0",
        "provider": "fake",
        "provider_version": "1.0.0",
        "title": "My Video",
        "description": "",
        "tags_json": '["a","b"]',
        "language": "en",
        "category": None,
        "visibility": "private",
        "made_for_kids": 0,
        "playlist_id": None,
        "thumbnail_path": None,
        "captions_path": None,
        "copyright_notice": None,
        "licensing_notes": None,
        "publication_notes": None,
        "schedule_type": "immediate",
        "scheduled_at": None,
        "timezone": None,
        "status": "draft",
        "approved_at": None,
        "rejected_at": None,
        "rejection_reason": None,
        "superseded_at": None,
        "superseded_by_id": None,
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00",
    }
    defaults.update(overrides)
    return defaults


class TestPublishingPlan:
    def test_tags_parsed_from_json(self):
        plan = PublishingPlan(**_plan_row())
        assert plan.tags == ["a", "b"]

    def test_tags_empty_json(self):
        plan = PublishingPlan(**_plan_row(tags_json="[]"))
        assert plan.tags == []

    def test_tags_malformed_json_returns_empty(self):
        plan = PublishingPlan(**_plan_row(tags_json="INVALID"))
        assert plan.tags == []

    def test_is_superseded_false_when_no_superseded_at(self):
        plan = PublishingPlan(**_plan_row())
        assert not plan.is_superseded

    def test_is_superseded_true_when_superseded_at_set(self):
        plan = PublishingPlan(**_plan_row(superseded_at="2024-01-02T00:00:00"))
        assert plan.is_superseded

    def test_frozen_model_immutable(self):
        from pydantic import ValidationError
        plan = PublishingPlan(**_plan_row())
        with pytest.raises((TypeError, ValidationError)):
            plan.title = "changed"  # type: ignore[misc]


class TestPublishingJob:
    def _job(self, **overrides) -> PublishingJob:
        base = {
            "id": 1,
            "publishing_plan_id": 1,
            "attempt_number": 1,
            "provider": "fake",
            "provider_version": "1.0.0",
            "status": "queued",
            "error_message": None,
            "retry_count": 0,
            "retry_after": None,
            "started_at": None,
            "completed_at": None,
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }
        base.update(overrides)
        return PublishingJob(**base)

    def test_is_active_queued(self):
        assert self._job(status="queued").is_active

    def test_is_active_running(self):
        assert self._job(status="running").is_active

    def test_is_not_active_completed(self):
        assert not self._job(status="completed").is_active

    def test_is_terminal_completed(self):
        assert self._job(status="completed").is_terminal

    def test_is_terminal_failed(self):
        assert self._job(status="failed").is_terminal

    def test_is_not_terminal_queued(self):
        assert not self._job(status="queued").is_terminal


class TestPublishingMetadataDraft:
    def test_defaults(self):
        m = PublishingMetadataDraft(title="Test")
        assert m.visibility == "private"
        assert m.made_for_kids is False
        assert m.tags == []
        assert m.language == "en"

    def test_explicit_values(self):
        m = PublishingMetadataDraft(
            title="T",
            description="D",
            tags=["x"],
            visibility="public",
            made_for_kids=True,
        )
        assert m.tags == ["x"]
        assert m.visibility == "public"
        assert m.made_for_kids is True


class TestPublishingScheduleDraft:
    def test_defaults_to_immediate(self):
        s = PublishingScheduleDraft()
        assert s.schedule_type == "immediate"
        assert s.scheduled_at is None

    def test_scheduled_type(self):
        s = PublishingScheduleDraft(
            schedule_type="scheduled",
            scheduled_at="2099-01-01T00:00:00+00:00",
            timezone="UTC",
        )
        assert s.schedule_type == "scheduled"
