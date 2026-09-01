"""Phase 18E — Visual Quality Intelligence, preflight, learning, and isolation.

Structured to match the phase's own claims, in the order those claims have to
hold: measure honestly, distinguish intent from outcome, block on the floors,
teach the learner, and keep tests structurally unable to touch the live system.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from app.core.database import open_db
from app.visuals.composition import (
    FALLBACK_CREATIVE,
    FALLBACK_NONE,
    FALLBACK_PROVIDER,
    FAMILY_GENERATED_DIAGRAM,
    FAMILY_MOTION_FOOTAGE,
    FAMILY_PHOTOGRAPHIC,
    FAMILY_TEXT_CARD,
    FAMILY_UNRESOLVED,
    BeatComposition,
    classify_fallback,
    classify_family,
    compose,
    composition_from_scene_manifest,
    planned_family,
)
from app.visuals.quality import (
    CODE_LONG_GAP,
    CODE_MEANINGFUL_FLOOR,
    CODE_NO_MEANINGFUL,
    CODE_PROVIDER_FAILURE,
    CODE_UNRESOLVED,
    CODE_WEAK_OPENING,
    VQ_BLOCKED,
    VQ_PASS,
    VQ_PASS_WITH_WARNINGS,
    assess_composition,
)

# ── Builders ─────────────────────────────────────────────────────────────────


def _beat(
    index: int,
    *,
    start_ms: int,
    duration_ms: int = 4_000,
    family: str = FAMILY_MOTION_FOOTAGE,
    planned: str = FAMILY_MOTION_FOOTAGE,
    intent: str = "entity",
    asset_key: str | None = None,
    provider: str = "pexels",
    fallback_reason: str | None = None,
    scene_index: int = 0,
) -> BeatComposition:
    return BeatComposition(
        beat_index=index,
        scene_index=scene_index,
        start_ms=start_ms,
        end_ms=start_ms + duration_ms,
        duration_ms=duration_ms,
        visual_intent=intent,
        planned_family=planned,
        realized_family=family,
        asset_key=asset_key if asset_key is not None else f"{provider}:{index}",
        provider=provider,
        fallback_reason=fallback_reason,
        fallback_class=classify_fallback(fallback_reason),
    )


def _healthy_beats(n: int = 15) -> list[BeatComposition]:
    """A diverse render: footage, photography, and purposeful diagrams."""
    families = [FAMILY_MOTION_FOOTAGE, FAMILY_PHOTOGRAPHIC, FAMILY_GENERATED_DIAGRAM]
    beats = []
    for i in range(n):
        family = families[i % 3]
        is_diagram = family == FAMILY_GENERATED_DIAGRAM
        beats.append(
            _beat(
                i,
                start_ms=i * 4_000,
                family=family,
                planned=FAMILY_GENERATED_DIAGRAM if is_diagram else FAMILY_MOTION_FOOTAGE,
                intent="timeline" if is_diagram else "entity",
                provider="programmatic" if is_diagram else "pexels",
                fallback_reason="structural_intent_prefers_graphic" if is_diagram else None,
            )
        )
    return beats


def _fallback_dominated_beats(n: int = 15, meaningful: int = 3) -> list[BeatComposition]:
    """The failure mode this phase exists to catch: retrieval failed everywhere."""
    beats = []
    for i in range(n):
        if i < meaningful:
            beats.append(_beat(i, start_ms=i * 4_000))
        else:
            beats.append(
                _beat(
                    i,
                    start_ms=i * 4_000,
                    family=FAMILY_TEXT_CARD,
                    planned=FAMILY_MOTION_FOOTAGE,
                    intent="action",
                    provider="programmatic",
                    asset_key=f"programmatic:{i}",
                    fallback_reason="all_candidates_rejected",
                )
            )
    return beats


# ── Family classification ────────────────────────────────────────────────────


class TestFamilyClassification:
    def test_media_types_map_to_families(self):
        assert classify_family("video", "entity") == FAMILY_MOTION_FOOTAGE
        assert classify_family("photo", "entity") == FAMILY_PHOTOGRAPHIC
        assert classify_family("illustration", "entity") == "illustration"
        assert classify_family(None, "entity") == FAMILY_UNRESOLVED
        assert classify_family("something_new", "entity") == FAMILY_UNRESOLVED

    @pytest.mark.parametrize("intent", ["number", "comparison", "process", "timeline", "diagram"])
    def test_structural_intents_make_a_graphic_a_diagram(self, intent):
        assert classify_family("graphic", intent) == FAMILY_GENERATED_DIAGRAM

    @pytest.mark.parametrize("intent", ["entity", "action", "concept", "cta", "emphasis"])
    def test_non_structural_intents_make_a_graphic_a_text_card(self, intent):
        # This is the distinction the previous QA gate could not draw, and the
        # reason a 94%-typeset video passed it.
        assert classify_family("graphic", intent) == FAMILY_TEXT_CARD

    def test_definition_tracks_the_renderer_dispatch_table(self):
        """The intent set is read from graphics._RENDERERS, never restated."""
        from app.visuals.composition import STRUCTURAL_GRAPHIC_INTENTS
        from app.visuals.graphics import _RENDERERS

        assert STRUCTURAL_GRAPHIC_INTENTS == frozenset(_RENDERERS)

    def test_planned_family_reads_the_head_of_the_preference_list(self):
        assert planned_family(["video", "photo", "graphic"], "entity") == FAMILY_MOTION_FOOTAGE
        assert planned_family(["graphic", "video"], "timeline") == FAMILY_GENERATED_DIAGRAM
        assert planned_family(["graphic", "video"], "entity") == FAMILY_TEXT_CARD
        assert planned_family([], "entity") == FAMILY_UNRESOLVED


# ── Fallback attribution ─────────────────────────────────────────────────────


class TestFallbackAttribution:
    def test_structural_preference_is_creative_not_provider(self):
        assert classify_fallback("structural_intent_prefers_graphic") == FALLBACK_CREATIVE

    @pytest.mark.parametrize(
        "reason", ["all_candidates_rejected", "no_candidates_returned", "download_failed"]
    )
    def test_retrieval_failures_are_provider(self, reason):
        assert classify_fallback(reason) == FALLBACK_PROVIDER

    def test_no_reason_is_no_fallback(self):
        assert classify_fallback(None) == FALLBACK_NONE
        assert classify_fallback("") == FALLBACK_NONE

    def test_unknown_reason_is_treated_as_provider_failure(self):
        # Fail toward "the pipeline broke" rather than "we meant it": the
        # opposite default would let a new failure mode masquerade as intent.
        assert classify_fallback("some_future_reason") == FALLBACK_PROVIDER

    def test_engine_reason_constants_are_all_classified(self):
        from app.visuals import engine

        for reason in (
            engine.FALLBACK_NO_CANDIDATES,
            engine.FALLBACK_ALL_REJECTED,
            engine.FALLBACK_DOWNLOAD_FAILED,
            engine.FALLBACK_STRUCTURAL_PREFERENCE,
        ):
            from app.visuals.composition import _FALLBACK_CLASS

            assert reason in _FALLBACK_CLASS, f"{reason} has no explicit attribution"

    def test_creative_and_provider_fallbacks_are_counted_separately(self):
        beats = [
            _beat(0, start_ms=0),
            _beat(
                1,
                start_ms=4_000,
                family=FAMILY_GENERATED_DIAGRAM,
                intent="timeline",
                provider="programmatic",
                fallback_reason="structural_intent_prefers_graphic",
            ),
            _beat(
                2,
                start_ms=8_000,
                family=FAMILY_TEXT_CARD,
                intent="action",
                provider="programmatic",
                fallback_reason="all_candidates_rejected",
            ),
        ]
        comp = compose(beats)
        assert comp.creative_fallback_beats == 1
        assert comp.provider_fallback_beats == 1
        assert comp.fallback_beat_count == 2


# ── Composition metrics ──────────────────────────────────────────────────────


class TestCompositionMetrics:
    def test_runtime_percentages_are_duration_weighted_not_beat_counted(self):
        beats = [
            _beat(0, start_ms=0, duration_ms=2_000),
            _beat(
                1,
                start_ms=2_000,
                duration_ms=18_000,
                family=FAMILY_TEXT_CARD,
                intent="action",
                provider="programmatic",
                fallback_reason="all_candidates_rejected",
            ),
        ]
        comp = compose(beats)
        assert comp.total_duration_ms == 20_000
        # Half the beats but a tenth of the runtime.
        assert comp.meaningful_runtime_pct == pytest.approx(0.1)
        assert comp.text_card_runtime_pct == pytest.approx(0.9)

    def test_distinct_and_reused_assets(self):
        beats = [
            _beat(0, start_ms=0, asset_key="pexels:a"),
            _beat(1, start_ms=4_000, asset_key="pexels:a"),
            _beat(2, start_ms=8_000, asset_key="pexels:b"),
        ]
        comp = compose(beats)
        assert comp.distinct_asset_count == 2
        assert comp.reused_asset_beat_count == 1
        assert comp.asset_reuse_ratio == pytest.approx(1 / 3)

    def test_consecutive_beats_sharing_an_asset_are_one_visual_change(self):
        beats = [
            _beat(0, start_ms=0, asset_key="pexels:a"),
            _beat(1, start_ms=4_000, asset_key="pexels:a"),
            _beat(2, start_ms=8_000, asset_key="pexels:b"),
        ]
        # Three beats, but the picture only changes twice: at the start and at
        # beat 2. Counting beats here would overstate a static video's pacing.
        assert compose(beats).visual_change_count == 2

    def test_longest_gap_is_the_longest_contiguous_non_meaningful_run(self):
        beats = [
            _beat(0, start_ms=0),
            _beat(1, start_ms=4_000, family=FAMILY_TEXT_CARD, intent="action"),
            _beat(2, start_ms=8_000, family=FAMILY_TEXT_CARD, intent="action"),
            _beat(3, start_ms=12_000),
            _beat(4, start_ms=16_000, family=FAMILY_TEXT_CARD, intent="action"),
        ]
        comp = compose(beats)
        assert comp.max_meaningful_gap_ms == 8_000  # beats 1+2
        assert comp.avg_meaningful_gap_ms == pytest.approx(6_000)  # (8000 + 4000) / 2

    def test_gap_is_zero_when_every_beat_is_meaningful(self):
        comp = compose([_beat(i, start_ms=i * 4_000) for i in range(4)])
        assert comp.max_meaningful_gap_ms == 0
        assert comp.avg_meaningful_gap_ms == 0.0

    def test_opening_visual_requires_a_meaningful_beat_in_the_first_seconds(self):
        strong = compose([_beat(0, start_ms=0), _beat(1, start_ms=4_000)])
        assert strong.opening_meaningful_visual is True

        weak = compose(
            [
                _beat(0, start_ms=0, duration_ms=6_000, family=FAMILY_TEXT_CARD, intent="action"),
                _beat(1, start_ms=6_000),
            ]
        )
        assert weak.opening_meaningful_visual is False

    def test_diversity_is_zero_for_one_family_and_one_for_an_even_split(self):
        single = compose([_beat(i, start_ms=i * 4_000) for i in range(4)])
        assert single.family_diversity == pytest.approx(0.0)

        split = compose(
            [
                _beat(0, start_ms=0),
                _beat(1, start_ms=4_000, family=FAMILY_PHOTOGRAPHIC),
            ]
        )
        assert split.family_diversity == pytest.approx(1.0)

    def test_deficient_beats_exclude_intentional_diagrams(self):
        beats = [
            _beat(
                0,
                start_ms=0,
                family=FAMILY_GENERATED_DIAGRAM,
                intent="timeline",
                provider="programmatic",
                fallback_reason="structural_intent_prefers_graphic",
            ),
            _beat(
                1,
                start_ms=4_000,
                family=FAMILY_TEXT_CARD,
                intent="action",
                provider="programmatic",
                fallback_reason="all_candidates_rejected",
            ),
        ]
        # Only the provider failure is a remediation target. Regenerating a
        # deliberately chosen diagram would be the system fighting its own plan.
        assert compose(beats).deficient_beat_indexes() == [1]

    def test_empty_composition_is_safe(self):
        comp = compose([])
        assert comp.total_beat_count == 0
        assert comp.meaningful_runtime_pct == 0.0
        assert comp.deficient_beat_indexes() == []

    def test_measurement_is_deterministic(self):
        beats = _healthy_beats()
        assert compose(beats).as_dict() == compose(list(beats)).as_dict()


# ── Quality policy ───────────────────────────────────────────────────────────


class TestQualityPolicy:
    def test_healthy_diverse_render_passes_cleanly(self):
        result = assess_composition(compose(_healthy_beats()))
        assert result.status == VQ_PASS
        assert result.findings == []

    def test_fallback_dominated_render_is_blocked(self):
        result = assess_composition(compose(_fallback_dominated_beats()))
        assert result.status == VQ_BLOCKED
        codes = {f.code for f in result.blocking}
        assert CODE_MEANINGFUL_FLOOR in codes
        assert CODE_PROVIDER_FAILURE in codes

    def test_mildly_weak_render_warns_without_blocking(self):
        # 60% meaningful runtime with a weak opening: below the comfort level,
        # comfortably above every floor.
        beats = [
            _beat(
                0,
                start_ms=0,
                duration_ms=5_000,
                family=FAMILY_TEXT_CARD,
                intent="action",
                provider="programmatic",
                fallback_reason="all_candidates_rejected",
            ),
        ]
        beats += [
            _beat(i, start_ms=5_000 + (i - 1) * 5_000, duration_ms=5_000) for i in range(1, 7)
        ]
        beats.append(
            _beat(
                7,
                start_ms=35_000,
                duration_ms=5_000,
                family=FAMILY_TEXT_CARD,
                intent="action",
                provider="programmatic",
            )
        )
        result = assess_composition(compose(beats))
        assert result.status == VQ_PASS_WITH_WARNINGS
        assert result.blocking == []
        assert CODE_WEAK_OPENING in {f.code for f in result.warnings}

    def test_blocking_findings_carry_the_actual_numbers_as_evidence(self):
        result = assess_composition(compose(_fallback_dominated_beats()))
        finding = next(f for f in result.blocking if f.code == CODE_MEANINGFUL_FLOOR)
        # The operator must be able to read WHY, not just THAT.
        assert "%" in finding.message
        assert "meaningful_runtime_pct" in finding.evidence
        assert finding.evidence["meaningful_runtime_pct"] < 0.25

    def test_long_gap_blocks(self):
        beats = [_beat(0, start_ms=0, duration_ms=4_000)]
        beats += [
            _beat(
                i,
                start_ms=4_000 + (i - 1) * 5_000,
                duration_ms=5_000,
                family=FAMILY_TEXT_CARD,
                intent="action",
                provider="programmatic",
            )
            for i in range(1, 6)
        ]
        beats.append(_beat(6, start_ms=29_000, duration_ms=20_000))
        result = assess_composition(compose(beats))
        assert CODE_LONG_GAP in {f.code for f in result.blocking}

    def test_gap_rules_do_not_fire_on_a_very_short_clip(self):
        beats = [
            _beat(0, start_ms=0, duration_ms=6_000),
            _beat(
                1,
                start_ms=6_000,
                duration_ms=6_000,
                family=FAMILY_TEXT_CARD,
                intent="action",
                provider="programmatic",
            ),
        ]
        result = assess_composition(compose(beats))
        assert CODE_LONG_GAP not in {f.code for f in result.findings}

    def test_unresolved_beats_block(self):
        beats = _healthy_beats()
        beats[3].realized_family = FAMILY_UNRESOLVED
        result = assess_composition(compose(beats))
        assert CODE_UNRESOLVED in {f.code for f in result.blocking}

    def test_a_video_with_no_meaningful_visual_at_all_blocks(self):
        result = assess_composition(compose(_fallback_dominated_beats(meaningful=0)))
        assert CODE_NO_MEANINGFUL in {f.code for f in result.blocking}

    def test_empty_render_blocks_rather_than_vacuously_passing(self):
        result = assess_composition(compose([]))
        assert result.status == VQ_BLOCKED

    def test_assessment_is_deterministic(self):
        comp = compose(_fallback_dominated_beats())
        first = assess_composition(comp)
        second = assess_composition(comp)
        assert first.status == second.status
        assert [f.code for f in first.findings] == [f.code for f in second.findings]


class TestIntentionalMinimalismIsNotABypass:
    """The load-bearing distinction: a decision is respected, a breakage is not."""

    def test_minimalism_relaxes_the_creative_floor(self):
        # 20% meaningful runtime, all fallbacks intentional. Blocked by default,
        # allowed when the treatment actually asked for minimalism.
        beats = [_beat(0, start_ms=0, duration_ms=4_000)]
        beats += [
            _beat(
                i,
                start_ms=4_000 + (i - 1) * 4_000,
                duration_ms=4_000,
                family=FAMILY_GENERATED_DIAGRAM,
                intent="timeline",
                provider="programmatic",
                fallback_reason="structural_intent_prefers_graphic",
            )
            for i in range(1, 5)
        ]
        assert assess_composition(compose(beats)).status != VQ_BLOCKED

    def test_minimalism_does_not_excuse_widespread_provider_failure(self):
        comp = compose(_fallback_dominated_beats())
        default = assess_composition(comp)
        minimal = assess_composition(comp, visual_style="minimalist")

        assert default.status == VQ_BLOCKED
        assert minimal.status == VQ_BLOCKED, "minimalism must not bypass broken generation"

        codes = {f.code for f in minimal.blocking}
        assert CODE_PROVIDER_FAILURE in codes
        # The creative floor is relaxed; the provider floor is not.
        assert CODE_MEANINGFUL_FLOOR not in codes
        assert minimal.minimalism_applied is True

    def test_minimalism_does_not_excuse_a_long_dead_stretch(self):
        beats = [_beat(0, start_ms=0, duration_ms=4_000)]
        beats += [
            _beat(
                i,
                start_ms=4_000 + (i - 1) * 6_000,
                duration_ms=6_000,
                family=FAMILY_TEXT_CARD,
                intent="action",
                provider="programmatic",
                fallback_reason="structural_intent_prefers_graphic",
            )
            for i in range(1, 6)
        ]
        beats.append(_beat(6, start_ms=34_000, duration_ms=20_000))
        result = assess_composition(compose(beats), visual_style="minimalist")
        assert CODE_LONG_GAP in {f.code for f in result.blocking}

    def test_an_unknown_style_does_not_relax_anything(self):
        comp = compose(_fallback_dominated_beats())
        result = assess_composition(comp, visual_style="not_a_real_style")
        assert result.minimalism_applied is False
        assert CODE_MEANINGFUL_FLOOR in {f.code for f in result.blocking}


# ── Persistence ──────────────────────────────────────────────────────────────


@pytest.fixture()
def vdb(tmp_path: Path) -> sqlite3.Connection:
    """A schema-complete database with FK enforcement relaxed.

    These tests measure beat lineage, so they seed `visual_beats` and
    `render_visual_assessments` directly rather than constructing an entire
    topic → script → narration → caption → scene-manifest → render chain to
    reach the two columns under test. The FK relationships themselves are the
    schema's concern and are exercised by the migration tests.
    """
    conn = open_db(tmp_path / "visual.db")
    conn.execute("PRAGMA foreign_keys = OFF")
    return conn


def _seed_beats(conn: sqlite3.Connection, scene_manifest_id: int, beats: list[dict]) -> None:
    for b in beats:
        conn.execute(
            """
            INSERT INTO visual_beats (
                scene_manifest_id, beat_index, scene_index, segment_id,
                start_ms, end_ms, duration_ms, visual_intent,
                media_type_preferences_json, resolved_media_type, resolved_provider,
                resolved_asset_key, resolved_local_path, fallback_reason,
                engine_version, planner_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '1.0', '1.0')
            """,
            (
                scene_manifest_id,
                b["i"],
                b.get("scene", 0),
                1,
                b["start"],
                b["start"] + b["dur"],
                b["dur"],
                b["intent"],
                json.dumps(b.get("prefs", ["video", "photo", "graphic"])),
                b["media"],
                b.get("provider", "pexels"),
                b.get("asset", f"pexels:{b['i']}"),
                b.get("path", "/tmp/x.jpg"),
                b.get("fallback"),
            ),
        )
    conn.commit()


class TestPersistence:
    def test_persisted_lineage_measures_identically_to_the_in_memory_plan(self, vdb):
        _seed_beats(
            vdb,
            1,
            [
                {"i": 0, "start": 0, "dur": 4_000, "intent": "entity", "media": "video"},
                {
                    "i": 1,
                    "start": 4_000,
                    "dur": 4_000,
                    "intent": "timeline",
                    "media": "graphic",
                    "provider": "programmatic",
                    "asset": "programmatic:x",
                    "fallback": "structural_intent_prefers_graphic",
                },
                {
                    "i": 2,
                    "start": 8_000,
                    "dur": 4_000,
                    "intent": "action",
                    "media": "graphic",
                    "provider": "programmatic",
                    "asset": "programmatic:y",
                    "fallback": "all_candidates_rejected",
                },
            ],
        )
        comp = composition_from_scene_manifest(vdb, 1)
        assert comp.total_beat_count == 3
        assert comp.meaningful_beat_count == 2
        assert comp.text_card_beat_count == 1
        assert comp.creative_fallback_beats == 1
        assert comp.provider_fallback_beats == 1
        assert comp.planned_meaningful_beats == 3

    def test_a_beat_with_no_file_is_unresolved_not_a_text_card(self, vdb):
        _seed_beats(
            vdb,
            2,
            [
                {
                    "i": 0,
                    "start": 0,
                    "dur": 4_000,
                    "intent": "entity",
                    "media": "video",
                    "path": None,
                },
            ],
        )
        comp = composition_from_scene_manifest(vdb, 2)
        assert comp.unresolved_beat_count == 1
        assert comp.meaningful_beat_count == 0

    def test_save_is_idempotent_on_render_manifest_id(self, vdb):
        from app.visuals.assessment_repository import assess_render

        _seed_beats(
            vdb,
            3,
            [
                {"i": i, "start": i * 4_000, "dur": 4_000, "intent": "entity", "media": "video"}
                for i in range(4)
            ],
        )
        first = assess_render(vdb, render_manifest_id=99, scene_manifest_id=3)[1]
        second = assess_render(vdb, render_manifest_id=99, scene_manifest_id=3)[1]

        assert first is not None and second is not None
        assert first.id == second.id
        assert first.input_hash == second.input_hash
        rows = vdb.execute(
            "SELECT COUNT(*) c FROM render_visual_assessments WHERE render_manifest_id = 99"
        ).fetchone()
        assert rows["c"] == 1

    def test_reassessment_never_resets_the_remediation_budget(self, vdb):
        """The counter's whole purpose is to survive the restart that would
        otherwise let a crashed remediation re-spend provider budget."""
        from app.visuals.assessment_repository import (
            assess_render,
            get_assessment,
            record_remediation_attempt,
        )

        _seed_beats(
            vdb,
            4,
            [
                {"i": i, "start": i * 4_000, "dur": 4_000, "intent": "entity", "media": "video"}
                for i in range(4)
            ],
        )
        assess_render(vdb, render_manifest_id=100, scene_manifest_id=4)
        record_remediation_attempt(vdb, 100)
        record_remediation_attempt(vdb, 100)

        assess_render(vdb, render_manifest_id=100, scene_manifest_id=4)
        assert get_assessment(vdb, 100).remediation_attempts == 2

    def test_read_only_assessment_writes_nothing(self, vdb):
        from app.visuals.assessment_repository import assess_render, get_assessment

        _seed_beats(
            vdb,
            5,
            [
                {"i": 0, "start": 0, "dur": 4_000, "intent": "entity", "media": "video"},
            ],
        )
        assess_render(vdb, render_manifest_id=101, scene_manifest_id=5, persist=False)
        assert get_assessment(vdb, 101) is None

    def test_scene_diagnostics_record_planned_versus_realized(self, vdb):
        from app.visuals.assessment_repository import assess_render

        _seed_beats(
            vdb,
            6,
            [
                {
                    "i": 0,
                    "start": 0,
                    "dur": 4_000,
                    "intent": "action",
                    "media": "graphic",
                    "provider": "programmatic",
                    "prefs": ["video", "photo"],
                    "fallback": "all_candidates_rejected",
                },
            ],
        )
        stored = assess_render(vdb, render_manifest_id=102, scene_manifest_id=6)[1]
        diag = stored.scene_diagnostics[0]
        assert diag["planned"] == FAMILY_MOTION_FOOTAGE
        assert diag["realized"] == FAMILY_TEXT_CARD
        assert diag["fallback_class"] == FALLBACK_PROVIDER


# ── Preflight integration ────────────────────────────────────────────────────


class TestPreflightGate:
    def test_blocked_assessment_stops_the_production_preflight(self, vdb):
        from app.intelligence.autonomy.models import ProductionCycleResult
        from app.intelligence.autonomy.production_cycle import _check_visual_quality
        from app.visuals.assessment_repository import assess_render

        _seed_beats(
            vdb,
            10,
            [
                {
                    "i": i,
                    "start": i * 4_000,
                    "dur": 4_000,
                    "intent": "action",
                    "media": "graphic",
                    "provider": "programmatic",
                    "asset": f"programmatic:{i}",
                    "fallback": "all_candidates_rejected",
                }
                for i in range(15)
            ],
        )
        assess_render(vdb, render_manifest_id=200, scene_manifest_id=10)

        result = ProductionCycleResult(
            channel_id="c", workspace_id="w", slot_id=1, started_at="now"
        )
        ok, errors = _check_visual_quality(
            vdb,
            approved_render=type("R", (), {"render_manifest_id": 200})(),
            experiment=type("E", (), {"id": "exp-1"})(),
            result=result,
        )
        assert ok is False
        assert errors
        assert result.visual_quality_status == VQ_BLOCKED

    def test_healthy_assessment_passes_the_production_preflight(self, vdb):
        from app.intelligence.autonomy.models import ProductionCycleResult
        from app.intelligence.autonomy.production_cycle import _check_visual_quality
        from app.visuals.assessment_repository import assess_render

        _seed_beats(
            vdb,
            11,
            [
                {
                    "i": i,
                    "start": i * 4_000,
                    "dur": 4_000,
                    "intent": "timeline" if i % 3 == 2 else "entity",
                    "media": "graphic" if i % 3 == 2 else ("video" if i % 3 == 0 else "photo"),
                    "provider": "programmatic" if i % 3 == 2 else "pexels",
                    "asset": f"a{i}",
                    "fallback": "structural_intent_prefers_graphic" if i % 3 == 2 else None,
                }
                for i in range(15)
            ],
        )
        assess_render(vdb, render_manifest_id=201, scene_manifest_id=11)

        result = ProductionCycleResult(
            channel_id="c", workspace_id="w", slot_id=1, started_at="now"
        )
        ok, errors = _check_visual_quality(
            vdb,
            approved_render=type("R", (), {"render_manifest_id": 201})(),
            experiment=type("E", (), {"id": "exp-1"})(),
            result=result,
        )
        assert ok is True
        assert errors == []
        assert result.visual_quality_status == VQ_PASS

    def test_a_missing_assessment_is_reported_but_does_not_block(self, vdb):
        """Blocking on absence would make every pre-18E render permanently
        unpublishable — trading one silent failure for a louder one."""
        from app.intelligence.autonomy.models import ProductionCycleResult
        from app.intelligence.autonomy.production_cycle import _check_visual_quality

        result = ProductionCycleResult(
            channel_id="c", workspace_id="w", slot_id=1, started_at="now"
        )
        ok, errors = _check_visual_quality(
            vdb,
            approved_render=type("R", (), {"render_manifest_id": 999})(),
            experiment=type("E", (), {"id": "exp-1"})(),
            result=result,
        )
        assert ok is True
        assert result.visual_quality_status is None
        assert any("No visual quality assessment" in e for e in result.errors)


# ── Visual treatment intent ──────────────────────────────────────────────────


class TestVisualTreatmentIntent:
    def test_visual_style_is_a_safe_controllable_enforced_factor(self):
        from app.intelligence.experiments.planning import (
            SAFE_CONTROLLABLE_FACTORS,
            ControlCapability,
        )

        spec = SAFE_CONTROLLABLE_FACTORS["visual_style"]
        assert spec.control_capability == ControlCapability.ENFORCED
        assert spec.actual_value_source == "visual_style"

    def test_safe_values_are_exactly_what_the_renderer_implements(self):
        from app.intelligence.experiments.planning import SAFE_CONTROLLABLE_FACTORS
        from app.visuals.policy import _PRESETS

        # An experiment must never be able to request a treatment the
        # pipeline cannot produce.
        assert set(SAFE_CONTROLLABLE_FACTORS["visual_style"].safe_values) == set(_PRESETS)

    def test_treatment_factor_reaches_the_effective_config(self):
        from app.intelligence.autonomy.production_cycle import _visual_style_from_brief

        brief = type(
            "B",
            (),
            {
                "id": "b1",
                "treatment_factors": [
                    type("F", (), {"factor_name": "visual_style", "intended_value": "fast_cut"})()
                ],
            },
        )()
        assert _visual_style_from_brief(brief) == "fast_cut"

    def test_an_unknown_style_is_refused_rather_than_passed_through(self):
        from app.intelligence.autonomy.production_cycle import _visual_style_from_brief

        brief = type(
            "B",
            (),
            {
                "id": "b1",
                "treatment_factors": [
                    type("F", (), {"factor_name": "visual_style", "intended_value": "cinematic"})()
                ],
            },
        )()
        assert _visual_style_from_brief(brief) is None

    def test_style_resolves_through_effective_config_into_the_policy(self):
        from app.visuals.policy import policy_from_config

        assert policy_from_config({"visual_style": "fast_cut"}).style == "fast_cut"
        assert policy_from_config({}).style == "balanced"


# ── Learning integration ─────────────────────────────────────────────────────


class TestLearningIntegration:
    def test_visual_features_are_registered_as_comparable(self):
        from app.learning.cross_publication import ALL_COMPARABLE_FEATURES

        for feature in (
            "visual_meaningful_runtime_pct",
            "visual_text_card_runtime_pct",
            "visual_generated_diagram_runtime_pct",
            "visual_retrieved_imagery_runtime_pct",
            "visual_changes_per_minute",
            "visual_max_meaningful_gap_s",
            "visual_distinct_assets",
            "visual_asset_reuse_ratio",
            "visual_dominant_family",
            "visual_opening_meaningful",
            "visual_provider_fallback_rate",
            "visual_style",
            "visual_quality_status",
        ):
            assert feature in ALL_COMPARABLE_FEATURES

    def test_features_are_classified_by_who_chose_their_value(self):
        from app.learning.cross_publication import (
            PLANNER_CONTROLLED_FEATURES,
            PROVIDER_RELIABILITY_FEATURES,
            REALIZED_PRODUCTION_FEATURES,
        )

        assert "visual_style" in PLANNER_CONTROLLED_FEATURES
        assert "visual_meaningful_runtime_pct" in REALIZED_PRODUCTION_FEATURES
        assert "visual_provider_fallback_rate" in PROVIDER_RELIABILITY_FEATURES
        # A provider-reliability feature must never be mistaken for something
        # the planner can choose.
        assert not (PLANNER_CONTROLLED_FEATURES & PROVIDER_RELIABILITY_FEATURES)
        assert not (REALIZED_PRODUCTION_FEATURES & PROVIDER_RELIABILITY_FEATURES)

    def test_visual_features_bucket_deterministically(self):
        from app.learning.cross_publication import feature_bucket

        assert feature_bucket("visual_meaningful_runtime_pct", 0.838) == "0.8–0.9"
        assert feature_bucket("visual_meaningful_runtime_pct", 0.838) == feature_bucket(
            "visual_meaningful_runtime_pct", 0.84
        )
        assert feature_bucket("visual_quality_status", "blocked") == "blocked"
        assert feature_bucket("visual_opening_meaningful", 1) == "true"

    def test_extraction_copies_the_stored_assessment_verbatim(self, vdb):
        from app.learning.features import ContentFeatureSnapshotDraft, _extract_visual_features
        from app.visuals.assessment_repository import assess_render

        _seed_beats(
            vdb,
            20,
            [
                {"i": 0, "start": 0, "dur": 4_000, "intent": "entity", "media": "video"},
                {
                    "i": 1,
                    "start": 4_000,
                    "dur": 4_000,
                    "intent": "action",
                    "media": "graphic",
                    "provider": "programmatic",
                    "asset": "programmatic:a",
                    "fallback": "all_candidates_rejected",
                },
            ],
        )
        assess_render(vdb, render_manifest_id=300, scene_manifest_id=20)

        draft = ContentFeatureSnapshotDraft(
            publication_id=1,
            topic_id=1,
            workspace_id="w",
            channel_id="c",
            feature_schema_version="v",
            extractor_version="v",
            input_hash="h",
            extracted_at="now",
            publishing_plan_id=1,
            production_plan_id=1,
            script_id=1,
            narration_run_id=1,
            caption_run_id=1,
            scene_manifest_id=20,
            render_manifest_id=300,
            voice_profile_id=1,
        )
        _extract_visual_features(vdb, draft, 300)

        assert draft.visual_meaningful_runtime_pct == pytest.approx(0.5)
        assert draft.visual_text_card_runtime_pct == pytest.approx(0.5)
        assert draft.visual_provider_fallback_rate == pytest.approx(0.5)
        assert draft.visual_distinct_assets == 2
        assert draft.visual_opening_meaningful == 1

    def test_no_assessment_leaves_features_null_not_zero(self, vdb):
        """NULL means "unmeasured". Substituting 0.0 would teach the learner
        that the whole back catalogue had no meaningful visuals."""
        from app.learning.features import ContentFeatureSnapshotDraft, _extract_visual_features

        draft = ContentFeatureSnapshotDraft(
            publication_id=1,
            topic_id=1,
            workspace_id="w",
            channel_id="c",
            feature_schema_version="v",
            extractor_version="v",
            input_hash="h",
            extracted_at="now",
            publishing_plan_id=1,
            production_plan_id=1,
            script_id=1,
            narration_run_id=1,
            caption_run_id=1,
            scene_manifest_id=1,
            render_manifest_id=888,
            voice_profile_id=1,
        )
        _extract_visual_features(vdb, draft, 888)

        assert draft.visual_meaningful_runtime_pct is None
        assert draft.visual_quality_status is None
        assert draft.visual_distinct_assets is None

    def test_two_publications_cannot_reach_an_actionable_maturity(self):
        from app.learning.cross_publication import (
            MATURITY_ACTIONABLE,
            MATURITY_EXPLORATORY,
            MATURITY_INSUFFICIENT,
            _classify_maturity,
        )

        assert _classify_maturity(1) == MATURITY_INSUFFICIENT
        assert _classify_maturity(2) == MATURITY_EXPLORATORY
        assert _classify_maturity(3) == MATURITY_EXPLORATORY
        assert _classify_maturity(10) == MATURITY_ACTIONABLE


# ── Runtime / test isolation ─────────────────────────────────────────────────


class TestRuntimeIsolation:
    """The structural replacement for per-test guards.

    Phase 18C shipped a guard inside the single Playwright test that revoked
    live authorization. A per-test guard is a list that must stay complete
    forever; these tests assert the property instead.
    """

    def test_test_mode_reads_only_recognised_values(self, monkeypatch):
        from app.core.runtime_mode import in_test_mode, test_mode

        monkeypatch.delenv("ACE_TEST_MODE", raising=False)
        assert test_mode() is None
        assert in_test_mode() is False

        monkeypatch.setenv("ACE_TEST_MODE", "e2e")
        assert test_mode() == "e2e"

        monkeypatch.setenv("ACE_TEST_MODE", "yes-please")
        assert test_mode() is None, "an unrecognised value must not enable test mode"

    def test_e2e_refuses_the_operational_database(self, monkeypatch):
        from app.core.runtime_mode import (
            RuntimeIsolationError,
            assert_runtime_isolation,
            operational_db_path,
        )

        monkeypatch.setenv("ACE_TEST_MODE", "e2e")
        with pytest.raises(RuntimeIsolationError, match="OPERATIONAL database"):
            assert_runtime_isolation(operational_db_path())

    def test_a_symlink_cannot_smuggle_the_operational_database_past_the_check(
        self, monkeypatch, tmp_path
    ):
        from app.core.runtime_mode import (
            RuntimeIsolationError,
            assert_runtime_isolation,
            operational_db_path,
        )

        real = operational_db_path()
        real.parent.mkdir(parents=True, exist_ok=True)
        if not real.exists():
            pytest.skip("no operational database on this machine to link to")

        link = tmp_path / "innocent-looking.db"
        link.symlink_to(real)
        monkeypatch.setenv("ACE_TEST_MODE", "e2e")
        with pytest.raises(RuntimeIsolationError):
            assert_runtime_isolation(link)

    def test_production_refuses_the_e2e_database(self, monkeypatch, tmp_path):
        from app.core.runtime_mode import (
            TEST_DB_FILENAME,
            RuntimeIsolationError,
            assert_runtime_isolation,
        )

        # The reverse mistake, and the more destructive one: the live daemons
        # coming up against a throwaway database look like total state loss.
        monkeypatch.delenv("ACE_TEST_MODE", raising=False)
        with pytest.raises(RuntimeIsolationError, match="E2E test database"):
            assert_runtime_isolation(tmp_path / TEST_DB_FILENAME)

    def test_matching_pairs_are_permitted(self, monkeypatch, tmp_path):
        from app.core.runtime_mode import TEST_DB_FILENAME, assert_runtime_isolation

        monkeypatch.setenv("ACE_TEST_MODE", "e2e")
        assert_runtime_isolation(tmp_path / TEST_DB_FILENAME)

        monkeypatch.delenv("ACE_TEST_MODE", raising=False)
        assert_runtime_isolation(tmp_path / "content.db")

    def test_a_live_upload_is_refused_in_test_mode(self, monkeypatch):
        from app.core.runtime_mode import RuntimeIsolationError
        from app.publishing.upload_gate import check_live_publishing_gate

        # Even with every live gate deliberately on, a test runtime cannot
        # create a real provider publication.
        monkeypatch.setenv("ACE_TEST_MODE", "e2e")
        monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
        from app.core.config import reset_config

        reset_config()
        try:
            with pytest.raises(RuntimeIsolationError, match="provider_upload"):
                check_live_publishing_gate()
        finally:
            reset_config()

    def test_authorization_changes_are_refused_in_test_mode(self, monkeypatch):
        from app.core.runtime_mode import RuntimeIsolationError, assert_live_effect_allowed

        monkeypatch.setenv("ACE_TEST_MODE", "e2e")
        for operation in (
            "publishing_authorization_grant",
            "publishing_authorization_revoke",
            "provider_release_public",
        ):
            with pytest.raises(RuntimeIsolationError):
                assert_live_effect_allowed(operation)

    def test_live_effects_are_permitted_outside_test_mode(self, monkeypatch):
        from app.core.runtime_mode import assert_live_effect_allowed

        monkeypatch.delenv("ACE_TEST_MODE", raising=False)
        assert_live_effect_allowed("provider_upload")
        assert_live_effect_allowed("publishing_authorization_grant")

    def test_meta_endpoint_reports_the_runtime_it_actually_has(self, monkeypatch, tmp_path):
        from fastapi.testclient import TestClient

        from app.api.main import app
        from app.core.config import reset_config

        monkeypatch.setenv("ACE_TEST_MODE", "e2e")
        monkeypatch.setenv("ACE_DB_PATH", str(tmp_path / "e2e-test.db"))
        monkeypatch.setenv("ACE_ENV", "development")
        reset_config()
        try:
            with TestClient(app) as client:
                runtime = client.get("/api/meta").json()["runtime"]
            assert runtime["test_mode"] == "e2e"
            assert runtime["operational_db"] is False
            assert runtime["db_name"] == "e2e-test.db"
        finally:
            reset_config()


class TestE2ELauncherIsolation:
    """The E2E backend script is part of the safety property, so it is tested."""

    ROOT = Path(__file__).resolve().parent.parent

    def test_e2e_launcher_never_sources_the_operator_env_file(self):
        """.env.local carries live API keys and live publishing gates. An E2E
        run must not be one `source` away from any of them."""
        script = (self.ROOT / "scripts" / "start-e2e-backend.sh").read_text()
        code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
        assert ".env.local" not in code
        assert "source " not in code

    def test_e2e_launcher_pins_isolation_and_gates(self):
        script = (self.ROOT / "scripts" / "start-e2e-backend.sh").read_text()
        assert "ACE_TEST_MODE=e2e" in script
        assert "e2e-test.db" in script
        assert "ACE_PUBLISHING_LIVE_ENABLED=false" in script
        assert "ACE_RELEASE_PUBLIC_ENABLED=false" in script

    def test_dev_launcher_lets_caller_env_win_over_the_env_file(self):
        """The original `set -a; source .env.local` silently overwrote every
        safety variable Playwright had exported. That is how the E2E suite
        ended up running with live publishing enabled."""
        script = (self.ROOT / "scripts" / "start-backend.sh").read_text()
        code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
        assert "set -a" not in code
        # The file's value is applied only when the variable is unset.
        assert "${!key+x}" in code

    def test_playwright_uses_dedicated_ports_and_never_reuses_a_server(self):
        config = (self.ROOT / "frontend" / "playwright.config.ts").read_text()
        assert "start-e2e-backend.sh" in config
        assert "start-backend.sh'" not in config
        assert "reuseExistingServer: false" in config
        assert "8100" in config
        assert "reuseExistingServer: !process.env.CI" not in config

    def test_vite_proxy_target_is_configurable(self):
        config = (self.ROOT / "frontend" / "vite.config.ts").read_text()
        assert "ACE_BACKEND_URL" in config


class TestMigration:
    def test_fresh_install_creates_the_assessment_table_and_feature_columns(self, tmp_path):
        conn = open_db(tmp_path / "fresh.db")
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='render_visual_assessments'"
        ).fetchone()
        cols = {r[1] for r in conn.execute("PRAGMA table_info('content_feature_snapshots')")}
        assert "visual_meaningful_runtime_pct" in cols
        assert "visual_style" in cols

    def test_the_assessment_table_admits_one_row_per_render(self, vdb):
        from app.visuals.assessment_repository import assess_render

        _seed_beats(
            vdb,
            40,
            [
                {"i": 0, "start": 0, "dur": 4_000, "intent": "entity", "media": "video"},
            ],
        )
        assess_render(vdb, render_manifest_id=500, scene_manifest_id=40)
        with pytest.raises(sqlite3.IntegrityError):
            vdb.execute(
                "INSERT INTO render_visual_assessments "
                "(render_manifest_id, scene_manifest_id, assessment_version, "
                " composition_version, policy_version, status, input_hash, "
                " created_at, updated_at) "
                "VALUES (500, 40, 'v', 'v', 'v', 'pass', 'h', 'now', 'now')"
            )


class TestIsolationRegressions:
    """Regressions for the two live-database incidents this phase caused and found.

    Both were the same shape: nothing between an environment variable and
    sqlite3.connect ever looked at which database it was about to open.
    """

    def test_open_db_itself_refuses_the_operational_database_in_test_mode(self, monkeypatch):
        """The lowest layer, and the one that actually runs migrations.

        A pytest run with `ACE_DB_PATH=` (set but empty) resolved to the
        operational database and MIGRATED it to v50 while this phase was being
        built, taking the live backend and the autonomous publisher down.
        Startup checks did not help: nothing was starting up.
        """
        from app.core.database import open_db
        from app.core.runtime_mode import RuntimeIsolationError, operational_db_path

        monkeypatch.setenv("ACE_TEST_MODE", "unit")
        with pytest.raises(RuntimeIsolationError, match="OPERATIONAL database"):
            open_db(operational_db_path())

    def test_empty_ace_db_path_resolves_to_the_operational_database(self, monkeypatch):
        """The trap itself, asserted so nobody 'simplifies' Config's fallback.

        `Path(raw) if raw else _default_db_path()` treats an empty string as
        unset, so `ACE_DB_PATH=` looks deliberate and silently means "live".
        """
        from app.core.config import get_config, reset_config
        from app.core.runtime_mode import is_operational_db

        monkeypatch.setenv("ACE_DB_PATH", "")
        reset_config()
        try:
            assert is_operational_db(get_config().db_path)
        finally:
            reset_config()

    def test_conftest_pins_a_non_operational_database_for_the_session(self):
        """conftest sets this at IMPORT time, before any module-level open_db."""
        from app.core.runtime_mode import is_operational_db, test_mode

        assert test_mode() == "unit"
        configured = os.environ.get("ACE_DB_PATH", "")
        assert configured.strip(), "ACE_DB_PATH must be pinned, not left to the default"
        assert not is_operational_db(Path(configured))

    def test_unit_mode_can_still_exercise_the_live_publishing_gate(self, monkeypatch):
        """Refusing provider_upload in unit mode made the gate untestable.

        An untestable safety gate is a worse outcome than a testable one, and
        unit runs cannot upload anything anyway — there is no provider. Only
        E2E, which drives a real server, is refused.
        """
        from app.core.config import reset_config
        from app.publishing.upload_gate import check_live_publishing_gate

        monkeypatch.setenv("ACE_TEST_MODE", "unit")
        monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
        reset_config()
        try:
            check_live_publishing_gate()  # must not raise
        finally:
            reset_config()

    def test_e2e_mode_still_refuses_the_live_publishing_gate(self, monkeypatch):
        from app.core.config import reset_config
        from app.core.runtime_mode import RuntimeIsolationError
        from app.publishing.upload_gate import check_live_publishing_gate

        monkeypatch.setenv("ACE_TEST_MODE", "e2e")
        monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
        reset_config()
        try:
            with pytest.raises(RuntimeIsolationError, match="provider_upload"):
                check_live_publishing_gate()
        finally:
            reset_config()

    def test_cli_commands_do_not_reach_the_operational_database(self, tmp_path):
        """`ace features extract` ran against the LIVE database for months.

        Its test patched `app.core.config.get_config`, but `app.cli` binds that
        name at import time, so the patch never applied and the command used
        the default path. It "passed" because the live database happens to
        contain a publication with id=1.
        """
        from app.core.config import get_config, reset_config
        from app.core.runtime_mode import is_operational_db

        reset_config()
        try:
            # Whatever a CLI test forgets to set, the session pin holds.
            assert not is_operational_db(get_config().db_path)
        finally:
            reset_config()


def test_the_session_itself_is_isolated_from_the_live_database():
    """The suite must be structurally unable to reach the operational DB.

    Written after this exact mistake was made while building the phase: a
    pytest run with `ACE_DB_PATH=` (set but empty) falls through Config's
    `raw if raw else _default_db_path()` to the OPERATIONAL database, and the
    CLI tests read that path directly. The session fixture in conftest.py now
    refuses to let that happen; this asserts the fixture is actually in force.
    """
    from app.core.config import get_config
    from app.core.runtime_mode import is_operational_db, test_mode

    assert test_mode() == "unit", "the session-level isolation fixture is not active"
    assert not is_operational_db(get_config().db_path)
    assert os.environ.get("ACE_DB_PATH")
