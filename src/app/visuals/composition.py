"""Deterministic measurement of what a video actually looks like.

Phase 18E.  Everything in this module is computed from persisted beat lineage
(`visual_beats`, or an in-memory `VisualPlan` before it is saved).  No model is
asked to judge anything a structured column already answers, and no metric is
defined that the lineage cannot honestly support.

The distinction this module exists to draw
------------------------------------------
A locally generated *diagram* (a timeline, a comparison, a process chain, a
number card) carries information that the narration alone does not.  A locally
generated *statement card* is the narration's own words typeset over a colour
field.  Both are `media_type == "graphic"` and both were previously
indistinguishable to every downstream consumer — which is how a video that was
94% typeset narration passed the existing visual QA gate.

`app.visuals.graphics` dispatches on `visual_intent`: five intents have a
dedicated structural renderer, everything else falls through to
`_render_statement`.  That dispatch table IS the deterministic definition, so
this module reads it rather than restating it.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.visuals.constants import (
    MEDIA_GRAPHIC,
    MEDIA_ILLUSTRATION,
    MEDIA_PHOTO,
    MEDIA_VIDEO,
)
from app.visuals.graphics import _RENDERERS as _STRUCTURAL_GRAPHIC_RENDERERS

COMPOSITION_VERSION: str = "visual-composition-v1"

# ── Visual families ──────────────────────────────────────────────────────────
# A family is *what kind of thing the viewer is looking at*, which is a
# coarser and more durable question than which provider supplied it.

FAMILY_MOTION_FOOTAGE = "motion_footage"
FAMILY_PHOTOGRAPHIC = "photographic"
FAMILY_ILLUSTRATION = "illustration"
FAMILY_GENERATED_DIAGRAM = "generated_diagram"
FAMILY_TEXT_CARD = "text_card"
FAMILY_UNRESOLVED = "unresolved"

ALL_FAMILIES: tuple[str, ...] = (
    FAMILY_MOTION_FOOTAGE,
    FAMILY_PHOTOGRAPHIC,
    FAMILY_ILLUSTRATION,
    FAMILY_GENERATED_DIAGRAM,
    FAMILY_TEXT_CARD,
    FAMILY_UNRESOLVED,
)

# THE MEANINGFUL-VISUAL DEFINITION.
#
# A beat is *meaningful* when its realized family shows the viewer something
# beyond the narration re-typeset: retrieved footage, a photograph, an
# illustration, or a generated graphic whose intent has a structural renderer
# (number / comparison / process / timeline / diagram).
#
# A statement card and an unresolved beat are not meaningful.  This is a
# deterministic property of two persisted columns — resolved_media_type and
# visual_intent — and involves no judgement at measurement time.
MEANINGFUL_FAMILIES: frozenset[str] = frozenset(
    {
        FAMILY_MOTION_FOOTAGE,
        FAMILY_PHOTOGRAPHIC,
        FAMILY_ILLUSTRATION,
        FAMILY_GENERATED_DIAGRAM,
    }
)

# Families sourced from an external provider rather than generated locally.
RETRIEVED_FAMILIES: frozenset[str] = frozenset(
    {FAMILY_MOTION_FOOTAGE, FAMILY_PHOTOGRAPHIC, FAMILY_ILLUSTRATION}
)

# Intents that `app.visuals.graphics` renders structurally.  Read from the
# renderer dispatch table so the two can never drift apart.
STRUCTURAL_GRAPHIC_INTENTS: frozenset[str] = frozenset(_STRUCTURAL_GRAPHIC_RENDERERS)

# ── Fallback attribution ─────────────────────────────────────────────────────
# Why a beat did not end up with what it asked for.  The creative/provider
# split is the whole point: a strategy that deliberately chose a diagram must
# never be scored as though a provider had failed.

FALLBACK_CREATIVE = "creative"  # the planner preferred a graphic on purpose
FALLBACK_PROVIDER = "provider"  # retrieval or download did not deliver
FALLBACK_NONE = "none"

# fallback_reason (as written by app.visuals.engine) → attribution class.
_FALLBACK_CLASS: dict[str, str] = {
    "structural_intent_prefers_graphic": FALLBACK_CREATIVE,
    "all_candidates_rejected": FALLBACK_PROVIDER,
    "no_candidates_returned": FALLBACK_PROVIDER,
    "download_failed": FALLBACK_PROVIDER,
}

# A meaningful visual must be on screen within this window for the opening to
# count as visually established.
OPENING_WINDOW_MS: int = 4_000


def classify_family(media_type: str | None, visual_intent: str | None) -> str:
    """Return the visual family for a realized beat.

    Total: an unrecognised media type is `unresolved` rather than an exception,
    because a measurement pass must never be the thing that fails a render.
    """
    if not media_type:
        return FAMILY_UNRESOLVED
    if media_type == MEDIA_VIDEO:
        return FAMILY_MOTION_FOOTAGE
    if media_type == MEDIA_PHOTO:
        return FAMILY_PHOTOGRAPHIC
    if media_type == MEDIA_ILLUSTRATION:
        return FAMILY_ILLUSTRATION
    if media_type == MEDIA_GRAPHIC:
        if (visual_intent or "") in STRUCTURAL_GRAPHIC_INTENTS:
            return FAMILY_GENERATED_DIAGRAM
        return FAMILY_TEXT_CARD
    return FAMILY_UNRESOLVED


def classify_fallback(fallback_reason: str | None) -> str:
    """Return the attribution class for a beat's fallback reason."""
    if not fallback_reason:
        return FALLBACK_NONE
    return _FALLBACK_CLASS.get(fallback_reason, FALLBACK_PROVIDER)


