"""Phase 12B — Content Feature Attribution.

Extracts structured content features from a publication's production lineage
and persists them as a frozen, versioned feature snapshot.

Design invariants:
  - One snapshot per publication (UNIQUE constraint).  Re-extraction is idempotent.
  - All features are derived from typed DB columns; no heuristic text parsing.
  - Analytics/performance metrics are explicitly excluded (features describe content,
    not outcome).
  - Natural-language fields (title, description, tags text) are NEVER classified;
    only structured/enumerated fields are captured as features.
  - Missing features are represented explicitly as NULL; zero is never substituted
    for absent data.
  - The same publication_id + lineage + schema version always produce the same
    input_hash (deterministic).
  - Historical snapshots are never mutated; new feature schema → new snapshot row
    would require a bump to FEATURE_SCHEMA_VERSION.

Phase 12C query example (no JSON parsing required):
    SELECT narration_speaking_rate, AVG(aa.metric_value)
    FROM content_feature_snapshots cfs
    JOIN analytics_aggregates aa ON cfs.publication_id = aa.publication_id
    WHERE aa.metric_name = 'views'
    GROUP BY ROUND(narration_speaking_rate, 1)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel

FEATURE_SCHEMA_VERSION: str = "features-v2"
EXTRACTOR_VERSION: str = "extractor-v2"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


# ── Domain models ──────────────────────────────────────────────────────────────


@dataclass
class ContentFeatureSnapshotDraft:
    """Mutable draft assembled during extraction; persisted via save_feature_snapshot."""

    publication_id: int
    topic_id: int
    workspace_id: str | None
    channel_id: str | None
    feature_schema_version: str
    extractor_version: str
    input_hash: str
    extracted_at: str

    # Lineage IDs
    publishing_plan_id: int
    production_plan_id: int
    script_id: int
    narration_run_id: int
    caption_run_id: int
    scene_manifest_id: int
    render_manifest_id: int
    voice_profile_id: int

    # Script features
    script_format: str | None = None
    script_word_count: int | None = None
    script_segment_count: int | None = None
    script_section_count: int | None = None
    has_hook: int = 0
    has_cta: int = 0
    hook_word_count: int | None = None

    # Production features
    target_duration_s: int | None = None

    # Narration features
    narration_speaking_rate: float | None = None
    narration_provider: str | None = None
    narration_model: str | None = None
    narration_voice_id: str | None = None
    narration_language: str | None = None
    narration_actual_duration_s: float | None = None
    narration_segment_count: int | None = None

    # Learning application features
    learning_application_used: int = 0
    learning_application_id: int | None = None
    learning_application_parameter: str | None = None
    learning_application_value: float | None = None

    # Caption features
    caption_total_cue_count: int | None = None
    caption_total_duration_ms: int | None = None
    caption_style_version: str | None = None
    caption_segmentation_version: str | None = None
    caption_timing_source: str | None = None

    # Scene features
    scene_count: int | None = None
    scene_asset_count: int | None = None
    scene_has_ai_generated_assets: int = 0
    scene_ai_generated_asset_count: int | None = None
    scene_dominant_shot_type: str | None = None
    scene_dominant_transition: str | None = None

    # Render features
    render_width: int | None = None
    render_height: int | None = None
    render_fps: int | None = None
    render_caption_burn_in: int = 0
    render_actual_duration_s: float | None = None
    render_file_size_bytes: int | None = None

    # Publishing features
    publish_provider: str | None = None
    publish_visibility: str | None = None
    publish_made_for_kids: int = 0
    publish_category: str | None = None
    publish_tag_count: int | None = None
    publish_published_at: str | None = None
    publish_day_of_week: int | None = None
    publish_hour_utc: int | None = None
    publish_schedule_type: str | None = None

    # Visual composition features (Phase 18E).
    #
    # Three distinct kinds, deliberately not blended together:
    #   planner-controlled   — visual_style
    #   realized-production  — the runtime percentages, cadence, gap, assets
    #   provider-reliability — visual_provider_fallback_rate, visual_quality_status
    #
    # All default to None. A publication rendered before this phase has no
    # assessment, and NULL ("unknown") must never be read as 0.0 ("it had no
    # meaningful visuals") — that would teach the learner the exact opposite of
    # the truth about the channel's back catalogue.
    visual_style: str | None = None
    visual_quality_status: str | None = None
    visual_meaningful_runtime_pct: float | None = None
    visual_text_card_runtime_pct: float | None = None
    visual_generated_diagram_runtime_pct: float | None = None
    visual_retrieved_imagery_runtime_pct: float | None = None
    visual_changes_per_minute: float | None = None
    visual_max_meaningful_gap_s: float | None = None
    visual_distinct_assets: int | None = None
    visual_asset_reuse_ratio: float | None = None
    visual_dominant_family: str | None = None
    visual_opening_meaningful: int | None = None
    visual_provider_fallback_rate: float | None = None

    # Derived features
    words_per_second: float | None = None
    scenes_per_minute: float | None = None
    avg_scene_duration_ms: float | None = None
    caption_cues_per_second: float | None = None

    # Overflow
    features_json: str = field(default_factory=lambda: "{}")


class ContentFeatureSnapshot(BaseModel):
    """Frozen row projection of content_feature_snapshots."""

    model_config = {"frozen": True}

    id: int
    publication_id: int
    topic_id: int
    workspace_id: str | None
    channel_id: str | None
    feature_schema_version: str
    extractor_version: str
    input_hash: str
    extracted_at: str
    created_at: str

    publishing_plan_id: int
    production_plan_id: int
    script_id: int
    narration_run_id: int
    caption_run_id: int
    scene_manifest_id: int
    render_manifest_id: int
    voice_profile_id: int

    script_format: str | None
    script_word_count: int | None
    script_segment_count: int | None
    script_section_count: int | None
    has_hook: int
    has_cta: int
    hook_word_count: int | None

    target_duration_s: int | None

    narration_speaking_rate: float | None
    narration_provider: str | None
    narration_model: str | None
    narration_voice_id: str | None
    narration_language: str | None
    narration_actual_duration_s: float | None
    narration_segment_count: int | None

    learning_application_used: int
    learning_application_id: int | None
    learning_application_parameter: str | None
    learning_application_value: float | None

    caption_total_cue_count: int | None
    caption_total_duration_ms: int | None
    caption_style_version: str | None
    caption_segmentation_version: str | None
    caption_timing_source: str | None

    scene_count: int | None
    scene_asset_count: int | None
    scene_has_ai_generated_assets: int
    scene_ai_generated_asset_count: int | None
    scene_dominant_shot_type: str | None
    scene_dominant_transition: str | None

    visual_style: str | None = None
    visual_quality_status: str | None = None
    visual_meaningful_runtime_pct: float | None = None
    visual_text_card_runtime_pct: float | None = None
    visual_generated_diagram_runtime_pct: float | None = None
    visual_retrieved_imagery_runtime_pct: float | None = None
    visual_changes_per_minute: float | None = None
    visual_max_meaningful_gap_s: float | None = None
    visual_distinct_assets: int | None = None
    visual_asset_reuse_ratio: float | None = None
    visual_dominant_family: str | None = None
    visual_opening_meaningful: int | None = None
    visual_provider_fallback_rate: float | None = None

    render_width: int | None
    render_height: int | None
    render_fps: int | None
    render_caption_burn_in: int
    render_actual_duration_s: float | None
    render_file_size_bytes: int | None

    publish_provider: str | None
    publish_visibility: str | None
    publish_made_for_kids: int
    publish_category: str | None
    publish_tag_count: int | None
    publish_published_at: str | None
    publish_day_of_week: int | None
    publish_hour_utc: int | None
    publish_schedule_type: str | None

    words_per_second: float | None
    scenes_per_minute: float | None
    avg_scene_duration_ms: float | None
    caption_cues_per_second: float | None

    features_json: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ContentFeatureSnapshot:
        return cls(**dict(row))


# ── Errors ─────────────────────────────────────────────────────────────────────


class FeatureExtractionError(Exception):
    """Base error for content feature extraction failures."""


class PublicationNotFoundError(FeatureExtractionError):
    """No publication with the given ID exists."""


class IncompleteLineageError(FeatureExtractionError):
    """Required lineage data is missing; cannot produce a complete feature snapshot."""


# ── Hashing ───────────────────────────────────────────────────────────────────


def _compute_feature_hash(
    *,
    publication_id: int,
    publishing_plan_id: int,
    production_plan_id: int,
    script_id: int,
    narration_run_id: int,
    caption_run_id: int,
    scene_manifest_id: int,
    render_manifest_id: int,
    feature_schema_version: str,
    extractor_version: str,
) -> str:
    """Deterministic SHA-256 hash over all lineage IDs + version strings.

    Same lineage + same schema → same hash.
    """
    payload = json.dumps(
        {
            "publication_id": publication_id,
            "publishing_plan_id": publishing_plan_id,
            "production_plan_id": production_plan_id,
            "script_id": script_id,
            "narration_run_id": narration_run_id,
            "caption_run_id": caption_run_id,
            "scene_manifest_id": scene_manifest_id,
            "render_manifest_id": render_manifest_id,
            "feature_schema_version": feature_schema_version,
            "extractor_version": extractor_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# ── Feature extraction ─────────────────────────────────────────────────────────


def _mode(values: list[str]) -> str | None:
    """Return the most frequent value in a list, or None if the list is empty."""
    if not values:
        return None
    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return max(counts, key=lambda k: counts[k])


def extract_features(
    conn: sqlite3.Connection,
    publication_id: int,
) -> ContentFeatureSnapshotDraft:
    """Traverse the production lineage for publication_id and extract features.

    Does NOT persist anything.  Raises PublicationNotFoundError if the
    publication doesn't exist, or IncompleteLineageError if required lineage
    rows are absent.

    Analytics/performance metrics (views, CTR, AVD, etc.) are explicitly
    excluded; this function reads only content production tables.
    """
    # ── 1. Fetch publication + publishing plan ─────────────────────────────
    pub_row = conn.execute("SELECT * FROM publications WHERE id = ?", (publication_id,)).fetchone()
    if pub_row is None:
        raise PublicationNotFoundError(f"No publication with id={publication_id}")

    plan_row = conn.execute(
        "SELECT * FROM publishing_plans WHERE id = ?",
        (pub_row["publishing_plan_id"],),
    ).fetchone()
    if plan_row is None:
        raise IncompleteLineageError(
            f"publishing_plan id={pub_row['publishing_plan_id']} not found "
            f"(referenced by publication id={publication_id})"
        )

    publishing_plan_id = plan_row["id"]
    production_plan_id = plan_row["production_plan_id"]
    script_id = plan_row["script_id"]
    narration_run_id = plan_row["narration_run_id"]
    caption_run_id = plan_row["caption_run_id"]
    scene_manifest_id = plan_row["scene_manifest_id"]
    render_manifest_id = plan_row["render_manifest_id"]
    topic_id = plan_row["topic_id"]

    input_hash = _compute_feature_hash(
        publication_id=publication_id,
        publishing_plan_id=publishing_plan_id,
        production_plan_id=production_plan_id,
        script_id=script_id,
        narration_run_id=narration_run_id,
        caption_run_id=caption_run_id,
        scene_manifest_id=scene_manifest_id,
        render_manifest_id=render_manifest_id,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        extractor_version=EXTRACTOR_VERSION,
    )

    draft = ContentFeatureSnapshotDraft(
        publication_id=publication_id,
        topic_id=topic_id,
        workspace_id=pub_row["workspace_id"] if "workspace_id" in pub_row.keys() else None,
        channel_id=pub_row["channel_id"] if "channel_id" in pub_row.keys() else None,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        extractor_version=EXTRACTOR_VERSION,
        input_hash=input_hash,
        extracted_at=_now(),
        publishing_plan_id=publishing_plan_id,
        production_plan_id=production_plan_id,
        script_id=script_id,
        narration_run_id=narration_run_id,
        caption_run_id=caption_run_id,
        scene_manifest_id=scene_manifest_id,
        render_manifest_id=render_manifest_id,
        voice_profile_id=0,  # filled below
    )

    # ── 2. Production plan features ────────────────────────────────────────
    pp_row = conn.execute(
        "SELECT * FROM production_plans WHERE id = ?", (production_plan_id,)
    ).fetchone()
    if pp_row is not None:
        draft.script_format = pp_row["format"]
        draft.script_word_count = pp_row["total_word_count"] or None
        draft.target_duration_s = pp_row["total_estimated_duration_s"] or None

    # ── 3. Production segments — section type analysis ─────────────────────
    seg_rows = conn.execute(
        """
        SELECT section_type, estimated_word_count
        FROM production_segments WHERE plan_id = ?
        """,
        (production_plan_id,),
    ).fetchall()
    if seg_rows:
        draft.script_segment_count = len(seg_rows)
        section_types = [r["section_type"] for r in seg_rows]
        draft.script_section_count = len(set(section_types))
        draft.has_hook = 1 if "hook" in section_types else 0
        draft.has_cta = 1 if "cta" in section_types else 0
        # hook word count: sum of estimated_word_count for hook segments
        hook_words = sum(
            (r["estimated_word_count"] or 0) for r in seg_rows if r["section_type"] == "hook"
        )
        draft.hook_word_count = hook_words if hook_words > 0 else None

    # ── 4. Narration run + voice profile ───────────────────────────────────
    nr_row = conn.execute(
        "SELECT * FROM narration_runs WHERE id = ?", (narration_run_id,)
    ).fetchone()
    if nr_row is not None:
        draft.narration_speaking_rate = nr_row["speaking_rate"]
        draft.narration_language = nr_row["language"]
        vp_id = nr_row["voice_profile_id"]
        draft.voice_profile_id = vp_id

        vp_row = conn.execute("SELECT * FROM voice_profiles WHERE id = ?", (vp_id,)).fetchone()
        if vp_row is not None:
            draft.narration_provider = vp_row["provider"]
            draft.narration_model = vp_row["model"]
            draft.narration_voice_id = vp_row["voice_id"]
    else:
        draft.voice_profile_id = 0

    # ── 5. Narration segment assets — actual duration ──────────────────────
    nsa_agg = conn.execute(
        """
        SELECT SUM(duration_seconds) AS total_s, COUNT(*) AS seg_count
        FROM narration_segment_assets
        WHERE run_id = ? AND status = 'synthesized' AND superseded_at IS NULL
        """,
        (narration_run_id,),
    ).fetchone()
    if nsa_agg and nsa_agg["total_s"] is not None:
        draft.narration_actual_duration_s = nsa_agg["total_s"]
        draft.narration_segment_count = nsa_agg["seg_count"]

    # ── 6. Learning application attribution ────────────────────────────────
    app_row = conn.execute(
        """
        SELECT id, parameter_name, value_applied
        FROM recommendation_applications
        WHERE topic_id = ? AND narration_run_id = ? AND status = 'applied'
        ORDER BY applied_at DESC
        LIMIT 1
        """,
        (topic_id, narration_run_id),
    ).fetchone()
    if app_row is not None:
        draft.learning_application_used = 1
        draft.learning_application_id = app_row["id"]
        draft.learning_application_parameter = app_row["parameter_name"]
        draft.learning_application_value = app_row["value_applied"]

    # ── 7. Caption run ─────────────────────────────────────────────────────
    cr_row = conn.execute("SELECT * FROM caption_runs WHERE id = ?", (caption_run_id,)).fetchone()
    if cr_row is not None:
        draft.caption_total_cue_count = cr_row["total_cue_count"] or None
        draft.caption_total_duration_ms = cr_row["total_duration_ms"] or None
        draft.caption_style_version = cr_row["style_version"]
        draft.caption_segmentation_version = cr_row["segmentation_version"]

    # Dominant timing source across cues
    timing_rows = conn.execute(
        """
        SELECT timing_source, COUNT(*) AS cnt
        FROM caption_cues
        WHERE run_id = ? AND superseded_at IS NULL
        GROUP BY timing_source
        ORDER BY cnt DESC
        LIMIT 1
        """,
        (caption_run_id,),
    ).fetchone()
    if timing_rows:
        draft.caption_timing_source = timing_rows["timing_source"]

    # ── 8. Scene manifest ──────────────────────────────────────────────────
    sm_row = conn.execute(
        "SELECT * FROM scene_manifests WHERE id = ?", (scene_manifest_id,)
    ).fetchone()
    if sm_row is not None:
        draft.scene_count = sm_row["total_scene_count"] or None
        draft.scene_asset_count = sm_row["total_asset_count"] or None

    # Dominant shot type and transition
    scene_rows = conn.execute(
        "SELECT shot_type, transition_out FROM scene_manifest_scenes WHERE manifest_id = ?",
        (scene_manifest_id,),
    ).fetchall()
    if scene_rows:
        shot_types = [r["shot_type"] for r in scene_rows if r["shot_type"]]
        transitions = [r["transition_out"] for r in scene_rows if r["transition_out"]]
        draft.scene_dominant_shot_type = _mode(shot_types)
        draft.scene_dominant_transition = _mode(transitions)

    # AI-generated asset count
    asset_agg = conn.execute(
        """
        SELECT SUM(ai_generation_requested) AS ai_count, COUNT(*) AS total_count
        FROM scene_manifest_assets WHERE manifest_id = ?
        """,
        (scene_manifest_id,),
    ).fetchone()
    if asset_agg and asset_agg["total_count"]:
        ai_count = int(asset_agg["ai_count"] or 0)
        draft.scene_ai_generated_asset_count = ai_count
        draft.scene_has_ai_generated_assets = 1 if ai_count > 0 else 0

    # ── 8b. Realized visual composition (Phase 18E) ────────────────────────
    # Read from the render's stored assessment rather than recomputed, so the
    # features a publication is learned from are byte-identical to the metrics
    # the preflight gate judged it by. Absent assessment → every visual
    # feature stays None, and cross-publication learning simply excludes this
    # publication from visual comparisons instead of inventing zeros.
    _extract_visual_features(conn, draft, render_manifest_id)

    # ── 9. Render manifest ─────────────────────────────────────────────────
    rm_row = conn.execute(
        "SELECT * FROM render_manifests WHERE id = ?", (render_manifest_id,)
    ).fetchone()
    if rm_row is not None:
        draft.render_width = rm_row["width"]
        draft.render_height = rm_row["height"]
        draft.render_fps = rm_row["fps"]
        draft.render_caption_burn_in = int(rm_row["caption_burn_in"] or 0)

    # Render job — latest completed
    rj_row = conn.execute(
        """
        SELECT duration_s, file_size_bytes
        FROM render_jobs
        WHERE render_manifest_id = ? AND status = 'completed'
        ORDER BY completed_at DESC
        LIMIT 1
        """,
        (render_manifest_id,),
    ).fetchone()
    if rj_row is not None:
        draft.render_actual_duration_s = rj_row["duration_s"]
        draft.render_file_size_bytes = rj_row["file_size_bytes"]

    # ── 10. Publishing metadata ────────────────────────────────────────────
    draft.publish_provider = plan_row["provider"]
    draft.publish_visibility = plan_row["visibility"]
    draft.publish_made_for_kids = int(plan_row["made_for_kids"] or 0)
    draft.publish_category = plan_row["category"]
    draft.publish_schedule_type = plan_row["schedule_type"]

    try:
        tags = json.loads(plan_row["tags_json"] or "[]")
        draft.publish_tag_count = len(tags) if isinstance(tags, list) else None
    except (json.JSONDecodeError, TypeError):
        draft.publish_tag_count = None

    published_at = pub_row["published_at"]
    if published_at:
        draft.publish_published_at = published_at
        try:
            dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            draft.publish_day_of_week = dt.weekday()  # 0=Monday, 6=Sunday
            draft.publish_hour_utc = dt.hour
        except (ValueError, AttributeError):
            pass

    # ── 11. Derived features ───────────────────────────────────────────────
    if (
        draft.script_word_count is not None
        and draft.narration_actual_duration_s is not None
        and draft.narration_actual_duration_s > 0
    ):
        draft.words_per_second = round(
            draft.script_word_count / draft.narration_actual_duration_s, 4
        )

    if (
        draft.scene_count is not None
        and draft.narration_actual_duration_s is not None
        and draft.narration_actual_duration_s > 0
    ):
        draft.scenes_per_minute = round(
            draft.scene_count / (draft.narration_actual_duration_s / 60.0), 4
        )

    if (
        draft.scene_count is not None
        and draft.scene_count > 0
        and draft.render_actual_duration_s is not None
    ):
        render_ms = draft.render_actual_duration_s * 1000
        draft.avg_scene_duration_ms = round(render_ms / draft.scene_count, 2)

    if (
        draft.caption_total_cue_count is not None
        and draft.narration_actual_duration_s is not None
        and draft.narration_actual_duration_s > 0
    ):
        draft.caption_cues_per_second = round(
            draft.caption_total_cue_count / draft.narration_actual_duration_s, 4
        )

    return draft


# ── Persistence ────────────────────────────────────────────────────────────────


def _extract_visual_features(
    conn: sqlite3.Connection,
    draft: ContentFeatureSnapshotDraft,
    render_manifest_id: int,
) -> None:
    """Copy the render's visual assessment onto the feature draft.

    Percentages are stored as fractions (0.0–1.0) to match every other rate
    feature in this table; the gap is stored in seconds because a bucket width
    expressed in milliseconds would be unreadable in a learning report.
    """
    from app.visuals.assessment_repository import get_assessment

    try:
        assessment = get_assessment(conn, render_manifest_id)
    except Exception:  # noqa: BLE001 — an unreadable assessment leaves features unknown
        return
    if assessment is None:
        return

    total_ms = assessment.total_duration_ms
    if not total_ms:
        return

    family = assessment.family_runtime or {}
    diagram_ms = family.get("generated_diagram", 0)
    retrieved_ms = sum(family.get(f, 0) for f in ("motion_footage", "photographic", "illustration"))

    draft.visual_style = assessment.visual_style
    draft.visual_quality_status = assessment.status
    draft.visual_meaningful_runtime_pct = round(assessment.meaningful_runtime_pct, 4)
    draft.visual_text_card_runtime_pct = round(assessment.text_card_runtime_pct, 4)
    draft.visual_generated_diagram_runtime_pct = round(diagram_ms / total_ms, 4)
    draft.visual_retrieved_imagery_runtime_pct = round(retrieved_ms / total_ms, 4)
    draft.visual_changes_per_minute = round(assessment.visual_changes_per_minute, 4)
    draft.visual_max_meaningful_gap_s = round(assessment.max_meaningful_gap_ms / 1000.0, 2)
    draft.visual_distinct_assets = assessment.distinct_asset_count
    draft.visual_asset_reuse_ratio = round(assessment.asset_reuse_ratio, 4)
    draft.visual_dominant_family = assessment.dominant_family
    draft.visual_opening_meaningful = 1 if assessment.opening_meaningful_visual else 0
    draft.visual_provider_fallback_rate = round(assessment.provider_fallback_rate, 4)


def save_feature_snapshot(
    conn: sqlite3.Connection,
    draft: ContentFeatureSnapshotDraft,
) -> int:
    """Persist a feature snapshot draft.  Idempotent: returns existing ID on duplicate.

    Raises sqlite3.IntegrityError only on hash collision (different lineage,
    same hash — should never happen with SHA-256).
    """
    existing = conn.execute(
        "SELECT id FROM content_feature_snapshots WHERE publication_id = ?",
        (draft.publication_id,),
    ).fetchone()
    if existing is not None:
        return existing["id"]

    now = _now()
    cursor = conn.execute(
        """
        INSERT INTO content_feature_snapshots (
            publication_id, topic_id, workspace_id, channel_id,
            feature_schema_version, extractor_version, input_hash,
            extracted_at, created_at,
            publishing_plan_id, production_plan_id, script_id,
            narration_run_id, caption_run_id, scene_manifest_id,
            render_manifest_id, voice_profile_id,
            script_format, script_word_count, script_segment_count,
            script_section_count, has_hook, has_cta, hook_word_count,
            target_duration_s,
            narration_speaking_rate, narration_provider, narration_model,
            narration_voice_id, narration_language,
            narration_actual_duration_s, narration_segment_count,
            learning_application_used, learning_application_id,
            learning_application_parameter, learning_application_value,
            caption_total_cue_count, caption_total_duration_ms,
            caption_style_version, caption_segmentation_version,
            caption_timing_source,
            scene_count, scene_asset_count,
            scene_has_ai_generated_assets, scene_ai_generated_asset_count,
            scene_dominant_shot_type, scene_dominant_transition,
            visual_style, visual_quality_status,
            visual_meaningful_runtime_pct, visual_text_card_runtime_pct,
            visual_generated_diagram_runtime_pct, visual_retrieved_imagery_runtime_pct,
            visual_changes_per_minute, visual_max_meaningful_gap_s,
            visual_distinct_assets, visual_asset_reuse_ratio,
            visual_dominant_family, visual_opening_meaningful,
            visual_provider_fallback_rate,
            render_width, render_height, render_fps,
            render_caption_burn_in, render_actual_duration_s,
            render_file_size_bytes,
            publish_provider, publish_visibility, publish_made_for_kids,
            publish_category, publish_tag_count, publish_published_at,
            publish_day_of_week, publish_hour_utc, publish_schedule_type,
            words_per_second, scenes_per_minute,
            avg_scene_duration_ms, caption_cues_per_second,
            features_json
        ) VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            draft.publication_id,
            draft.topic_id,
            draft.workspace_id,
            draft.channel_id,
            draft.feature_schema_version,
            draft.extractor_version,
            draft.input_hash,
            draft.extracted_at,
            now,
            draft.publishing_plan_id,
            draft.production_plan_id,
            draft.script_id,
            draft.narration_run_id,
            draft.caption_run_id,
            draft.scene_manifest_id,
            draft.render_manifest_id,
            draft.voice_profile_id,
            draft.script_format,
            draft.script_word_count,
            draft.script_segment_count,
            draft.script_section_count,
            draft.has_hook,
            draft.has_cta,
            draft.hook_word_count,
            draft.target_duration_s,
            draft.narration_speaking_rate,
            draft.narration_provider,
            draft.narration_model,
            draft.narration_voice_id,
            draft.narration_language,
            draft.narration_actual_duration_s,
            draft.narration_segment_count,
            draft.learning_application_used,
            draft.learning_application_id,
            draft.learning_application_parameter,
            draft.learning_application_value,
            draft.caption_total_cue_count,
            draft.caption_total_duration_ms,
            draft.caption_style_version,
            draft.caption_segmentation_version,
            draft.caption_timing_source,
            draft.scene_count,
            draft.scene_asset_count,
            draft.scene_has_ai_generated_assets,
            draft.scene_ai_generated_asset_count,
            draft.scene_dominant_shot_type,
            draft.scene_dominant_transition,
            draft.visual_style,
            draft.visual_quality_status,
            draft.visual_meaningful_runtime_pct,
            draft.visual_text_card_runtime_pct,
            draft.visual_generated_diagram_runtime_pct,
            draft.visual_retrieved_imagery_runtime_pct,
            draft.visual_changes_per_minute,
            draft.visual_max_meaningful_gap_s,
            draft.visual_distinct_assets,
            draft.visual_asset_reuse_ratio,
            draft.visual_dominant_family,
            draft.visual_opening_meaningful,
            draft.visual_provider_fallback_rate,
            draft.render_width,
            draft.render_height,
            draft.render_fps,
            draft.render_caption_burn_in,
            draft.render_actual_duration_s,
            draft.render_file_size_bytes,
            draft.publish_provider,
            draft.publish_visibility,
            draft.publish_made_for_kids,
            draft.publish_category,
            draft.publish_tag_count,
            draft.publish_published_at,
            draft.publish_day_of_week,
            draft.publish_hour_utc,
            draft.publish_schedule_type,
            draft.words_per_second,
            draft.scenes_per_minute,
            draft.avg_scene_duration_ms,
            draft.caption_cues_per_second,
            draft.features_json,
        ),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore[return-value]


def get_feature_snapshot(
    conn: sqlite3.Connection,
    publication_id: int,
) -> ContentFeatureSnapshot | None:
    """Return the feature snapshot for publication_id, or None if not yet extracted."""
    row = conn.execute(
        "SELECT * FROM content_feature_snapshots WHERE publication_id = ?",
        (publication_id,),
    ).fetchone()
    if row is None:
        return None
    return ContentFeatureSnapshot.from_row(row)


def extract_and_save(
    conn: sqlite3.Connection,
    publication_id: int,
) -> tuple[ContentFeatureSnapshot, bool]:
    """Extract features and persist.  Returns (snapshot, created).

    created=True when a new row was inserted; created=False when an existing
    snapshot was returned unchanged (idempotent).
    """
    existing = get_feature_snapshot(conn, publication_id)
    if existing is not None:
        return existing, False

    draft = extract_features(conn, publication_id)
    save_feature_snapshot(conn, draft)
    snapshot = get_feature_snapshot(conn, publication_id)
    assert snapshot is not None
    return snapshot, True
