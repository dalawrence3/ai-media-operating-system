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
    INTENT_COMPARISON,
    LICENSE_UNSAFE,
    MAX_REJECTED_CANDIDATES_PERSISTED,
    MEDIA_GRAPHIC,
    MEDIA_ILLUSTRATION,
    MEDIA_PHOTO,
    MEDIA_VIDEO,
    MIN_EVIDENCE_WEIGHT,
    MOTION_NONE,
    QA_FAIL,
    QA_PASS,
    STRUCTURAL_INTENTS,
)
from app.visuals.engine import (
    FALLBACK_ALL_REJECTED,
    FALLBACK_NO_CANDIDATES,
    VisualEngine,
    VisualEngineConfig,
    _diagram_continuation_intents,
)
from app.visuals.memory import get_asset_usage, record_asset_usage
from app.visuals.models import (
    BeatResolution,
    CandidateEvidence,
    VisualBeat,
    VisualCandidate,
    VisualPlan,
)
from app.visuals.motion import choose_fit, motion_filter
from app.visuals.policy import STYLE_FAST_CUT, resolve_policy
from app.visuals.providers.base import ProviderCapability
from app.visuals.qa import (
    CODE_DOMINANT_ASSET,
    CODE_LOW_VISUAL_CHANGE,
    CODE_UNSAFE_LICENSE,
    audit_visual_plan,
)
from app.visuals.repository import list_candidate_evidence, save_visual_plan
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


