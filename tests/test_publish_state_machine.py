"""Tests for publishing state machine guards."""

from __future__ import annotations

import pytest

from app.publishing.errors import (
    IllegalJobTransitionError,
    IllegalPublicationTransitionError,
    IllegalPublishingPlanTransitionError,
)
from app.publishing.state_machine import (
    check_job_transition,
    check_plan_transition,
    check_publication_transition,
)


class TestPlanStateMachine:
    def test_draft_to_approved(self):
        check_plan_transition("draft", "approved")

    def test_draft_to_rejected(self):
        check_plan_transition("draft", "rejected")

    def test_approved_to_draft_blocked(self):
        with pytest.raises(IllegalPublishingPlanTransitionError):
            check_plan_transition("approved", "draft")

    def test_rejected_to_approved_blocked(self):
        with pytest.raises(IllegalPublishingPlanTransitionError):
            check_plan_transition("rejected", "approved")

    def test_approved_to_rejected_blocked(self):
        with pytest.raises(IllegalPublishingPlanTransitionError):
            check_plan_transition("approved", "rejected")


class TestJobStateMachine:
    def test_queued_to_running(self):
        check_job_transition("queued", "running")

    def test_queued_to_cancelled(self):
        check_job_transition("queued", "cancelled")

    def test_running_to_completed(self):
        check_job_transition("running", "completed")

    def test_running_to_failed(self):
        check_job_transition("running", "failed")

    def test_failed_to_retry_scheduled(self):
        check_job_transition("failed", "retry_scheduled")

    def test_retry_scheduled_to_running(self):
        check_job_transition("retry_scheduled", "running")

    def test_completed_to_running_blocked(self):
        with pytest.raises(IllegalJobTransitionError):
            check_job_transition("completed", "running")

    def test_cancelled_to_running_blocked(self):
        with pytest.raises(IllegalJobTransitionError):
            check_job_transition("cancelled", "running")


class TestPublicationStateMachine:
    def test_uploading_to_uploaded(self):
        check_publication_transition("uploading", "uploaded")

    def test_uploading_to_failed(self):
        check_publication_transition("uploading", "failed")

    def test_uploaded_to_published(self):
        check_publication_transition("uploaded", "published")

    def test_uploaded_to_scheduled(self):
        check_publication_transition("uploaded", "scheduled")

    def test_scheduled_to_published(self):
        check_publication_transition("scheduled", "published")

    def test_published_to_deleted(self):
        check_publication_transition("published", "deleted")

    def test_published_to_uploading_blocked(self):
        with pytest.raises(IllegalPublicationTransitionError):
            check_publication_transition("published", "uploading")

    def test_failed_to_published_blocked(self):
        with pytest.raises(IllegalPublicationTransitionError):
            check_publication_transition("failed", "published")
