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

from app.visuals import graphics, motion, semantics
from app.visuals.beats import BEAT_PLANNER_VERSION
from app.visuals.constants import (
    FIT_CONTAIN,
    LICENSE_NOT_REQUIRED,
    MEDIA_GRAPHIC,
    MEDIA_VIDEO,
    STRUCTURAL_INTENTS,
    VISUAL_ENGINE_VERSION,
)
from app.visuals.memory import channel_reuse_weights
from app.visuals.models import (
    BeatResolution,
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
from app.visuals.scoring import ScoringContext, rank_candidates

logger = logging.getLogger(__name__)

FALLBACK_NO_CANDIDATES = "no_candidates_returned"
FALLBACK_ALL_REJECTED = "all_candidates_rejected"
FALLBACK_DOWNLOAD_FAILED = "download_failed"
FALLBACK_STRUCTURAL_PREFERENCE = "structural_intent_prefers_graphic"

# Bounded remediation (Phase 18E). Relaxed, not removed: two ordinary words
# in common is thin evidence, but it beats showing the viewer another wall
# of typeset narration. An unrelated clip still cannot clear these.
REMEDIATION_MIN_EVIDENCE: float = 0.6
REMEDIATION_MIN_SCORE: float = 0.34
REMEDIATION_MAX_QUERIES_PER_BEAT: int = 4


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
    ) -> BeatResolution:
        stem = graphics.graphic_cache_key(beat, self._config.width, self._config.height)
        path = self._config.graphics_dir / f"beat_{beat.beat_index:03d}_{stem}.png"
        if not path.exists():
            graphics.render_beat_graphic(
                beat, path, width=self._config.width, height=self._config.height
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
            topic_terms=semantics.extract_terms(" ".join(b.narration_text for b in beats), limit=6),
            topic_entities=sorted({e for b in beats for e in b.entities}),
        )

        self._config.cache_dir.mkdir(parents=True, exist_ok=True)
        self._config.graphics_dir.mkdir(parents=True, exist_ok=True)
        # Retained so a remediation pass reasons about the same in-video usage,
        # channel reuse and topic vocabulary the first pass built up.
        self._ctx = ctx

        resolutions: list[BeatResolution] = []
        still_index = 0

        for beat in beats:
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
                resolutions.append(self._graphic_resolution(beat, reason=FALLBACK_NO_CANDIDATES))
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
                resolution = self._graphic_resolution(
                    beat,
                    reason=reason,
                    best_score=best_score,
                    considered=len(candidates),
                )

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