def planned_family(media_type_preferences: list[str], visual_intent: str | None) -> str:
    """Return the family the planner asked for, before retrieval happened.

    The beat planner writes an ordered media-type preference list; its head is
    what the beat wanted.  A head of `graphic` means the planner deliberately
    wanted a generated visual, which for a structural intent is a diagram and
    otherwise a statement card.
    """
    head = next((m for m in media_type_preferences if m), None)
    return classify_family(head, visual_intent)


# ── Per-beat measurement record ──────────────────────────────────────────────


@dataclass
class BeatComposition:
    """One beat, reduced to the facts the quality model reasons about."""

    beat_index: int
    scene_index: int
    start_ms: int
    end_ms: int
    duration_ms: int
    visual_intent: str
    planned_family: str
    realized_family: str
    asset_key: str | None
    provider: str | None
    fallback_reason: str | None
    fallback_class: str

    @property
    def meaningful(self) -> bool:
        return self.realized_family in MEANINGFUL_FAMILIES

    @property
    def fallback_used(self) -> bool:
        return self.fallback_class != FALLBACK_NONE

    @property
    def planned_meaningful(self) -> bool:
        return self.planned_family in MEANINGFUL_FAMILIES

    def as_diagnostic(self) -> dict[str, Any]:
        """The operator-facing per-scene row: planned, realized, and why."""
        return {
            "beat_index": self.beat_index,
            "scene_index": self.scene_index,
            "start_ms": self.start_ms,
            "duration_ms": self.duration_ms,
            "visual_intent": self.visual_intent,
            "planned": self.planned_family,
            "realized": self.realized_family,
            "meaningful": self.meaningful,
            "provider": self.provider,
            "fallback_reason": self.fallback_reason,
            "fallback_class": self.fallback_class,
        }


# ── Whole-video measurement ──────────────────────────────────────────────────