@pytest.fixture()
def evidence_db() -> sqlite3.Connection:
    """An in-memory DB with visual_beats AND the new v52 evidence table.

    Foreign keys are explicitly enabled (they are not by default for a raw
    sqlite3 connection) so the ON DELETE CASCADE this table relies on for
    idempotent re-save is actually exercised, matching production's
    open_db, which turns the pragma on unconditionally.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    from app.core.database import (
        _apply_v44_visual_intelligence,
        _apply_v52_visual_candidate_evidence,
    )

    conn.execute("CREATE TABLE IF NOT EXISTS scene_manifests (id INTEGER PRIMARY KEY)")
    _apply_v44_visual_intelligence(conn)
    _apply_v52_visual_candidate_evidence(conn)
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
        """'repair' matches a car garage and a DNA repair diagram equally well.

        Phase 4: a single ordinary word still can never ACCEPT outright, but
        provider metadata this sparse is exactly what actual-content
        verification exists to resolve, rather than discarding a candidate
        that might genuinely be fine — so this now defers to VERIFY instead
        of a hard reject, and must never become an automatic accept."""
        beat = _beat(
            "So the cell's repair choice determines your outcome.",
            keywords=["repair", "choice", "cell"],
            entities=[],
        )
        ctx = ScoringContext(topic_terms=["dna", "repair"], topic_entities=["DNA"])
        garage = score_candidate(beat, _candidate(tags=["car", "repair", "garage", "welding"]), ctx)
        assert garage.accepted is False
        assert garage.verify_eligible is True
        assert garage.disposition == "verify"

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


# ---------------------------------------------------------------------------
# Phase 18G — concrete_subjects threading (Part 1)
# ---------------------------------------------------------------------------


class TestConcreteSubjectThreading:
    def test_concrete_subject_phrase_splits_into_separate_query_words(self):
        """_push() already splits a multi-word topic term on whitespace —
        "Cosmic horizon" must not become one unmatchable lexical blob."""
        topic_terms = semantics.merge_topic_terms(["Cosmic horizon"], [], limit=4)
        queries = semantics.build_queries(
            "What lies beyond what we can ever observe.",
            intent="entity",
            topic_terms=topic_terms,
        )
        assert queries
        assert any(
            "cosmic" in q.lower().split() and "horizon" in q.lower().split() for q in queries
        ), queries

    def test_concrete_subject_phrase_reaches_topic_coherence_scoring(self):
        """_join() tokenizes every topic_terms element, so a phrase-derived
        term reaches real scoring, not only query construction."""
        ctx = ScoringContext(topic_terms=semantics.merge_topic_terms(["Cosmic horizon"], []))
        beat = _beat(
            text="What lies beyond what we can ever observe.",
            keywords=[],
            entities=[],
            intent="entity",
        )
        candidate = _candidate(tags=["cosmic", "horizon", "space"], asset_id="ch1")
        scored = score_candidate(beat, candidate, ctx)
        assert scored.factors["topic"] > 0.6  # exceeds the no-vocabulary neutral value

    def test_concrete_subject_acronym_becomes_high_specificity_entity(self):
        """The real gap the plan revision found: _term_specificity's
        high-specificity bypass reads ctx.topic_entities, not topic_terms —
        an acronym inside a concrete subject phrase must be extracted into
        topic_entities per-subject for a single match to clear the evidence
        floor, exactly like a beat-level acronym already does."""
        concrete_subjects = ["CMB visualization"]
        topic_entities = sorted(
            {e for subject in concrete_subjects for e in semantics.extract_entities(subject)}
        )
        assert "CMB" in topic_entities

        ctx = ScoringContext(
            topic_terms=semantics.merge_topic_terms(concrete_subjects, []),
            topic_entities=topic_entities,
        )
        beat = _beat(text="Some deep space imagery.", keywords=[], entities=[], intent="entity")
        candidate = _candidate(tags=["cmb", "map"], asset_id="cmb1")
        scored = score_candidate(beat, candidate, ctx)
        assert scored.factors["evidence"] >= MIN_EVIDENCE_WEIGHT
        assert scored.rejected_reason not in (REASON_WEAK_EVIDENCE, REASON_NO_RELEVANCE_EVIDENCE)

    def test_no_concrete_subjects_leaves_narration_only_behaviour_unchanged(self):
        narration_terms = semantics.extract_terms("Space is expanding in every direction.", limit=6)
        assert semantics.merge_topic_terms(None, narration_terms, limit=6) == narration_terms[:6]
        assert semantics.merge_topic_terms([], narration_terms, limit=6) == narration_terms[:6]


# ---------------------------------------------------------------------------
# Phase 18G — extract_terms / specificity hardening (Part 2)
# ---------------------------------------------------------------------------


class TestNarrationVocabularyHardening:
    def test_sentence_initial_capital_gets_no_capitalisation_bonus(self):
        """extract_terms() must apply the same sentence-boundary check
        extract_entities() already applies: "Astronomers" opens the second
        sentence and must not be treated as more likely a named entity than
        "Hubble", which is genuinely mid-sentence."""
        text = (
            "Consider the night sky. Astronomers study distant galaxies using the Hubble telescope."
        )
        terms = semantics.extract_terms(text, limit=8)
        assert "hubble" in terms and "astronomers" in terms
        assert terms.index("hubble") < terms.index("astronomers")

    def test_generic_agent_term_alone_cannot_clear_the_evidence_floor(self):
        """Before this fix, "scientists" (11 chars) scored 0.9 specificity
        purely on length — exactly enough to single-handedly clear
        MIN_EVIDENCE_WEIGHT and pass generic "scientists in a lab" stock as
        relevant to any beat."""
        ctx = ScoringContext(topic_terms=["scientists"])
        beat = _beat(
            text="Scientists explain the science.",
            keywords=["scientists"],
            entities=[],
            intent="entity",
        )
        candidate = _candidate(tags=["scientists", "lab", "researcher"], asset_id="sci1")
        scored = score_candidate(beat, candidate, ctx)
        # Phase 4: still never an automatic accept — generic "scientists in a
        # lab" stock is exactly the kind of sparse-metadata case actual-content
        # verification exists for, so it defers to VERIFY rather than
        # discarding outright.
        assert scored.accepted is False
        assert scored.verify_eligible is True

    def test_ordinary_specific_term_of_similar_length_is_unaffected(self):
        """The generic-agent fix must not blunt specificity for real subject
        vocabulary that happens to share a similar length."""
        ctx = ScoringContext(topic_terms=["cosmology"])
        beat = _beat(
            text="Cosmology explains the structure of the universe.",
            keywords=["cosmology"],
            entities=[],
            intent="entity",
        )
        candidate = _candidate(tags=["cosmology", "universe", "space"], asset_id="cos1")
        scored = score_candidate(beat, candidate, ctx)
        assert scored.rejected_reason is None


# ---------------------------------------------------------------------------
# Phase 18G — cosmology regression (Part 3)
#
# Reconstructed from the Phase 18G audit's beat table, not the live DB: the
# real narration (18 beats across several scenes) explains that the universe
# has no edge, using the balloon-inflation analogy. Assertions are semantic,
# not exact-string, since this reconstruction approximates the real wording.
# ---------------------------------------------------------------------------

_COSMOLOGY_SENTENCES = [
    "Do you ever wonder if the universe has an edge?",
    "Do you hit a wall? Do you fall off?",
    "Scientists think space itself is expanding in every direction, stretching the "
    "fabric of the cosmos.",
    "Expanding in these terms doesn't mean galaxies fly through space; space itself "
    "grows between them.",
    "Imagine drawing dots on a balloon and blowing it up.",
    "Every dot moves away from every other dot, and none of them sits at the center.",
    "There's no boundary and no center on the balloon's surface, just more surface "
    "appearing between the dots.",
    "Similarly, the universe works the same way, with no edge and no middle point.",
    "The observable universe is like a bubble around us; beyond it, light simply "
    "hasn't had time to reach us yet.",
    "We genuinely don't know what lies beyond that bubble.",
    "Even if the universe is infinite, the balloon analogy can still curve back on itself.",
    "This mind-bending idea, explained — follow for more science.",
]
_COSMOLOGY_SCRIPT = " ".join(_COSMOLOGY_SENTENCES)


class TestCosmologyRegression:
    def test_scientists_is_not_a_top_ranked_term(self):
        terms = semantics.extract_terms(_COSMOLOGY_SCRIPT, limit=4)
        assert "scientists" not in terms[:2]

    def test_concrete_subjects_lead_the_merged_topic_terms(self):
        subjects = ["Cosmic horizon", "Diagrams of universe structure", "CMB visualization"]
        narration_terms = semantics.extract_terms(_COSMOLOGY_SCRIPT, limit=8)
        merged = semantics.merge_topic_terms(subjects, narration_terms, limit=4)
        assert merged[:3] == subjects

    def test_opening_beat_queries_do_not_anchor_on_scientists(self):
        topic_terms = semantics.merge_topic_terms(
            None, semantics.extract_terms(_COSMOLOGY_SCRIPT, limit=8), limit=4
        )
        queries = semantics.build_queries(
            _COSMOLOGY_SENTENCES[0], intent="entity", topic_terms=topic_terms
        )
        assert not any("scientist" in q.lower() for q in queries)

    def test_minority_of_beats_anchor_on_scientists(self):
        """Regression guard: the audit observed roughly 16 of 18 beats
        anchored on "scientists" before this fix."""
        scenes = [
            BeatPlannerInput(
                scene_index=i,
                scene_id=i,
                segment_id=i,
                start_ms=i * 3000,
                duration_ms=3000,
                narration_text=text,
            )
            for i, text in enumerate(_COSMOLOGY_SENTENCES)
        ]
        beats = plan_beats(scenes)
        anchored = [b for b in beats if any("scientist" in q.lower() for q in b.search_queries)]
        assert len(anchored) <= len(beats) // 2

    def test_concrete_narration_content_still_extractable(self):
        terms = semantics.extract_terms(_COSMOLOGY_SCRIPT, limit=10)
        assert "balloon" in terms
        entities = semantics.extract_entities("Everything traces back to the Big Bang.")
        assert "Big Bang" in entities


# ---------------------------------------------------------------------------
# Phase 18G — diagram-continuation fallback routing (Part 4)
#
# Built directly from the real scene_index=1 beat fixture (cosmology
# publication, scene_manifest_id=8), queried read-only from the live DB
# during the audit.
# ---------------------------------------------------------------------------


def _scene_beat(
    index: int, intent: str, keywords: list[str], *, scene_index: int = 1
) -> VisualBeat:
    return VisualBeat(
        beat_index=index,
        scene_index=scene_index,
        scene_id=1,
        segment_id=37,
        start_ms=index * 1000,
        end_ms=(index + 1) * 1000,
        duration_ms=1000,
        narration_text=" ".join(keywords),
        keywords=keywords,
        entities=[],
        visual_intent=intent,
        media_type_preferences=[MEDIA_VIDEO, MEDIA_PHOTO, MEDIA_GRAPHIC],
        search_queries=[" ".join(keywords[:2])],
    )


def _cosmology_scene_beats() -> list[VisualBeat]:
    return [
        _scene_beat(5, "comparison", ["expanding", "terms"]),
        _scene_beat(6, "action", ["imagine", "drawing", "balloon", "blowing", "dots"]),
        _scene_beat(7, "entity", ["dot", "moves", "away"]),
        _scene_beat(8, "comparison", ["boundary", "center", "balloon", "surface"]),
        _scene_beat(9, "entity", ["similarly", "universe", "works"]),
        _scene_beat(12, "comparison", ["observable", "bubble", "beyond", "lies"]),
        _scene_beat(13, "entity", ["genuinely", "don't"]),
        _scene_beat(14, "comparison", ["infinite", "balloon", "curve", "back"]),
    ]


class TestDiagramContinuationRouting:
    def test_balloon_and_dot_beats_borrow_a_structural_renderer(self):
        """Beats 6 and 7 are concrete and diagrammable (a balloon-inflation
        analogy) but are classified action/entity, not comparison. Verified
        against the real data: neither reaches a structural neighbour
        through an IMMEDIATE-neighbour check (beat 6 vs beat 5 shares
        nothing; beat 7 vs beat 8 shares nothing) — only a transitive
        connected-components search finds beat 6 -> beat 8/14 via "balloon"
        and beat 7 -> beat 6 via "dot"/"dots"."""
        result = _diagram_continuation_intents(_cosmology_scene_beats())
        assert result[6] in STRUCTURAL_INTENTS
        assert result[7] in STRUCTURAL_INTENTS

    def test_immediate_neighbour_overlap_alone_would_have_failed(self):
        """Documents why a simpler immediate-neighbour design was rejected."""
        beats = _cosmology_scene_beats()
        beat5, beat6, beat7, beat8 = beats[0], beats[1], beats[2], beats[3]
        assert not (set(beat6.keywords) & set(beat5.keywords))
        assert not (set(beat7.keywords) & set(beat8.keywords))

    def test_isolated_beats_are_not_included(self):
        """Beat 13 shares no vocabulary with anything; beat 9 only connects
        to a non-structural sibling. Neither should borrow a renderer."""
        result = _diagram_continuation_intents(_cosmology_scene_beats())
        assert 13 not in result
        assert 9 not in result

    def test_existing_structural_beats_are_never_in_the_map(self):
        result = _diagram_continuation_intents(_cosmology_scene_beats())
        for beat in _cosmology_scene_beats():
            if beat.visual_intent in STRUCTURAL_INTENTS:
                assert beat.beat_index not in result

    def test_cta_never_borrows_even_with_synthetic_overlap(self):
        beats = _cosmology_scene_beats()
        cta = _scene_beat(17, "cta", ["balloon", "mind-bending", "explained"])
        result = _diagram_continuation_intents([*beats, cta])
        assert 17 not in result

    def test_resolve_end_to_end_uses_borrowed_renderer_not_a_text_card(self, tmp_path):
        """The full resolve() path: beats 6/7 with nothing retrieved must
        get a graphic tagged with the borrowed structural intent, not the
        default text-card fallback. Their structural sibling (beat 8) must
        be part of the same resolve() call — the continuation map is
        computed once per call from the beats actually passed in, exactly
        as it would be from a scene's full beat list in production."""
        beats = _cosmology_scene_beats()
        beat6, beat7, beat8 = beats[1], beats[2], beats[3]
        engine = _engine(tmp_path, [_StubProvider(results=[])])
        plan = engine.resolve([beat6, beat7, beat8])
        r6, r7 = plan.resolutions[0], plan.resolutions[1]
        assert r6.media_type == MEDIA_GRAPHIC and r7.media_type == MEDIA_GRAPHIC
        assert r6.render_intent_override == INTENT_COMPARISON
        assert r7.render_intent_override == INTENT_COMPARISON
        assert r6.fallback_reason in (FALLBACK_NO_CANDIDATES, FALLBACK_ALL_REJECTED)


