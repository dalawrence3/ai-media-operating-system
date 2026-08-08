"""Tests for the publishing repository layer."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core.database import open_db
from app.publishing.errors import (
    IllegalJobTransitionError,
    IllegalPublishingPlanTransitionError,
    PublicationNotFoundError,
    PublishingJobNotFoundError,
    PublishingPlanNotFoundError,
)
from app.publishing.models import (
    PublishingMetadataDraft,
    PublishingPlan,
    PublishingScheduleDraft,
)
from app.publishing.repository import (
    approve_publishing_plan,
    create_publication,
    create_publishing_job,
    create_publishing_plan,
    get_active_publishing_job,
    get_latest_publication,
    get_latest_publishing_job,
    get_publication,
    get_publishing_plan,
    get_publishing_plan_by_hash,
    list_publishing_jobs,
    list_publishing_plans,
    list_publishing_review_events,
    record_publishing_review_event,
    reject_publishing_plan,
    supersede_publishing_plan,
    update_publication_status,
    update_publishing_job_status,
    update_publishing_plan_status,
)


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = open_db(tmp_path / "test.db")
    # Disable FKs for seeding
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        "INSERT INTO render_manifests"
        " (id, scene_manifest_id, narration_run_id, caption_run_id,"
        "  topic_id, plan_id, script_id, input_hash,"
        "  render_schema_version, compositor_version,"
        "  total_scene_count, total_duration_ms, width, height, fps,"
        "  status, created_at, updated_at)"
        " VALUES (1,1,1,1,1,1,1,'rmh','Rv1','cv1',0,0,1080,1920,30,"
        "  'approved','2024-01-01','2024-01-01')"
    )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    return conn


def _metadata(title: str = "Test Video", **kw) -> PublishingMetadataDraft:
    return PublishingMetadataDraft(title=title, **kw)


def _schedule() -> PublishingScheduleDraft:
    return PublishingScheduleDraft()


def _create_plan(conn, input_hash: str = "hash1", **kw) -> PublishingPlan:
    return create_publishing_plan(
        conn,
        render_manifest_id=1,
        render_job_id=None,
        topic_id=1,
        production_plan_id=1,
        script_id=1,
        scene_manifest_id=1,
        narration_run_id=1,
        caption_run_id=1,
        experiment_id=None,
        input_hash=input_hash,
        provider="fake",
        provider_version="1.0.0",
        metadata=_metadata(**kw),
        schedule=_schedule(),
    )


class TestCreatePublishingPlan:
    def test_creates_in_draft_status(self, db):
        plan = _create_plan(db)
        assert plan.status == "draft"
        assert plan.id is not None

    def test_persists_metadata(self, db):
        plan = _create_plan(db, title="My Title", description="Desc", tags=["a", "b"])
        assert plan.title == "My Title"
        assert plan.description == "Desc"
        assert plan.tags == ["a", "b"]

    def test_unique_input_hash_enforced(self, db):
        import sqlite3 as _sqlite3

        _create_plan(db, input_hash="same_hash")
        with pytest.raises(_sqlite3.IntegrityError):
            _create_plan(db, input_hash="same_hash")

    def test_get_by_hash(self, db):
        plan = _create_plan(db, input_hash="my_hash")
        found = get_publishing_plan_by_hash(db, "my_hash")
        assert found is not None
        assert found.id == plan.id

    def test_get_by_hash_missing(self, db):
        assert get_publishing_plan_by_hash(db, "no_such_hash") is None

    def test_get_missing_returns_none(self, db):
        assert get_publishing_plan(db, 9999) is None


class TestListPublishingPlans:
    def test_list_empty(self, db):
        assert list_publishing_plans(db) == []

    def test_list_all(self, db):
        _create_plan(db, input_hash="h1")
        _create_plan(db, input_hash="h2")
        plans = list_publishing_plans(db)
        assert len(plans) == 2

    def test_filter_by_render_manifest(self, db):
        _create_plan(db, input_hash="h1")
        result = list_publishing_plans(db, render_manifest_id=1)
        assert len(result) == 1

    def test_filter_by_status(self, db):
        _create_plan(db, input_hash="h1")
        result = list_publishing_plans(db, status="draft")
        assert len(result) == 1
        result_approved = list_publishing_plans(db, status="approved")
        assert len(result_approved) == 0

    def test_superseded_excluded_by_default(self, db):
        plan1 = _create_plan(db, input_hash="h1")
        plan2 = _create_plan(db, input_hash="h2")
        supersede_publishing_plan(db, plan1.id, plan2.id)
        db.commit()
        result = list_publishing_plans(db)
        ids = {p.id for p in result}
        assert plan1.id not in ids
        assert plan2.id in ids

    def test_superseded_included_when_requested(self, db):
        plan1 = _create_plan(db, input_hash="h1")
        plan2 = _create_plan(db, input_hash="h2")
        supersede_publishing_plan(db, plan1.id, plan2.id)
        db.commit()
        result = list_publishing_plans(db, include_superseded=True)
        assert len(result) == 2


class TestPlanStatusTransitions:
    def test_approve_plan(self, db):
        plan = _create_plan(db)
        updated = update_publishing_plan_status(db, plan.id, "approved")
        assert updated.status == "approved"
        assert updated.approved_at is not None

    def test_reject_plan(self, db):
        plan = _create_plan(db)
        updated = update_publishing_plan_status(db, plan.id, "rejected")
        assert updated.status == "rejected"
        assert updated.rejected_at is not None

    def test_illegal_transition_raises(self, db):
        plan = _create_plan(db)
        update_publishing_plan_status(db, plan.id, "approved")
        with pytest.raises(IllegalPublishingPlanTransitionError):
            update_publishing_plan_status(db, plan.id, "rejected")

    def test_plan_not_found_raises(self, db):
        with pytest.raises(PublishingPlanNotFoundError):
            update_publishing_plan_status(db, 9999, "approved")

    def test_approve_records_event(self, db):
        plan = _create_plan(db)
        approve_publishing_plan(db, plan.id, actor="alice")
        events = list_publishing_review_events(db, plan.id)
        assert any(e.event_type == "plan_approved" and e.actor == "alice" for e in events)

    def test_reject_records_event_with_reason(self, db):
        plan = _create_plan(db)
        reject_publishing_plan(db, plan.id, reason_code="quality", notes="too dark")
        events = list_publishing_review_events(db, plan.id)
        assert any(
            e.event_type == "plan_rejected" and e.reason_code == "quality" and e.notes == "too dark"
            for e in events
        )

    def test_supersede_sets_fields(self, db):
        plan1 = _create_plan(db, input_hash="h1")
        plan2 = _create_plan(db, input_hash="h2")
        supersede_publishing_plan(db, plan1.id, plan2.id)
        db.commit()
        p1 = get_publishing_plan(db, plan1.id)
        assert p1 is not None
        assert p1.superseded_at is not None
        assert p1.superseded_by_id == plan2.id
        assert p1.status == "draft"  # status unchanged


class TestPublishingJobs:
    def test_create_job(self, db):
        plan = _create_plan(db)
        job = create_publishing_job(db, plan.id, 1, "fake", "1.0.0")
        assert job.status == "queued"
        assert job.attempt_number == 1
        assert job.retry_count == 0

    def test_get_latest_job(self, db):
        plan = _create_plan(db)
        create_publishing_job(db, plan.id, 1, "fake", "1.0.0")
        job2 = create_publishing_job(db, plan.id, 2, "fake", "1.0.0")
        latest = get_latest_publishing_job(db, plan.id)
        assert latest is not None
        assert latest.id == job2.id

    def test_get_active_job_queued(self, db):
        plan = _create_plan(db)
        job = create_publishing_job(db, plan.id, 1, "fake", "1.0.0")
        active = get_active_publishing_job(db, plan.id)
        assert active is not None
        assert active.id == job.id

    def test_get_active_job_none_when_completed(self, db):
        plan = _create_plan(db)
        job = create_publishing_job(db, plan.id, 1, "fake", "1.0.0")
        update_publishing_job_status(db, job.id, "running", mark_started=True)
        update_publishing_job_status(db, job.id, "completed", mark_completed=True)
        assert get_active_publishing_job(db, plan.id) is None

    def test_transition_to_running(self, db):
        plan = _create_plan(db)
        job = create_publishing_job(db, plan.id, 1, "fake", "1.0.0")
        updated = update_publishing_job_status(db, job.id, "running", mark_started=True)
        assert updated.status == "running"
        assert updated.started_at is not None

    def test_illegal_job_transition_raises(self, db):
        plan = _create_plan(db)
        job = create_publishing_job(db, plan.id, 1, "fake", "1.0.0")
        update_publishing_job_status(db, job.id, "running")
        update_publishing_job_status(db, job.id, "completed")
        with pytest.raises(IllegalJobTransitionError):
            update_publishing_job_status(db, job.id, "running")

    def test_job_not_found_raises(self, db):
        with pytest.raises(PublishingJobNotFoundError):
            update_publishing_job_status(db, 9999, "running")

    def test_list_jobs_ordered_by_attempt(self, db):
        plan = _create_plan(db)
        create_publishing_job(db, plan.id, 1, "fake", "1.0.0")
        create_publishing_job(db, plan.id, 2, "fake", "1.0.0")
        jobs = list_publishing_jobs(db, plan.id)
        assert [j.attempt_number for j in jobs] == [1, 2]


class TestPublications:
    def _setup(self, db):
        plan = _create_plan(db)
        job = create_publishing_job(db, plan.id, 1, "fake", "1.0.0")
        pub = create_publication(
            db,
            publishing_plan_id=plan.id,
            publishing_job_id=job.id,
            provider="fake",
            provider_version="1.0.0",
            provider_video_id="vid001",
            provider_url="https://fake.example/watch?v=vid001",
            provider_status={"status": "uploaded"},
            visibility="private",
            scheduled_at=None,
            input_hash=plan.input_hash,
            output_sha256="sha256abc",
        )
        return plan, job, pub

    def test_create_publication(self, db):
        _, _, pub = self._setup(db)
        assert pub.status == "uploading"
        assert pub.provider_video_id == "vid001"

    def test_get_publication(self, db):
        _, _, pub = self._setup(db)
        found = get_publication(db, pub.id)
        assert found is not None
        assert found.id == pub.id

    def test_get_missing_publication(self, db):
        assert get_publication(db, 9999) is None

    def test_get_latest_publication(self, db):
        _, _, pub = self._setup(db)
        latest = get_latest_publication(db, pub.publishing_plan_id)
        assert latest is not None
        assert latest.id == pub.id

    def test_update_status_uploaded(self, db):
        _, _, pub = self._setup(db)
        updated = update_publication_status(db, pub.id, "uploaded")
        assert updated.status == "uploaded"

    def test_update_status_published(self, db):
        _, _, pub = self._setup(db)
        update_publication_status(db, pub.id, "uploaded")
        updated = update_publication_status(
            db, pub.id, "published", published_at="2024-01-01T12:00:00"
        )
        assert updated.status == "published"
        assert updated.published_at == "2024-01-01T12:00:00"

    def test_illegal_publication_transition_raises(self, db):
        from app.publishing.errors import IllegalPublicationTransitionError

        _, _, pub = self._setup(db)
        with pytest.raises(IllegalPublicationTransitionError):
            update_publication_status(db, pub.id, "published")  # must go through uploaded

    def test_publication_not_found_raises(self, db):
        with pytest.raises(PublicationNotFoundError):
            update_publication_status(db, 9999, "uploaded")

    def test_provider_status_json_parsed(self, db):
        _, _, pub = self._setup(db)
        assert pub.provider_status == {"status": "uploaded"}


class TestPublishingReviewEvents:
    def test_record_event(self, db):
        plan = _create_plan(db)
        ev = record_publishing_review_event(
            db,
            publishing_plan_id=plan.id,
            event_type="plan_prepared",
            topic_id=1,
            production_plan_id=1,
            script_id=1,
            render_manifest_id=1,
            provider="fake",
            actor="alice",
            notes="prepared for qa",
        )
        assert ev.event_type == "plan_prepared"
        assert ev.actor == "alice"
        assert ev.notes == "prepared for qa"

    def test_events_are_append_only(self, db):
        plan = _create_plan(db)
        record_publishing_review_event(
            db,
            publishing_plan_id=plan.id,
            event_type="plan_prepared",
            topic_id=1,
            production_plan_id=1,
            script_id=1,
            render_manifest_id=1,
            provider="fake",
        )
        record_publishing_review_event(
            db,
            publishing_plan_id=plan.id,
            event_type="plan_approved",
            topic_id=1,
            production_plan_id=1,
            script_id=1,
            render_manifest_id=1,
            provider="fake",
        )
        events = list_publishing_review_events(db, plan.id)
        assert len(events) == 2
        assert events[0].id < events[1].id

    def test_list_events_empty(self, db):
        plan = _create_plan(db)
        assert list_publishing_review_events(db, plan.id) == []

    def test_event_has_immutable_context(self, db):
        plan = _create_plan(db)
        ev = record_publishing_review_event(
            db,
            publishing_plan_id=plan.id,
            event_type="plan_prepared",
            topic_id=42,
            production_plan_id=7,
            script_id=3,
            render_manifest_id=1,
            provider="fake",
            experiment_id="exp-1",
        )
        assert ev.topic_id == 42
        assert ev.production_plan_id == 7
        assert ev.experiment_id == "exp-1"