@dataclass
class VisualComposition:
    """Deterministic description of one render's actual visual composition."""

    composition_version: str = COMPOSITION_VERSION
    beats: list[BeatComposition] = field(default_factory=list)

    total_beat_count: int = 0
    total_duration_ms: int = 0
    scene_count: int = 0

    meaningful_beat_count: int = 0
    meaningful_runtime_ms: int = 0
    text_card_beat_count: int = 0
    text_card_runtime_ms: int = 0
    unresolved_beat_count: int = 0

    family_runtime_ms: dict[str, int] = field(default_factory=dict)
    family_beat_count: dict[str, int] = field(default_factory=dict)
    dominant_family: str | None = None
    dominant_family_share: float = 0.0
    family_diversity: float = 0.0

    distinct_asset_count: int = 0
    reused_asset_beat_count: int = 0
    asset_reuse_ratio: float = 0.0

    visual_change_count: int = 0
    visual_changes_per_minute: float = 0.0
    avg_meaningful_gap_ms: float = 0.0
    max_meaningful_gap_ms: int = 0
    opening_meaningful_visual: bool = False

    planned_meaningful_beats: int = 0
    intentional_text_beats: int = 0
    fallback_beat_count: int = 0
    fallback_runtime_ms: int = 0
    provider_fallback_beats: int = 0
    creative_fallback_beats: int = 0
    provider_fallback_rate: float = 0.0
    fallback_reasons: dict[str, int] = field(default_factory=dict)

    # ── Derived shares (never stored twice; computed from the counters) ──

    @property
    def meaningful_runtime_pct(self) -> float:
        return (
            self.meaningful_runtime_ms / self.total_duration_ms if self.total_duration_ms else 0.0
        )

    @property
    def text_card_runtime_pct(self) -> float:
        return self.text_card_runtime_ms / self.total_duration_ms if self.total_duration_ms else 0.0

    @property
    def fallback_runtime_pct(self) -> float:
        return self.fallback_runtime_ms / self.total_duration_ms if self.total_duration_ms else 0.0

    def family_runtime_pct(self, family: str) -> float:
        if not self.total_duration_ms:
            return 0.0
        return self.family_runtime_ms.get(family, 0) / self.total_duration_ms

    @property
    def retrieved_imagery_runtime_pct(self) -> float:
        return sum(self.family_runtime_pct(f) for f in RETRIEVED_FAMILIES)

    @property
    def generated_diagram_runtime_pct(self) -> float:
        return self.family_runtime_pct(FAMILY_GENERATED_DIAGRAM)

    def deficient_beat_indexes(self) -> list[int]:
        """Beats that wanted a real visual and did not get one.

        This is the remediation target set: a beat whose planner asked for
        retrieved imagery but which fell back for a PROVIDER reason.  A beat
        that fell back for a creative reason is working as designed and is
        never regenerated.
        """
        return [
            b.beat_index
            for b in self.beats
            if b.fallback_class == FALLBACK_PROVIDER and not b.meaningful
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "composition_version": self.composition_version,
            "total_beat_count": self.total_beat_count,
            "total_duration_ms": self.total_duration_ms,
            "scene_count": self.scene_count,
            "meaningful_beat_count": self.meaningful_beat_count,
            "meaningful_runtime_ms": self.meaningful_runtime_ms,
            "meaningful_runtime_pct": round(self.meaningful_runtime_pct, 4),
            "text_card_beat_count": self.text_card_beat_count,
            "text_card_runtime_ms": self.text_card_runtime_ms,
            "text_card_runtime_pct": round(self.text_card_runtime_pct, 4),
            "unresolved_beat_count": self.unresolved_beat_count,
            "family_runtime_ms": dict(self.family_runtime_ms),
            "family_beat_count": dict(self.family_beat_count),
            "dominant_family": self.dominant_family,
            "dominant_family_share": round(self.dominant_family_share, 4),
            "family_diversity": round(self.family_diversity, 4),
            "distinct_asset_count": self.distinct_asset_count,
            "reused_asset_beat_count": self.reused_asset_beat_count,
            "asset_reuse_ratio": round(self.asset_reuse_ratio, 4),
            "visual_change_count": self.visual_change_count,
            "visual_changes_per_minute": round(self.visual_changes_per_minute, 4),
            "avg_meaningful_gap_ms": round(self.avg_meaningful_gap_ms, 2),
            "max_meaningful_gap_ms": self.max_meaningful_gap_ms,
            "opening_meaningful_visual": self.opening_meaningful_visual,
            "planned_meaningful_beats": self.planned_meaningful_beats,
            "intentional_text_beats": self.intentional_text_beats,
            "fallback_beat_count": self.fallback_beat_count,
            "fallback_runtime_ms": self.fallback_runtime_ms,
            "provider_fallback_beats": self.provider_fallback_beats,
            "creative_fallback_beats": self.creative_fallback_beats,
            "provider_fallback_rate": round(self.provider_fallback_rate, 4),
            "fallback_reasons": dict(self.fallback_reasons),
        }