# ---------------------------------------------------------------------------
# Phase 18G — candidate evidence persistence (Parts 5-6)
# ---------------------------------------------------------------------------


class TestCandidateEvidence:
    def test_selected_candidate_is_recorded_as_selected(self, tmp_path):
        winner = _candidate(asset_id="win", tags=["dna", "helix", "cas9"])
        engine = _engine(tmp_path, [_StubProvider(identity="pexels", results=[winner])])
        plan = engine.resolve([_beat()])
        evidence = plan.resolutions[0].candidate_evidence
        assert evidence
        selected = [e for e in evidence if e.decision == "selected"]
        assert len(selected) == 1
        assert selected[0].asset_key == winner.asset_key
        assert selected[0].rejection_reason is None

    def test_acceptable_runner_up_is_eligible_not_selected_not_rejected(self, tmp_path):
        """Two candidates that both genuinely clear every threshold: only
        one becomes the resolution, but the other must not be mislabelled
        "rejected" — it simply wasn't chosen, and never got a rejection
        reason (rank_candidates confirms both are .accepted)."""
        one = _candidate(asset_id="one", tags=["dna", "helix", "cas9"])
        two = _candidate(asset_id="two", tags=["dna", "cas9", "strand"])
        engine = _engine(tmp_path, [_StubProvider(identity="pexels", results=[one, two])])
        plan = engine.resolve([_beat()])
        resolution = plan.resolutions[0]
        evidence = resolution.candidate_evidence
        assert {e.asset_key for e in evidence} == {one.asset_key, two.asset_key}
        runner_up = [e for e in evidence if e.asset_key != resolution.asset_key][0]
        assert runner_up.decision == "eligible_not_selected"
        assert runner_up.rejection_reason is None

    def test_below_threshold_candidate_is_rejected_with_a_real_reason(self, tmp_path):
        winner = _candidate(asset_id="win", tags=["dna", "helix", "cas9"])
        irrelevant = _candidate(asset_id="bad", tags=["surfer", "ocean", "waves"])
        engine = _engine(tmp_path, [_StubProvider(identity="pexels", results=[winner, irrelevant])])
        plan = engine.resolve([_beat()])
        evidence = plan.resolutions[0].candidate_evidence
        rejected = [e for e in evidence if e.asset_key == irrelevant.asset_key]
        assert rejected, evidence
        assert rejected[0].decision == "rejected"
        assert rejected[0].rejection_reason == REASON_NO_RELEVANCE_EVIDENCE

    def test_all_rejected_fallback_preserves_accurate_evidence(self, tmp_path):
        """A beat that falls back to a graphic still records why every
        candidate it actually retrieved was or wasn't usable — never a
        fabricated "rejected" for one that simply wasn't chosen."""
        irrelevant = _candidate(asset_id="bad", tags=["surfer", "ocean", "waves"])
        engine = _engine(tmp_path, [_StubProvider(results=[irrelevant])])
        plan = engine.resolve([_beat()])
        resolution = plan.resolutions[0]
        assert resolution.media_type == MEDIA_GRAPHIC
        evidence = resolution.candidate_evidence
        assert evidence
        assert all(e.decision != "selected" for e in evidence)
        assert evidence[0].decision == "rejected"
        assert evidence[0].rejection_reason == REASON_NO_RELEVANCE_EVIDENCE

    def test_evidence_row_count_is_bounded(self, tmp_path):
        candidates = [_candidate(asset_id=str(i), tags=["dna", "helix", "cas9"]) for i in range(10)]
        engine = _engine(tmp_path, [_StubProvider(identity="pexels", results=candidates)])
        plan = engine.resolve([_beat()])
        evidence = plan.resolutions[0].candidate_evidence
        assert len(evidence) <= 1 + MAX_REJECTED_CANDIDATES_PERSISTED

    def test_evidence_persists_and_survives_idempotent_resave(self, evidence_db):
        evidence_db.execute("INSERT INTO scene_manifests (id) VALUES (5)")
        winner = _candidate(asset_id="win", tags=["dna", "helix", "cas9"])
        runner_up = _candidate(asset_id="runner", tags=["dna", "cas9", "strand"])
        beat = _beat()
        resolution = BeatResolution(
            beat=beat,
            media_type=MEDIA_VIDEO,
            local_path="/tmp/x.mp4",
            asset_key=winner.asset_key,
            provider="pexels",
            candidate_evidence=[
                CandidateEvidence(
                    provider="pexels",
                    provider_asset_id="win",
                    asset_key=winner.asset_key,
                    title="dna helix",
                    tags=["dna", "helix"],
                    query="dna cas9",
                    score=0.9,
                    decision="selected",
                ),
                CandidateEvidence(
                    provider="pexels",
                    provider_asset_id="runner",
                    asset_key=runner_up.asset_key,
                    title="dna strand",
                    tags=["dna", "strand"],
                    query="dna cas9",
                    score=0.6,
                    decision="eligible_not_selected",
                ),
            ],
        )
        plan = VisualPlan(
            scene_manifest_id=5,
            topic_id=4,
            experiment_id=None,
            channel_key="channel-a",
            engine_version="1.0",
            planner_version="1.0",
            beats=[beat],
            resolutions=[resolution],
        )
        save_visual_plan(evidence_db, plan)
        rows = evidence_db.execute("SELECT id FROM visual_beats").fetchall()
        assert len(rows) == 1
        first_count = evidence_db.execute(
            "SELECT COUNT(*) AS n FROM visual_candidate_evidence"
        ).fetchone()["n"]
        assert first_count == 2

        evidence = list_candidate_evidence(evidence_db, rows[0]["id"])
        decisions = {e.asset_key: e.decision for e in evidence}
        assert decisions[winner.asset_key] == "selected"
        assert decisions[runner_up.asset_key] == "eligible_not_selected"

        # Re-save the identical plan: row count must not double.
        save_visual_plan(evidence_db, plan)
        second_count = evidence_db.execute(
            "SELECT COUNT(*) AS n FROM visual_candidate_evidence"
        ).fetchone()["n"]
        assert second_count == first_count


