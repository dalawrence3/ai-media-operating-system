"""Tests for src/app/captions/orchestrator.py."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.captions.errors import (
    FailedCaptionRunError,
    MissingNarrationSegmentAssetError,
    NoApprovedNarrationRunError,
)
from app.captions.orchestrator import generate_captions
from app.core.database import open_db

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "test.db")


def _seed_full_approved_narration(conn: sqlite3.Connection) -> None:
    """Insert a complete, approved narration run with synthesized assets."""
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO topics (id, title, angle) VALUES (1, 'T', 'A')")
    conn.execute(
        "INSERT INTO scripts (id, topic_id, version, body, status)"
        " VALUES (1, 1, 1, 'body', 'approved')"
    )
    conn.execute(
        "INSERT INTO voice_profiles"
        " (id, provider, model, voice_id, name, language, speaking_rate)"
        " VALUES (1, 'mock', 'm1', 'v1', 'Voice', 'en-US', 1.0)"
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
        "INSERT INTO production_segments"
        " (id, plan_id, segment_index, section_index, section_type,"
        "  narration_text, estimated_duration_s, created_at)"
        " VALUES (1, 1, 0, 0, 'hook',"
        "  'Scientists discovered a new species of fish. It lives very deep.', 4, '2024-01-01')"
    )
    conn.execute(
        "INSERT INTO narration_runs"
        " (id, plan_id, plan_input_hash, voice_profile_id, voice_profile_version,"
        "  language, speaking_rate, settings_json, output_format, sample_rate_hz,"
        "  input_hash, status, approved_at, created_at, updated_at)"
        " VALUES (1, 1, 'ph', 1, 1, 'en-US', 1.0, '{}', 'wav', 22050,"
        "  'nr-hash', 'approved', '2024-01-01T00:00:00', '2024-01-01', '2024-01-01')"
    )
    conn.execute(
        "INSERT INTO narration_segment_assets"
        " (id, run_id, segment_id, narration_text_hash, provider, model, voice_id,"
        "  voice_profile_id, voice_profile_version, language, speaking_rate,"
        "  settings_json_hash, output_format, sample_rate_hz, input_hash, status,"
        "  audio_path, audio_sha256, duration_seconds, created_at, updated_at)"
        " VALUES (1, 1, 1, 'th1', 'mock', 'm1', 'v1', 1, 1, 'en-US', 1.0,"
        "  'sh1', 'wav', 22050, 'ah', 'synthesized',"
        "  'narration/plan_1/run_1/segment_1.wav', 'abc123', 4.0,"
        "  '2024-01-01', '2024-01-01')"
    )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()


class TestGenerateCaptions:
    def test_generates_completed_run(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        _seed_full_approved_narration(db)
        run = generate_captions(db, plan_id=1, artifacts_path=tmp_path)
        assert run.status == "completed"
        assert run.total_cue_count > 0
        assert run.total_duration_ms > 0

    def test_export_files_created(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        _seed_full_approved_narration(db)
        run = generate_captions(db, plan_id=1, artifacts_path=tmp_path)
        assert run.srt_path is not None
        assert run.vtt_path is not None
        assert run.json_path is not None
        srt_dest = tmp_path / run.srt_path
        vtt_dest = tmp_path / run.vtt_path
        json_dest = tmp_path / run.json_path
        assert srt_dest.exists()
        assert vtt_dest.exists()
        assert json_dest.exists()

    def test_srt_content_is_valid(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        _seed_full_approved_narration(db)
        run = generate_captions(db, plan_id=1, artifacts_path=tmp_path)
        srt = (tmp_path / run.srt_path).read_text(encoding="utf-8")
        assert "1\n" in srt
        assert "-->" in srt

    def test_vtt_starts_with_webvtt(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        _seed_full_approved_narration(db)
        run = generate_captions(db, plan_id=1, artifacts_path=tmp_path)
        vtt = (tmp_path / run.vtt_path).read_text(encoding="utf-8")
        assert vtt.startswith("WEBVTT")

    def test_json_is_valid(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        _seed_full_approved_narration(db)
        run = generate_captions(db, plan_id=1, artifacts_path=tmp_path)
        doc = json.loads((tmp_path / run.json_path).read_text(encoding="utf-8"))
        assert "cues" in doc
        assert len(doc["cues"]) == run.total_cue_count

    def test_export_hashes_stored(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        _seed_full_approved_narration(db)
        run = generate_captions(db, plan_id=1, artifacts_path=tmp_path)
        assert run.srt_sha256 is not None and len(run.srt_sha256) == 64
        assert run.vtt_sha256 is not None and len(run.vtt_sha256) == 64
        assert run.json_sha256 is not None and len(run.json_sha256) == 64

    def test_idempotent_returns_same_run(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        _seed_full_approved_narration(db)
        run1 = generate_captions(db, plan_id=1, artifacts_path=tmp_path)
        run2 = generate_captions(db, plan_id=1, artifacts_path=tmp_path)
        assert run1.id == run2.id

    def test_no_approved_narration_run_raises(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        with pytest.raises(NoApprovedNarrationRunError):
            generate_captions(db, plan_id=999, artifacts_path=tmp_path)

    def test_missing_segments_raises(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        _seed_full_approved_narration(db)
        # Remove the asset so segments tuple is empty
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("DELETE FROM narration_segment_assets")
        db.commit()
        with pytest.raises(MissingNarrationSegmentAssetError):
            generate_captions(db, plan_id=1, artifacts_path=tmp_path)

    def test_cues_persisted_to_db(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        from app.captions.repository import get_caption_cues
        _seed_full_approved_narration(db)
        run = generate_captions(db, plan_id=1, artifacts_path=tmp_path)
        cues = get_caption_cues(db, run.id)
        assert len(cues) == run.total_cue_count
        assert cues[0].cue_index == 0

    def test_cue_timestamps_valid(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        from app.captions.repository import get_caption_cues
        _seed_full_approved_narration(db)
        run = generate_captions(db, plan_id=1, artifacts_path=tmp_path)
        cues = get_caption_cues(db, run.id)
        for cue in cues:
            assert cue.start_ms >= 0
            assert cue.end_ms > cue.start_ms
        # Last cue ends at segment duration (4000ms)
        assert cues[-1].end_ms == 4000

    def test_failed_run_raises_on_retry(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        from app.captions.constants import (
            CAPTION_EXPORTER_VERSION,
            CAPTION_SCHEMA_VERSION,
            CAPTION_SEGMENTATION_VERSION,
            CAPTION_STYLE_VERSION,
            CAPTION_TIMING_ALGORITHM_VERSION,
        )
        from app.captions.hashing import NarrationSegmentHashInput, compute_caption_input_hash
        from app.captions.models import CaptionRunDraft
        from app.captions.repository import (
            create_caption_run,
            fail_caption_run,
        )
        from app.narration.repository import get_approved_narration_run_full

        _seed_full_approved_narration(db)
        handoff = get_approved_narration_run_full(db, 1)
        seg_inputs = [
            NarrationSegmentHashInput(
                segment_id=s.segment_id, asset_id=s.asset_id,
                audio_sha256=s.audio_sha256, narration_text_hash=s.narration_text_hash,
                duration_ms=s.duration_ms,
            )
            for s in handoff.segments
        ]
        h = compute_caption_input_hash(
            narration_run_id=handoff.run_id, narration_run_input_hash=handoff.input_hash,
            segments=seg_inputs,
            caption_schema_version=CAPTION_SCHEMA_VERSION,
            segmentation_version=CAPTION_SEGMENTATION_VERSION,
            timing_algorithm_version=CAPTION_TIMING_ALGORITHM_VERSION,
            style_version=CAPTION_STYLE_VERSION, exporter_version=CAPTION_EXPORTER_VERSION,
            language="en-US", experiment_id=None,
        )
        draft = CaptionRunDraft(
            narration_run_id=handoff.run_id, plan_id=handoff.plan_id,
            script_id=handoff.script_id, topic_id=handoff.topic_id,
            experiment_id=None, input_hash=h,
            caption_schema_version=CAPTION_SCHEMA_VERSION,
            segmentation_version=CAPTION_SEGMENTATION_VERSION,
            timing_algorithm_version=CAPTION_TIMING_ALGORITHM_VERSION,
            style_version=CAPTION_STYLE_VERSION, exporter_version=CAPTION_EXPORTER_VERSION,
            language="en-US",
        )
        run = create_caption_run(db, draft)
        fail_caption_run(db, run.id, failure_reason="prior failure")
        db.commit()

        with pytest.raises(FailedCaptionRunError):
            generate_captions(db, plan_id=1, artifacts_path=tmp_path)
