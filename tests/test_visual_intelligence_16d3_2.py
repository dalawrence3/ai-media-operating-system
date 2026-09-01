"""Phase 16D.3.2 — semantic visual intelligence invariants.

These cover the architectural properties that made Video 2's render
unacceptable, so a regression reintroduces a test failure rather than a bad
video:

  * beats tile their parent scene exactly, so visuals can be re-planned
    without disturbing narration timing or audio lineage
  * a candidate must show evidence of being about the beat, not merely share
    an ordinary English word with it
  * one asset cannot dominate the runtime
  * an asset used recently on the same channel is penalised
  * the fallback chain always terminates in a locally generated visual
  * licensing is enforced per asset, not assumed per provider
  * the QA gate distinguishes "renderable" from "releasable"
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.visuals import semantics
from app.visuals.beats import BeatPlannerInput, CueWindow, plan_beats
from app.visuals.constants import (
    FIT_CONTAIN,
    FIT_COVER,
    LICENSE_UNSAFE,
    MEDIA_GRAPHIC,
    MEDIA_ILLUSTRATION,
    MEDIA_PHOTO,
    MEDIA_VIDEO,
    MOTION_NONE,
    QA_FAIL,
    QA_PASS,
)
from app.visuals.engine import VisualEngine, VisualEngineConfig
from app.visuals.memory import get_asset_usage, record_asset_usage
from app.visuals.models import BeatResolution, VisualBeat, VisualCandidate, VisualPlan
from app.visuals.motion import choose_fit, motion_filter
from app.visuals.policy import STYLE_FAST_CUT, resolve_policy
from app.visuals.providers.base import ProviderCapability
from app.visuals.qa import (
    CODE_DOMINANT_ASSET,
    CODE_LOW_VISUAL_CHANGE,
    CODE_UNSAFE_LICENSE,
    audit_visual_plan,
)
from app.visuals.scoring import (
    REASON_NO_RELEVANCE_EVIDENCE,
    REASON_NOT_COMMERCIAL_SAFE,
    REASON_OVERUSED_IN_VIDEO,
    REASON_WEAK_EVIDENCE,
    ScoringContext,
    rank_candidates,
    score_candidate,
)

# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _beat(
    text: str = "Cas9 slices both strands of your DNA",
    *,
    index: int = 0,
    duration_ms: int = 3000,
    intent: str = "entity",
    keywords: list[str] | None = None,
    entities: list[str] | None = None,
    media_prefs: list[str] | None = None,
) -> VisualBeat:
    return VisualBeat(
        beat_index=index,
        scene_index=0,
        scene_id=1,
        segment_id=10,
        start_ms=index * duration_ms,
        end_ms=(index + 1) * duration_ms,
        duration_ms=duration_ms,
        narration_text=text,
        keywords=keywords if keywords is not None else semantics.extract_terms(text, limit=6),
        entities=entities if entities is not None else semantics.extract_entities(text),
        visual_intent=intent,
        media_type_preferences=media_prefs or [MEDIA_VIDEO, MEDIA_PHOTO, MEDIA_GRAPHIC],
        search_queries=["dna cas9"],
    )


def _candidate(
    *,
    asset_id: str = "1",
    tags: list[str] | None = None,
    media_type: str = MEDIA_VIDEO,
    provider: str = "pexels",
    width: int = 1080,
    height: int = 1920,
    duration_s: float = 10.0,
    commercial_safe: bool = True,
    license_status: str = "verified",
    query: str = "dna cas9",
) -> VisualCandidate:
    return VisualCandidate(
        provider=provider,
        provider_asset_id=asset_id,
        media_type=media_type,
        query=query,
        download_url=f"https://example.test/{asset_id}",
        width=width,
        height=height,
        duration_s=duration_s,
        license_status=license_status,
        commercial_safe=commercial_safe,
        tags=tags if tags is not None else ["dna", "helix", "animation"],
        title=" ".join(tags or ["dna", "helix", "animation"]),
    )


class _StubProvider:
    """In-memory provider: no network, deterministic results."""

    capability = ProviderCapability(
        media_types=(MEDIA_VIDEO, MEDIA_PHOTO, MEDIA_ILLUSTRATION),
        tier=1,
        cost_units=0,
        supports_orientation=True,
    )

    def __init__(self, identity="stub", results=None, available=True, raises=False):
        self.identity = identity
        self._results = results if results is not None else []
        self._available = available
        self._raises = raises
        self.searches: list[str] = []
        self.downloads: list[str] = []

    def available(self) -> bool:
        return self._available

    def search(self, query, *, media_type, limit=10, orientation=None):
        self.searches.append(query)
        if self._raises:
            raise RuntimeError("provider outage")
        return [c for c in self._results if c.media_type == media_type]

    def download(self, candidate, cache_dir: Path):
        self.downloads.append(candidate.asset_key)
        cache_dir.mkdir(parents=True, exist_ok=True)
        dest = cache_dir / f"{candidate.asset_key.replace(':', '_')}.mp4"
        dest.write_bytes(b"stub")
        return dest


def _engine(tmp_path: Path, providers, *, conn=None, policy=None, **kwargs) -> VisualEngine:
    config = VisualEngineConfig(
        policy=policy or resolve_policy(),
        cache_dir=tmp_path / "cache",
        graphics_dir=tmp_path / "graphics",
        **kwargs,
    )
    return VisualEngine(config, providers=providers, conn=conn)


@pytest.fixture()
def usage_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from app.core.database import _apply_v44_visual_intelligence

    conn.execute("CREATE TABLE IF NOT EXISTS scene_manifests (id INTEGER PRIMARY KEY)")
    _apply_v44_visual_intelligence(conn)
    return conn


# ---------------------------------------------------------------------------
# Beat segmentation
# ---------------------------------------------------------------------------


class TestBeatSegmentation:
    @staticmethod
    def _scene(duration_ms=47554, cues=None) -> BeatPlannerInput:
        return BeatPlannerInput(
            scene_index=0,
            scene_id=1,
            segment_id=25,
            start_ms=12539,
            duration_ms=duration_ms,
            narration_text=(
                "Here's what happens. Cas9's two nuclease domains slice both strands of "
                "your DNA, creating a double-strand break. Now your cell panics and tries "
                "to fix it. If it uses Non-Homologous End Joining, it introduces indels."
            ),
            cues=cues,
        )

    def test_beats_tile_the_scene_exactly(self):
        """The invariant that keeps narration timing untouched by visual re-planning."""
        cues = [
            CueWindow(text="Here's what happens.", start_ms=0, end_ms=1598),
            CueWindow(text="Cas9's two nuclease domains slice.", start_ms=1598, end_ms=6314),
            CueWindow(text="creating a double-strand break.", start_ms=6314, end_ms=8871),
            CueWindow(text="Now your cell panics.", start_ms=8871, end_ms=12148),
        ]
        scene = self._scene(duration_ms=12148, cues=cues)
        beats = plan_beats([scene])

        assert beats[0].start_ms == scene.start_ms
        assert beats[-1].end_ms == scene.start_ms + scene.duration_ms
        for earlier, later in zip(beats, beats[1:], strict=False):
            assert earlier.end_ms == later.start_ms, "beats must not gap or overlap"
        assert sum(b.duration_ms for b in beats) == scene.duration_ms

    def test_long_scene_becomes_many_beats(self):
        """A 47s scene rendered as one clip is the Video 2 defect."""
        beats = plan_beats([self._scene()])
        assert len(beats) >= 8

    def test_beat_durations_stay_inside_the_pacing_envelope(self):
        beats = plan_beats([self._scene()], target_beat_ms=3400, min_beat_ms=1500, max_beat_ms=6500)
        assert all(b.duration_ms <= 6500 for b in beats)

    def test_segmentation_without_cues_still_tiles(self):
        scene = self._scene(cues=None)
        beats = plan_beats([scene])
        assert beats[0].start_ms == scene.start_ms
        assert beats[-1].end_ms == scene.start_ms + scene.duration_ms

    def test_segmentation_is_deterministic(self):
        first = plan_beats([self._scene()])
        second = plan_beats([self._scene()])
        assert [(b.start_ms, b.end_ms, b.visual_intent) for b in first] == [
            (b.start_ms, b.end_ms, b.visual_intent) for b in second
        ]

    def test_channel_policy_changes_pacing(self):
        """Visual policy is the seam for per-channel style, not a forked engine."""
        scene = self._scene()
        fast = resolve_policy(STYLE_FAST_CUT)
        balanced = resolve_policy()
        fast_beats = plan_beats(
            [scene],
            target_beat_ms=fast.target_beat_ms,
            min_beat_ms=fast.min_beat_ms,
            max_beat_ms=fast.max_beat_ms,
        )
        balanced_beats = plan_beats(
            [scene],
            target_beat_ms=balanced.target_beat_ms,
            min_beat_ms=balanced.min_beat_ms,
            max_beat_ms=balanced.max_beat_ms,
        )
        assert len(fast_beats) > len(balanced_beats)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


class TestCandidateScoring:
    def test_relevant_candidate_outscores_irrelevant_one(self):
        beat = _beat()
        ctx = ScoringContext(topic_terms=["dna", "cas9"], topic_entities=["DNA", "Cas9"])
        relevant = score_candidate(beat, _candidate(tags=["dna", "helix", "cas9"]), ctx)
        irrelevant = score_candidate(
            beat, _candidate(asset_id="2", tags=["surfer", "ocean", "waves"]), ctx
        )
        assert relevant.score > irrelevant.score

    def test_candidate_sharing_nothing_is_rejected(self):
        """Technical quality must not carry an unrelated clip over the floor."""
        beat = _beat()
        ctx = ScoringContext(topic_terms=["dna"], topic_entities=["DNA"])
        scored = score_candidate(
            beat,
            _candidate(tags=["surfer", "ocean", "waves"], width=2160, height=3840),
            ctx,
        )
        assert scored.rejected_reason == REASON_NO_RELEVANCE_EVIDENCE

    def test_single_ordinary_word_is_not_evidence(self):
        """'repair' matches a car garage and a DNA repair diagram equally well."""
        beat = _beat(
            "So the cell's repair choice determines your outcome.",
            keywords=["repair", "choice", "cell"],
            entities=[],
        )
        ctx = ScoringContext(topic_terms=["dna", "repair"], topic_entities=["DNA"])
        garage = score_candidate(beat, _candidate(tags=["car", "repair", "garage", "welding"]), ctx)
        assert garage.rejected_reason == REASON_WEAK_EVIDENCE

    def test_acronym_match_is_sufficient_evidence(self):
        beat = _beat(
            "HDR only activates during S and G2 phases",
            keywords=["hdr", "activates"],
            entities=["HDR", "G2"],
        )
        ctx = ScoringContext(topic_terms=["dna", "hdr"], topic_entities=["HDR", "DNA"])
        scored = score_candidate(beat, _candidate(tags=["hdr", "repair", "pathway"]), ctx)
        assert scored.rejected_reason is None

    def test_candidate_is_not_evidence_for_its_own_query(self):
        """Crediting a candidate for the query that fetched it is circular."""
        beat = _beat()
        ctx = ScoringContext(topic_terms=["dna"], topic_entities=["DNA"])
        scored = score_candidate(
            beat, _candidate(tags=["surfer", "waves"], query="dna cas9 helix"), ctx
        )
        assert scored.factors["semantic"] == 0.0

    def test_non_commercial_asset_is_rejected_when_safety_required(self):
        beat = _beat()
        ctx = ScoringContext(
            require_commercial_safe=True, topic_terms=["dna"], topic_entities=["DNA"]
        )
        scored = score_candidate(beat, _candidate(commercial_safe=False), ctx)
        assert scored.rejected_reason == REASON_NOT_COMMERCIAL_SAFE

    def test_unsafe_license_is_rejected(self):
        beat = _beat()
        ctx = ScoringContext(topic_terms=["dna"], topic_entities=["DNA"])
        scored = score_candidate(beat, _candidate(license_status=LICENSE_UNSAFE), ctx)
        assert scored.rejected_reason is not None

    def test_portrait_source_beats_landscape_for_vertical_output(self):
        beat = _beat()
        ctx = ScoringContext(topic_terms=["dna"], topic_entities=["DNA"])
        portrait = score_candidate(beat, _candidate(width=1080, height=1920), ctx)
        landscape = score_candidate(beat, _candidate(asset_id="2", width=1920, height=1080), ctx)
        assert portrait.factors["technical"] > landscape.factors["technical"]

    def test_ranking_is_deterministic(self):
        beat = _beat()
        ctx = ScoringContext(topic_terms=["dna"], topic_entities=["DNA"])
        pool = [
            _candidate(asset_id="1", tags=["dna", "helix"]),
            _candidate(asset_id="2", tags=["dna", "cell"]),
            _candidate(asset_id="3", tags=["dna", "strand"]),
        ]
        first = [s.candidate.asset_key for s in rank_candidates(beat, pool, ctx)]
        second = [s.candidate.asset_key for s in rank_candidates(beat, list(reversed(pool)), ctx)]
        assert first == second


# ---------------------------------------------------------------------------
# Repetition / diversity
# ---------------------------------------------------------------------------


class TestRepetitionSafeguards:
    def test_asset_already_used_scores_lower(self):
        beat = _beat()
        candidate = _candidate()
        fresh = ScoringContext(topic_terms=["dna"], topic_entities=["DNA"])
        reused = ScoringContext(
            topic_terms=["dna"],
            topic_entities=["DNA"],
            used_count_in_video={candidate.asset_key: 1},
            used_ms_in_video={candidate.asset_key: 4000},
        )
        assert (
            score_candidate(beat, candidate, reused).score
            < score_candidate(beat, candidate, fresh).score
        )

    def test_asset_past_its_use_budget_is_rejected(self):
        """Directly prevents one clip covering 30-40s of a 60s video."""
        beat = _beat()
        candidate = _candidate()
        ctx = ScoringContext(
            topic_terms=["dna"],
            topic_entities=["DNA"],
            used_ms_in_video={candidate.asset_key: 12_000},
            max_asset_total_ms=12_000,
        )
        assert score_candidate(beat, candidate, ctx).rejected_reason == REASON_OVERUSED_IN_VIDEO

    def test_recent_channel_reuse_is_penalised(self):
        """A cached asset must not win merely because it is already local."""
        beat = _beat()
        candidate = _candidate()
        fresh = ScoringContext(topic_terms=["dna"], topic_entities=["DNA"])
        recent = ScoringContext(
            topic_terms=["dna"],
            topic_entities=["DNA"],
            channel_reuse={candidate.asset_key: 1.0},
        )
        assert (
            score_candidate(beat, candidate, recent).score
            < score_candidate(beat, candidate, fresh).score
        )

    def test_visually_similar_asset_is_penalised(self):
        """Fifteen distinct ids of the same abstract subject is one visual idea."""
        beat = _beat()
        candidate = _candidate(tags=["abstract", "blue", "dna", "helix", "animation"])
        fresh = ScoringContext(topic_terms=["dna"], topic_entities=["DNA"])
        crowded = ScoringContext(
            topic_terms=["dna"],
            topic_entities=["DNA"],
            used_descriptors=[{"abstract", "blue", "dna", "helix", "animation"}],
        )
        assert (
            score_candidate(beat, candidate, crowded).score
            < score_candidate(beat, candidate, fresh).score
        )

    def test_engine_does_not_reuse_one_asset_across_every_beat(self, tmp_path):
        only = _candidate(asset_id="solo", tags=["dna", "helix"])
        provider = _StubProvider(results=[only])
        engine = _engine(tmp_path, [provider])
        beats = [_beat(index=i, duration_ms=5000) for i in range(6)]
        plan = engine.resolve(beats)
        used = sum(1 for r in plan.resolutions if r.asset_key == only.asset_key)
        assert used <= resolve_policy().max_asset_uses_per_video


# ---------------------------------------------------------------------------
# Channel-aware asset memory
# ---------------------------------------------------------------------------


class TestChannelAssetMemory:
    def test_usage_is_recorded_and_recalled(self, usage_db):
        record_asset_usage(
            usage_db,
            asset_key="pexels:123",
            provider="pexels",
            provider_asset_id="123",
            media_type=MEDIA_VIDEO,
            duration_ms=3000,
            channel_key="channel-a",
        )
        stats = get_asset_usage(usage_db, "pexels:123", channel_key="channel-a")
        assert stats.channel_uses == 1
        assert stats.total_duration_ms == 3000
        assert stats.reuse_weight > 0

    def test_memory_is_channel_scoped(self, usage_db):
        """Reuse across different channels is a policy question, not a defect."""
        record_asset_usage(
            usage_db,
            asset_key="pexels:123",
            provider="pexels",
            provider_asset_id="123",
            media_type=MEDIA_VIDEO,
            duration_ms=3000,
            channel_key="channel-a",
        )
        other = get_asset_usage(usage_db, "pexels:123", channel_key="channel-b")
        assert other.channel_uses == 0
        assert other.reuse_weight == 0.0

    def test_unknown_asset_has_no_history(self, usage_db):
        stats = get_asset_usage(usage_db, "pexels:absent", channel_key="channel-a")
        assert stats.total_uses == 0 and stats.reuse_weight == 0.0

    def test_manifest_can_be_excluded_from_its_own_history(self, usage_db):
        """Re-rendering a manifest must not inflate its own reuse penalties."""
        record_asset_usage(
            usage_db,
            asset_key="pexels:123",
            provider="pexels",
            provider_asset_id="123",
            media_type=MEDIA_VIDEO,
            duration_ms=3000,
            channel_key="channel-a",
            scene_manifest_id=5,
        )
        stats = get_asset_usage(
            usage_db, "pexels:123", channel_key="channel-a", exclude_scene_manifest_id=5
        )
        assert stats.total_uses == 0


# ---------------------------------------------------------------------------
# Fallback chain
# ---------------------------------------------------------------------------


class TestFallbackChain:
    def test_no_candidates_falls_back_to_generated_graphic(self, tmp_path):
        engine = _engine(tmp_path, [_StubProvider(results=[])])
        plan = engine.resolve([_beat()])
        resolution = plan.resolutions[0]
        assert resolution.media_type == MEDIA_GRAPHIC
        assert Path(resolution.local_path).exists()

    def test_provider_outage_does_not_fail_the_render(self, tmp_path):
        engine = _engine(tmp_path, [_StubProvider(raises=True)])
        plan = engine.resolve([_beat()])
        assert plan.resolutions[0].resolved

    def test_unavailable_provider_is_skipped(self, tmp_path):
        down = _StubProvider(identity="down", available=False, results=[_candidate()])
        up = _StubProvider(identity="up", results=[_candidate(asset_id="9", tags=["dna", "helix"])])
        engine = _engine(tmp_path, [down, up])
        engine.resolve([_beat()])
        assert down.searches == []
        assert up.searches

    def test_irrelevant_stock_loses_to_a_generated_graphic(self, tmp_path):
        """Prefer no stock asset over a clearly irrelevant one."""
        provider = _StubProvider(results=[_candidate(tags=["surfer", "ocean", "waves"])])
        engine = _engine(tmp_path, [provider])
        plan = engine.resolve([_beat()])
        assert plan.resolutions[0].media_type == MEDIA_GRAPHIC
        assert provider.downloads == []

    def test_every_beat_resolves(self, tmp_path):
        engine = _engine(tmp_path, [_StubProvider(results=[])])
        plan = engine.resolve([_beat(index=i) for i in range(5)])
        assert all(r.resolved for r in plan.resolutions)

    def test_ai_generation_is_off_unless_authorised(self, monkeypatch):
        from app.visuals.providers.ai_image import AiImageProvider, set_generator

        monkeypatch.delenv("ACE_VISUAL_AI_GENERATION_ENABLED", raising=False)
        set_generator(None)
        assert AiImageProvider().available() is False


# ---------------------------------------------------------------------------
# Still-image motion
# ---------------------------------------------------------------------------


class TestStillMotion:
    def test_motion_filter_targets_exact_output_size(self):
        chain = motion_filter("zoom_in", width=1080, height=1920, frames=120, fps=30)
        assert "1080x1920" in chain and "zoompan" in chain

    def test_no_motion_still_normalises_the_frame(self):
        chain = motion_filter(MOTION_NONE, width=1080, height=1920, frames=120, fps=30)
        assert "zoompan" not in chain and "crop=1080:1920" in chain

    def test_zoom_travel_is_bounded(self):
        """Aggressive zoompan on portrait video is visually uncomfortable."""
        chain = motion_filter("zoom_in", width=1080, height=1920, frames=120, fps=30)
        assert "1.08" in chain

    def test_zoompan_frame_count_matches_the_request(self):
        """zoompan emits d frames per input frame; d must match the beat exactly."""
        chain = motion_filter("zoom_in", width=1080, height=1920, frames=173, fps=30)
        assert "d=173" in chain

    def test_video_assets_receive_no_still_motion(self, tmp_path):
        provider = _StubProvider(results=[_candidate(media_type=MEDIA_VIDEO)])
        engine = _engine(tmp_path, [provider])
        plan = engine.resolve([_beat()])
        assert plan.resolutions[0].motion == MOTION_NONE


# ---------------------------------------------------------------------------
# Geometric fit: cover vs. contain
# ---------------------------------------------------------------------------


class TestGeometricFit:
    """Prevents the defect where labelled diagrams were centre-cropped.

    The decision is type-led (MEDIA_ILLUSTRATION vs. MEDIA_PHOTO/MEDIA_VIDEO)
    because that is the metadata the retrieval layer already assigns, with
    geometry deciding only whether cropping an illustration would actually be
    destructive.
    """

    def test_ordinary_photo_covers_even_when_wide(self):
        """Photography is compositionally forgiving under a crop."""
        assert (
            choose_fit(MEDIA_PHOTO, 1920, 1280, target_width=1080, target_height=1920) == FIT_COVER
        )

    def test_video_always_covers(self):
        assert (
            choose_fit(MEDIA_VIDEO, 1920, 1080, target_width=1080, target_height=1920) == FIT_COVER
        )

    def test_wide_illustration_is_contained(self):
        """A labelled diagram must not lose its labels to a centre-crop."""
        assert (
            choose_fit(MEDIA_ILLUSTRATION, 1200, 400, target_width=1080, target_height=1920)
            == FIT_CONTAIN
        )

    def test_illustration_already_near_target_aspect_covers(self):
        """No reason to pay for a blur-fill treatment when cropping loses little."""
        assert (
            choose_fit(MEDIA_ILLUSTRATION, 900, 1600, target_width=1080, target_height=1920)
            == FIT_COVER
        )

    def test_illustration_with_unknown_geometry_stays_conservative(self):
        assert (
            choose_fit(MEDIA_ILLUSTRATION, None, None, target_width=1080, target_height=1920)
            == FIT_CONTAIN
        )

    def test_contain_filter_preserves_full_source_bounds(self):
        """The foreground pass must not force-crop — that would defeat the point."""
        chain = motion_filter(
            MOTION_NONE, width=1080, height=1920, frames=90, fps=30, fit=FIT_CONTAIN
        )
        assert "force_original_aspect_ratio=decrease" in chain  # foreground: fit, don't crop
        assert "force_original_aspect_ratio=increase" in chain  # background: fill for blur
        assert "overlay" in chain

    def test_contain_output_is_still_exactly_the_target_frame(self):
        chain = motion_filter(
            MOTION_NONE, width=1080, height=1920, frames=90, fps=30, fit=FIT_CONTAIN
        )
        assert "1080:1920" in chain

    def test_contain_ignores_requested_motion(self):
        """A contained diagram is shown for its detail; it must not drift."""
        chain = motion_filter(
            "zoom_in", width=1080, height=1920, frames=90, fps=30, fit=FIT_CONTAIN
        )
        assert "zoompan" not in chain

    def test_engine_contains_a_wide_illustration_and_covers_a_wide_photo(self, tmp_path):
        wide_illustration = _candidate(
            asset_id="diagram",
            media_type=MEDIA_ILLUSTRATION,
            width=1200,
            height=400,
            provider="wikimedia",
            tags=["dna", "diagram"],
        )
        engine = _engine(
            tmp_path, [_StubProvider(identity="wikimedia", results=[wide_illustration])]
        )
        plan = engine.resolve([_beat(media_prefs=[MEDIA_ILLUSTRATION, MEDIA_PHOTO, MEDIA_GRAPHIC])])
        resolution = plan.resolutions[0]
        assert resolution.provider == "wikimedia", resolution.fallback_reason
        assert resolution.fit_mode == FIT_CONTAIN
        assert resolution.motion == MOTION_NONE

        wide_photo = _candidate(asset_id="photo", media_type=MEDIA_PHOTO, width=1920, height=1280)
        engine2 = _engine(tmp_path, [_StubProvider(results=[wide_photo])])
        plan2 = engine2.resolve([_beat(media_prefs=[MEDIA_PHOTO, MEDIA_GRAPHIC])])
        assert plan2.resolutions[0].fit_mode == FIT_COVER


# ---------------------------------------------------------------------------
# Render timing
# ---------------------------------------------------------------------------


class TestBeatRenderTiming:
    """Beats must not drift the video track away from the narration."""

    @staticmethod
    def _draft(beat_durations_ms: list[int], fps: int = 30):
        from app.media.models import (
            RenderManifestDraft,
            RenderSceneDraft,
            RenderVisualBeat,
        )

        beats, cursor = [], 0
        for i, duration in enumerate(beat_durations_ms):
            beats.append(
                RenderVisualBeat(
                    beat_index=i,
                    start_ms=cursor,
                    end_ms=cursor + duration,
                    duration_ms=duration,
                    local_path="/tmp/asset.mp4",
                    media_type=MEDIA_VIDEO,
                )
            )
            cursor += duration
        scene = RenderSceneDraft(
            scene_index=0,
            scene_id=1,
            segment_id=10,
            narration_asset_id=28,
            audio_path="/tmp/a.wav",
            audio_sha256=None,
            start_ms=0,
            end_ms=cursor,
            duration_ms=cursor,
            shot_type="medium",
            camera_movement="static",
            visual_objective="",
            caption_cue_ids=[],
            primary_asset=None,
            visual_beats=beats,
        )
        return RenderManifestDraft(
            scene_manifest_id=1,
            narration_run_id=1,
            caption_run_id=None,
            topic_id=1,
            plan_id=1,
            script_id=1,
            experiment_id=None,
            input_hash="h" * 64,
            render_schema_version="Render-v1",
            compositor_version="compositor-1.0.0",
            total_scene_count=1,
            total_duration_ms=cursor,
            scenes=[scene],
            caption_burn_in=False,
            width=1080,
            height=1920,
            fps=fps,
        ), cursor

    def test_beat_clip_durations_sum_to_the_scene_duration(self):
        """Per-beat rounding must telescope away, not accumulate."""
        from unittest.mock import MagicMock, patch

        from app.media.backend import FFmpegRenderBackend

        # Durations chosen so independent rounding would drift.
        draft, total_ms = self._draft(
            [
                5909,
                4468,
                2162,
                1598,
                4716,
                2557,
                3277,
                3037,
                6314,
                5755,
                3117,
                5994,
                5355,
                5834,
                4133,
            ]
        )
        backend = FFmpegRenderBackend()
        with patch(
            "app.media.backend.subprocess.run", return_value=MagicMock(returncode=0, stderr="")
        ):
            clips = backend._generate_scene_clips(
                draft, Path("/tmp/ace-beat-timing"), "libx264", allow_placeholders=False
            )

        assert len(clips) == 15
        frame_total = sum(round(d * draft.fps / 1000) for _, d in clips)
        assert frame_total == round(total_ms * draft.fps / 1000)

    def test_scene_without_beats_still_renders_as_one_clip(self):
        """Backward compatibility: the beat layer is additive."""
        from unittest.mock import MagicMock, patch

        from app.media.backend import FFmpegRenderBackend

        draft, total_ms = self._draft([4000])
        draft.scenes[0].visual_beats = []
        draft.scenes[0].primary_asset = None
        backend = FFmpegRenderBackend()
        with patch(
            "app.media.backend.subprocess.run", return_value=MagicMock(returncode=0, stderr="")
        ):
            clips = backend._generate_scene_clips(
                draft, Path("/tmp/ace-beat-timing"), "libx264", allow_placeholders=True
            )
        assert len(clips) == 1
        assert clips[0][1] == total_ms

    def test_contained_still_does_not_reintroduce_frame_drift(self):
        """The zoompan drift bug was still-image specific; contain must not repeat it."""
        from unittest.mock import MagicMock, patch

        from app.media.backend import FFmpegRenderBackend
        from app.media.models import RenderManifestDraft, RenderSceneDraft, RenderVisualBeat

        durations = [5909, 4468, 2162, 1598, 4716, 2557, 3277]
        beats, cursor = [], 0
        for i, duration in enumerate(durations):
            beats.append(
                RenderVisualBeat(
                    beat_index=i,
                    start_ms=cursor,
                    end_ms=cursor + duration,
                    duration_ms=duration,
                    local_path="/tmp/asset.jpg",
                    media_type=MEDIA_ILLUSTRATION,
                    motion=MOTION_NONE,
                    fit_mode=FIT_CONTAIN,
                )
            )
            cursor += duration
        scene = RenderSceneDraft(
            scene_index=0,
            scene_id=1,
            segment_id=10,
            narration_asset_id=28,
            audio_path="/tmp/a.wav",
            audio_sha256=None,
            start_ms=0,
            end_ms=cursor,
            duration_ms=cursor,
            shot_type="medium",
            camera_movement="static",
            visual_objective="",
            caption_cue_ids=[],
            primary_asset=None,
            visual_beats=beats,
        )
        draft = RenderManifestDraft(
            scene_manifest_id=1,
            narration_run_id=1,
            caption_run_id=None,
            topic_id=1,
            plan_id=1,
            script_id=1,
            experiment_id=None,
            input_hash="h" * 64,
            render_schema_version="Render-v1",
            compositor_version="compositor-1.0.0",
            total_scene_count=1,
            total_duration_ms=cursor,
            scenes=[scene],
            caption_burn_in=False,
            width=1080,
            height=1920,
            fps=30,
        )

        captured_cmds = []

        def _record(cmd, **kwargs):
            captured_cmds.append(cmd)
            return MagicMock(returncode=0, stderr="")

        backend = FFmpegRenderBackend()
        with patch("app.media.backend.subprocess.run", side_effect=_record):
            clips = backend._generate_scene_clips(
                draft, Path("/tmp/ace-beat-timing"), "libx264", allow_placeholders=False
            )

        assert len(clips) == len(durations)
        frame_total = sum(round(d * 30 / 1000) for _, d in clips)
        assert frame_total == round(cursor * 30 / 1000)

        # Every still clip must bound its output to an exact frame count so
        # the contain treatment cannot drift the video track, same as cover.
        for cmd in captured_cmds:
            assert "-frames:v" in cmd
            vf = cmd[cmd.index("-vf") + 1]
            assert "overlay" in vf  # contain treatment applied, not a plain crop


# ---------------------------------------------------------------------------
# QA gate
# ---------------------------------------------------------------------------


def _resolution(
    index: int,
    *,
    duration_ms: int = 4000,
    asset_key: str | None = None,
    path: str = __file__,
    commercial_safe: bool = True,
    license_status: str = "verified",
    provider: str = "pexels",
    descriptors: list[str] | None = None,
) -> BeatResolution:
    return BeatResolution(
        beat=_beat(index=index, duration_ms=duration_ms),
        media_type=MEDIA_VIDEO,
        local_path=path,
        asset_key=asset_key or f"pexels:{index}",
        provider=provider,
        license_status=license_status,
        commercial_safe=commercial_safe,
        score=0.8,
        descriptors=descriptors or [f"tag{index}", "dna"],
    )


def _plan(resolutions: list[BeatResolution]) -> VisualPlan:
    return VisualPlan(
        scene_manifest_id=5,
        topic_id=4,
        experiment_id=None,
        channel_key="channel-a",
        engine_version="1.0",
        planner_version="1.0",
        beats=[r.beat for r in resolutions],
        resolutions=resolutions,
    )


class TestVisualQaGate:
    def test_healthy_plan_passes(self):
        report = audit_visual_plan(_plan([_resolution(i) for i in range(15)]))
        assert report.status == QA_PASS

    def test_one_asset_dominating_the_video_blocks_release(self):
        """The Video 2 defect: a single clip covering most of the runtime."""
        resolutions = [
            _resolution(i, asset_key="pexels:same", duration_ms=20_000) for i in range(2)
        ]
        resolutions += [_resolution(i + 2) for i in range(3)]
        report = audit_visual_plan(_plan(resolutions))
        assert report.status == QA_FAIL
        assert any(f.code == CODE_DOMINANT_ASSET for f in report.blocking)

    def test_too_few_visual_changes_blocks_release(self):
        """Three coarse scenes over a minute is not short-form pacing."""
        report = audit_visual_plan(_plan([_resolution(i, duration_ms=20_000) for i in range(3)]))
        assert any(f.code == CODE_LOW_VISUAL_CHANGE for f in report.blocking)

    def test_unlicensed_asset_blocks_release(self):
        resolutions = [_resolution(i) for i in range(14)]
        resolutions.append(_resolution(14, commercial_safe=False, license_status=LICENSE_UNSAFE))
        report = audit_visual_plan(_plan(resolutions))
        assert any(f.code == CODE_UNSAFE_LICENSE for f in report.blocking)

    def test_missing_asset_file_blocks_release(self):
        resolutions = [_resolution(i) for i in range(14)]
        resolutions.append(_resolution(14, path="/nonexistent/asset.mp4"))
        report = audit_visual_plan(_plan(resolutions))
        assert report.status == QA_FAIL

    def test_unresolved_beat_blocks_release(self):
        resolutions = [_resolution(i) for i in range(14)]
        unresolved = _resolution(14)
        unresolved.local_path = None
        resolutions.append(unresolved)
        report = audit_visual_plan(_plan(resolutions))
        assert report.status == QA_FAIL

    def test_empty_plan_fails(self):
        assert audit_visual_plan(_plan([])).status == QA_FAIL

    def test_generated_graphics_are_not_counted_as_weak_retrieval(self):
        """Choosing a graphic is a decision, not a low-confidence retrieval."""
        resolutions = [_resolution(i) for i in range(15)]
        for r in resolutions[:8]:
            r.provider = "programmatic"
            r.score = 0.1
        assert audit_visual_plan(_plan(resolutions)).low_confidence_count == 0