# ---------------------------------------------------------------------------
# Phase 18G — v52 schema migration
# ---------------------------------------------------------------------------


class TestV52Migration:
    def test_migration_is_idempotent_and_creates_expected_columns(self):
        from app.core.database import _apply_v52_visual_candidate_evidence

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE visual_beats (id INTEGER PRIMARY KEY)")
        _apply_v52_visual_candidate_evidence(conn)
        _apply_v52_visual_candidate_evidence(conn)  # must not raise
        columns = {r[1] for r in conn.execute("PRAGMA table_info('visual_candidate_evidence')")}
        assert {"decision", "rejection_reason", "visual_beat_id", "asset_key"} <= columns

    def test_schema_version_is_52(self):
        from app.core.database import SCHEMA_VERSION

        assert SCHEMA_VERSION == 60


# ---------------------------------------------------------------------------
# Phase 18G — polysemous single-word evidence hardening (Part 7)
#
# Reconstructed read-only from the live DB (render_manifest_id=12,
# scene_manifest_id=10, "galaxy formation astrophysics astronomy",
# publication 7, published 2026-09-05) and from
# artifacts/visuals/cache/pexels_b886deacefd94ac2.mp4.attr.json: beat 13
# (scene_index=2) resolved to pexels:6455082, titled "man and woman working
# out on elliptical bicycles", for the narration "Violent collisions between
# galaxies can scramble everything into an elliptical blob." The candidate's
# only shared vocabulary with the beat was the single word "elliptical" (10
# letters -> 0.9 specificity by length alone, exactly meeting the pre-fix
# MIN_EVIDENCE_WEIGHT floor of 0.9), and it was accepted as a *primary* match
# (fallback_reason was NULL in visual_beats) — not a lenient fallback path.
# ---------------------------------------------------------------------------

