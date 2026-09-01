"""Persistence for render-level visual quality assessments (Phase 18E).

One row per render manifest, upserted.  Reassessing a render overwrites its
verdict rather than appending a second one, which is what makes a restart
mid-preflight safe: the assessment is a *derived measurement* of an immutable
render, so there is exactly one correct answer for it at any policy version.

Remediation bookkeeping is the deliberate exception — `remediation_attempts`
is never reset by a reassessment, because the point of the counter is to
survive exactly the restart that would otherwise re-spend provider budget.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.visuals.composition import (
    COMPOSITION_VERSION,
    VisualComposition,
    composition_from_scene_manifest,
)
from app.visuals.quality import (
    ASSESSMENT_VERSION,
    DEFAULT_THRESHOLDS,
    QUALITY_POLICY_VERSION,
    VQ_BLOCKED,
    VisualQualityAssessment,
    VisualQualityThresholds,
    assess_composition,
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
    )


def assessment_input_hash(
    *, render_manifest_id: int, composition: VisualComposition, visual_style: str | None
) -> str:
    """Stable identity for (this render, this measurement, this policy).

    Changes only when the render's own beats change or a version constant is
    bumped — so an unchanged render reassessed twice produces the same hash,
    and a policy revision is visibly a different assessment rather than a
    silent overwrite.
    """
    payload = json.dumps(
        {
            "render_manifest_id": render_manifest_id,
            "assessment_version": ASSESSMENT_VERSION,
            "composition_version": COMPOSITION_VERSION,
            "policy_version": QUALITY_POLICY_VERSION,
            "visual_style": visual_style,
            "composition": composition.as_dict(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


@dataclass
class RenderVisualAssessment:
    """A persisted assessment row, as read back."""

    id: int
    render_manifest_id: int
    scene_manifest_id: int
    workspace_id: str | None
    channel_id: str | None
    experiment_id: str | None
    publication_id: int | None
    assessment_version: str
    composition_version: str
    policy_version: str
    status: str
    total_beat_count: int
    total_duration_ms: int
    scene_count: int
    meaningful_beat_count: int
    meaningful_runtime_ms: int
    text_card_beat_count: int
    text_card_runtime_ms: int
    unresolved_beat_count: int
    family_runtime: dict[str, int]
    family_beat_count: dict[str, int]
    dominant_family: str | None
    dominant_family_share: float
    family_diversity: float
    distinct_asset_count: int
    reused_asset_beat_count: int
    asset_reuse_ratio: float
    visual_change_count: int
    visual_changes_per_minute: float
    avg_meaningful_gap_ms: float
    max_meaningful_gap_ms: int
    opening_meaningful_visual: bool
    visual_style: str | None
    planned_meaningful_beats: int
    intentional_text_beats: int
    fallback_beat_count: int
    fallback_runtime_ms: int
    provider_fallback_beats: int
    creative_fallback_beats: int
    provider_fallback_rate: float
    fallback_reasons: dict[str, int]
    findings: list[dict[str, Any]]
    scene_diagnostics: list[dict[str, Any]]
    remediation_attempts: int
    remediated: bool
    input_hash: str
    created_at: str
    updated_at: str

    @property
    def blocked(self) -> bool:
        return self.status == VQ_BLOCKED

    @property
    def meaningful_runtime_pct(self) -> float:
        return (
            self.meaningful_runtime_ms / self.total_duration_ms if self.total_duration_ms else 0.0
        )

    @property
    def text_card_runtime_pct(self) -> float:
        return self.text_card_runtime_ms / self.total_duration_ms if self.total_duration_ms else 0.0

    @property
    def blocking_findings(self) -> list[dict[str, Any]]:
        return [f for f in self.findings if f.get("severity") == "blocking"]

    @property
    def warning_findings(self) -> list[dict[str, Any]]:
        return [f for f in self.findings if f.get("severity") == "warning"]


def _loads(raw: Any, default: Any) -> Any:
    try:
        return json.loads(raw) if raw else default
    except (json.JSONDecodeError, TypeError):
        return default


def _row_to_assessment(row: sqlite3.Row) -> RenderVisualAssessment:
    return RenderVisualAssessment(
        id=row["id"],
        render_manifest_id=row["render_manifest_id"],
        scene_manifest_id=row["scene_manifest_id"],
        workspace_id=row["workspace_id"],
        channel_id=row["channel_id"],
        experiment_id=row["experiment_id"],
        publication_id=row["publication_id"],
        assessment_version=row["assessment_version"],
        composition_version=row["composition_version"],
        policy_version=row["policy_version"],
        status=row["status"],
        total_beat_count=row["total_beat_count"],
        total_duration_ms=row["total_duration_ms"],
        scene_count=row["scene_count"],
        meaningful_beat_count=row["meaningful_beat_count"],
        meaningful_runtime_ms=row["meaningful_runtime_ms"],
        text_card_beat_count=row["text_card_beat_count"],
        text_card_runtime_ms=row["text_card_runtime_ms"],
        unresolved_beat_count=row["unresolved_beat_count"],
        family_runtime=_loads(row["family_runtime_json"], {}),
        family_beat_count=_loads(row["family_beat_count_json"], {}),
        dominant_family=row["dominant_family"],
        dominant_family_share=row["dominant_family_share"],
        family_diversity=row["family_diversity"],
        distinct_asset_count=row["distinct_asset_count"],
        reused_asset_beat_count=row["reused_asset_beat_count"],
        asset_reuse_ratio=row["asset_reuse_ratio"],
        visual_change_count=row["visual_change_count"],
        visual_changes_per_minute=row["visual_changes_per_minute"],
        avg_meaningful_gap_ms=row["avg_meaningful_gap_ms"],
        max_meaningful_gap_ms=row["max_meaningful_gap_ms"],
        opening_meaningful_visual=bool(row["opening_meaningful_visual"]),
        visual_style=row["visual_style"],
        planned_meaningful_beats=row["planned_meaningful_beats"],
        intentional_text_beats=row["intentional_text_beats"],
        fallback_beat_count=row["fallback_beat_count"],
        fallback_runtime_ms=row["fallback_runtime_ms"],
        provider_fallback_beats=row["provider_fallback_beats"],
        creative_fallback_beats=row["creative_fallback_beats"],
        provider_fallback_rate=row["provider_fallback_rate"],
        fallback_reasons=_loads(row["fallback_reasons_json"], {}),
        findings=_loads(row["findings_json"], []),
        scene_diagnostics=_loads(row["scene_diagnostics_json"], []),
        remediation_attempts=row["remediation_attempts"],
        remediated=bool(row["remediated"]),
        input_hash=row["input_hash"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def save_assessment(
    conn: sqlite3.Connection,
    assessment: VisualQualityAssessment,
    *,
    render_manifest_id: int,
    scene_manifest_id: int,
    workspace_id: str | None = None,
    channel_id: str | None = None,
    experiment_id: str | None = None,
    publication_id: int | None = None,
    remediation_attempts: int | None = None,
    remediated: bool | None = None,
) -> RenderVisualAssessment | None:
    """Upsert the assessment for one render manifest.

    Idempotent by construction (UNIQUE on render_manifest_id).  Passing
    `remediation_attempts`/`remediated` as None preserves whatever is already
    stored, so a plain reassessment can never reset the spend guard.
    """
    if not _table_exists(conn, "render_visual_assessments"):
        return None

    comp = assessment.composition
    now = _now()
    input_hash = assessment_input_hash(
        render_manifest_id=render_manifest_id,
        composition=comp,
        visual_style=assessment.visual_style,
    )

    existing = conn.execute(
        "SELECT remediation_attempts, remediated, created_at FROM render_visual_assessments "
        "WHERE render_manifest_id = ?",
        (render_manifest_id,),
    ).fetchone()

    attempts = (
        remediation_attempts
        if remediation_attempts is not None
        else (existing["remediation_attempts"] if existing else 0)
    )
    was_remediated = (
        remediated
        if remediated is not None
        else bool(existing["remediated"])
        if existing
        else False
    )
    created_at = existing["created_at"] if existing else now

    conn.execute(
        """
        INSERT INTO render_visual_assessments (
            render_manifest_id, scene_manifest_id, workspace_id, channel_id,
            experiment_id, publication_id,
            assessment_version, composition_version, policy_version, status,
            total_beat_count, total_duration_ms, scene_count,
            meaningful_beat_count, meaningful_runtime_ms,
            text_card_beat_count, text_card_runtime_ms, unresolved_beat_count,
            family_runtime_json, family_beat_count_json,
            dominant_family, dominant_family_share, family_diversity,
            distinct_asset_count, reused_asset_beat_count, asset_reuse_ratio,
            visual_change_count, visual_changes_per_minute,
            avg_meaningful_gap_ms, max_meaningful_gap_ms, opening_meaningful_visual,
            visual_style, planned_meaningful_beats, intentional_text_beats,
            fallback_beat_count, fallback_runtime_ms,
            provider_fallback_beats, creative_fallback_beats, provider_fallback_rate,
            fallback_reasons_json, findings_json, scene_diagnostics_json,
            remediation_attempts, remediated, input_hash, created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(render_manifest_id) DO UPDATE SET
            scene_manifest_id = excluded.scene_manifest_id,
            workspace_id = COALESCE(excluded.workspace_id, render_visual_assessments.workspace_id),
            channel_id = COALESCE(excluded.channel_id, render_visual_assessments.channel_id),
            experiment_id = COALESCE(excluded.experiment_id,
                                     render_visual_assessments.experiment_id),
            publication_id = COALESCE(excluded.publication_id,
                                      render_visual_assessments.publication_id),
            assessment_version = excluded.assessment_version,
            composition_version = excluded.composition_version,
            policy_version = excluded.policy_version,
            status = excluded.status,
            total_beat_count = excluded.total_beat_count,
            total_duration_ms = excluded.total_duration_ms,
            scene_count = excluded.scene_count,
            meaningful_beat_count = excluded.meaningful_beat_count,
            meaningful_runtime_ms = excluded.meaningful_runtime_ms,
            text_card_beat_count = excluded.text_card_beat_count,
            text_card_runtime_ms = excluded.text_card_runtime_ms,
            unresolved_beat_count = excluded.unresolved_beat_count,
            family_runtime_json = excluded.family_runtime_json,
            family_beat_count_json = excluded.family_beat_count_json,
            dominant_family = excluded.dominant_family,
            dominant_family_share = excluded.dominant_family_share,
            family_diversity = excluded.family_diversity,
            distinct_asset_count = excluded.distinct_asset_count,
            reused_asset_beat_count = excluded.reused_asset_beat_count,
            asset_reuse_ratio = excluded.asset_reuse_ratio,
            visual_change_count = excluded.visual_change_count,
            visual_changes_per_minute = excluded.visual_changes_per_minute,
            avg_meaningful_gap_ms = excluded.avg_meaningful_gap_ms,
            max_meaningful_gap_ms = excluded.max_meaningful_gap_ms,
            opening_meaningful_visual = excluded.opening_meaningful_visual,
            visual_style = excluded.visual_style,
            planned_meaningful_beats = excluded.planned_meaningful_beats,
            intentional_text_beats = excluded.intentional_text_beats,
            fallback_beat_count = excluded.fallback_beat_count,
            fallback_runtime_ms = excluded.fallback_runtime_ms,
            provider_fallback_beats = excluded.provider_fallback_beats,
            creative_fallback_beats = excluded.creative_fallback_beats,
            provider_fallback_rate = excluded.provider_fallback_rate,
            fallback_reasons_json = excluded.fallback_reasons_json,
            findings_json = excluded.findings_json,
            scene_diagnostics_json = excluded.scene_diagnostics_json,
            remediation_attempts = excluded.remediation_attempts,
            remediated = excluded.remediated,
            input_hash = excluded.input_hash,
            updated_at = excluded.updated_at
        """,
        (
            render_manifest_id,
            scene_manifest_id,
            workspace_id,
            channel_id,
            experiment_id,
            publication_id,
            assessment.assessment_version,
            comp.composition_version,
            assessment.policy_version,
            assessment.status,
            comp.total_beat_count,
            comp.total_duration_ms,
            comp.scene_count,
            comp.meaningful_beat_count,
            comp.meaningful_runtime_ms,
            comp.text_card_beat_count,
            comp.text_card_runtime_ms,
            comp.unresolved_beat_count,
            json.dumps(comp.family_runtime_ms, sort_keys=True),
            json.dumps(comp.family_beat_count, sort_keys=True),
            comp.dominant_family,
            comp.dominant_family_share,
            comp.family_diversity,
            comp.distinct_asset_count,
            comp.reused_asset_beat_count,
            comp.asset_reuse_ratio,
            comp.visual_change_count,
            comp.visual_changes_per_minute,
            comp.avg_meaningful_gap_ms,
            comp.max_meaningful_gap_ms,
            1 if comp.opening_meaningful_visual else 0,
            assessment.visual_style,
            comp.planned_meaningful_beats,
            comp.intentional_text_beats,
            comp.fallback_beat_count,
            comp.fallback_runtime_ms,
            comp.provider_fallback_beats,
            comp.creative_fallback_beats,
            comp.provider_fallback_rate,
            json.dumps(comp.fallback_reasons, sort_keys=True),
            json.dumps([f.as_dict() for f in assessment.findings]),
            json.dumps([b.as_diagnostic() for b in comp.beats]),
            attempts,
            1 if was_remediated else 0,
            input_hash,
            created_at,
            now,
        ),
    )
    conn.commit()
    return get_assessment(conn, render_manifest_id)


def get_assessment(
    conn: sqlite3.Connection, render_manifest_id: int
) -> RenderVisualAssessment | None:
    if not _table_exists(conn, "render_visual_assessments"):
        return None
    row = conn.execute(
        "SELECT * FROM render_visual_assessments WHERE render_manifest_id = ?",
        (render_manifest_id,),
    ).fetchone()
    return _row_to_assessment(row) if row else None


def get_assessment_for_publication(
    conn: sqlite3.Connection, publication_id: int
) -> RenderVisualAssessment | None:
    """Read a publication's assessment, joining via its plan when unlinked.

    The publication_id backfill happens after upload, so a publication created
    before this phase (or between upload and backfill) is still resolvable
    through publishing_plans.render_manifest_id.
    """
    if not _table_exists(conn, "render_visual_assessments"):
        return None
    row = conn.execute(
        "SELECT rva.* FROM render_visual_assessments rva "
        "JOIN publishing_plans pp ON pp.render_manifest_id = rva.render_manifest_id "
        "JOIN publications p ON p.publishing_plan_id = pp.id "
        "WHERE p.id = ?",
        (publication_id,),
    ).fetchone()
    return _row_to_assessment(row) if row else None


def attach_publication(
    conn: sqlite3.Connection, *, render_manifest_id: int, publication_id: int
) -> None:
    """Backfill the publication id once the render has actually been published."""
    if not _table_exists(conn, "render_visual_assessments"):
        return
    conn.execute(
        "UPDATE render_visual_assessments SET publication_id = ?, updated_at = ? "
        "WHERE render_manifest_id = ? AND publication_id IS NULL",
        (publication_id, _now(), render_manifest_id),
    )
    conn.commit()


def record_remediation_attempt(conn: sqlite3.Connection, render_manifest_id: int) -> int:
    """Increment and return the persisted remediation counter.

    Committed before any provider work begins, so a crash mid-remediation
    still consumes its budget rather than allowing the next run to re-spend.
    """
    if not _table_exists(conn, "render_visual_assessments"):
        return 0
    conn.execute(
        "UPDATE render_visual_assessments "
        "SET remediation_attempts = remediation_attempts + 1, updated_at = ? "
        "WHERE render_manifest_id = ?",
        (_now(), render_manifest_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT remediation_attempts FROM render_visual_assessments WHERE render_manifest_id = ?",
        (render_manifest_id,),
    ).fetchone()
    return row["remediation_attempts"] if row else 0


def assess_render(
    conn: sqlite3.Connection,
    *,
    render_manifest_id: int,
    scene_manifest_id: int,
    workspace_id: str | None = None,
    channel_id: str | None = None,
    experiment_id: str | None = None,
    visual_style: str | None = None,
    thresholds: VisualQualityThresholds = DEFAULT_THRESHOLDS,
    persist: bool = True,
) -> tuple[VisualQualityAssessment, RenderVisualAssessment | None]:
    """Measure and (optionally) persist a render's visual quality.

    `persist=False` is the read-only path used for historical analysis: it
    measures an existing render without writing anything at all.
    """
    composition = composition_from_scene_manifest(conn, scene_manifest_id)
    assessment = assess_composition(composition, visual_style=visual_style, thresholds=thresholds)
    stored = None
    if persist:
        stored = save_assessment(
            conn,
            assessment,
            render_manifest_id=render_manifest_id,
            scene_manifest_id=scene_manifest_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            experiment_id=experiment_id,
        )
    return assessment, stored
