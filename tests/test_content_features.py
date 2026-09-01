"""Phase 12B — Content Feature Attribution tests.

Tests A–P as specified in the Phase 12B requirements, plus migration test
(TestMigrationFrom26 lives in test_learning_migration.py).

Design constraints asserted by these tests:
  - Extraction is deterministic and idempotent (A, B)
  - Zero vs NULL distinction preserved (C)
  - Correct publication lineage traversal (D)
  - Actual narration speaking_rate extracted (E)
  - Phase 12A learning application attribution (F)
  - workspace/channel scope isolation (G)
  - Derived numeric features correct (H)
  - Missing optional features handled safely (I)
  - Historical snapshot immutability (J)
  - Feature schema version stored (K)
  - input_hash changes when source features change (L)
  - Analytics metrics NOT stored as content features (M)
  - Unstructured characteristics not fabricated (N)
  - CLI inspection works (O)
  - Publication 1 backfill does not mutate historical state (P)
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from app.core.config import reset_config
from app.core.database import open_db
from app.learning.features import (
    EXTRACTOR_VERSION,
    FEATURE_SCHEMA_VERSION,
    ContentFeatureSnapshot,
    IncompleteLineageError,
    PublicationNotFoundError,
    extract_and_save,
    extract_features,
    get_feature_snapshot,
    save_feature_snapshot,
)

_NOW = "2026-01-01T00:00:00"
_PUBLISHED_AT = "2026-01-01T10:30:00"  # Wednesday, hour 10

runner = CliRunner()


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path: Path) -> Generator[sqlite3.Connection]:
    conn = open_db(tmp_path / "test.db")
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def _insert_full_lineage(
    conn: sqlite3.Connection,
    *,
    publication_id: int = 1,
    topic_id: int = 1,
    workspace_id: str | None = "ws-1",
    channel_id: str | None = "ch-1",
    speaking_rate: float = 1.0,
    word_count: int = 120,
    with_hook: bool = True,
    with_cta: bool = True,
    published_at: str = _PUBLISHED_AT,
    visibility: str = "public",
) -> None:
    """Seed a complete production lineage for one publication.

    Foreign-key enforcement is OFF (matching the established project pattern in
    test_api_publications._seed_workspace_with_publication).  All CHECK constraints
    use valid values — CHECK violations are NOT suppressed; INSERT OR IGNORE is
    intentionally avoided so fixture errors are immediately visible.

    Each block notes which columns the extractor actually reads, so the minimal
    required set stays auditable without scanning features.py.
    """
    conn.execute("PRAGMA foreign_keys = OFF")

    # Topic
    conn.execute(
        "INSERT INTO topics (id, title, angle, created_at, updated_at) "
        "VALUES (?, 'Test Topic', 'angle', ?, ?)",
        (topic_id, _NOW, _NOW),
    )

    # Voice profile — extractor reads: provider, model, voice_id
    # (via narration_run.voice_profile_id)
    conn.execute(
        "INSERT INTO voice_profiles "
        "(id, channel_id, provider, model, voice_id, name, language, "
        "speaking_rate, stability, similarity_boost, settings_json, "
        "version, is_default, created_at, updated_at) "
        "VALUES (1, NULL, 'elevenlabs', 'eleven_multilingual_v2', 'vid1', 'V', "
        "'en-US', 1.0, 0.5, 0.75, '{}', 1, 1, ?, ?)",
        (_NOW, _NOW),
    )

    # Script (minimal)
    conn.execute(
        "INSERT INTO scripts (id, topic_id, body, version, created_at, updated_at) "
        "VALUES (1, ?, 'Script body', 1, ?, ?)",
        (topic_id, _NOW, _NOW),
    )

    # Production plan — extractor reads: format, total_word_count, total_estimated_duration_s
    conn.execute(
        "INSERT INTO production_plans "
        "(id, topic_id, script_id, script_version, input_hash, script_body_hash, "
        "plan_schema_version, renderer_version, duration_algorithm_version, "
        "format, total_word_count, total_estimated_duration_s, status, created_at, updated_at) "
        "VALUES (1, ?, 1, 1, ?, ?, 'v1', 'v1', 'v1', 'short', ?, 60, 'approved', ?, ?)",
        (topic_id, "a" * 64, "b" * 64, word_count, _NOW, _NOW),
    )

    # Production segments — extractor reads: section_type, estimated_word_count
    section_types: list[tuple[str, int]] = []
    if with_hook:
        section_types.append(("hook", 20))
    body_wc = word_count - (20 if with_hook else 0) - (10 if with_cta else 0)
    section_types.append(("body", max(body_wc, 1)))
    if with_cta:
        section_types.append(("cta", 10))
    for idx, (stype, wc) in enumerate(section_types):
        conn.execute(
            "INSERT INTO production_segments "
            "(id, plan_id, segment_index, section_index, section_type, "
            "narration_text, estimated_word_count, created_at) "
            "VALUES (?, 1, ?, ?, ?, 'text', ?, ?)",
            (idx + 1, idx, idx, stype, wc, _NOW),
        )

    # Narration run — extractor reads: speaking_rate, language, voice_profile_id
    conn.execute(
        "INSERT INTO narration_runs "
        "(id, plan_id, plan_input_hash, voice_profile_id, voice_profile_version, "
        "language, speaking_rate, settings_json, output_format, sample_rate_hz, "
        "input_hash, status, created_at, updated_at) "
        "VALUES (1, 1, ?, 1, 1, 'en-US', ?, '{}', 'mp3_44100_128', 44100, "
        "?, 'completed', ?, ?)",
        ("c" * 64, speaking_rate, "nr_hash_1", _NOW, _NOW),
    )

    # Narration segment assets — extractor: SUM(duration_seconds) WHERE status='synthesized'
    conn.execute(
        "INSERT INTO narration_segment_assets "
        "(id, run_id, segment_id, narration_text_hash, provider, model, voice_id, "
        "voice_profile_id, voice_profile_version, language, speaking_rate, "
        "settings_json_hash, output_format, sample_rate_hz, input_hash, "
        "audio_path, duration_seconds, status, created_at, updated_at) "
        "VALUES (1, 1, 1, 'txthash', 'elevenlabs', 'eleven_multilingual_v2', 'vid1', "
        "1, 1, 'en-US', ?, 'settingshash', 'mp3_44100_128', 44100, 'nsa_hash_1', "
        "'path/audio.mp3', 45.0, 'synthesized', ?, ?)",
        (speaking_rate, _NOW, _NOW),
    )

    # Caption run — extractor reads: total_cue_count, total_duration_ms,
    #   style_version, segmentation_version
    conn.execute(
        "INSERT INTO caption_runs "
        "(id, plan_id, narration_run_id, script_id, topic_id, language, "
        "total_cue_count, total_duration_ms, caption_schema_version, "
        "segmentation_version, timing_algorithm_version, style_version, "
        "exporter_version, input_hash, status, created_at, updated_at) "
        "VALUES (1, 1, 1, 1, ?, 'en-US', 24, 45000, 'v1', 'v1', 'v1', 'v1', "
        "'v1', 'cr_hash_1', 'completed', ?, ?)",
        (topic_id, _NOW, _NOW),
    )

    # Caption cues — extractor reads: timing_source (dominant mode)
    # All NOT NULL: segment_id, narration_asset_id, narration_text_hash, audio_sha256,
    #   cue_index, segment_cue_index, text, start_ms, end_ms, line_count, char_count, timing_source
    conn.execute(
        "INSERT INTO caption_cues "
        "(id, run_id, segment_id, narration_asset_id, narration_text_hash, "
        "audio_sha256, cue_index, segment_cue_index, start_ms, end_ms, "
        "text, line_count, char_count, timing_source, created_at) "
        "VALUES (1, 1, 1, 1, 'txthash', 'audiosha256', 0, 0, 0, 2000, "
        "'Hello', 1, 5, 'estimated', ?)",
        (_NOW,),
    )

    # Scene manifest — extractor reads: total_scene_count, total_asset_count
    conn.execute(
        "INSERT INTO scene_manifests "
        "(id, caption_run_id, narration_run_id, plan_id, script_id, topic_id, "
        "total_scene_count, total_asset_count, total_duration_ms, "
        "input_hash, manifest_schema_version, planner_version, status, created_at, updated_at) "
        "VALUES (1, 1, 1, 1, 1, ?, 3, 6, 45000, 'sm_hash_1', 'v1', 'v1', "
        "'approved', ?, ?)",
        (topic_id, _NOW, _NOW),
    )

    # Scene manifest scenes — extractor reads: shot_type, transition_out
    # All NOT NULL: segment_id, narration_text, shot_type, camera_movement,
    #   transition_in, transition_out, visual_objective
    for i, (shot, transition) in enumerate(
        [("close_up", "cut"), ("wide", "fade"), ("medium", "cut")]
    ):
        conn.execute(
            "INSERT INTO scene_manifest_scenes "
            "(id, manifest_id, scene_index, segment_id, narration_text, "
            "shot_type, camera_movement, transition_in, transition_out, "
            "visual_objective, visual_rationale, created_at) "
            "VALUES (?, 1, ?, 1, 'narration text', ?, 'static', 'cut', ?, 'obj', 'rationale', ?)",
            (i + 1, i, shot, transition, _NOW),
        )

    # Scene manifest assets — extractor reads: ai_generation_requested
    conn.execute(
        "INSERT INTO scene_manifest_assets "
        "(id, scene_id, manifest_id, asset_index, category, priority, description, "
        "ai_generation_requested, created_at) "
        "VALUES (1, 1, 1, 0, 'footage', 'primary', 'stock footage', 0, ?)",
        (_NOW,),
    )

    # Render manifest — extractor reads: width, height, fps, caption_burn_in
    conn.execute(
        "INSERT INTO render_manifests "
        "(id, scene_manifest_id, narration_run_id, caption_run_id, topic_id, plan_id, script_id, "
        "input_hash, render_schema_version, compositor_version, "
        "total_scene_count, total_duration_ms, width, height, fps, caption_burn_in, "
        "status, created_at, updated_at) "
        "VALUES (1, 1, 1, 1, ?, 1, 1, 'rm_hash_1', 'v1', 'v1', "
        "3, 45000, 1920, 1080, 30, 1, 'approved', ?, ?)",
        (topic_id, _NOW, _NOW),
    )

    # Render job — extractor reads: duration_s, file_size_bytes WHERE status='completed'
    conn.execute(
        "INSERT INTO render_jobs "
        "(id, render_manifest_id, backend, backend_version, "
        "width, height, fps, video_codec, audio_codec, crf, audio_bitrate, "
        "caption_burn_in, ffmpeg_cmd_json, "
        "status, duration_s, file_size_bytes, completed_at, created_at, updated_at) "
        "VALUES (1, 1, 'ffmpeg', '6.0', "
        "1920, 1080, 30, 'libx264', 'aac', 23, '128k', "
        "1, '[]', 'completed', 45.0, 8000000, ?, ?, ?)",
        (_NOW, _NOW, _NOW),
    )

    # Publishing plan — extractor reads: all 7 lineage IDs + provider/visibility/
    #   made_for_kids/category/tags_json/schedule_type
    # status CHECK: 'draft'|'approved'|'rejected' — use 'approved'
    conn.execute(
        "INSERT INTO publishing_plans "
        "(id, render_manifest_id, topic_id, production_plan_id, script_id, "
        "scene_manifest_id, narration_run_id, caption_run_id, "
        "input_hash, publishing_engine_version, metadata_version, "
        "provider, provider_version, title, description, tags_json, "
        "visibility, category, created_at, updated_at) "
        "VALUES (1, 1, ?, 1, 1, 1, 1, 1, "
        "'pp_hash_1', '1.0', '1.0', "
        "'youtube', '1.0', 'Test Video', 'A test.', '[\"ai\",\"shorts\",\"tech\"]', "
        "?, '22', ?, ?)",
        (topic_id, visibility, _NOW, _NOW),
    )

    # Publishing job (required by publications FK — FK is OFF but keeps semantics clear)
    conn.execute(
        "INSERT INTO publishing_jobs "
        "(id, publishing_plan_id, attempt_number, provider, provider_version, "
        "status, retry_count, created_at, updated_at) "
        "VALUES (1, 1, 1, 'youtube', '1.0', 'completed', 0, ?, ?)",
        (_NOW, _NOW),
    )

    # Publication — extractor reads: publishing_plan_id, workspace_id, channel_id, published_at
    conn.execute(
        "INSERT INTO publications "
        "(id, publishing_plan_id, publishing_job_id, "
        "provider, provider_version, provider_video_id, provider_url, "
        "visibility, status, publishing_engine_version, "
        "input_hash, output_sha256, published_at, "
        "workspace_id, channel_id, created_at, updated_at) "
        "VALUES (?, 1, 1, "
        "'youtube', '1.0', 'vid_ext_1', 'https://youtube.com/watch?v=vid_ext_1', "
        "?, 'published', '1.0', "
        "'pub_hash_1', 'sha256-pub', ?, "
        "?, ?, ?, ?)",
        (publication_id, visibility, published_at, workspace_id, channel_id, _NOW, _NOW),
    )
    conn.commit()


# ── A: Deterministic extraction ────────────────────────────────────────────────


class TestA_Deterministic:
    def test_same_lineage_produces_same_hash(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        d1 = extract_features(db, 1)
        d2 = extract_features(db, 1)
        assert d1.input_hash == d2.input_hash

    def test_same_lineage_produces_same_feature_values(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        d1 = extract_features(db, 1)
        d2 = extract_features(db, 1)
        assert d1.narration_speaking_rate == d2.narration_speaking_rate
        assert d1.script_word_count == d2.script_word_count
        assert d1.scene_count == d2.scene_count


# ── B: Idempotent persistence ──────────────────────────────────────────────────


class TestB_Idempotent:
    def test_second_extract_and_save_returns_same_id(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        _, created1 = extract_and_save(db, 1)
        snap2, created2 = extract_and_save(db, 1)
        assert created1 is True
        assert created2 is False

    def test_only_one_row_after_two_calls(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        extract_and_save(db, 1)
        extract_and_save(db, 1)
        count = db.execute(
            "SELECT COUNT(*) FROM content_feature_snapshots WHERE publication_id = 1"
        ).fetchone()[0]
        assert count == 1


# ── C: Zero vs NULL distinction ────────────────────────────────────────────────


class TestC_ZeroVsNull:
    def test_zero_hook_word_count_vs_absent_hook(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db, with_hook=False)
        draft = extract_features(db, 1)
        assert draft.has_hook == 0
        # hook_word_count must be None (no hook exists), not 0
        assert draft.hook_word_count is None

    def test_has_hook_true_when_hook_segment_present(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db, with_hook=True)
        draft = extract_features(db, 1)
        assert draft.has_hook == 1
        assert draft.hook_word_count is not None
        assert draft.hook_word_count > 0


# ── D: Correct publication lineage traversal ──────────────────────────────────


class TestD_LineageTraversal:
    def test_all_lineage_ids_populated(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        draft = extract_features(db, 1)
        assert draft.publishing_plan_id == 1
        assert draft.production_plan_id == 1
        assert draft.script_id == 1
        assert draft.narration_run_id == 1
        assert draft.caption_run_id == 1
        assert draft.scene_manifest_id == 1
        assert draft.render_manifest_id == 1

    def test_topic_id_derived_from_publishing_plan(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db, topic_id=1)
        draft = extract_features(db, 1)
        assert draft.topic_id == 1

    def test_missing_publication_raises(self, db: sqlite3.Connection) -> None:
        with pytest.raises(PublicationNotFoundError):
            extract_features(db, 999)


# ── E: Actual narration speaking_rate extracted ────────────────────────────────


class TestE_SpeakingRate:
    def test_speaking_rate_extracted_from_narration_run(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db, speaking_rate=0.85)
        draft = extract_features(db, 1)
        assert draft.narration_speaking_rate == pytest.approx(0.85)

    def test_speaking_rate_is_actual_not_profile_default(self, db: sqlite3.Connection) -> None:
        """narration_runs.speaking_rate is the actual effective rate."""
        _insert_full_lineage(db, speaking_rate=0.9)
        # Voice profile has speaking_rate=1.0; narration run has 0.9 (overridden by Phase 12A)
        draft = extract_features(db, 1)
        assert draft.narration_speaking_rate == pytest.approx(0.9)


# ── F: Phase 12A learning application attribution ─────────────────────────────


class TestF_LearningApplicationAttribution:
    def _insert_application(
        self,
        conn: sqlite3.Connection,
        *,
        topic_id: int = 1,
        narration_run_id: int = 1,
        value_applied: float = 0.9,
    ) -> int:
        # Minimal learning_run + rec + application
        lr_id = conn.execute(
            "INSERT INTO learning_runs "
            "(topic_id, status, engine_version, schema_version, input_hash, created_at) "
            "VALUES (?, 'completed', 'v1', 'v1', 'lrhash', ?)",
            (topic_id, _NOW),
        ).lastrowid
        rec_id = conn.execute(
            "INSERT INTO optimization_recommendations "
            "(learning_run_id, topic_id, domain, subsystem, measure, "
            "title, explanation, expected_improvement, "
            "confidence, confidence_score, "
            "evidence_json, evidence_classification, recommendation_strength, "
            "engine_version, schema_version, input_hash, status, created_at) "
            "VALUES (?, ?, 'narration', 'narration_pace', 'speaking_rate', "
            "'T', 'E', 'I', 'medium', 0.6, '[]', 'observational', 'actionable', "
            "'v1', 'v1', 'rechash', 'accepted', ?)",
            (lr_id, topic_id, _NOW),
        ).lastrowid
        app_id = conn.execute(
            "INSERT INTO recommendation_applications "
            "(recommendation_id, learning_run_id, topic_id, parameter_name, "
            "domain, subsystem, "
            "intent_direction, intent_magnitude, intent_target_value, "
            "safety_min, safety_max, safety_max_delta, "
            "input_hash, proposed_at, "
            "status, applied_at, narration_run_id, value_applied, created_at, updated_at) "
            "VALUES (?, ?, ?, 'narration_pace', 'narration', 'narration_pace', "
            "'decrease', 0.1, ?, "
            "0.5, 1.5, 0.2, "
            "'app_hash', ?, "
            "'applied', ?, ?, ?, ?, ?)",
            (
                rec_id,
                lr_id,
                topic_id,
                value_applied,
                _NOW,
                _NOW,
                narration_run_id,
                value_applied,
                _NOW,
                _NOW,
            ),
        ).lastrowid
        conn.commit()
        return app_id

    def test_application_detected_when_present(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db, speaking_rate=0.9)
        app_id = self._insert_application(db, value_applied=0.9)
        draft = extract_features(db, 1)
        assert draft.learning_application_used == 1
        assert draft.learning_application_id == app_id
        assert draft.learning_application_value == pytest.approx(0.9)

    def test_no_application_when_absent(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        draft = extract_features(db, 1)
        assert draft.learning_application_used == 0
        assert draft.learning_application_id is None
        assert draft.learning_application_parameter is None
        assert draft.learning_application_value is None

    def test_application_parameter_stored(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        self._insert_application(db)
        draft = extract_features(db, 1)
        assert draft.learning_application_parameter == "narration_pace"


# ── G: workspace/channel scope isolation ─────────────────────────────────────


class TestG_ScopeIsolation:
    def test_workspace_channel_stored(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db, workspace_id="ws-alpha", channel_id="ch-beta")
        draft = extract_features(db, 1)
        assert draft.workspace_id == "ws-alpha"
        assert draft.channel_id == "ch-beta"

    def test_null_workspace_channel_accepted(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db, workspace_id=None, channel_id=None)
        draft = extract_features(db, 1)
        assert draft.workspace_id is None
        assert draft.channel_id is None


# ── H: Derived numeric features ───────────────────────────────────────────────


class TestH_DerivedFeatures:
    def test_words_per_second(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db, word_count=90)
        draft = extract_features(db, 1)
        # word_count=90, narration_actual_duration_s=45.0 → 2.0
        assert draft.words_per_second == pytest.approx(2.0, rel=1e-3)

    def test_scenes_per_minute(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        draft = extract_features(db, 1)
        # scene_count=3, duration=45s → 3/(45/60)=4.0
        assert draft.scenes_per_minute == pytest.approx(4.0, rel=1e-3)

    def test_avg_scene_duration_ms(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        draft = extract_features(db, 1)
        # render duration=45s, 3 scenes → 45000/3=15000 ms
        assert draft.avg_scene_duration_ms == pytest.approx(15000.0, rel=1e-3)

    def test_caption_cues_per_second(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        draft = extract_features(db, 1)
        # 24 cues / 45s → 0.5333...
        assert draft.caption_cues_per_second == pytest.approx(24 / 45.0, rel=1e-2)

    def test_derived_features_none_when_duration_missing(self, db: sqlite3.Connection) -> None:
        """If narration_actual_duration_s is not available, derived rates are None."""
        _insert_full_lineage(db)
        # Remove the narration segment assets (so actual_duration_s will be None)
        db.execute("DELETE FROM narration_segment_assets")
        db.commit()
        draft = extract_features(db, 1)
        assert draft.narration_actual_duration_s is None
        assert draft.words_per_second is None
        assert draft.scenes_per_minute is None
        assert draft.caption_cues_per_second is None


# ── I: Missing optional features handled safely ───────────────────────────────


class TestI_MissingOptionalFeatures:
    def test_no_render_job_still_extracts(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        db.execute("DELETE FROM render_jobs")
        db.commit()
        draft = extract_features(db, 1)
        assert draft.render_actual_duration_s is None
        assert draft.render_file_size_bytes is None
        # Other features should still be present
        assert draft.narration_speaking_rate is not None

    def test_no_caption_cues_timing_source_none(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        db.execute("DELETE FROM caption_cues")
        db.commit()
        draft = extract_features(db, 1)
        assert draft.caption_timing_source is None

    def test_incomplete_lineage_raises(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        # Delete the publishing plan to break lineage
        db.execute("DELETE FROM publishing_plans")
        db.commit()
        with pytest.raises(IncompleteLineageError):
            extract_features(db, 1)


# ── J: Historical snapshot immutability ───────────────────────────────────────


class TestJ_SnapshotImmutability:
    def test_second_save_call_does_not_overwrite(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        extract_and_save(db, 1)

        # Modify the speaking rate in the DB (simulating a later narration)
        db.execute("UPDATE narration_runs SET speaking_rate = 0.5 WHERE id = 1")
        db.commit()

        # Second extract_and_save returns the ORIGINAL snapshot
        snap2, created2 = extract_and_save(db, 1)
        assert created2 is False
        assert snap2.narration_speaking_rate == pytest.approx(1.0)  # original value


# ── K: Feature schema version stored ──────────────────────────────────────────


class TestK_SchemaVersionStored:
    def test_feature_schema_version_in_draft(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        draft = extract_features(db, 1)
        assert draft.feature_schema_version == FEATURE_SCHEMA_VERSION

    def test_extractor_version_in_draft(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        draft = extract_features(db, 1)
        assert draft.extractor_version == EXTRACTOR_VERSION

    def test_versions_persisted_to_db(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        extract_and_save(db, 1)
        snap = get_feature_snapshot(db, 1)
        assert snap is not None
        assert snap.feature_schema_version == FEATURE_SCHEMA_VERSION
        assert snap.extractor_version == EXTRACTOR_VERSION


# ── L: input_hash changes when source features change ────────────────────────


class TestL_InputHashChanges:
    def test_different_publication_ids_produce_different_hash(self, db: sqlite3.Connection) -> None:
        """publication_id is an input to the hash; different publications always differ."""
        from app.learning.features import _compute_feature_hash

        hash1 = _compute_feature_hash(
            publication_id=1,
            publishing_plan_id=1,
            production_plan_id=1,
            script_id=1,
            narration_run_id=1,
            caption_run_id=1,
            scene_manifest_id=1,
            render_manifest_id=1,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            extractor_version=EXTRACTOR_VERSION,
        )
        hash2 = _compute_feature_hash(
            publication_id=2,  # only publication_id differs
            publishing_plan_id=1,
            production_plan_id=1,
            script_id=1,
            narration_run_id=1,
            caption_run_id=1,
            scene_manifest_id=1,
            render_manifest_id=1,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            extractor_version=EXTRACTOR_VERSION,
        )
        assert hash1 != hash2

    def test_different_narration_run_ids_produce_different_hash(
        self, db: sqlite3.Connection
    ) -> None:
        """narration_run_id change (speaking_rate change) → different hash."""
        from app.learning.features import _compute_feature_hash

        base = dict(
            publication_id=1,
            publishing_plan_id=1,
            production_plan_id=1,
            script_id=1,
            narration_run_id=1,
            caption_run_id=1,
            scene_manifest_id=1,
            render_manifest_id=1,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            extractor_version=EXTRACTOR_VERSION,
        )
        hash1 = _compute_feature_hash(**base)
        hash2 = _compute_feature_hash(**{**base, "narration_run_id": 2})
        assert hash1 != hash2

    def test_different_schema_versions_produce_different_hash(self, db: sqlite3.Connection) -> None:
        """Schema version bump → different hash even for same lineage."""
        from app.learning.features import _compute_feature_hash

        base = dict(
            publication_id=1,
            publishing_plan_id=1,
            production_plan_id=1,
            script_id=1,
            narration_run_id=1,
            caption_run_id=1,
            scene_manifest_id=1,
            render_manifest_id=1,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            extractor_version=EXTRACTOR_VERSION,
        )
        hash1 = _compute_feature_hash(**base)
        # Derived from the real version rather than a hardcoded literal: this
        # test previously pinned "features-v2", which silently became a
        # self-comparison the moment the schema actually reached v2.
        other_version = FEATURE_SCHEMA_VERSION + "-other"
        hash2 = _compute_feature_hash(**{**base, "feature_schema_version": other_version})
        assert hash1 != hash2


# ── M: Analytics metrics NOT stored ───────────────────────────────────────────


class TestM_NoAnalyticsMetrics:
    def test_snapshot_has_no_views_column(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        extract_and_save(db, 1)
        cols = {r[1] for r in db.execute("PRAGMA table_info(content_feature_snapshots)").fetchall()}
        # Explicitly verify analytics metric columns are absent
        for col in ("views", "ctr", "avd", "watch_time", "metric_value", "impressions"):
            assert col not in cols, f"Analytics column '{col}' must not be in feature snapshot"

    def test_draft_has_no_analytics_fields(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        draft = extract_features(db, 1)
        for attr in ("views", "ctr", "avd", "watch_time"):
            assert not hasattr(draft, attr)


# ── N: Unstructured characteristics not fabricated ────────────────────────────


class TestN_NoFabricatedData:
    def test_hook_type_not_classified(self, db: sqlite3.Connection) -> None:
        """hook section exists but we don't classify its text content."""
        _insert_full_lineage(db, with_hook=True)
        draft = extract_features(db, 1)
        # hook_word_count is a count (numeric) — text classification would be fabrication
        assert isinstance(draft.hook_word_count, (int, type(None)))
        # No hook_type field exists
        assert not hasattr(draft, "hook_type")

    def test_tag_count_not_classified(self, db: sqlite3.Connection) -> None:
        """Tags are counted but not classified into categories."""
        _insert_full_lineage(db)
        draft = extract_features(db, 1)
        assert draft.publish_tag_count == 3  # ["ai","shorts","tech"] → count only
        assert not hasattr(draft, "tag_categories")

    def test_no_brand_voice_feature(self, db: sqlite3.Connection) -> None:
        """brand_voice is unstructured text in channel_profile_versions — not extracted."""
        _insert_full_lineage(db)
        draft = extract_features(db, 1)
        assert not hasattr(draft, "brand_voice")