_GALAXY_TOPIC_TERMS = ["galaxy", "formation", "astrophysics", "astronomy", "cosmic", "stars"]


def _galaxy_collision_beat() -> VisualBeat:
    return _beat(
        "Violent collisions between galaxies can scramble everything into an elliptical blob.",
        keywords=["collisions", "everything", "elliptical", "galaxies", "violent", "scramble"],
        entities=[],
        intent="action",
    )


def _gym_bicycle_candidate() -> VisualCandidate:
    """The real pexels:6455082 asset that slipped through, by its real title."""
    return _candidate(
        asset_id="6455082",
        tags=["man", "woman", "working", "out", "elliptical", "bicycles", "gym", "fitness"],
        query="collisions everything elliptical",
    )


def _spiral_galaxy_candidate() -> VisualCandidate:
    """A plausible on-topic astronomy stock candidate for the same beat."""
    return _candidate(
        asset_id="galaxy-collision-1",
        tags=["galaxies", "collisions", "elliptical", "violent", "cosmic", "space"],
        query="collisions everything elliptical",
    )


class TestPolysemousSingleWordEvidence:
    def test_gym_bicycle_candidate_is_never_accepted_for_astronomy_beat(self):
        """A single shared word ("elliptical") that is long but ordinary and
        polysemous must not alone carry an unrelated candidate over the
        evidence floor, regardless of how that length scores in isolation.

        Phase 4: the deterministic scorer alone no longer has to be the
        candidate's only chance — a sparse-metadata match like this may
        defer to actual-content verification (§3's explicit allowance:
        "REJECT deterministically OR VERIFY -> vision rejects") — but it
        must NEVER become an automatic accept either way. See
        TestVisionRescue's real-incident regression for proof that when this
        exact candidate is actually run through vision, it is rejected."""
        beat = _galaxy_collision_beat()
        ctx = ScoringContext(topic_terms=_GALAXY_TOPIC_TERMS, topic_entities=[])
        scored = score_candidate(beat, _gym_bicycle_candidate(), ctx)
        assert scored.accepted is False
        assert scored.disposition in ("verify", "reject")
        assert scored.factors["evidence"] >= MIN_EVIDENCE_WEIGHT  # length alone still hits 0.9
        assert scored.factors["corroborated"] == 0.0

    def test_gym_bicycle_candidate_cannot_win_even_as_sole_candidate(self):
        """Repair-scenario for section 8's hard floor: if the only retrieved
        candidate is the mismatched one, it must not become the accepted
        asset merely because nothing better was found."""
        beat = _galaxy_collision_beat()
        ctx = ScoringContext(topic_terms=_GALAXY_TOPIC_TERMS, topic_entities=[])
        ranked = rank_candidates(beat, [_gym_bicycle_candidate()], ctx)
        assert not any(r.accepted for r in ranked)

    def test_astronomy_candidate_outranks_and_beats_the_gym_clip(self):
        """When a genuinely relevant candidate is present, it must win the
        beat outright over the polysemous mismatch."""
        beat = _galaxy_collision_beat()
        ctx = ScoringContext(topic_terms=_GALAXY_TOPIC_TERMS, topic_entities=[])
        ranked = rank_candidates(beat, [_gym_bicycle_candidate(), _spiral_galaxy_candidate()], ctx)
        assert ranked[0].candidate.provider_asset_id == "galaxy-collision-1"
        assert ranked[0].accepted
        gym_result = next(r for r in ranked if r.candidate.provider_asset_id == "6455082")
        assert not gym_result.accepted

    def test_two_independent_matched_terms_is_sufficient_without_an_entity(self):
        """The fix must not become "single word never enough" in a way that
        blocks genuinely well-tagged candidates that share real vocabulary
        with the beat, not just one incidental word."""
        beat = _galaxy_collision_beat()
        ctx = ScoringContext(topic_terms=_GALAXY_TOPIC_TERMS, topic_entities=[])
        candidate = _candidate(
            asset_id="galaxy-2",
            tags=["galaxies", "elliptical", "cosmic"],
            query="collisions everything elliptical",
        )
        scored = score_candidate(beat, candidate, ctx)
        assert scored.factors["corroborated"] == 1.0
        assert scored.rejected_reason is None


