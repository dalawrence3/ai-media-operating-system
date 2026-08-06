"""Tests for rendering engine models."""

import sqlite3
from datetime import datetime

import pytest
from pydantic import ValidationError

from app.media.models import (
    ApprovedRender,
    RenderJob,
    RenderManifest,
    RenderManifestDraft,
    RenderSceneDraft,
    ResolvedAsset,
)


def _make_manifest_row(**overrides) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE t (
            id, scene_manifest_id, narration_run_id, caption_run_id,
            topic_id, plan_id, script_id, experiment_id,
            input_hash, render_schema_version, compositor_version,
            total_scene_count, total_duration_ms, width, height, fps,
            caption_burn_in, status, approved_at, rejected_at,
            superseded_at, superseded_by_id, created_at, updated_at
        )
        """
    )
    defaults = dict(
        id=1, scene_manifest_id=10, narration_run_id=20, caption_run_id=30,
        topic_id=1, plan_id=2, script_id=3, experiment_id=None,
        input_hash="abc123", render_schema_version="Render-v1",
        compositor_version="compositor-1.0.0",
        total_scene_count=5, total_duration_ms=30000,
        width=1080, height=1920, fps=30,
        caption_burn_in=0, status="draft",
        approved_at=None, rejected_at=None, superseded_at=None,
        superseded_by_id=None,
        created_at="2026-08-06T10:00:00",
        updated_at="2026-08-06T10:00:00",
    )
    defaults.update(overrides)
    conn.execute(
        "INSERT INTO t VALUES (" + ",".join("?" * len(defaults)) + ")",
        list(defaults.values()),
    )
    return conn.execute("SELECT * FROM t").fetchone()


def _make_job_row(**overrides) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE t (
            id, render_manifest_id, backend, backend_version,
            output_path, output_sha256, duration_s, file_size_bytes, render_time_s,
            width, height, fps, video_codec, audio_codec, crf, audio_bitrate,
            caption_burn_in, ffmpeg_cmd_json, status, error_message,
            validated, validation_metadata_json, started_at, completed_at,
            created_at, updated_at
        )
        """
    )
    defaults = dict(
        id=1, render_manifest_id=1, backend="ffmpeg", backend_version="ffmpeg-1.0.0",
        output_path=None, output_sha256=None, duration_s=None,
        file_size_bytes=None, render_time_s=None,
        width=1080, height=1920, fps=30,
        video_codec="libx264", audio_codec="aac", crf=23, audio_bitrate="128k",
        caption_burn_in=0, ffmpeg_cmd_json=None, status="pending",
        error_message=None, validated=0, validation_metadata_json=None,
        started_at=None, completed_at=None,
        created_at="2026-08-06T10:00:00",
        updated_at="2026-08-06T10:00:00",
    )
    defaults.update(overrides)
    conn.execute(
        "INSERT INTO t VALUES (" + ",".join("?" * len(defaults)) + ")",
        list(defaults.values()),
    )
    return conn.execute("SELECT * FROM t").fetchone()


class TestRenderManifest:
    def test_from_row_basic(self):
        row = _make_manifest_row()
        m = RenderManifest.from_row(row)
        assert m.id == 1
        assert m.scene_manifest_id == 10
        assert m.status == "draft"
        assert m.caption_burn_in is False

    def test_caption_burn_in_true(self):
        row = _make_manifest_row(caption_burn_in=1)
        m = RenderManifest.from_row(row)
        assert m.caption_burn_in is True

    def test_is_frozen(self):
        row = _make_manifest_row()
        m = RenderManifest.from_row(row)
        with pytest.raises(ValidationError):
            m.status = "approved"  # type: ignore

    def test_timestamps_parsed(self):
        row = _make_manifest_row()
        m = RenderManifest.from_row(row)
        assert isinstance(m.created_at, datetime)


class TestRenderJob:
    def test_from_row_pending(self):
        row = _make_job_row()
        j = RenderJob.from_row(row)
        assert j.status == "pending"
        assert j.output_path is None
        assert j.validated is False
        assert j.ffmpeg_cmd is None

    def test_from_row_with_cmd(self):
        import json
        cmd = ["ffmpeg", "-i", "input.wav", "output.mp4"]
        row = _make_job_row(ffmpeg_cmd_json=json.dumps(cmd), status="completed")
        j = RenderJob.from_row(row)
        assert j.ffmpeg_cmd == cmd

    def test_from_row_validated(self):
        import json
        meta = {"duration_s": 30.1, "has_video": True}
        row = _make_job_row(
            validated=1,
            validation_metadata_json=json.dumps(meta),
            status="completed",
        )
        j = RenderJob.from_row(row)
        assert j.validated is True
        assert j.validation_metadata == meta

    def test_is_frozen(self):
        row = _make_job_row()
        j = RenderJob.from_row(row)
        with pytest.raises(ValidationError):
            j.status = "completed"  # type: ignore


class TestRenderSceneDraft:
    def test_basic_construction(self):
        s = RenderSceneDraft(
            scene_index=0,
            scene_id=1,
            segment_id=10,
            narration_asset_id=None,
            audio_path="/tmp/seg.wav",
            audio_sha256="abc",
            start_ms=0,
            end_ms=3000,
            duration_ms=3000,
            shot_type="medium",
            camera_movement="static",
            visual_objective="Introduce the topic",
            caption_cue_ids=[1, 2],
            primary_asset=None,
        )
        assert s.scene_index == 0
        assert s.duration_ms == 3000


class TestResolvedAsset:
    def test_construction_with_none_path(self):
        a = ResolvedAsset(
            asset_id=1,
            scene_id=10,
            asset_index=0,
            category="b_roll",
            priority="required",
            local_path=None,
            local_sha256=None,
            source_url="https://example.com/video.mp4",
            license_status="royalty_free",
            commercial_safe=True,
        )
        assert a.local_path is None


class TestRenderManifestDraft:
    def test_resolution_label(self):
        draft = RenderManifestDraft(
            scene_manifest_id=1,
            narration_run_id=2,
            caption_run_id=3,
            topic_id=1,
            plan_id=1,
            script_id=1,
            experiment_id=None,
            input_hash="abc",
            render_schema_version="Render-v1",
            compositor_version="compositor-1.0.0",
            total_scene_count=3,
            total_duration_ms=15000,
            width=1080,
            height=1920,
            fps=30,
            caption_burn_in=False,
        )
        assert draft.resolution_label == "1080x1920"


class TestApprovedRender:
    def test_construction(self):
        now = datetime.utcnow()
        ar = ApprovedRender(
            render_manifest_id=1,
            render_job_id=5,
            scene_manifest_id=10,
            narration_run_id=20,
            caption_run_id=30,
            topic_id=1,
            plan_id=2,
            script_id=3,
            experiment_id=None,
            output_path="/renders/out.mp4",
            output_sha256="deadbeef",
            duration_s=30.5,
            width=1080,
            height=1920,
            fps=30,
            video_codec="libx264",
            audio_codec="aac",
            approved_at=now,
        )
        assert ar.output_path == "/renders/out.mp4"
        assert ar.duration_s == 30.5
