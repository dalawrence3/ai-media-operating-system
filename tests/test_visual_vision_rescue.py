"""Phase 4 — vision-assisted candidate rescue (§15-18).

A textually-inconclusive candidate (scoring.REASON_NEEDS_VERIFICATION) is
tried through actual-content verification BEFORE a deficient beat falls back
to a text card — engine.VisualEngine.vision_rescue(). Covers the tri-state
scoring split, the rescue orchestration itself (rounds, batching, bounded
candidate count), and the real gym/galaxy and vaccine-canary regressions.

No real provider calls: `_StubProvider` and `_AssetKeyedVerifier` are both
synchronous, in-memory fakes.
"""

from __future__ import annotations

from pathlib import Path

from app.visuals.constants import MEDIA_PHOTO, MEDIA_VIDEO
from app.visuals.engine import (
    VISION_RESCUE_MAX_CANDIDATES_PER_BEAT,
    VisualEngine,
    VisualEngineConfig,
)
from app.visuals.models import VisualBeat, VisualCandidate
from app.visuals.policy import resolve_policy
from app.visuals.providers.base import ProviderCapability
from app.visuals.scoring import (
    REASON_AVOID_TERM,
    REASON_NEEDS_VERIFICATION,
    REASON_WEAK_EVIDENCE,
    ScoringContext,
    score_candidate,
)
from app.visuals.verification import (
    MAX_BEATS_PER_CONTACT_SHEET,
    STATUS_QUESTIONABLE,
    STATUS_REJECTED,
    STATUS_UNAVAILABLE,
    STATUS_VERIFIED,
    BeatIntent,
    BeatVerification,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _beat(
    text: str,
    *,
    index: int = 0,
    keywords: list[str] | None = None,
    entities: list[str] | None = None,
    avoid_terms: list[str] | None = None,
    intent: str = "entity",
    media_prefs: list[str] | None = None,
) -> VisualBeat:
    return VisualBeat(
        beat_index=index,
        scene_index=0,
        scene_id=1,
        segment_id=1,
        start_ms=index * 3000,
        end_ms=(index + 1) * 3000,
        duration_ms=3000,
        narration_text=text,
        keywords=keywords or [],
        entities=entities or [],
        avoid_terms=avoid_terms or [],
        visual_intent=intent,
        media_type_preferences=media_prefs or [MEDIA_VIDEO, MEDIA_PHOTO, "graphic"],
        search_queries=[text[:40]],
    )


def _candidate(
    *,
    asset_id: str = "1",
    tags: list[str] | None = None,
    media_type: str = MEDIA_PHOTO,
    provider: str = "pexels",
    query: str = "x",
) -> VisualCandidate:
    return VisualCandidate(
        provider=provider,
        provider_asset_id=asset_id,
        media_type=media_type,
        query=query,
        download_url=f"https://example.test/{asset_id}",
        width=1080,
        height=1920,
        duration_s=10.0,
        license_status="verified",
        commercial_safe=True,
        tags=tags or [],
        title=" ".join(tags or []),
    )


class _StubProvider:
    capability = ProviderCapability(
        media_types=(MEDIA_VIDEO, MEDIA_PHOTO),
        tier=1,
        cost_units=0,
        supports_orientation=True,
    )

    def __init__(self, identity: str = "pexels", results: list[VisualCandidate] | None = None):
        self.identity = identity
        self._results = results or []
        self.downloads: list[str] = []

    def available(self) -> bool:
        return True

    def search(self, query, *, media_type, limit=10, orientation=None):
        return [c for c in self._results if c.media_type == media_type]

    def download(self, candidate, cache_dir: Path):
        from PIL import Image

        self.downloads.append(candidate.asset_key)
        cache_dir.mkdir(parents=True, exist_ok=True)
        # A real, valid tiny PNG — extract_representative_frame() reads
        # photo/illustration candidates directly (no ffmpeg), so this must
        # actually decode, unlike a `.mp4`-suffixed stub with garbage bytes.
        dest = cache_dir / f"{candidate.asset_key.replace(':', '_')}.png"
        Image.new("RGB", (32, 32), (10, 20, 30)).save(dest)
        return dest


def _engine(tmp_path: Path, providers, **kwargs) -> VisualEngine:
    config = VisualEngineConfig(
        policy=resolve_policy(),
        cache_dir=tmp_path / "cache",
        graphics_dir=tmp_path / "graphics",
        **kwargs,
    )
    return VisualEngine(config, providers=providers)


class _AssetKeyedVerifier:
    """A verifier whose verdict is keyed by which asset_key ended up in each
    beat's downloaded candidate — set by the test via `expect`."""

    def __init__(self):
        self.expect: dict[int, list[str]] = {}  # beat_index -> [status, status, ...] per round
        self._round_calls: dict[int, int] = {}
        self.calls: list[list[int]] = []

    def verify(self, contact_sheet: bytes, beats: list[BeatIntent]) -> list[BeatVerification]:
        self.calls.append([b.beat_index for b in beats])
        out = []
        for b in beats:
            n = self._round_calls.get(b.beat_index, 0)
            statuses = self.expect.get(b.beat_index, [STATUS_UNAVAILABLE])
            status = statuses[min(n, len(statuses) - 1)]
            self._round_calls[b.beat_index] = n + 1
            out.append(BeatVerification(b.beat_index, status, "scripted", 0.8))
        return out


# ---------------------------------------------------------------------------
# §15 — tri-state scoring disposition
# ---------------------------------------------------------------------------


class TestTriStateDisposition:
    def test_strong_deterministic_candidate_accepts_without_vision(self):
        beat = _beat(
            "Cas9 slices both strands of your DNA",
            keywords=["cas9", "dna", "strands"],
            entities=["Cas9", "DNA"],
        )
        ctx = ScoringContext(topic_terms=["dna", "cas9"], topic_entities=["DNA", "Cas9"])
        candidate = _candidate(tags=["dna", "cas9", "helix"])
        scored = score_candidate(beat, candidate, ctx)
        assert scored.disposition == "accept"
        assert scored.verify_eligible is False

    def test_obvious_mismatch_rejects_outright(self):
        beat = _beat("Cas9 slices both strands of your DNA", entities=["Cas9", "DNA"])
        ctx = ScoringContext(topic_terms=["dna"], topic_entities=["DNA"])
        candidate = _candidate(tags=["surfer", "ocean", "waves"])
        scored = score_candidate(beat, candidate, ctx)
        assert scored.disposition == "reject"
        assert scored.verify_eligible is False

    def test_sparse_but_plausible_metadata_defers_to_verify(self):
        beat = _beat(
            "So the cell's repair choice determines your outcome.",
            keywords=["repair", "choice", "cell"],
            entities=[],
        )
        ctx = ScoringContext(topic_terms=["dna", "repair"], topic_entities=["DNA"])
        candidate = _candidate(tags=["car", "repair", "garage", "welding"])
        scored = score_candidate(beat, candidate, ctx)
        assert scored.disposition == "verify"
        assert scored.rejected_reason == REASON_NEEDS_VERIFICATION

    def test_single_polysemous_word_never_auto_accepts(self):
        beat = _beat(
            "Violent collisions between galaxies scramble everything into an elliptical blob.",
            keywords=["collisions", "everything", "elliptical", "galaxies", "violent"],
            entities=[],
            intent="action",
        )
        ctx = ScoringContext(
            topic_terms=["galaxy", "formation", "astrophysics", "astronomy"], topic_entities=[]
        )
        candidate = _candidate(
            tags=["man", "woman", "working", "elliptical", "bicycles", "gym"],
            query="collisions everything elliptical",
        )
        scored = score_candidate(beat, candidate, ctx)
        assert scored.accepted is False
        assert scored.disposition in ("verify", "reject")

    def test_avoid_term_conflict_rejects_even_if_score_is_plausible(self):
        beat = _beat(
            "The immune system remembers.",
            keywords=["immune", "remembers"],
            entities=[],
            avoid_terms=["bicycle", "gym"],
        )
        ctx = ScoringContext(topic_terms=["immune", "body"], topic_entities=[])
        # "body" matches the video's own topic vocabulary (so the candidate
        # clears the zero-overlap check), "gym"/"bicycle" are the beat's own
        # avoid-terms, and nothing here overlaps the beat's OWN keywords —
        # exactly the shape REASON_AVOID_TERM exists to catch.
        candidate = _candidate(tags=["body", "gym", "bicycle", "fitness"])
        scored = score_candidate(beat, candidate, ctx)
        assert scored.disposition == "reject"
        assert scored.rejected_reason in (REASON_AVOID_TERM, REASON_WEAK_EVIDENCE)

    def test_below_rescue_floor_rejects_outright(self):
        """A candidate whose aggregate score is too weak to be worth a real
        vision call stays a hard reject, even with some textual overlap."""
        beat = _beat(
            "So the cell's repair choice determines your outcome.",
            keywords=["repair", "choice", "cell"],
            entities=[],
        )
        ctx = ScoringContext(topic_terms=["dna", "repair"], topic_entities=[])
        # Landscape, tiny, wrong media-type-preference position, heavily
        # reused — every non-relevance factor tanked so the aggregate score
        # drops under VISION_RESCUE_MIN_SCORE despite the one matched term.
        candidate = _candidate(tags=["car", "repair", "garage"], media_type=MEDIA_PHOTO)
        candidate.width, candidate.height = 100, 60
        beat.media_type_preferences = [MEDIA_VIDEO]
        ctx.used_count_in_video[candidate.asset_key] = 1
        ctx.channel_reuse[candidate.asset_key] = 1.0
        scored = score_candidate(beat, candidate, ctx)
        assert scored.score < 0.34
        assert scored.disposition == "reject"
        assert scored.rejected_reason == REASON_WEAK_EVIDENCE


# ---------------------------------------------------------------------------
# §16 — vision rescue orchestration
# ---------------------------------------------------------------------------


class TestVisionRescueOrchestration:
    def _garage_beat_and_engine(self, tmp_path, provider):
        beat = _beat(
            "So the cell's repair choice determines your outcome.",
            index=0,
            keywords=["repair", "choice", "cell"],
            entities=[],
        )
        engine = _engine(tmp_path, [provider])
        plan = engine.resolve([beat])
        # Deterministically falls back to a graphic first — no ACCEPT exists.
        assert plan.resolutions[0].media_type == "graphic"
        return beat, engine, plan

    def test_sparse_metadata_relevant_visual_is_verified_and_selected(self, tmp_path):
        candidate = _candidate(asset_id="cell1", tags=["car", "repair", "garage", "welding"])
        provider = _StubProvider(results=[candidate])
        beat, engine, plan = self._garage_beat_and_engine(tmp_path, provider)

        verifier = _AssetKeyedVerifier()
        verifier.expect[0] = [STATUS_VERIFIED]
        stats = engine.vision_rescue(plan, [0], verifier)

        assert stats.verified == 1
        assert stats.beats_saved == 1
        resolution = stats.plan.resolutions[0]
        assert resolution.media_type == MEDIA_PHOTO
        assert resolution.asset_key == "pexels:cell1"
        assert resolution.vision_rescued is True
        assert resolution.verification_status == STATUS_VERIFIED

    def test_sparse_metadata_irrelevant_visual_is_rejected_no_alternate(self, tmp_path):
        candidate = _candidate(asset_id="cell1", tags=["car", "repair", "garage", "welding"])
        provider = _StubProvider(results=[candidate])
        beat, engine, plan = self._garage_beat_and_engine(tmp_path, provider)

        verifier = _AssetKeyedVerifier()
        verifier.expect[0] = [STATUS_REJECTED]
        stats = engine.vision_rescue(plan, [0], verifier)

        assert stats.rejected == 1
        assert stats.beats_saved == 0
        assert stats.plan.resolutions[0].media_type == "graphic"
        assert stats.plan.resolutions[0].vision_rescued is False

    def test_candidate_two_is_tried_after_candidate_one_is_rejected(self, tmp_path):
        cand1 = _candidate(asset_id="garage1", tags=["car", "repair", "garage", "welding"])
        cand2 = _candidate(asset_id="labcoat1", tags=["lab", "repair", "cell", "scientist"])
        provider = _StubProvider(results=[cand1, cand2])
        beat, engine, plan = self._garage_beat_and_engine(tmp_path, provider)

        verifier = _AssetKeyedVerifier()
        verifier.expect[0] = [STATUS_REJECTED, STATUS_VERIFIED]
        stats = engine.vision_rescue(plan, [0], verifier)

        assert stats.beats_saved == 1
        resolution = stats.plan.resolutions[0]
        assert resolution.vision_rescued is True
        # Exactly two verifier rounds happened for this beat.
        assert sum(0 in call for call in verifier.calls) == 2

    def test_both_candidates_rejected_falls_back_safely(self, tmp_path):
        cand1 = _candidate(asset_id="garage1", tags=["car", "repair", "garage"])
        cand2 = _candidate(asset_id="labcoat1", tags=["lab", "repair", "cell"])
        provider = _StubProvider(results=[cand1, cand2])
        beat, engine, plan = self._garage_beat_and_engine(tmp_path, provider)

        verifier = _AssetKeyedVerifier()
        verifier.expect[0] = [STATUS_REJECTED, STATUS_REJECTED]
        stats = engine.vision_rescue(plan, [0], verifier)

        assert stats.beats_saved == 0
        assert stats.plan.resolutions[0].media_type == "graphic"

    def test_questionable_does_not_win_while_an_alternate_remains(self, tmp_path):
        cand1 = _candidate(asset_id="garage1", tags=["car", "repair", "garage"])
        cand2 = _candidate(asset_id="labcoat1", tags=["lab", "repair", "cell"])
        provider = _StubProvider(results=[cand1, cand2])
        beat, engine, plan = self._garage_beat_and_engine(tmp_path, provider)

        # Whichever candidate the scorer ranks first gets tried (and comes
        # back questionable); the point under test is that a "questionable"
        # verdict is never the final selection while another candidate is
        # still available — not which specific candidate that happens to be.
        verifier = _AssetKeyedVerifier()
        verifier.expect[0] = [STATUS_QUESTIONABLE, STATUS_VERIFIED]
        stats = engine.vision_rescue(plan, [0], verifier)

        assert stats.questionable == 1
        assert stats.verified == 1
        assert stats.beats_saved == 1
        assert stats.plan.resolutions[0].media_type == MEDIA_PHOTO
        assert stats.plan.resolutions[0].vision_rescued is True

    def test_questionable_alone_never_becomes_the_final_selection(self, tmp_path):
        candidate = _candidate(asset_id="cell1", tags=["car", "repair", "garage"])
        provider = _StubProvider(results=[candidate])
        beat, engine, plan = self._garage_beat_and_engine(tmp_path, provider)

        verifier = _AssetKeyedVerifier()
        verifier.expect[0] = [STATUS_QUESTIONABLE]
        stats = engine.vision_rescue(plan, [0], verifier)

        assert stats.beats_saved == 0
        assert stats.plan.resolutions[0].media_type == "graphic"

    def test_verifier_unavailable_never_unsafely_accepts(self, tmp_path):
        candidate = _candidate(asset_id="cell1", tags=["car", "repair", "garage"])
        provider = _StubProvider(results=[candidate])
        beat, engine, plan = self._garage_beat_and_engine(tmp_path, provider)

        class _RaisingVerifier:
            def verify(self, contact_sheet, beats):
                raise RuntimeError("provider outage")

        stats = engine.vision_rescue(plan, [0], _RaisingVerifier())
        assert stats.beats_saved == 0
        assert stats.plan.resolutions[0].media_type == "graphic"

    def test_max_two_candidates_per_beat_is_enforced(self, tmp_path):
        candidates = [
            _candidate(asset_id=f"c{i}", tags=["car", "repair", "garage"]) for i in range(5)
        ]
        provider = _StubProvider(results=candidates)
        beat, engine, plan = self._garage_beat_and_engine(tmp_path, provider)

        verifier = _AssetKeyedVerifier()
        verifier.expect[0] = [STATUS_REJECTED, STATUS_REJECTED, STATUS_REJECTED]
        engine.vision_rescue(plan, [0], verifier)
        # Never more than VISION_RESCUE_MAX_CANDIDATES_PER_BEAT rounds tried.
        rounds_for_beat_0 = sum(0 in call for call in verifier.calls)
        assert rounds_for_beat_0 <= VISION_RESCUE_MAX_CANDIDATES_PER_BEAT

    def test_no_verify_eligible_candidates_is_a_cheap_no_op(self, tmp_path):
        # A candidate the deterministic scorer REJECTS outright never reaches
        # vision at all.
        candidate = _candidate(asset_id="c1", tags=["surfer", "ocean", "waves"])
        provider = _StubProvider(results=[candidate])
        beat, engine, plan = self._garage_beat_and_engine(tmp_path, provider)

        verifier = _AssetKeyedVerifier()
        stats = engine.vision_rescue(plan, [0], verifier)
        assert stats.contact_sheets == 0
        assert verifier.calls == []

    def test_beats_are_batched_within_the_contact_sheet_bound(self, tmp_path):
        beats = [
            _beat(
                f"So the cell's repair choice determines outcome number {i}.",
                index=i,
                keywords=["repair", "choice", "cell"],
                entities=[],
            )
            for i in range(MAX_BEATS_PER_CONTACT_SHEET + 3)
        ]
        candidate_sets = [
            [_candidate(asset_id=f"c{i}", tags=["car", "repair", "garage"])]
            for i in range(len(beats))
        ]
        # One stub provider whose results depend on the query is overkill;
        # reuse a single shared candidate pool keyed by asset id per beat via
        # distinct queries isn't needed since _StubProvider ignores query —
        # give each beat its own asset by asset_id and let all beats share
        # the provider's full candidate list (each beat's own gather() calls
        # rank_candidates against ITS OWN beat, so only same-domain overlap
        # matters, not per-beat filtering here).
        provider = _StubProvider(results=[c for group in candidate_sets for c in group])
        engine = _engine(tmp_path, [provider])
        plan = engine.resolve(beats)
        assert all(r.media_type == "graphic" for r in plan.resolutions)

        verifier = _AssetKeyedVerifier()
        for b in beats:
            verifier.expect[b.beat_index] = [STATUS_VERIFIED]
        stats = engine.vision_rescue(plan, list(range(len(beats))), verifier)

        assert stats.contact_sheets >= 2  # more beats than fit in one sheet
        for call in verifier.calls:
            assert len(call) <= MAX_BEATS_PER_CONTACT_SHEET


# ---------------------------------------------------------------------------
# §17 — real incident regression (galaxy / gym)
# ---------------------------------------------------------------------------


class TestRealIncidentRegressionWithVisionRescue:
    def _galaxy_beat_and_engine(self, tmp_path, provider):
        beat = _beat(
            "Violent collisions between galaxies scramble everything into an elliptical blob.",
            keywords=["collisions", "everything", "elliptical", "galaxies", "violent"],
            entities=[],
            intent="action",
        )
        engine = _engine(
            tmp_path,
            [provider],
            concrete_subjects=["galaxy formation astrophysics astronomy"],
        )
        plan = engine.resolve([beat])
        return beat, engine, plan

    def test_gym_clip_is_never_selected_even_via_vision_rescue(self, tmp_path):
        gym = _candidate(
            asset_id="6455082",
            tags=["man", "woman", "working", "elliptical", "bicycles", "gym", "fitness"],
            query="collisions everything elliptical",
        )
        provider = _StubProvider(results=[gym])
        beat, engine, plan = self._galaxy_beat_and_engine(tmp_path, provider)

        verifier = _AssetKeyedVerifier()
        verifier.expect[0] = [STATUS_REJECTED]  # the real Phase-3 live probe's actual verdict
        stats = engine.vision_rescue(plan, [0], verifier)

        assert stats.beats_saved == 0
        assert stats.plan.resolutions[0].asset_key != "pexels:6455082"
        assert stats.plan.resolutions[0].media_type != MEDIA_VIDEO

    def test_valid_galaxy_candidate_still_wins_via_vision_rescue(self, tmp_path):
        galaxy = _candidate(
            asset_id="galaxy-collision-1",
            tags=["galaxies", "collisions", "elliptical", "violent", "cosmic", "space"],
            query="collisions everything elliptical",
        )
        provider = _StubProvider(results=[galaxy])
        beat, engine, plan = self._galaxy_beat_and_engine(tmp_path, provider)
        # This candidate has 4 matched terms — likely already an outright
        # ACCEPT, not even needing vision. Prove it wins either way.
        if plan.resolutions[0].media_type == MEDIA_VIDEO:
            assert plan.resolutions[0].asset_key == "pexels:galaxy-collision-1"
            return
        verifier = _AssetKeyedVerifier()
        verifier.expect[0] = [STATUS_VERIFIED]
        stats = engine.vision_rescue(plan, [0], verifier)
        assert stats.plan.resolutions[0].asset_key == "pexels:galaxy-collision-1"


# ---------------------------------------------------------------------------
# §18 — failed vaccine-canary regression
# ---------------------------------------------------------------------------


class TestVaccineCanaryRegression:
    def test_memory_cells_beat_can_now_enter_verify_instead_of_collapsing(self, tmp_path):
        """The exact real beat from canary #1: sparse provider metadata for
        an abstract, entity-free sentence used to hard-collapse straight to
        a text card. It must now at least reach VERIFY."""
        beat = _beat(
            "It creates memory cells that stick around for years.",
            keywords=["creates", "memory", "around", "cells", "stick", "years"],
            entities=[],
        )
        ctx = ScoringContext(topic_terms=["immune", "body"], topic_entities=[])
        # Real-shaped sparse metadata: a medical/lab stock clip that shares
        # only one ordinary word ("cells") with the beat's own vocabulary —
        # no hardcoded vaccine-specific scoring rule anywhere in play here.
        candidate = _candidate(tags=["laboratory", "cells", "microscope", "science"])
        scored = score_candidate(beat, candidate, ctx)
        assert scored.accepted is False
        assert scored.disposition == "verify"

    def test_vision_confirms_the_relevant_medical_candidate(self, tmp_path):
        beat = _beat(
            "It creates memory cells that stick around for years.",
            index=0,
            keywords=["creates", "memory", "around", "cells", "stick", "years"],
            entities=[],
        )
        candidate = _candidate(
            asset_id="cells1", tags=["laboratory", "cells", "microscope", "science"]
        )
        provider = _StubProvider(results=[candidate])
        engine = _engine(tmp_path, [provider], concrete_subjects=["immune system"])
        plan = engine.resolve([beat])
        assert plan.resolutions[0].media_type == "graphic"

        verifier = _AssetKeyedVerifier()
        verifier.expect[0] = [STATUS_VERIFIED]
        stats = engine.vision_rescue(plan, [0], verifier)

        assert stats.beats_saved == 1
        assert stats.plan.resolutions[0].vision_rescued is True