# ---------------------------------------------------------------------------
# Phase 18G — Part 2 (Phase 2): context-aware query generation (§1)
#
# The real `topic_terms` for the galaxy video are dominated by a multi-word
# `concrete_subjects` phrase (reconstructed from the shape of the queries
# actually persisted for every beat in render_manifest_id=12: "galaxy
# formation process and" appeared, truncated identically, on every single
# beat). Uncapped, that phrase alone consumed build_queries' entire
# max_words budget before any beat-specific word was added.
# ---------------------------------------------------------------------------

_GALAXY_CONCRETE_SUBJECT = "galaxy formation process and stages cosmic dust and gas clouds"


class TestContextAwareQueryGeneration:
    def test_long_topic_phrase_no_longer_starves_beat_specific_words(self):
        """Before the fix, an anchor built from a long concrete_subjects
        phrase consumed the whole word budget on its own generic words,
        producing the identical query for every beat and never reaching the
        beat's own vocabulary at all."""
        topic_terms = semantics.merge_topic_terms([_GALAXY_CONCRETE_SUBJECT], [], limit=4)
        queries = semantics.build_queries(
            "Violent collisions between galaxies can scramble everything into an elliptical blob.",
            intent="action",
            topic_terms=topic_terms,
        )
        assert queries
        assert any("elliptical" in q.lower().split() for q in queries)
        assert any(
            "galaxy" in q.lower().split() or "formation" in q.lower().split() for q in queries
        )

    def test_every_query_is_grounded_when_an_anchor_exists(self):
        """No query should be a bare, context-free clause fragment when the
        video's subject is known — that is what let a beat about galaxy
        collisions retrieve gym-bicycle stock on the word "elliptical" alone."""
        topic_terms = semantics.merge_topic_terms([_GALAXY_CONCRETE_SUBJECT], [], limit=4)
        queries = semantics.build_queries(
            "Violent collisions between galaxies can scramble everything into an elliptical blob.",
            intent="action",
            topic_terms=topic_terms,
        )
        anchor_tokens = {"galaxy", "formation"}
        assert all(anchor_tokens & set(q.lower().split()) for q in queries), queries

    def test_queries_are_still_diverse_not_one_repeated_phrase(self):
        topic_terms = semantics.merge_topic_terms([_GALAXY_CONCRETE_SUBJECT], [], limit=4)
        queries = semantics.build_queries(
            "Violent collisions between galaxies can scramble everything into an elliptical blob.",
            intent="action",
            topic_terms=topic_terms,
        )
        assert len(queries) >= 2
        assert len({q.lower() for q in queries}) == len(queries)

    def test_short_single_word_anchor_still_works_as_before(self):
        """The fix must not regress the common case where topic_terms are
        already short single words, not a long descriptive phrase."""
        queries = semantics.build_queries(
            "Cas9 slices both strands of your DNA",
            intent="entity",
            topic_terms=["dna", "cas9"],
        )
        assert queries
        assert any("dna" in q.lower().split() or "cas9" in q.lower().split() for q in queries)


