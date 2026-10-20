"""Semantic visual engine — orchestration.

Per beat:

    build queries  →  retrieve candidates across available providers
                   →  score every candidate against the beat
                   →  accept the best, or reject them all
                   →  fall back to a locally generated explanatory graphic

The fallback is not a failure path.  For structural intents (comparison,
process, number, diagram, timeline) a generated graphic routinely *outscores*
whatever a stock library returns, and choosing it is the correct outcome.

The engine performs no DB writes.  Persistence and usage recording belong to
the repository so the engine stays testable without a database.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from app.visuals import graphics, motion, semantics
from app.visuals.beats import BEAT_PLANNER_VERSION
from app.visuals.constants import (
    FIT_CONTAIN,
    INTENT_COMPARISON,
    INTENT_CTA,
    INTENT_DIAGRAM,
    INTENT_NUMBER,
    INTENT_PROCESS,
    INTENT_QUOTE,
    INTENT_TIMELINE,
    LICENSE_NOT_REQUIRED,
    MAX_REJECTED_CANDIDATES_PERSISTED,
    MEDIA_GRAPHIC,
    MEDIA_VIDEO,
    STRUCTURAL_INTENTS,
    VISUAL_ENGINE_VERSION,
)
from app.visuals.memory import channel_reuse_weights
from app.visuals.models import (
    BeatResolution,
    CandidateEvidence,
    ScoredCandidate,
    VisualBeat,
    VisualCandidate,
    VisualPlan,
)
from app.visuals.policy import VisualPolicy, resolve_policy
from app.visuals.providers import (
    ProviderCallLog,
    VisualProvider,
    build_default_providers,
    providers_for,
)
from app.visuals.scoring import REASON_BELOW_FLOOR, ScoringContext, rank_candidates

logger = logging.getLogger(__name__)

FALLBACK_NO_CANDIDATES = "no_candidates_returned"
FALLBACK_ALL_REJECTED = "all_candidates_rejected"
FALLBACK_DOWNLOAD_FAILED = "download_failed"
FALLBACK_STRUCTURAL_PREFERENCE = "structural_intent_prefers_graphic"
# Fallback hierarchy tier 5 (Phase 2): a still that is genuinely evidenced as
# relevant — it cleared every relevance/corroboration/license check in
# score_candidate — but fell short only on the aggregate *technical* score
# (resolution, duration fit) is a better outcome than a text card. Relevance
# is never relaxed here, only the technical-fit floor, and only by a bounded
# fraction — this must never become a second "least-bad" door.
FALLBACK_RELEVANT_STILL_RELAXED_FLOOR = "relevant_still_relaxed_floor"
RELAXED_STILL_SCORE_FRACTION: float = 0.75

# Bounded remediation (Phase 18E). Relaxed, not removed: two ordinary words
# in common is thin evidence, but it beats showing the viewer another wall
# of typeset narration. An unrelated clip still cannot clear these.
REMEDIATION_MIN_EVIDENCE: float = 0.6
REMEDIATION_MIN_SCORE: float = 0.34
REMEDIATION_MAX_QUERIES_PER_BEAT: int = 4

# Vision-assisted candidate rescue (Phase 4, §7). At most this many of a
# deficient beat's verify-eligible candidates are ever downloaded and shown
# to a vision verifier — a bounded, round-based rescue, not an unbounded
# "try everything" search.
VISION_RESCUE_MAX_CANDIDATES_PER_BEAT: int = 2


@dataclass
class VisionRescueStats:
    """Non-secret telemetry from one `vision_rescue()` call (Phase 4, §13)."""

    candidates_considered: int = 0
    contact_sheets: int = 0
    verified: int = 0
    rejected: int = 0
    questionable: int = 0
    unavailable: int = 0
    beats_saved: int = 0
    plan: VisualPlan | None = None


# ── Diagram-continuation fallback routing (Phase 18G) ───────────────────────
# Intents that already own a real renderer in graphics.py::_RENDERERS. Kept
# in sync with that dict by hand — there is no shared source of truth to
# import from without inverting the graphics->engine dependency direction.
_RENDERER_INTENTS: frozenset[str] = frozenset(
    {INTENT_COMPARISON, INTENT_PROCESS, INTENT_DIAGRAM, INTENT_TIMELINE, INTENT_NUMBER}
)
# Never eligible to borrow a sibling's renderer, however much vocabulary they
# share: a CTA or a quote is a distinct rhetorical beat, not a continuation
# of the explanation around it.
_DIAGRAM_CONTINUATION_EXCLUDED: frozenset[str] = frozenset({INTENT_CTA, INTENT_QUOTE})


def _light_singular(term: str) -> str:
    """Fold an obvious plural onto its singular so "dots" matches "dot".

    Not a stemmer — just enough to bridge the plural/singular pairs that
    show up constantly in narration ("dot"/"dots", "move"/"moves") without
    inventing a general morphology subsystem. The threshold is >= 4, not
    > 4: "dots" is itself 4 characters, and excluding it would silently
    break exactly the plural it exists to catch.
    """
    lowered = term.lower()
    if len(lowered) >= 4 and lowered.endswith("s") and not lowered.endswith("ss"):
        return lowered[:-1]
    return lowered


def _beat_vocab(beat: VisualBeat) -> set[str]:
    return {_light_singular(t) for t in (*beat.keywords, *beat.entities)}


def _diagram_continuation_intents(scene_beats: list[VisualBeat]) -> dict[int, str]:
    """Map beat_index -> a borrowed renderer intent, for beats that are part
    of the same contiguous explanation as a structural sibling but were
    themselves classified action/entity (e.g. "imagine drawing dots on a
    balloon" — concrete and diagrammable, but grammatically an action, not a
    comparison).

    Scoped to one scene (one narration segment): continuity is never assumed
    to cross into an unrelated scene. Connectivity is transitive — a beat
    reaches a structural sibling through a chain of shared vocabulary, not
    only through an immediate neighbour, because an analogy is regularly
    spread across several beats that individually share no words with the
    structural beat but do share words with each other (verified against the
    real cosmology fixture: beat 6 reaches beat 8/14 only via beat 7, and
    beat 7 reaches them only via beat 6 — an immediate-neighbour check finds
    neither).

    A beat with no such connection (a standalone aside sharing no vocabulary
    with anything structural) is not included and keeps the ordinary
    text-card fallback.
    """
    parent = {b.beat_index: b.beat_index for b in scene_beats}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    vocabs = {b.beat_index: _beat_vocab(b) for b in scene_beats}
    for i, a in enumerate(scene_beats):
        for b in scene_beats[i + 1 :]:
            if vocabs[a.beat_index] & vocabs[b.beat_index]:
                union(a.beat_index, b.beat_index)

    structural_by_root: dict[int, str] = {}
    for beat in scene_beats:
        if beat.visual_intent in _RENDERER_INTENTS:
            structural_by_root.setdefault(find(beat.beat_index), beat.visual_intent)

    result: dict[int, str] = {}
    for beat in scene_beats:
        if beat.visual_intent in STRUCTURAL_INTENTS or beat.visual_intent in (
            _DIAGRAM_CONTINUATION_EXCLUDED
        ):
            continue
        borrowed = structural_by_root.get(find(beat.beat_index))
        if borrowed is not None:
            result[beat.beat_index] = borrowed
    return result


# ── Text-card cascade mitigation (Phase 2) ──────────────────────────────────


def _collapse_text_card_cascades(resolutions: list[BeatResolution]) -> None:
    """Hold one card across a run of adjacent, unplanned text-card beats.

    Several consecutive abstract clauses that each fail retrieval used to
    each render and cut to their OWN distinct, unrelated card — several
    solid-background flashes in a row is worse than one continuous card held
    across the whole run. This never changes a beat's timing (beats still
    tile the scene exactly, so audio/caption lineage is untouched); it only
    makes a run of *unplanned* text cards (never a diagram-continuation or a
    structural intent's deliberate graphic) share one rendered visual, which
    ``composition.py``'s asset-identity visual-change counter then correctly
    treats as a single continuous visual rather than several cuts.
    """
    run: list[BeatResolution] = []

    def flush() -> None:
        if len(run) >= 2:
            anchor = run[0]
            for follower in run[1:]:
                follower.local_path = anchor.local_path
                follower.asset_key = anchor.asset_key
                follower.held_visual = True
        run.clear()

    for resolution in resolutions:
        is_unplanned_text_card = (
            resolution.media_type == MEDIA_GRAPHIC
            and resolution.fallback_reason in (FALLBACK_ALL_REJECTED, FALLBACK_NO_CANDIDATES)
            and resolution.render_intent_override is None
        )
        same_scene_as_run = bool(run) and run[-1].beat.scene_index == resolution.beat.scene_index
        if is_unplanned_text_card and (not run or same_scene_as_run):
            run.append(resolution)
        else:
            flush()
            if is_unplanned_text_card:
                run.append(resolution)
    flush()


# ── Candidate evidence (Phase 18G) ──────────────────────────────────────────


def _one_evidence(scored: ScoredCandidate, *, decision: str) -> CandidateEvidence:
    candidate = scored.candidate
    return CandidateEvidence(
        provider=candidate.provider,
        provider_asset_id=candidate.provider_asset_id,
        asset_key=candidate.asset_key,
        title=candidate.title,
        tags=list(candidate.tags),
        query=candidate.query,
        score=scored.score,
        semantic_score=scored.factors.get("semantic", 0.0),
        topic_score=scored.factors.get("topic", 0.0),
        evidence_score=scored.factors.get("evidence", 0.0),
        decision=decision,
        rejection_reason=scored.rejected_reason if decision == "rejected" else None,
        provider_rank=candidate.provider_rank,
    )


def _candidate_evidence_list(
    ranked: list[ScoredCandidate], resolution: BeatResolution
) -> list[CandidateEvidence]:
    """Bounded, three-way-classified evidence for one beat's actual run.

    The selected entry (if any — a graphic fallback selects nothing) is
    always included; up to MAX_REJECTED_CANDIDATES_PERSISTED of the rest
    follow, in ``ranked``'s own accepted-first/score-sorted order, each
    classified strictly from its own already-computed ScoredCandidate state
    — never a fabricated reason for a candidate that simply wasn't chosen.
    """
    selected_key = resolution.asset_key if resolution.provider != "programmatic" else None
    evidence: list[CandidateEvidence] = []
    others: list[ScoredCandidate] = []
    selected_found = False
    for scored in ranked:
        if (
            not selected_found
            and selected_key is not None
            and scored.candidate.asset_key == selected_key
        ):
            evidence.append(_one_evidence(scored, decision="selected"))
            selected_found = True
        else:
            others.append(scored)
    for scored in others[:MAX_REJECTED_CANDIDATES_PERSISTED]:
        decision = "eligible_not_selected" if scored.accepted else "rejected"
        evidence.append(_one_evidence(scored, decision=decision))
    return evidence


@dataclass
class VisualEngineConfig:
    width: int = 1080
    height: int = 1920
    policy: VisualPolicy = field(default_factory=resolve_policy)
    channel_key: str | None = None
    scene_manifest_id: int = 0
    topic_id: int = 0
    experiment_id: str | None = None
    cache_dir: Path = Path("visual_cache")
    graphics_dir: Path = Path("visual_graphics")
    # Trusted topic-level subjects from the Phase 18E.1 semantic-fit
    # evaluation, when one exists for this opportunity. [] is the
    # narration-only behaviour that already existed before this field.
    concrete_subjects: list[str] = field(default_factory=list)
    # Source suitability (Phase 2, §4): providers to prefer, in order, ahead
    # of the ordinary cost/tier sort. () is the pre-existing behaviour.
    # Nothing in this engine decides what belongs here — see
    # providers/registry.py::providers_for.
    preferred_provider_identities: tuple[str, ...] = ()

    @property
    def orientation(self) -> str:
        return "portrait" if self.height >= self.width else "landscape"


class VisualEngine:
    """Resolves a visual for every beat, cheapest acceptable source first."""

    def __init__(
        self,
        config: VisualEngineConfig,
        *,
        providers: list[VisualProvider] | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        self._config = config
        self._providers = providers if providers is not None else build_default_providers()
        self._conn = conn
        self._calls = ProviderCallLog()
        self._searched: dict[tuple[str, str, str], list[VisualCandidate]] = {}
        self._ctx: ScoringContext | None = None

    @property
    def call_log(self) -> ProviderCallLog:
        return self._calls

    # ── Retrieval ───────────────────────────────────────────────────────────

    def _search(
        self, provider: VisualProvider, query: str, media_type: str, limit: int
    ) -> list[VisualCandidate]:
        """Search with per-run memoisation.

        Two beats often share a query; issuing the same provider call twice
        wastes quota and returns the same rows.
        """
        cache_key = (provider.identity, media_type, query.lower())
        if cache_key in self._searched:
            return self._searched[cache_key]

        self._calls.record_search(provider.identity)
        try:
            results = provider.search(
                query,
                media_type=media_type,
                limit=limit,
                orientation=(
                    self._config.orientation if provider.capability.supports_orientation else None
                ),
            )
        except Exception as exc:  # a provider outage must not fail the render
            logger.warning("provider %s raised %s", provider.identity, type(exc).__name__)
            results = []

        self._searched[cache_key] = results
        return results

    def _gather(self, beat: VisualBeat, queries: list[str] | None = None) -> list[VisualCandidate]:
        policy = self._config.policy
        if queries is None:
            queries = beat.search_queries[: max(1, policy.queries_per_beat)]
        queries = [q for q in queries if q.strip()]
        if not queries:
            return []

        gathered: list[VisualCandidate] = []
        seen: set[str] = set()

        # Media types in the beat's own preference order; graphics are local
        # and never retrieved.
        wanted = [m for m in beat.media_type_preferences if m != MEDIA_GRAPHIC]

        for media_type in wanted:
            for provider in providers_for(
                self._providers,
                media_type,
                max_cost_units=policy.max_provider_cost_units,
                preferred_identities=self._config.preferred_provider_identities,
            ):
                for query in queries:
                    for candidate in self._search(
                        provider, query, media_type, policy.candidates_per_beat
                    ):
                        if candidate.asset_key in seen:
                            continue
                        seen.add(candidate.asset_key)
                        gathered.append(candidate)
            if len(gathered) >= policy.candidates_per_beat:
                break

        return gathered

    # ── Resolution ──────────────────────────────────────────────────────────

    def _graphic_resolution(
        self,
        beat: VisualBeat,
        *,
        reason: str,
        best_score: float = 0.0,
        considered: int = 0,
        render_intent: str | None = None,
    ) -> BeatResolution:
        stem = graphics.graphic_cache_key(
            beat, self._config.width, self._config.height, render_intent=render_intent
        )
        path = self._config.graphics_dir / f"beat_{beat.beat_index:03d}_{stem}.png"
        if not path.exists():
            graphics.render_beat_graphic(
                beat,
                path,
                width=self._config.width,
                height=self._config.height,
                render_intent=render_intent,
            )
        return BeatResolution(
            beat=beat,
            media_type=MEDIA_GRAPHIC,
            local_path=str(path),
            asset_key=f"programmatic:{stem}",
            provider="programmatic",
            source_url=None,
            license_status=LICENSE_NOT_REQUIRED,
            license_name="Generated locally",
            attribution_required=False,
            attribution_text=None,
            commercial_safe=True,
            # Generated cards already carry composition; adding drift would
            # make the typography swim.
            motion=motion.MOTION_NONE,
            score=best_score,
            fallback_reason=reason,
            candidates_considered=considered,
            is_placeholder=False,
            render_intent_override=render_intent,
        )

    def _accept(
        self, beat: VisualBeat, scored: ScoredCandidate, local_path: Path, still_index: int
    ) -> BeatResolution:
        candidate = scored.candidate
        is_video = candidate.media_type == MEDIA_VIDEO

        # Type-led, not domain-specific: MEDIA_ILLUSTRATION is what the
        # retrieval layer already calls diagrams/charts/infographics, so a
        # labelled diagram is contained rather than centre-cropped through
        # its own labels. Ordinary photography and video tolerate the crop.
        fit_mode = motion.choose_fit(
            candidate.media_type,
            candidate.width,
            candidate.height,
            target_width=self._config.width,
            target_height=self._config.height,
        )
        if is_video:
            beat_motion = motion.MOTION_NONE
        elif fit_mode == FIT_CONTAIN:
            # A contained image is shown for its detail; drifting it around
            # would slide the subject off-centre and expose the blurred fill.
            beat_motion = motion.MOTION_NONE
        else:
            beat_motion = motion.motion_for(
                still_index, enabled=self._config.policy.allow_still_motion
            )

        return BeatResolution(
            beat=beat,
            media_type=candidate.media_type,
            local_path=str(local_path),
            asset_key=candidate.asset_key,
            provider=candidate.provider,
            source_url=candidate.source_url,
            license_status=candidate.license_status,
            license_name=candidate.license_name,
            attribution_required=candidate.attribution_required,
            attribution_text=candidate.attribution_text,
            commercial_safe=candidate.commercial_safe,
            motion=beat_motion,
            fit_mode=fit_mode,
            score=scored.score,
            score_factors=dict(scored.factors),
            candidates_considered=0,
            is_placeholder=False,
            descriptors=sorted(set(candidate.tags) | set(candidate.title.split())),
        )

    def _reuse_previous_for_sequence(
        self,
        beat: VisualBeat,
        resolutions: list[BeatResolution],
        ctx: ScoringContext,
    ) -> BeatResolution | None:
        """Try reusing the immediately-preceding beat's real asset for a beat
        translation marked as continuing that beat's process (Phase 5, §8-9).

        Returns None (never a graphic, never an error) whenever reuse is not
        cleanly available, so the caller always has an ordinary search to
        fall back to. Deliberately does not touch `beat.duration_ms`/
        `start_ms`/`end_ms` — narration timing is never affected, only which
        asset a beat shows.
        """
        if not beat.continues_sequence or not resolutions:
            return None
        prev = resolutions[-1]
        if (
            prev.beat.scene_index != beat.scene_index
            or prev.provider == "programmatic"
            or not prev.resolved
            or not prev.asset_key
        ):
            return None
        used_count = ctx.used_count_in_video.get(prev.asset_key, 0)
        used_ms = ctx.used_ms_in_video.get(prev.asset_key, 0)
        if (
            used_count >= ctx.max_uses_per_video
            or used_ms + beat.duration_ms > ctx.max_asset_total_ms
        ):
            return None

        resolution = replace(
            prev,
            beat=beat,
            fallback_reason=None,
            remediated=False,
            vision_rescued=False,
            held_visual=False,
            candidate_evidence=[],
            candidates_considered=0,
            continued_from_previous=True,
        )
        # Charge this beat's duration against the reused asset's repetition
        # budget exactly as the ordinary path does at the bottom of the main
        # loop — a beat resolved by this shortcut must count the same as one
        # resolved by a fresh search, or later beats would under-count how
        # much of the video this asset already occupies.
        ctx.used_ms_in_video[prev.asset_key] = used_ms + beat.duration_ms
        ctx.used_count_in_video[prev.asset_key] = used_count + 1
        return resolution

    # ── Bounded remediation ─────────────────────────────────────────────────

    def remediate(
        self,
        plan: VisualPlan,
        beat_indexes: list[int],
        *,
        min_evidence: float = REMEDIATION_MIN_EVIDENCE,
        min_score: float = REMEDIATION_MIN_SCORE,
    ) -> VisualPlan:
        """Re-resolve the named beats with widened queries and a lower bar.

        This runs BEFORE the render, inside the same stage that resolved the
        beats in the first place, which is what makes it cheap and safe:

          * no render is discarded and re-encoded;
          * provider searches are memoised for the whole run, so a query the
            first pass already issued costs nothing to reconsider;
          * only beats that fell back for a PROVIDER reason are targeted — a
            beat that chose a diagram on purpose is left exactly as it is;
          * a beat that still finds nothing keeps its original generated card,
            so remediation can improve a plan but never damage one.

        The relaxed floors are deliberately modest.  The first pass rejects a
        candidate whose only link to the narration is one ordinary word;
        remediation will accept two ordinary words rather than show the viewer
        another wall of text, but it will still not accept an unrelated clip.
        """
        if not beat_indexes:
            return plan

        ctx = self._ctx
        if ctx is None:
            return plan

        targets = set(beat_indexes)
        by_index = {r.beat.beat_index: r for r in plan.resolutions}

        # A relaxed *copy* of the context: the first pass's own verdicts must
        # not be retroactively reinterpreted under a lower bar.
        relaxed = replace(ctx, min_evidence=min_evidence, min_score=min_score)

        still_index = sum(
            1
            for r in plan.resolutions
            if r.provider != "programmatic" and r.media_type != MEDIA_VIDEO
        )
        repaired = 0

        for index in sorted(targets):
            resolution = by_index.get(index)
            if resolution is None:
                continue
            beat = resolution.beat

            candidates = self._gather(beat, self._remediation_queries(beat, ctx))
            if not candidates:
                continue

            if self._conn is not None:
                relaxed.channel_reuse.update(
                    channel_reuse_weights(
                        self._conn,
                        [c.asset_key for c in candidates],
                        channel_key=self._config.channel_key,
                        exclude_scene_manifest_id=self._config.scene_manifest_id or None,
                    )
                )

            accepted = [s for s in rank_candidates(beat, candidates, relaxed) if s.accepted]
            for scored in accepted:
                provider = next(
                    (p for p in self._providers if p.identity == scored.candidate.provider), None
                )
                if provider is None:
                    continue
                self._calls.record_download(provider.identity)
                local = provider.download(scored.candidate, self._config.cache_dir)
                if local is None:
                    continue

                # Release the old card's in-video allocation before charging
                # the new asset for it, or the beat is billed twice.
                if resolution.asset_key:
                    ctx.used_ms_in_video[resolution.asset_key] = max(
                        0,
                        ctx.used_ms_in_video.get(resolution.asset_key, 0) - beat.duration_ms,
                    )
                    ctx.used_count_in_video[resolution.asset_key] = max(
                        0, ctx.used_count_in_video.get(resolution.asset_key, 0) - 1
                    )

                replacement = self._accept(beat, scored, local, still_index)
                replacement.candidates_considered = len(candidates)
                replacement.remediated = True
                by_index[index] = replacement
                repaired += 1
                if scored.candidate.media_type != MEDIA_VIDEO:
                    still_index += 1

                ctx.used_ms_in_video[replacement.asset_key] = (
                    ctx.used_ms_in_video.get(replacement.asset_key, 0) + beat.duration_ms
                )
                ctx.used_count_in_video[replacement.asset_key] = (
                    ctx.used_count_in_video.get(replacement.asset_key, 0) + 1
                )
                ctx.used_descriptors.append(
                    {t.lower() for t in replacement.descriptors if len(t) > 2}
                )
                break

        if repaired:
            logger.info(
                "Visual remediation repaired %d of %d deficient beat(s)",
                repaired,
                len(targets),
            )

        plan.resolutions = [by_index[b.beat_index] for b in plan.beats if b.beat_index in by_index]
        return plan

    # ── Vision-assisted candidate rescue (Phase 4) ──────────────────────────

    def vision_rescue(
        self,
        plan: VisualPlan,
        beat_indexes: list[int],
        verifier: Any,
        *,
        max_candidates_per_beat: int = VISION_RESCUE_MAX_CANDIDATES_PER_BEAT,
        ffmpeg_bin: str = "ffmpeg",
    ) -> VisionRescueStats:
        """Give textually-inconclusive candidates one bounded, real look
        before a deficient beat falls back to a text card.

        Runs BEFORE remediation and BEFORE the text-card fallback becomes
        final — the ordering the whole phase exists to change. For each
        targeted beat (normally `composition.deficient_beat_indexes()`),
        re-gathers its candidates (memoised — free if `resolve()` already
        searched this beat) and looks at the ones the deterministic scorer
        marked `verify_eligible` (scoring.REASON_NEEDS_VERIFICATION: some
        real textual connection, just not independently corroborated). Up to
        `max_candidates_per_beat` of those, highest-scored first, are tried
        round by round: every beat's round-N candidate is downloaded, a
        frame extracted, and batched with every other beat's round-N
        candidate into contact sheets (bounded at
        verification.MAX_BEATS_PER_CONTACT_SHEET) — so N beats cost at most
        `max_candidates_per_beat * ceil(N / 12)` verifier calls, not one per
        candidate. The first "verified" candidate a beat gets replaces its
        graphic fallback; "questionable"/"rejected"/no-verdict-at-all moves
        it to the next round's candidate. A beat that exhausts its
        candidates, or had none to begin with, keeps whatever resolution it
        already had — this can only improve a plan, never damage one, the
        same guarantee `remediate()` makes.

        A candidate the deterministic scorer actually rejected (REJECT, not
        VERIFY — REASON_NO_RELEVANCE_EVIDENCE, REASON_AVOID_TERM, license/
        commercial-safety failures, ...) never reaches this method at all:
        `verify_eligible` is false for all of them. Vision only ever
        arbitrates candidates the deterministic layer already judged
        plausible-but-unproven, never ones it judged wrong.
        """
        stats = VisionRescueStats(plan=plan)
        if not beat_indexes or self._ctx is None:
            return stats

        from app.visuals.verification import (
            MAX_BEATS_PER_CONTACT_SHEET,
            STATUS_VERIFIED,
            BeatIntent,
            build_contact_sheet,
            extract_representative_frame,
        )

        ctx = self._ctx
        by_index = {r.beat.beat_index: r for r in plan.resolutions}

        # Round 0 = every targeted beat's best verify-eligible candidate,
        # round 1 = its second-best, etc. — bounded by max_candidates_per_beat.
        pending: dict[int, list[ScoredCandidate]] = {}
        for index in beat_indexes:
            resolution = by_index.get(index)
            if resolution is None:
                continue
            beat = resolution.beat
            candidates = self._gather(beat)
            if not candidates:
                continue
            ranked = rank_candidates(beat, candidates, ctx)
            eligible = [s for s in ranked if s.verify_eligible][:max_candidates_per_beat]
            if eligible:
                pending[index] = eligible
        stats.candidates_considered = sum(len(v) for v in pending.values())
        if not pending:
            return stats

        still_index = sum(
            1
            for r in plan.resolutions
            if r.provider != "programmatic" and r.media_type != MEDIA_VIDEO
        )
        remaining = dict(pending)
        round_index = 0

        while remaining and round_index < max_candidates_per_beat:
            round_targets = {
                index: candidates[round_index]
                for index, candidates in remaining.items()
                if round_index < len(candidates)
            }
            if not round_targets:
                break

            downloaded: dict[int, tuple[ScoredCandidate, Path]] = {}
            frames: list[tuple[int, bytes]] = []
            intents: list[BeatIntent] = []
            for index, scored in round_targets.items():
                beat = by_index[index].beat
                provider = next(
                    (p for p in self._providers if p.identity == scored.candidate.provider), None
                )
                if provider is None:
                    continue
                self._calls.record_download(provider.identity)
                local = provider.download(scored.candidate, self._config.cache_dir)
                if local is None:
                    continue
                downloaded[index] = (scored, local)
                probe = BeatResolution(
                    beat=beat,
                    media_type=scored.candidate.media_type,
                    local_path=str(local),
                    asset_key=scored.candidate.asset_key,
                    provider=scored.candidate.provider,
                )
                frame = extract_representative_frame(probe, ffmpeg_bin=ffmpeg_bin)
                if frame is None:
                    continue
                frames.append((index, frame))
                intents.append(
                    BeatIntent(
                        beat_index=index,
                        narration_text=beat.narration_text,
                        keywords=list(beat.keywords),
                        entities=list(beat.entities),
                        visual_intent=beat.visual_intent,
                        avoid_terms=list(beat.avoid_terms),
                        topic_context=beat.topic_context,
                        literal_visual_subject=beat.literal_visual_subject,
                    )
                )

            verdicts_by_index: dict[int, Any] = {}
            for i in range(0, len(frames), MAX_BEATS_PER_CONTACT_SHEET):
                chunk_frames = frames[i : i + MAX_BEATS_PER_CONTACT_SHEET]
                chunk_intents = intents[i : i + MAX_BEATS_PER_CONTACT_SHEET]
                sheet = build_contact_sheet(chunk_frames)
                if sheet is None:
                    continue
                stats.contact_sheets += 1
                try:
                    verdicts = verifier.verify(sheet, chunk_intents)
                except Exception:  # a verifier outage must never fail the render
                    continue
                for verdict in verdicts:
                    verdicts_by_index[verdict.beat_index] = verdict

            for index, (scored, local) in downloaded.items():
                verdict = verdicts_by_index.get(index)
                if verdict is None:
                    stats.unavailable += 1
                    continue
                if verdict.status == STATUS_VERIFIED:
                    stats.verified += 1
                    resolution = by_index[index]
                    beat = resolution.beat
                    if resolution.asset_key:
                        ctx.used_ms_in_video[resolution.asset_key] = max(
                            0,
                            ctx.used_ms_in_video.get(resolution.asset_key, 0) - beat.duration_ms,
                        )
                        ctx.used_count_in_video[resolution.asset_key] = max(
                            0, ctx.used_count_in_video.get(resolution.asset_key, 0) - 1
                        )
                    replacement = self._accept(beat, scored, local, still_index)
                    replacement.candidates_considered = len(pending[index])
                    replacement.vision_rescued = True
                    replacement.verification_status = verdict.status
                    replacement.verification_reason = verdict.reason
                    replacement.visual_relevance_confidence = verdict.confidence
                    by_index[index] = replacement
                    if scored.candidate.media_type != MEDIA_VIDEO:
                        still_index += 1
                    ctx.used_ms_in_video[replacement.asset_key] = (
                        ctx.used_ms_in_video.get(replacement.asset_key, 0) + beat.duration_ms
                    )
                    ctx.used_count_in_video[replacement.asset_key] = (
                        ctx.used_count_in_video.get(replacement.asset_key, 0) + 1
                    )
                    ctx.used_descriptors.append(
                        {t.lower() for t in replacement.descriptors if len(t) > 2}
                    )
                    stats.beats_saved += 1
                    del remaining[index]
                else:
                    # QUESTIONABLE never becomes the final selection while a
                    # further candidate remains (§9) — only REJECTED counts
                    # toward that tally; a questionable verdict still simply
                    # moves on to the next round's candidate, same as reject.
                    if verdict.status == "rejected":
                        stats.rejected += 1
                    elif verdict.status == "questionable":
                        stats.questionable += 1
                    if round_index + 1 >= len(remaining[index]):
                        del remaining[index]

            round_index += 1

        if stats.beats_saved:
            logger.info(
                "Vision rescue: saved %d of %d deficient beat(s) from text-card fallback "
                "(%d contact sheet(s), %d verified, %d rejected, %d questionable, %d unavailable)",
                stats.beats_saved,
                len(pending),
                stats.contact_sheets,
                stats.verified,
                stats.rejected,
                stats.questionable,
                stats.unavailable,
            )

        plan.resolutions = [by_index[b.beat_index] for b in plan.beats if b.beat_index in by_index]
        stats.plan = plan
        return stats

    def _remediation_queries(self, beat: VisualBeat, ctx: ScoringContext) -> list[str]:
        """Widen from clause-shaped phrases to subject-shaped ones.

        The first pass queries the beat's own wording, which for an abstract
        narration line produces phrases like "history hundreds choices" that no
        stock library has ever been asked for.  Remediation asks progressively
        broader questions: the beat's own entities, then the video's subject,
        then single high-value terms.  Duplicates are dropped because an
        already-issued query is already in the memo table.
        """
        queries: list[str] = []
        seen: set[str] = set()

        def add(text: str) -> None:
            cleaned = " ".join(text.split()).strip()
            if cleaned and cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                queries.append(cleaned)

        for entity in beat.entities[:2]:
            add(entity)
        if ctx.topic_entities:
            add(" ".join(ctx.topic_entities[:2]))
        if ctx.topic_terms:
            add(" ".join(ctx.topic_terms[:2]))
            add(ctx.topic_terms[0])
        for keyword in beat.keywords[:2]:
            add(keyword)

        return queries[:REMEDIATION_MAX_QUERIES_PER_BEAT]

    def resolve(self, beats: list[VisualBeat]) -> VisualPlan:
        """Resolve every beat and return the completed plan."""
        policy = self._config.policy
        ctx = ScoringContext(
            target_width=self._config.width,
            target_height=self._config.height,
            require_commercial_safe=policy.require_commercial_safe,
            max_uses_per_video=policy.max_asset_uses_per_video,
            max_asset_total_ms=policy.max_asset_total_ms,
            topic_terms=semantics.merge_topic_terms(
                self._config.concrete_subjects,
                semantics.extract_terms(" ".join(b.narration_text for b in beats), limit=6),
                limit=6,
            ),
            topic_entities=sorted(
                {e for b in beats for e in b.entities}
                # A concrete subject's own entities/acronyms (e.g. "CMB" in
                # "CMB visualization") must be recognised as high-specificity
                # by scoring — extracted per-subject, not joined into one
                # string, so a sentence-initial capital in one subject can't
                # falsely borrow another subject's mid-sentence exemption.
                | {
                    e
                    for subject in self._config.concrete_subjects
                    for e in semantics.extract_entities(subject)
                }
            ),
        )

        self._config.cache_dir.mkdir(parents=True, exist_ok=True)
        self._config.graphics_dir.mkdir(parents=True, exist_ok=True)
        # Retained so a remediation pass reasons about the same in-video usage,
        # channel reuse and topic vocabulary the first pass built up.
        self._ctx = ctx

        # Computed once per resolve() call, grouped by scene (one narration
        # segment): which non-structural beats are part of the same
        # contiguous explanation as a structural sibling and may therefore
        # borrow its renderer instead of falling back to a plain text card.
        by_scene: dict[int, list[VisualBeat]] = {}
        for b in beats:
            by_scene.setdefault(b.scene_index, []).append(b)
        diagram_continuation: dict[int, str] = {}
        for scene_beats in by_scene.values():
            diagram_continuation.update(_diagram_continuation_intents(scene_beats))

        resolutions: list[BeatResolution] = []
        still_index = 0

        for beat in beats:
            # Visual sequence continuity (Phase 5, §8-9): a beat translation
            # grouped with its immediate predecessor as part of one
            # continuous process gets first offered the predecessor's own
            # already-resolved real asset, before searching cold. This never
            # touches beat timing (still tiles the scene exactly) — only
            # which asset a beat ends up showing. Falls through to the
            # ordinary search below whenever reuse is not available (the
            # predecessor was itself a graphic, in a different scene, or
            # already at its repetition budget) — reuse is an optimization
            # attempted first, never a substitute for normal resolution.
            reused = self._reuse_previous_for_sequence(beat, resolutions, ctx)
            if reused is not None:
                resolutions.append(reused)
                continue

            candidates = self._gather(beat)

            # Channel memory is consulted once per beat, for the candidates
            # actually in hand.
            if self._conn is not None and candidates:
                ctx.channel_reuse.update(
                    channel_reuse_weights(
                        self._conn,
                        [c.asset_key for c in candidates],
                        channel_key=self._config.channel_key,
                        exclude_scene_manifest_id=self._config.scene_manifest_id or None,
                    )
                )

            if not candidates:
                resolutions.append(
                    self._graphic_resolution(
                        beat,
                        reason=FALLBACK_NO_CANDIDATES,
                        render_intent=diagram_continuation.get(beat.beat_index),
                    )
                )
                continue

            ranked = rank_candidates(beat, candidates, ctx)
            accepted = [s for s in ranked if s.accepted]
            best_score = ranked[0].score if ranked else 0.0

            # Nothing the beat's own wording retrieved is relevant. Before
            # giving up on footage entirely, ask for the video's subject
            # itself: for a depictive beat, on-topic imagery beats a text
            # card. Structural beats skip this — a diagram is already the
            # better answer for them. The query is identical for every beat,
            # so search memoisation makes this one provider call per video.
            if not accepted and beat.visual_intent not in STRUCTURAL_INTENTS:
                topic_query = " ".join(ctx.topic_terms[:2]).strip()
                if topic_query:
                    extra = [
                        c
                        for c in self._gather(beat, [topic_query])
                        if c.asset_key not in {x.asset_key for x in candidates}
                    ]
                    if extra:
                        candidates = candidates + extra
                        ranked = rank_candidates(beat, candidates, ctx)
                        accepted = [s for s in ranked if s.accepted]
                        best_score = max(best_score, ranked[0].score if ranked else 0.0)

            relaxed_still_used = False
            if not accepted and beat.visual_intent not in STRUCTURAL_INTENTS:
                # Fallback hierarchy tier 5: a still image the scorer already
                # confirmed is relevant — it cleared license, corroboration,
                # and evidence, exactly like an ordinary accepted candidate —
                # but missed only the aggregate *technical* score (resolution,
                # duration fit). That is a better outcome than a text card, so
                # long as relevance itself was never in question. Bounded to a
                # fraction of the ordinary floor and to media that already
                # carries Ken Burns treatment when accepted (see _accept());
                # a candidate the scorer actually flagged as off-topic
                # (weak/no evidence, avoid-term drift) never reaches here.
                relaxed = [
                    s
                    for s in ranked
                    if s.rejected_reason == REASON_BELOW_FLOOR
                    and s.candidate.media_type != MEDIA_VIDEO
                    and s.score >= ctx.min_score * RELAXED_STILL_SCORE_FRACTION
                ]
                if relaxed:
                    accepted = [max(relaxed, key=lambda s: s.score)]
                    relaxed_still_used = True

            resolution: BeatResolution | None = None
            for scored in accepted:
                provider = next(
                    (p for p in self._providers if p.identity == scored.candidate.provider),
                    None,
                )
                if provider is None:
                    continue
                self._calls.record_download(provider.identity)
                local = provider.download(scored.candidate, self._config.cache_dir)
                if local is None:
                    continue
                resolution = self._accept(beat, scored, local, still_index)
                resolution.candidates_considered = len(candidates)
                if relaxed_still_used:
                    resolution.fallback_reason = FALLBACK_RELEVANT_STILL_RELAXED_FLOOR
                ctx.used_descriptors.append(
                    {t.lower() for t in resolution.descriptors if len(t) > 2}
                )
                if scored.candidate.media_type != MEDIA_VIDEO:
                    still_index += 1
                break

            if resolution is None:
                reason = (
                    FALLBACK_STRUCTURAL_PREFERENCE
                    if beat.visual_intent in STRUCTURAL_INTENTS and accepted == []
                    else (FALLBACK_ALL_REJECTED if accepted == [] else FALLBACK_DOWNLOAD_FAILED)
                )
                render_intent = (
                    diagram_continuation.get(beat.beat_index)
                    if reason in (FALLBACK_NO_CANDIDATES, FALLBACK_ALL_REJECTED)
                    else None
                )
                resolution = self._graphic_resolution(
                    beat,
                    reason=reason,
                    best_score=best_score,
                    considered=len(candidates),
                    render_intent=render_intent,
                )

            resolution.candidate_evidence = _candidate_evidence_list(ranked, resolution)

            # Update in-video usage so the next beat sees the real cost of
            # repeating this asset.
            if resolution.asset_key:
                ctx.used_ms_in_video[resolution.asset_key] = (
                    ctx.used_ms_in_video.get(resolution.asset_key, 0) + beat.duration_ms
                )
                ctx.used_count_in_video[resolution.asset_key] = (
                    ctx.used_count_in_video.get(resolution.asset_key, 0) + 1
                )

            resolutions.append(resolution)

        _collapse_text_card_cascades(resolutions)

        return VisualPlan(
            scene_manifest_id=self._config.scene_manifest_id,
            topic_id=self._config.topic_id,
            experiment_id=self._config.experiment_id,
            channel_key=self._config.channel_key,
            engine_version=VISUAL_ENGINE_VERSION,
            planner_version=BEAT_PLANNER_VERSION,
            beats=list(beats),
            resolutions=resolutions,
            provider_calls=self._calls.as_dict(),
        )