# ── O: CLI inspection works ────────────────────────────────────────────────────


class TestO_CLIInspection:
    def test_features_extract_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Uses the repo's ACE_DB_PATH + reset_config isolation pattern.

        This test previously patched `app.core.config.get_config` to return a
        `_FakeCfg` carrying a `database_path` attribute. That did nothing:
        `app.cli` binds `get_config` at import time, so the patch never reached
        the CLI, and `_FakeCfg` did not even have the `db_path` attribute
        `_get_db()` reads. The command therefore ran against the DEFAULT
        database — the live operational one — and "passed" only because that
        database happens to contain a publication with id=1.

        Phase 18E's session isolation guard is what surfaced it.
        """
        from app.cli import app as cli_app

        db_path = tmp_path / "cli_extract.db"
        conn = open_db(db_path)
        _insert_full_lineage(conn)
        conn.close()

        monkeypatch.setenv("ACE_DB_PATH", str(db_path))
        reset_config()
        try:
            result = runner.invoke(cli_app, ["features", "extract", "1"])
        finally:
            reset_config()

        assert result.exit_code == 0, result.output
        assert "Extracted" in result.output or "Exists" in result.output

    def test_features_show_command(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.cli import app as cli_app

        db_path = tmp_path / "cli_show.db"
        conn2 = open_db(db_path)
        _insert_full_lineage(conn2)
        extract_and_save(conn2, 1)
        conn2.close()

        monkeypatch.setenv("ACE_DB_PATH", str(db_path))
        reset_config()
        try:
            result = runner.invoke(cli_app, ["features", "show", "1"])
        finally:
            reset_config()

        assert result.exit_code == 0, result.output
        assert "Content Feature Snapshot" in result.output
        assert "narration_speaking_rate" in result.output or "speaking_rate" in result.output

    def test_features_show_missing_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Same inert-monkeypatch defect as the two tests above; this one would
        # have "passed" against any database, which is why it hid longer.
        from app.cli import app as cli_app

        db_path = tmp_path / "empty.db"
        conn = open_db(db_path)
        conn.close()

        monkeypatch.setenv("ACE_DB_PATH", str(db_path))
        reset_config()
        try:
            result = runner.invoke(cli_app, ["features", "show", "999"])
        finally:
            reset_config()

        assert result.exit_code != 0


# ── P: Publication 1 backfill does not mutate state ───────────────────────────


class TestP_BackfillSafety:
    def test_backfill_reads_not_writes_to_production_tables(self, db: sqlite3.Connection) -> None:
        """extract_features() is read-only; it does not modify any production table."""
        _insert_full_lineage(db)

        # Capture current state of key tables before extraction
        before_topics = db.execute("SELECT * FROM topics").fetchall()
        before_pub = db.execute("SELECT * FROM publications").fetchall()
        before_pp = db.execute("SELECT * FROM publishing_plans").fetchall()
        before_nr = db.execute("SELECT * FROM narration_runs").fetchall()

        extract_features(db, 1)

        after_topics = db.execute("SELECT * FROM topics").fetchall()
        after_pub = db.execute("SELECT * FROM publications").fetchall()
        after_pp = db.execute("SELECT * FROM publishing_plans").fetchall()
        after_nr = db.execute("SELECT * FROM narration_runs").fetchall()

        assert before_topics == after_topics
        assert before_pub == after_pub
        assert before_pp == after_pp
        assert before_nr == after_nr

    def test_save_only_writes_to_feature_snapshot_table(self, db: sqlite3.Connection) -> None:
        """save_feature_snapshot() only inserts into content_feature_snapshots."""
        _insert_full_lineage(db)
        draft = extract_features(db, 1)

        before_pub = db.execute("SELECT * FROM publications").fetchall()
        before_nr = db.execute("SELECT * FROM narration_runs").fetchall()

        save_feature_snapshot(db, draft)

        after_pub = db.execute("SELECT * FROM publications").fetchall()
        after_nr = db.execute("SELECT * FROM narration_runs").fetchall()

        assert before_pub == after_pub
        assert before_nr == after_nr

        # Snapshot was written
        count = db.execute(
            "SELECT COUNT(*) FROM content_feature_snapshots WHERE publication_id = 1"
        ).fetchone()[0]
        assert count == 1


# ── Publishing date parsing ────────────────────────────────────────────────────


class TestPublishingDateParsing:
    def test_day_of_week_extracted(self, db: sqlite3.Connection) -> None:
        # _PUBLISHED_AT = "2026-01-01T10:30:00" → Thursday (weekday 3)
        _insert_full_lineage(db, published_at=_PUBLISHED_AT)
        draft = extract_features(db, 1)
        assert draft.publish_day_of_week == 3  # Thursday

    def test_hour_utc_extracted(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db, published_at=_PUBLISHED_AT)
        draft = extract_features(db, 1)
        assert draft.publish_hour_utc == 10

    def test_tag_count_from_json_array(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        draft = extract_features(db, 1)
        assert draft.publish_tag_count == 3  # ["ai","shorts","tech"]


# ── Scene features ─────────────────────────────────────────────────────────────


class TestSceneFeatures:
    def test_dominant_shot_type_is_mode(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        draft = extract_features(db, 1)
        # Scenes: close_up, wide, medium → all different; first in DB ordering wins
        assert draft.scene_dominant_shot_type in ("close_up", "wide", "medium")

    def test_scene_count_extracted(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        draft = extract_features(db, 1)
        assert draft.scene_count == 3

    def test_no_ai_assets(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        draft = extract_features(db, 1)
        assert draft.scene_has_ai_generated_assets == 0
        assert draft.scene_ai_generated_asset_count == 0


# ── Render features ────────────────────────────────────────────────────────────


class TestRenderFeatures:
    def test_render_dimensions_extracted(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        draft = extract_features(db, 1)
        assert draft.render_width == 1920
        assert draft.render_height == 1080
        assert draft.render_fps == 30

    def test_render_caption_burn_in(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        draft = extract_features(db, 1)
        assert draft.render_caption_burn_in == 1

    def test_render_file_size_bytes(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        draft = extract_features(db, 1)
        assert draft.render_file_size_bytes == 8_000_000


# ── ContentFeatureSnapshot Pydantic model ─────────────────────────────────────


class TestContentFeatureSnapshotModel:
    def test_from_row_populates_all_fields(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        extract_and_save(db, 1)
        snap = get_feature_snapshot(db, 1)
        assert snap is not None
        assert isinstance(snap, ContentFeatureSnapshot)
        assert snap.publication_id == 1
        assert snap.feature_schema_version == FEATURE_SCHEMA_VERSION

    def test_snapshot_is_frozen(self, db: sqlite3.Connection) -> None:
        _insert_full_lineage(db)
        extract_and_save(db, 1)
        snap = get_feature_snapshot(db, 1)
        assert snap is not None
        with pytest.raises(ValidationError):
            snap.narration_speaking_rate = 99.9  # type: ignore[misc]

    def test_get_feature_snapshot_returns_none_when_absent(self, db: sqlite3.Connection) -> None:
        snap = get_feature_snapshot(db, 999)
        assert snap is None
