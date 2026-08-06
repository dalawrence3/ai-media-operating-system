"""Tests for the publishing orchestrator — fake provider, zero network calls."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core.database import open_db
from app.publishing.errors import (
    ActiveJobExistsError,
    JobNotCancellableError,
    JobNotRetryableError,
    MaxRetriesExceededError,
    ProviderUploadError,
    PublishingPlanNotFoundError,
    PublishingValidationError,
    RenderManifestNotApprovedError,
)
from app.publishing.models import PublishingMetadataDraft, PublishingScheduleDraft
from app.publishing.orchestrator import (
    cancel_publishing_job,
    prepare_publishing_plan,
    retry_publishing_job,
    start_publishing_job,
    update_plan_schedule,
)
from app.publishing.providers.fake import FakePublishingProvider
from app.publishing.repository import (
    create_publishing_job,
    get_publishing_plan,
    list_publishing_jobs,
    list_publishing_review_events,
    update_publishing_job_status,
)


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = open_db(tmp_path / "test.db")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO topics (id, title, angle) VALUES (1,'T','A')")
    conn.execute(
        "INSERT INTO scripts (id, topic_id, version, body, status)"
        " VALUES (1,1,1,'body','approved')"
    )
    conn.execute(
        "INSERT INTO production_plans"
        " (id, topic_id, script_id, script_version, input_hash, script_body_hash,"
        "  plan_schema_version, renderer_version, duration_algorithm_version,"
        "  status, created_at, updated_at)"
        " VALUES (1,1,1,1,'ph','bh','v1','rv1','dv1','approved','2024-01-01','2024-01-01')"
    )
    conn.execute(
        "INSERT INTO narration_runs"
        " (id, plan_id, plan_input_hash, voice_profile_id, voice_profile_version,"
        "  language, speaking_rate, settings_json, output_format, sample_rate_hz,"
        "  input_hash, status, created_at, updated_at)"
        " VALUES (1,1,'ph',1,1,'en-US',1.0,'{}','wav',22050,"
        "  'nrh','approved','2024-01-01','2024-01-01')"
    )
    conn.execute(
        "INSERT INTO caption_runs"
        " (id, narration_run_id, plan_id, script_id, topic_id,"
        "  input_hash, caption_schema_version, segmentation_version,"
        "  timing_algorithm_version, style_version, exporter_version, language,"
        "  status, total_cue_count, total_duration_ms, created_at, updated_at)"
        " VALUES (1,1,1,1,1,'crh','csv1','sgv1','tav1','stv1','exv1','en-US',"
        "  'approved',2,8000,'2024-01-01','2024-01-01')"
    )
    conn.execute(
        "INSERT INTO scene_manifests"
        " (id, caption_run_id, narration_run_id, plan_id, script_id, topic_id,"
        "  input_hash, manifest_schema_version, planner_version,"
        "  status, total_scene_count, total_asset_count, total_duration_ms,"
        "  approved_at, created_at, updated_at)"
        " VALUES (1,1,1,1,1,1,'smh','Mv1','pv1','approved',0,0,15000,"
        "  '2024-01-01','2024-01-01','2024-01-01')"
    )
    conn.execute(
        "INSERT INTO render_manifests"
        " (id, scene_manifest_id, narration_run_id, caption_run_id,"
        "  topic_id, plan_id, script_id, input_hash,"
        "  render_schema_version, compositor_version,"
        "  total_scene_count, total_duration_ms, width, height, fps,"
        "  status, created_at, updated_at)"
        " VALUES (1,1,1,1,1,1,1,'rmh','Rv1','cv1',0,15000,1080,1920,30,"
        "  'approved','2024-01-01','2024-01-01')"
    )
    conn.execute(
        "INSERT INTO render_jobs"
        " (id, render_manifest_id, backend, backend_version,"
        "  status, output_path, output_sha256, duration_s,"
        "  width, height, fps, video_codec, audio_codec,"
        "  created_at, updated_at)"
        " VALUES (1,1,'ffmpeg','1.0','completed','/tmp/out.mp4','sha256out',15.0,"
        "  1080,1920,30,'h264','aac','2024-01-01','2024-01-01')"
    )
    conn.execute(
        "UPDATE render_manifests SET status='approved', approved_at='2024-01-01T00:00:00'"
        " WHERE id=1"
    )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    return conn


def _metadata(title: str = "Test Video", **kw) -> PublishingMetadataDraft:
    return PublishingMetadataDraft(title=title, **kw)


def _schedule() -> PublishingScheduleDraft:
    return PublishingScheduleDraft()


def _fake_provider(**kw) -> FakePublishingProvider:
    return FakePublishingProvider(**kw)


def _prepare(db, title: str = "Test Video", **kw):
    return prepare_publishing_plan(
        db, 1, _metadata(title=title), _schedule(), provider=_fake_provider(), **kw
    )


def _start(db, plan_id: int, *, provider=None, **kw):
    return start_publishing_job(
        db, plan_id, provider or _fake_provider(),
        output_path="/tmp/out.mp4", output_sha256="sha256out", **kw
    )


class TestPreparePlan:
    def test_creates_plan(self, db):
        plan, created = _prepare(db)
        assert created is True
        assert plan.status == "draft"
        assert plan.render_manifest_id == 1
        db.commit()

    def test_idempotent_same_inputs(self, db):
        plan1, _ = _prepare(db)
        db.commit()
        plan2, created2 = _prepare(db)
        assert created2 is False
        assert plan1.id == plan2.id

    def test_different_title_creates_new_plan(self, db):
        p1, _ = _prepare(db, "A")
        db.commit()
        p2, created = _prepare(db, "B")
        db.commit()
        assert created is True
        assert p1.id != p2.id

    def test_manifest_not_found_raises(self, db):
        with pytest.raises(PublishingPlanNotFoundError):
            prepare_publishing_plan(
                db, 999, _metadata(), _schedule(), provider=_fake_provider()
            )

    def test_manifest_not_approved_raises(self, db):
        db.execute("UPDATE render_manifests SET status='draft' WHERE id=1")
        db.commit()
        with pytest.raises(RenderManifestNotApprovedError):
            _prepare(db)

    def test_empty_title_raises(self, db):
        with pytest.raises(PublishingValidationError):
            _prepare(db, title="")

    def test_records_plan_prepared_event(self, db):
        plan, _ = _prepare(db)
        db.commit()
        events = list_publishing_review_events(db, plan.id)
        assert any(e.event_type == "plan_prepared" for e in events)

    def test_supersede_existing_plans(self, db):
        p1, _ = _prepare(db, "Old")
        db.commit()
        p2, _ = _prepare(db, "New", supersede_existing=True)
        db.commit()
        p1_refreshed = get_publishing_plan(db, p1.id)
        assert p1_refreshed is not None
        assert p1_refreshed.superseded_at is not None
        assert p1_refreshed.superseded_by_id == p2.id


class TestStartPublishingJob:
    def test_successful_job(self, db):
        plan, _ = _prepare(db)
        db.commit()

        job, pub = _start(db, plan.id)
        db.commit()

        assert job.status == "completed"
        assert pub is not None
        assert pub.status in {"published", "scheduled"}
        assert pub.provider_video_id == "fake_vid_001"

    def test_job_records_events(self, db):
        plan, _ = _prepare(db)
        db.commit()
        job, _ = _start(db, plan.id)
        db.commit()
        events = list_publishing_review_events(db, plan.id)
        event_types = {e.event_type for e in events}
        assert "job_queued" in event_types
        assert "job_started" in event_types
        assert "job_completed" in event_types

    def test_active_job_blocks_new_start(self, db):
        plan, _ = _prepare(db)
        db.commit()
        create_publishing_job(db, plan.id, 1, "fake", "1.0.0")
        db.commit()
        with pytest.raises(ActiveJobExistsError):
            _start(db, plan.id)

    def test_upload_failure_marks_job_failed(self, db):
        plan, _ = _prepare(db)
        db.commit()
        with pytest.raises(ProviderUploadError):
            _start(db, plan.id, provider=FakePublishingProvider(simulate_upload_failure=True))
        db.commit()
        jobs = list_publishing_jobs(db, plan.id)
        assert jobs[-1].status == "failed"
        assert jobs[-1].error_message is not None

    def test_max_retries_blocked(self, db):
        from app.publishing.constants import MAX_RETRY_ATTEMPTS
        plan, _ = _prepare(db)
        db.commit()
        for i in range(MAX_RETRY_ATTEMPTS):
            j = create_publishing_job(db, plan.id, i + 1, "fake", "1.0.0")
            update_publishing_job_status(db, j.id, "running")
            update_publishing_job_status(db, j.id, "completed")
        db.commit()
        with pytest.raises(MaxRetriesExceededError):
            _start(db, plan.id)

    def test_dry_run_no_db_writes(self, db):
        plan, _ = _prepare(db)
        db.commit()
        job, pub = _start(db, plan.id, dry_run=True)
        db.commit()
        assert job.id == -1
        assert pub is None
        assert list_publishing_jobs(db, plan.id) == []

    def test_plan_not_found_raises(self, db):
        with pytest.raises(PublishingPlanNotFoundError):
            _start(db, 9999)


class TestRetryPublishingJob:
    def _setup_failed_job(self, db) -> int:
        plan, _ = _prepare(db)
        db.commit()
        try:
            _start(db, plan.id, provider=FakePublishingProvider(simulate_upload_failure=True))
        except ProviderUploadError:
            pass
        db.commit()
        return plan.id

    def test_retry_creates_new_job(self, db):
        plan_id = self._setup_failed_job(db)
        job, pub = retry_publishing_job(
            db, plan_id, _fake_provider(),
            output_path="/tmp/out.mp4", output_sha256="sha256out"
        )
        db.commit()
        assert job.attempt_number == 2
        assert job.status == "completed"

    def test_retry_no_failed_job_raises(self, db):
        plan, _ = _prepare(db)
        db.commit()
        with pytest.raises(JobNotRetryableError):
            retry_publishing_job(
                db, plan.id, _fake_provider(),
                output_path="/tmp/out.mp4", output_sha256="sha256out"
            )

    def test_retry_records_retry_requested_event(self, db):
        plan_id = self._setup_failed_job(db)
        retry_publishing_job(
            db, plan_id, _fake_provider(),
            output_path="/tmp/out.mp4", output_sha256="sha256out"
        )
        db.commit()
        events = list_publishing_review_events(db, plan_id)
        assert any(e.event_type == "retry_requested" for e in events)


class TestCancelPublishingJob:
    def test_cancel_queued_job(self, db):
        plan, _ = _prepare(db)
        db.commit()
        create_publishing_job(db, plan.id, 1, "fake", "1.0.0")
        db.commit()
        job = cancel_publishing_job(db, plan.id, actor="alice", notes="changed mind")
        db.commit()
        assert job.status == "cancelled"
        events = list_publishing_review_events(db, plan.id)
        assert any(
            e.event_type == "cancellation_requested" and e.actor == "alice"
            for e in events
        )

    def test_cancel_no_active_job_raises(self, db):
        plan, _ = _prepare(db)
        db.commit()
        with pytest.raises(JobNotCancellableError):
            cancel_publishing_job(db, plan.id)


class TestUpdatePlanSchedule:
    def test_updates_schedule(self, db):
        plan, _ = _prepare(db)
        db.commit()
        updated = update_plan_schedule(
            db, plan.id,
            PublishingScheduleDraft(
                schedule_type="scheduled",
                scheduled_at="2099-06-01T12:00:00+00:00",
                timezone="UTC",
            ),
        )
        db.commit()
        assert updated.schedule_type == "scheduled"
        assert updated.scheduled_at == "2099-06-01T12:00:00+00:00"

    def test_schedule_change_records_event(self, db):
        plan, _ = _prepare(db)
        db.commit()
        update_plan_schedule(
            db, plan.id,
            PublishingScheduleDraft(schedule_type="manual"),
            actor="ops",
        )
        db.commit()
        events = list_publishing_review_events(db, plan.id)
        assert any(e.event_type == "schedule_changed" and e.actor == "ops" for e in events)

    def test_rejected_plan_schedule_update_raises(self, db):
        from app.publishing.errors import IllegalPublishingPlanTransitionError
        from app.publishing.repository import reject_publishing_plan
        plan, _ = _prepare(db)
        db.commit()
        reject_publishing_plan(db, plan.id)
        db.commit()
        with pytest.raises(IllegalPublishingPlanTransitionError):
            update_plan_schedule(
                db, plan.id,
                PublishingScheduleDraft(schedule_type="manual"),
            )