def _normalised_entropy(shares: list[float]) -> float:
    """Shannon entropy of a share vector, normalised to [0, 1].

    One family covering everything → 0.  An even split across the families
    actually present → 1.  Normalising by the observed family count (not by
    len(ALL_FAMILIES)) keeps a two-family 50/50 video from being scored as
    poorly diverse merely because four other families exist in the enum.
    """
    present = [s for s in shares if s > 0]
    if len(present) < 2:
        return 0.0
    entropy = -sum(s * math.log(s) for s in present)
    return entropy / math.log(len(present))


def _gap_runs(beats: list[BeatComposition]) -> list[int]:
    """Maximal contiguous runs of non-meaningful runtime, in ms.

    Measured on the beat sequence rather than the wall clock: beats tile the
    video exactly, so a run of consecutive non-meaningful beats is exactly a
    stretch during which nothing meaningful was on screen.
    """
    runs: list[int] = []
    current = 0
    for beat in beats:
        if beat.meaningful:
            if current:
                runs.append(current)
            current = 0
        else:
            current += beat.duration_ms
    if current:
        runs.append(current)
    return runs


def compose(beats: list[BeatComposition]) -> VisualComposition:
    """Reduce per-beat records to whole-video composition metrics."""
    comp = VisualComposition(beats=list(beats))
    if not beats:
        return comp

    comp.total_beat_count = len(beats)
    comp.total_duration_ms = sum(b.duration_ms for b in beats)
    comp.scene_count = len({b.scene_index for b in beats})

    family_ms: Counter[str] = Counter()
    family_n: Counter[str] = Counter()
    reasons: Counter[str] = Counter()

    for beat in beats:
        family_ms[beat.realized_family] += beat.duration_ms
        family_n[beat.realized_family] += 1
        if beat.meaningful:
            comp.meaningful_beat_count += 1
            comp.meaningful_runtime_ms += beat.duration_ms
        if beat.realized_family == FAMILY_TEXT_CARD:
            comp.text_card_beat_count += 1
            comp.text_card_runtime_ms += beat.duration_ms
        if beat.realized_family == FAMILY_UNRESOLVED:
            comp.unresolved_beat_count += 1
        if beat.planned_meaningful:
            comp.planned_meaningful_beats += 1
        elif beat.planned_family == FAMILY_TEXT_CARD:
            comp.intentional_text_beats += 1
        if beat.fallback_used:
            comp.fallback_beat_count += 1
            comp.fallback_runtime_ms += beat.duration_ms
            reasons[beat.fallback_reason or "unknown"] += 1
            if beat.fallback_class == FALLBACK_PROVIDER:
                comp.provider_fallback_beats += 1
            else:
                comp.creative_fallback_beats += 1

    comp.family_runtime_ms = dict(family_ms)
    comp.family_beat_count = dict(family_n)
    comp.fallback_reasons = dict(reasons)
    comp.provider_fallback_rate = comp.provider_fallback_beats / comp.total_beat_count

    if comp.total_duration_ms:
        family, ms = family_ms.most_common(1)[0]
        comp.dominant_family = family
        comp.dominant_family_share = ms / comp.total_duration_ms
        comp.family_diversity = _normalised_entropy(
            [v / comp.total_duration_ms for v in family_ms.values()]
        )

    asset_keys = [b.asset_key for b in beats if b.asset_key]
    comp.distinct_asset_count = len(set(asset_keys))
    comp.reused_asset_beat_count = len(asset_keys) - comp.distinct_asset_count
    comp.asset_reuse_ratio = comp.reused_asset_beat_count / len(asset_keys) if asset_keys else 0.0

    # A "visual change" is the moment the picture is replaced by a different
    # one. Two consecutive beats sharing an asset are one continuous visual,
    # however the beat planner divided the narration under them.
    changes = 1
    for prev, curr in zip(beats, beats[1:], strict=False):
        if curr.asset_key != prev.asset_key or curr.asset_key is None:
            changes += 1
    comp.visual_change_count = changes
    minutes = comp.total_duration_ms / 60_000.0
    comp.visual_changes_per_minute = changes / minutes if minutes else 0.0

    runs = _gap_runs(beats)
    comp.max_meaningful_gap_ms = max(runs) if runs else 0
    comp.avg_meaningful_gap_ms = sum(runs) / len(runs) if runs else 0.0

    comp.opening_meaningful_visual = any(
        b.meaningful and b.start_ms < OPENING_WINDOW_MS for b in beats
    )

    return comp