# ---------------------------------------------------------------------------
# Phase 2 — fallback hierarchy tier 5 & text-card cascade mitigation (§5, §15)
# ---------------------------------------------------------------------------


class TestRelaxedStillFallbackTier:
    def test_relevant_still_below_score_floor_is_used_instead_of_a_text_card(self, tmp_path):
        """A candidate that clears relevance/corroboration/license but misses
        only the aggregate technical-fit floor should still beat a text card."""
        beat = VisualBeat(
            beat_index=0,
            scene_index=0,
            scene_id=1,
            segment_id=1,
            start_ms=0,
            end_ms=3000,
            duration_ms=3000,
            narration_text="Cas9 slices both strands of your DNA",
            keywords=["cas9", "slices", "strands", "dna"],
            entities=["Cas9", "DNA"],
            visual_intent="entity",
            media_type_preferences=["video", "illustration", "photo", "graphic"],
            search_queries=["dna cas9 strands"],
        )
        candidate = VisualCandidate(
            provider="stub",
            provider_asset_id="1",
            media_type=MEDIA_PHOTO,
            query="unrelated search phrase",
            width=200,
            height=150,
            commercial_safe=True,
            license_status="verified",
            tags=["dna"],
            title="dna",
            provider_rank=9,
            tier=3,
        )
        provider = _StubProvider(results=[candidate])
        engine = _engine(
            tmp_path, [provider], concrete_subjects=["dna", "cas9", "genome", "editing"]
        )
        plan = engine.resolve([beat])
        resolution = plan.resolutions[0]
        assert resolution.media_type == MEDIA_PHOTO
        assert resolution.fallback_reason == "relevant_still_relaxed_floor"

    def test_irrelevant_candidate_still_cannot_use_the_relaxed_tier(self, tmp_path):
        """The relaxed tier must never become a second "least-bad" door: a
        candidate the scorer actually rejected for lacking relevance evidence
        (not merely a technical shortfall) must still fall through to a
        generated graphic."""
        provider = _StubProvider(
            results=[_candidate(tags=["surfer", "ocean", "waves"], media_type=MEDIA_PHOTO)]
        )
        engine = _engine(tmp_path, [provider])
        plan = engine.resolve([_beat(media_prefs=[MEDIA_VIDEO, MEDIA_PHOTO, MEDIA_GRAPHIC])])
        assert plan.resolutions[0].media_type == MEDIA_GRAPHIC
        assert provider.downloads == []


