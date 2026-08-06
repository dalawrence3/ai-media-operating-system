"""Tests for Phase 8 render manifest and job repository."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core.database import open_db
from app.media.errors import (
    IllegalRenderJobTransitionError,
    IllegalRenderTransitionError,
    RenderJobNotFoundError,
    RenderManifestAlreadyExistsError,
    RenderManifestNotFoundError,
)
from app.media.models import (
    RenderJob,
    RenderManifest,
    RenderManifestDraft,
    RenderReviewEvent,
    RenderThumbnail,
)
from app.media.repository import (
    approve_render_manifest,
    create_render_job,
    create_render_manifest,
    create_render_thumbnail,
    get_approved_render,
    get_or_create_render_manifest,
    get_render_job,
    get_render_manifest,
    list_render_jobs,
    list_render_manifests,
    list_render_review_events,
    list_render_thumbnails,
    mark_render_job_completed,
    mark_render_job_failed,
    mark_render_job_rendering,
    mark_render_job_validated,
    record_render_review_event,
    reject_render_manifest,
    select_thumbnail,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "test.db")


def _seed(conn: sqlite3.Connection) -> dict:
    """Insert minimal upstream rows (FK checks off)."""
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO topics (id, title, angle) VALUES (1, 'T', 'A')")
    conn.execute(
        "INSERT INTO scripts (id, topic_id, version, body, status)"
        " VALUES (1, 1, 1, 'body', 'approved')"
    )
    conn.execute(
        "INSERT INTO production_plans"
        " (id, topic_id, script_id, script_version, input_hash, script_body_hash,"
        "  plan_schema_version, renderer_version, duration_algorithm_version,"
        "  status, created_at, updated_at)"
        " VALUES (1, 1, 1, 1, 'ph', 'bh', 'v1', 'rv1', 'dv1',"
        "  'approved', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO narration_runs"
        " (id, plan_id, plan_input_hash, voice_profile_id, voice_profile_version,"
        "  language, speaking_rate, settings_json, output_format, sample_rate_hz,"
        "  input_hash, status, created_at, updated_at)"
        " VALUES (1, 1, 'ph', 1, 1,"
        "  'en-US', 1.0, '{}', 'wav', 22050,"
        "  'nrh', 'approved', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO caption_runs"
        " (id, narration_run_id, plan_id, script_id, topic_id,"
        "  input_hash, caption_schema_version, segmentation_version,"
        "  timing_algorithm_version, style_version, exporter_version, language,"
        "  status, total_cue_count, total_duration_ms, created_at, updated_at)"
        " VALUES (1, 1, 1, 1, 1,"
        "  'crh', 'csv1', 'sgv1', 'tav1', 'stv1', 'exv1', 'en-US',"
        "  'approved', 2, 8000, '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO scene_manifests"
        " (id, caption_run_id, narration_run_id, plan_id, script_id, topic_id,"
        "  input_hash, manifest_schema_version, planner_version,"
        "  status, total_scene_count, total_asset_count, total_duration_ms,"
        "  approved_at, created_at, updated_at)"
        " VALUES (1, 1, 1, 1, 1, 1,"
        "  'smh', 'Manifest-v1', 'planner-1.0.0',"
        "  'approved', 3, 6, 15000,"
        "  '2024-01-01T00:00:00', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    return {
        "scene_manifest_id": 1,
        "narration_run_id": 1,
        "caption_run_id": 1,
        "topic_id": 1,
        "plan_id": 1,
        "script_id": 1,
    }


def _make_draft(seed: dict, **overrides) -> RenderManifestDraft:
    defaults = dict(
        scene_manifest_id=seed["scene_manifest_id"],
        narration_run_id=seed["narration_run_id"],
        caption_run_id=seed["caption_run_id"],
        topic_id=seed["topic_id"],
        plan_id=seed["plan_id"],
        script_id=seed["script_id"],
        experiment_id=None,
        input_hash="render_hash_001",
        render_schema_version="Render-v1",
        compositor_version="compositor-1.0.0",
        total_scene_count=3,
        total_duration_ms=15000,
        width=1080,
        height=1920,
        fps=30,
        caption_burn_in=False,
    )
    defaults.update(overrides)
    return RenderManifestDraft(**defaults)


def _make_job(conn: sqlite3.Connection, manifest_id: int = 1) -> RenderJob:
    return create_render_job(
        conn,
        manifest_id,
        backend="ffmpeg",
        backend_version="ffmpeg-1.0.0",
        width=1080,
        height=1920,
        fps=30,
        video_codec="libx264",
        audio_codec="aac",
        crf=23,
        audio_bitrate="128k",
        caption_burn_in=False,
    )


# ── RenderManifest tests ──────────────────────────────────────────────────────


class TestCreateRenderManifest:
    def test_creates_manifest(self, db):
        seed = _seed(db)
        draft = _make_draft(seed)
        m = create_render_manifest(db, draft)
        assert isinstance(m, RenderManifest)
        assert m.id >= 1
        assert m.status == "draft"
        assert m.total_scene_count == 3
        assert m.total_duration_ms == 15000
        assert m.width == 1080
        assert m.height == 1920
        assert m.caption_burn_in is False

    def test_raises_on_duplicate_hash(self, db):
        seed = _seed(db)
        draft = _make_draft(seed)
        create_render_manifest(db, draft)
        with pytest.raises(RenderManifestAlreadyExistsError):
            create_render_manifest(db, draft)

    def test_caption_burn_in_stored(self, db):
        seed = _seed(db)
        draft = _make_draft(seed, caption_burn_in=True, input_hash="h2")
        m = create_render_manifest(db, draft)
        assert m.caption_burn_in is True


class TestGetOrCreateRenderManifest:
    def test_creates_new(self, db):
        seed = _seed(db)
        draft = _make_draft(seed)
        m, created = get_or_create_render_manifest(db, draft)
        assert created is True
        assert m.id >= 1

    def test_returns_existing_on_duplicate(self, db):
        seed = _seed(db)
        draft = _make_draft(seed)
        m1, c1 = get_or_create_render_manifest(db, draft)
        m2, c2 = get_or_create_render_manifest(db, draft)
        assert c1 is True
        assert c2 is False
        assert m1.id == m2.id


class TestGetRenderManifest:
    def test_returns_none_for_missing(self, db):
        _seed(db)
        assert get_render_manifest(db, 9999) is None

    def test_returns_manifest_for_existing(self, db):
        seed = _seed(db)
        draft = _make_draft(seed)
        created = create_render_manifest(db, draft)
        fetched = get_render_manifest(db, created.id)
        assert fetched is not None
        assert fetched.id == created.id


class TestListRenderManifests:
    def test_list_empty(self, db):
        _seed(db)
        assert list_render_manifests(db) == []

    def test_list_by_topic(self, db):
        seed = _seed(db)
        create_render_manifest(db, _make_draft(seed, input_hash="h1"))
        create_render_manifest(db, _make_draft(seed, input_hash="h2"))
        results = list_render_manifests(db, topic_id=1)
        assert len(results) == 2

    def test_list_by_status(self, db):
        seed = _seed(db)
        m = create_render_manifest(db, _make_draft(seed, input_hash="h1"))
        create_render_manifest(db, _make_draft(seed, input_hash="h2"))
        approve_render_manifest(db, m.id)
        db.commit()
        approved = list_render_manifests(db, status="approved")
        assert len(approved) == 1
        draft = list_render_manifests(db, status="draft")
        assert len(draft) == 1


class TestApproveRenderManifest:
    def test_approve_transitions_status(self, db):
        seed = _seed(db)
        m = create_render_manifest(db, _make_draft(seed))
        approved = approve_render_manifest(db, m.id, actor="reviewer")
        db.commit()
        assert approved.status == "approved"
        assert approved.approved_at is not None

    def test_approve_creates_review_event(self, db):
        seed = _seed(db)
        m = create_render_manifest(db, _make_draft(seed))
        approve_render_manifest(db, m.id, actor="alice")
        db.commit()
        events = list_render_review_events(db, m.id)
        assert len(events) == 1
        assert events[0].event_type == "render_approved"
        assert events[0].actor == "alice"

    def test_approve_supersedes_previous(self, db):
        seed = _seed(db)
        m1 = create_render_manifest(db, _make_draft(seed, input_hash="h1"))
        approve_render_manifest(db, m1.id)
        m2 = create_render_manifest(db, _make_draft(seed, input_hash="h2"))
        approve_render_manifest(db, m2.id)
        db.commit()
        old = get_render_manifest(db, m1.id)
        assert old.status == "superseded"
        assert old.superseded_by_id == m2.id

    def test_approve_raises_on_invalid_transition(self, db):
        seed = _seed(db)
        m = create_render_manifest(db, _make_draft(seed))
        reject_render_manifest(db, m.id)
        db.commit()
        with pytest.raises(IllegalRenderTransitionError):
            approve_render_manifest(db, m.id)

    def test_approve_raises_on_missing_manifest(self, db):
        _seed(db)
        with pytest.raises(RenderManifestNotFoundError):
            approve_render_manifest(db, 9999)


class TestRejectRenderManifest:
    def test_reject_transitions_status(self, db):
        seed = _seed(db)
        m = create_render_manifest(db, _make_draft(seed))
        rejected = reject_render_manifest(
            db, m.id,
            reason_code="visual_quality",
            severity=3,
            actor="bob",
            notes="Too dark",
        )
        db.commit()
        assert rejected.status == "rejected"
        assert rejected.rejected_at is not None

    def test_reject_creates_review_event(self, db):
        seed = _seed(db)
        m = create_render_manifest(db, _make_draft(seed))
        reject_render_manifest(db, m.id, reason_code="audio_sync")
        db.commit()
        events = list_render_review_events(db, m.id)
        assert events[0].event_type == "render_rejected"
        assert events[0].reason_code == "audio_sync"

    def test_reject_raises_on_invalid_transition(self, db):
        seed = _seed(db)
        m = create_render_manifest(db, _make_draft(seed))
        approve_render_manifest(db, m.id)
        db.commit()
        with pytest.raises(IllegalRenderTransitionError):
            reject_render_manifest(db, m.id)


# ── RenderJob tests ───────────────────────────────────────────────────────────


class TestCreateRenderJob:
    def test_creates_pending_job(self, db):
        seed = _seed(db)
        m = create_render_manifest(db, _make_draft(seed))
        job = _make_job(db, m.id)
        assert job.status == "pending"
        assert job.render_manifest_id == m.id
        assert job.backend == "ffmpeg"
        assert job.output_path is None


class TestMarkRenderJobRendering:
    def test_transitions_to_rendering(self, db):
        seed = _seed(db)
        m = create_render_manifest(db, _make_draft(seed))
        job = _make_job(db, m.id)
        updated = mark_render_job_rendering(db, job.id)
        assert updated.status == "rendering"
        assert updated.started_at is not None

    def test_raises_on_invalid_transition(self, db):
        seed = _seed(db)
        m = create_render_manifest(db, _make_draft(seed))
        job = _make_job(db, m.id)
        mark_render_job_rendering(db, job.id)
        mark_render_job_completed(
            db, job.id,
            output_path="/out.mp4", output_sha256="sha",
            duration_s=30.0, file_size_bytes=1000, render_time_s=5.0,
            ffmpeg_cmd=["ffmpeg"],
        )
        with pytest.raises(IllegalRenderJobTransitionError):
            mark_render_job_rendering(db, job.id)


class TestMarkRenderJobCompleted:
    def test_stores_result(self, db):
        seed = _seed(db)
        m = create_render_manifest(db, _make_draft(seed))
        job = _make_job(db, m.id)
        mark_render_job_rendering(db, job.id)
        completed = mark_render_job_completed(
            db, job.id,
            output_path="/renders/out.mp4",
            output_sha256="deadbeef",
            duration_s=30.5,
            file_size_bytes=5_000_000,
            render_time_s=45.2,
            ffmpeg_cmd=["ffmpeg", "-i", "in", "out.mp4"],
        )
        assert completed.status == "completed"
        assert completed.output_path == "/renders/out.mp4"
        assert completed.output_sha256 == "deadbeef"
        assert completed.duration_s == pytest.approx(30.5)
        assert completed.file_size_bytes == 5_000_000
        assert completed.ffmpeg_cmd == ["ffmpeg", "-i", "in", "out.mp4"]


class TestMarkRenderJobFailed:
    def test_stores_error(self, db):
        seed = _seed(db)
        m = create_render_manifest(db, _make_draft(seed))
        job = _make_job(db, m.id)
        mark_render_job_rendering(db, job.id)
        failed = mark_render_job_failed(db, job.id, error_message="FFmpeg died")
        assert failed.status == "failed"
        assert failed.error_message == "FFmpeg died"


class TestMarkRenderJobValidated:
    def test_sets_validated_flag(self, db):
        seed = _seed(db)
        m = create_render_manifest(db, _make_draft(seed))
        job = _make_job(db, m.id)
        mark_render_job_rendering(db, job.id)
        mark_render_job_completed(
            db, job.id, output_path="/o.mp4", output_sha256="s",
            duration_s=30.0, file_size_bytes=100, render_time_s=2.0,
            ffmpeg_cmd=[],
        )
        meta = {"duration_s": 30.1, "has_video": True}
        validated = mark_render_job_validated(db, job.id, validation_metadata=meta)
        assert validated.validated is True
        assert validated.validation_metadata == meta


class TestListRenderJobs:
    def test_returns_empty_list(self, db):
        seed = _seed(db)
        m = create_render_manifest(db, _make_draft(seed))
        assert list_render_jobs(db, m.id) == []

    def test_returns_jobs_in_desc_order(self, db):
        seed = _seed(db)
        m = create_render_manifest(db, _make_draft(seed))
        j1 = _make_job(db, m.id)
        j2 = _make_job(db, m.id)
        jobs = list_render_jobs(db, m.id)
        assert len(jobs) == 2


class TestGetRenderJobNotFound:
    def test_returns_none_for_missing(self, db):
        _seed(db)
        assert get_render_job(db, 9999) is None


# ── Review events ─────────────────────────────────────────────────────────────


class TestRecordRenderReviewEvent:
    def test_records_event(self, db):
        seed = _seed(db)
        m = create_render_manifest(db, _make_draft(seed))
        approve_render_manifest(db, m.id)
        events = list_render_review_events(db, m.id)
        assert len(events) == 1
        assert isinstance(events[0], RenderReviewEvent)

    def test_event_has_correct_fields(self, db):
        seed = _seed(db)
        m = create_render_manifest(db, _make_draft(seed))
        reject_render_manifest(
            db, m.id,
            reason_code="visual_quality",
            severity=4,
            expected_correction="Re-render with better lighting",
            actor="reviewer",
            notes="Colours are off",
        )
        events = list_render_review_events(db, m.id)
        e = events[0]
        assert e.event_type == "render_rejected"
        assert e.reason_code == "visual_quality"
        assert e.severity == 4
        assert e.expected_correction == "Re-render with better lighting"
        assert e.actor == "reviewer"
        assert e.notes == "Colours are off"


# ── Thumbnails ────────────────────────────────────────────────────────────────


class TestRenderThumbnails:
    def _setup(self, db):
        seed = _seed(db)
        m = create_render_manifest(db, _make_draft(seed))
        j = _make_job(db, m.id)
        return j

    def test_create_thumbnail(self, db):
        job = self._setup(db)
        t = create_render_thumbnail(
            db, job.id,
            file_path="/thumbs/t0.jpg",
            timestamp_ms=5000,
            scene_index=0,
        )
        assert isinstance(t, RenderThumbnail)
        assert t.timestamp_ms == 5000
        assert t.selected is False

    def test_list_thumbnails(self, db):
        job = self._setup(db)
        create_render_thumbnail(db, job.id, file_path="/t0.jpg", timestamp_ms=1000)
        create_render_thumbnail(db, job.id, file_path="/t1.jpg", timestamp_ms=2000)
        thumbs = list_render_thumbnails(db, job.id)
        assert len(thumbs) == 2
        assert thumbs[0].timestamp_ms == 1000

    def test_select_thumbnail(self, db):
        job = self._setup(db)
        t1 = create_render_thumbnail(db, job.id, file_path="/t0.jpg", timestamp_ms=1000)
        t2 = create_render_thumbnail(db, job.id, file_path="/t1.jpg", timestamp_ms=2000)
        sel = select_thumbnail(db, t2.id)
        assert sel.selected is True
        # t1 should be deselected
        thumbs = list_render_thumbnails(db, job.id)
        t1_fetched = next(t for t in thumbs if t.id == t1.id)
        assert t1_fetched.selected is False


# ── Approved render handoff ───────────────────────────────────────────────────


class TestGetApprovedRender:
    def test_returns_none_when_no_approved(self, db):
        seed = _seed(db)
        create_render_manifest(db, _make_draft(seed))
        result = get_approved_render(db, seed["scene_manifest_id"])
        assert result is None

    def test_returns_approved_render(self, db):
        seed = _seed(db)
        m = create_render_manifest(db, _make_draft(seed))
        j = _make_job(db, m.id)
        mark_render_job_rendering(db, j.id)
        mark_render_job_completed(
            db, j.id,
            output_path="/out.mp4", output_sha256="sha",
            duration_s=30.0, file_size_bytes=100, render_time_s=5.0,
            ffmpeg_cmd=[],
        )
        approve_render_manifest(db, m.id, render_job_id=j.id)
        db.commit()

        result = get_approved_render(db, seed["scene_manifest_id"])
        assert result is not None
        assert result.render_manifest_id == m.id
        assert result.output_path == "/out.mp4"