# ── Sources ──────────────────────────────────────────────────────────────────


def composition_from_plan(plan: Any) -> VisualComposition:
    """Measure an in-memory `VisualPlan` (pre-persistence, pre-render)."""
    records = [
        BeatComposition(
            beat_index=r.beat.beat_index,
            scene_index=r.beat.scene_index,
            start_ms=r.beat.start_ms,
            end_ms=r.beat.end_ms,
            duration_ms=r.beat.duration_ms,
            visual_intent=r.beat.visual_intent,
            planned_family=planned_family(
                list(r.beat.media_type_preferences or []), r.beat.visual_intent
            ),
            realized_family=(
                classify_family(r.media_type, r.beat.visual_intent)
                if r.resolved
                else FAMILY_UNRESOLVED
            ),
            asset_key=r.asset_key,
            provider=r.provider,
            fallback_reason=r.fallback_reason,
            fallback_class=classify_fallback(r.fallback_reason),
        )
        for r in sorted(plan.resolutions, key=lambda r: r.beat.beat_index)
    ]
    return compose(records)


def composition_from_scene_manifest(
    conn: sqlite3.Connection, scene_manifest_id: int
) -> VisualComposition:
    """Measure a persisted render from its `visual_beats` lineage.

    Read-only.  This is the path used for historical assessment and for
    re-assessment after a restart, and it must produce byte-identical metrics
    to `composition_from_plan` for the same beats — the persisted columns are
    written verbatim from the plan.
    """
    rows = conn.execute(
        "SELECT beat_index, scene_index, start_ms, end_ms, duration_ms, visual_intent, "
        "       media_type_preferences_json, resolved_media_type, resolved_provider, "
        "       resolved_asset_key, resolved_local_path, fallback_reason "
        "FROM visual_beats WHERE scene_manifest_id = ? ORDER BY beat_index",
        (scene_manifest_id,),
    ).fetchall()

    records: list[BeatComposition] = []
    for row in rows:
        try:
            prefs = json.loads(row["media_type_preferences_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            prefs = []
        resolved = bool(row["resolved_local_path"])
        records.append(
            BeatComposition(
                beat_index=row["beat_index"],
                scene_index=row["scene_index"],
                start_ms=row["start_ms"],
                end_ms=row["end_ms"],
                duration_ms=row["duration_ms"],
                visual_intent=row["visual_intent"] or "",
                planned_family=planned_family(prefs, row["visual_intent"]),
                realized_family=(
                    classify_family(row["resolved_media_type"], row["visual_intent"])
                    if resolved
                    else FAMILY_UNRESOLVED
                ),
                asset_key=row["resolved_asset_key"],
                provider=row["resolved_provider"],
                fallback_reason=row["fallback_reason"],
                fallback_class=classify_fallback(row["fallback_reason"]),
            )
        )
    return compose(records)