class TestTextCardCascadeMitigation:
    def test_adjacent_unplanned_text_cards_hold_one_shared_visual(self, tmp_path):
        engine = _engine(tmp_path, [_StubProvider(results=[])])
        beats = [_beat(index=i, intent="action") for i in range(3)]
        plan = engine.resolve(beats)
        resolutions = plan.resolutions
        assert all(r.media_type == MEDIA_GRAPHIC for r in resolutions)
        assert resolutions[1].asset_key == resolutions[0].asset_key
        assert resolutions[2].asset_key == resolutions[0].asset_key
        assert not resolutions[0].held_visual
        assert resolutions[1].held_visual and resolutions[2].held_visual

    def test_non_adjacent_or_cross_scene_text_cards_are_not_merged(self, tmp_path):
        engine = _engine(tmp_path, [_StubProvider(results=[])])
        beats = [
            _beat(text="First scene narration line.", index=0, intent="action"),
            _beat(text="Second scene, entirely different narration.", index=1, intent="action"),
        ]
        beats[1].scene_index = 1  # a different scene: no continuity to hold
        plan = engine.resolve(beats)
        resolutions = plan.resolutions
        assert resolutions[0].asset_key != resolutions[1].asset_key
        assert not resolutions[0].held_visual and not resolutions[1].held_visual

    def test_single_isolated_text_card_beat_is_never_marked_held(self, tmp_path):
        engine = _engine(tmp_path, [_StubProvider(results=[])])
        plan = engine.resolve([_beat()])
        assert not plan.resolutions[0].held_visual
