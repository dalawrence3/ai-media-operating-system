"""Visual quality floors for autonomous publishing.

Phase 18E.  This module answers one question: is this render's *actual* visual
composition above the floor below which an autonomous system should not
publish without a human looking at it?

It is deliberately not an aesthetic judge.  There is no rule here about which
visual family is better; the learning system is what eventually discovers
whether diagrams, footage or minimalism perform on a given channel.  These are
failure-mode floors — the conditions under which a video is *obviously* weak
regardless of taste:

  * almost nothing but typeset narration on screen;
  * half a minute at a time with no meaningful visual;
  * a visual track that is mostly the wreckage of failed retrieval.

Creative intent is respected but never used as a bypass.  A channel that
deliberately chose a minimalist treatment gets its creative floors relaxed; it
does NOT get the provider-failure floors relaxed, because "we meant to use
text" and "every image search returned nothing usable" are different facts and
only the first one is a decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.visuals.composition import (
    FAMILY_TEXT_CARD,
    RETRIEVED_FAMILIES,
    VisualComposition,
)
from app.visuals.policy import STYLE_MINIMALIST

QUALITY_POLICY_VERSION: str = "visual-quality-policy-v1"
ASSESSMENT_VERSION: str = "visual-assessment-v1"

# ── Verdicts ─────────────────────────────────────────────────────────────────

VQ_PASS = "pass"
VQ_PASS_WITH_WARNINGS = "pass_with_warnings"
VQ_BLOCKED = "blocked"

SEVERITY_WARNING = "warning"
SEVERITY_BLOCKING = "blocking"

# ── Finding codes (stable identifiers for UI and downstream tooling) ─────────

CODE_NO_BEATS = "visual_no_beats"
CODE_UNRESOLVED = "visual_unresolved_beats"
CODE_NO_MEANINGFUL = "visual_no_meaningful_visual"
CODE_MEANINGFUL_FLOOR = "visual_meaningful_runtime_below_floor"
CODE_LONG_GAP = "visual_excessive_meaningful_gap"
CODE_PROVIDER_FAILURE = "visual_provider_fallback_dominant"

CODE_MEANINGFUL_LOW = "visual_meaningful_runtime_low"
CODE_TEXT_HEAVY = "visual_text_card_heavy"
CODE_WEAK_OPENING = "visual_weak_opening"
CODE_NOTABLE_GAP = "visual_notable_meaningful_gap"
CODE_FAMILY_DOMINANCE = "visual_single_family_dominance"
CODE_LOW_CADENCE = "visual_low_change_cadence"
CODE_ASSET_REUSE = "visual_asset_reuse_high"
CODE_PROVIDER_DEGRADED = "visual_provider_fallback_elevated"
CODE_SEVERE_MISMATCH = "visual_severe_mismatch"
CODE_RELEVANT_COVERAGE_LOW = "visual_relevant_coverage_low"
CODE_VERIFICATION_INCOMPLETE = "visual_verification_incomplete"


@dataclass(frozen=True)
class VisualQualityThresholds:
    """The floors, in one place, so a channel can carry its own.

    Initial values are conservative and derived from what this production
    architecture actually produces: a render whose retrieval works lands near
    90%+ meaningful runtime with single-digit provider fallback, so a floor at
    25% meaningful runtime and 50% provider fallback sits far below any
    working render rather than being fitted to any particular failure.
    """

    # Blocking
    min_meaningful_runtime_pct: float = 0.25
    max_meaningful_gap_ms: int = 20_000
    max_provider_fallback_rate: float = 0.50
    # Phase 7C.1, Part C — True (every format until now) means "a render
    # with literally zero meaningful (retrieved/diagram-family) beats
    # anywhere is always a hard failure" and enables the max_meaningful_gap_ms
    # check (which is meaningless without an expectation of meaningful
    # content existing at all). A format whose defining mechanic IS
    # intentional text/graphic cards (interactive_recall_quiz) sets this
    # False — its own min_meaningful_runtime_pct/text-card floors above
    # still apply and can still block a render that HAS provider fallback,
    # this only removes the absolute "not one beat" rule that no relaxed
    # percentage floor could ever satisfy for a 100%-designed-text render.
    require_any_meaningful_visual: bool = True
    # A severe mismatch (an actual-content verification rejection — §7-9) is
    # never diluted by an otherwise-strong render: this floor is 0 on
    # purpose, not a percentage. NOTE: with the Phase-1/Phase-2 scoring
    # hardening in place, an *accepted* beat can no longer be a severe
    # mismatch via the lexical scorer alone — this is the backstop for when
    # the (optional, off-by-default) verification pass catches something the
    # scorer's own metadata-only view could not.
    max_severe_mismatch_count: int = 0
    # §9's fast path: a beat verification could not check (provider outage,
    # malformed response — STATUS_UNAVAILABLE) is still safe to publish if
    # the DETERMINISTIC scorer already carries extremely high confidence in
    # it — no vision check needed to trust a candidate the lexical scorer
    # was already very sure about. Below this bar, an unchecked beat holds
    # the render (see `verification_required` on `assess_composition`).
    verification_fast_path_min_score: float = 0.75
    # Gap and cadence floors are meaningless on a very short clip.
    gap_rules_min_duration_ms: int = 20_000

    # Warning
    warn_meaningful_runtime_pct: float = 0.50
    warn_text_card_runtime_pct: float = 0.35
    warn_meaningful_gap_ms: int = 12_000
    warn_dominant_family_share: float = 0.70
    warn_min_changes_per_minute: float = 8.0
    warn_asset_reuse_ratio: float = 0.35
    warn_provider_fallback_rate: float = 0.25
    # The product aspiration (§12) is >=85% relevant coverage. This is
    # deliberately NOT that number: `relevant_visual_runtime_pct` is a brand
    # new metric with no accumulated production distribution to calibrate a
    # BLOCKING floor against yet (the same reasoning `min_meaningful_runtime_pct`'s
    # own docstring gives for its conservative 25%). Warn well below the
    # aspiration for now; tighten once real renders establish where the
    # working/failing boundary actually falls.
    warn_relevant_visual_runtime_pct: float = 0.55

    # An explicitly minimalist treatment lowers the CREATIVE floor only.
    minimalist_min_meaningful_runtime_pct: float = 0.15


DEFAULT_THRESHOLDS = VisualQualityThresholds()

# ── Phase 7C.1, Part C — format-aware quality profiles ───────────────────────
#
# The smallest possible format-aware policy: a lookup from exploration
# format -> a tuned VisualQualityThresholds instance. No new assessment
# logic — assess_composition() already takes `thresholds` as a parameter;
# this only chooses WHICH instance to pass it.
#
# Every profile below relaxes ONLY the CREATIVE-content floors
# (meaningful/relevant/text-card runtime — how much of the video "looks
# like" retrieved footage vs. a designed card), because those floors were
# tuned against curiosity_explainer's own retrieved-imagery-heavy shape and
# do not describe what a well-executed quiz or lesson looks like.
#
# Every profile leaves max_provider_fallback_rate, warn_provider_fallback_rate,
# max_severe_mismatch_count, and the gap floors at DEFAULT_THRESHOLDS'
# values, UNCHANGED, on every format — these describe genuine failure
# (retrieval/download did not deliver, or a visual was confirmed to depict
# the wrong thing), which is exactly as bad in a quiz as in an explainer.
# composition.py already classifies every fallback beat as FALLBACK_CREATIVE
# ("the planner preferred a graphic on purpose") or FALLBACK_PROVIDER
# ("retrieval or download did not deliver") — provider_fallback_rate only
# ever counts the latter, so an intentional quiz prompt/reveal card (which
# never attempted retrieval at all, or was a deliberate structural choice)
# was never counted against this gate in the first place; relaxing it here
# would only ever hide real failures, which these profiles deliberately do
# not do.

_EXPLAINER_THRESHOLDS = DEFAULT_THRESHOLDS

# interactive_recall_quiz: prompt/reveal/next_challenge cards ARE the
# content, by design (see SECTION_ASSET_PREFERENCES in scenes/constants.py
# — "prompt"/"reveal" point at icon/animation, never stock photography).
# Requiring 25% "meaningful" (retrieved-family) runtime or capping text-card
# runtime at 35% would penalize a well-executed quiz for succeeding at what
# it set out to do.
_QUIZ_THRESHOLDS = VisualQualityThresholds(
    # A purely prompt/reveal/next_challenge quiz can legitimately be 100%
    # intentional text/graphic cards with zero retrieved imagery anywhere
    # — that is a success, not a failure, for this format. 0.0 combined
    # with require_any_meaningful_visual=False below means this floor can
    # never block on content-mix alone; only genuine provider failure
    # (max_provider_fallback_rate, unchanged) or a severe mismatch can.
    min_meaningful_runtime_pct=0.0,
    warn_meaningful_runtime_pct=0.20,
    warn_text_card_runtime_pct=0.85,
    warn_relevant_visual_runtime_pct=0.20,
    # A well-executed quiz can legitimately have ZERO "meaningful"
    # (retrieved/diagram-family) beats at all — every beat is an
    # intentional prompt/reveal/next_challenge card. The absolute "not one
    # beat" rule and the max-gap check (which is meaningless without an
    # expectation of meaningful content existing anywhere) both assume
    # every format wants photographic/motion/diagram content somewhere;
    # a quiz does not. min_meaningful_runtime_pct/warn_text_card_runtime_pct
    # above still apply when a quiz DOES retrieve some imagery.
    require_any_meaningful_visual=False,
    # Unchanged from DEFAULT_THRESHOLDS — genuine failure floors:
    max_meaningful_gap_ms=DEFAULT_THRESHOLDS.max_meaningful_gap_ms,
    max_provider_fallback_rate=DEFAULT_THRESHOLDS.max_provider_fallback_rate,
    warn_provider_fallback_rate=DEFAULT_THRESHOLDS.warn_provider_fallback_rate,
    max_severe_mismatch_count=DEFAULT_THRESHOLDS.max_severe_mismatch_count,
    verification_fast_path_min_score=DEFAULT_THRESHOLDS.verification_fast_path_min_score,
    gap_rules_min_duration_ms=DEFAULT_THRESHOLDS.gap_rules_min_duration_ms,
)

# practical_micro_lesson: a real mix of programmatic example graphics,
# number callouts, diagrams, and relevant footage/images is the intended
# shape (§13) — not every beat needs to be moving stock footage, but this
# format leans on visual variety more than a quiz's prompt/reveal cards do,
# so its relaxation is moderate, between explainer and quiz.
_MICRO_LESSON_THRESHOLDS = VisualQualityThresholds(
    min_meaningful_runtime_pct=0.18,
    warn_meaningful_runtime_pct=0.35,
    warn_text_card_runtime_pct=0.55,
    warn_relevant_visual_runtime_pct=0.40,
    # Unchanged from DEFAULT_THRESHOLDS — genuine failure floors:
    max_meaningful_gap_ms=DEFAULT_THRESHOLDS.max_meaningful_gap_ms,
    max_provider_fallback_rate=DEFAULT_THRESHOLDS.max_provider_fallback_rate,
    warn_provider_fallback_rate=DEFAULT_THRESHOLDS.warn_provider_fallback_rate,
    max_severe_mismatch_count=DEFAULT_THRESHOLDS.max_severe_mismatch_count,
    verification_fast_path_min_score=DEFAULT_THRESHOLDS.verification_fast_path_min_score,
    gap_rules_min_duration_ms=DEFAULT_THRESHOLDS.gap_rules_min_duration_ms,
)

_QUALITY_PROFILES: dict[str, VisualQualityThresholds] = {
    "curiosity_explainer": _EXPLAINER_THRESHOLDS,
    "interactive_recall_quiz": _QUIZ_THRESHOLDS,
    "practical_micro_lesson": _MICRO_LESSON_THRESHOLDS,
}


def quality_profile_for(format_name: str | None) -> VisualQualityThresholds:
    """The quality thresholds to assess a render against, given its
    exploration format. Any format with no dedicated profile (including
    the default explainer, an unlinked/legacy topic with no format at
    all, and every other exploration-cell format seed with no production
    contract yet) gets DEFAULT_THRESHOLDS — the current hardened
    standard, completely unchanged. This function only ever RELAXES
    creative-content floors for the formats explicitly designed around
    intentional text/graphic cards; it never changes provider-failure or
    severe-mismatch floors for anyone."""
    return _QUALITY_PROFILES.get(format_name or "", DEFAULT_THRESHOLDS)


@dataclass
class VisualQualityFinding:
    code: str
    severity: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "evidence": self.evidence,
        }


@dataclass
class VisualQualityAssessment:
    """A verdict plus the evidence that produced it."""

    status: str
    composition: VisualComposition
    findings: list[VisualQualityFinding] = field(default_factory=list)
    assessment_version: str = ASSESSMENT_VERSION
    policy_version: str = QUALITY_POLICY_VERSION
    visual_style: str | None = None
    minimalism_applied: bool = False

    @property
    def blocking(self) -> list[VisualQualityFinding]:
        return [f for f in self.findings if f.severity == SEVERITY_BLOCKING]

    @property
    def warnings(self) -> list[VisualQualityFinding]:
        return [f for f in self.findings if f.severity == SEVERITY_WARNING]

    @property
    def blocked(self) -> bool:
        return self.status == VQ_BLOCKED

    def summary(self) -> str:
        """A one-line operator explanation naming the actual numbers."""
        if not self.findings:
            comp = self.composition
            return (
                f"Visual quality passed: {comp.meaningful_runtime_pct:.0%} of runtime "
                f"carries a meaningful visual across {comp.distinct_asset_count} distinct assets."
            )
        lead = self.blocking or self.warnings
        return "; ".join(f.message for f in lead)


def _pct(value: float) -> str:
    return f"{value:.0%}"


def assess_composition(
    composition: VisualComposition,
    *,
    visual_style: str | None = None,
    thresholds: VisualQualityThresholds = DEFAULT_THRESHOLDS,
    verification_required: bool = False,
) -> VisualQualityAssessment:
    """Apply the quality floors to a measured composition.

    Pure and deterministic: the same composition, style and
    `verification_required` always produce the same verdict, findings and
    ordering.

    `verification_required` (Phase 3, §9) is the caller's declaration that
    actual-content verification was actually turned on for this render (this
    module has no other way to know that — a render where verification was
    never enabled ALSO has every beat's `verification_status` at None, and
    that must never be mistaken for "checked and fine"). When True, any
    meaningful beat verification could not confirm — status is still None or
    "unavailable" — blocks the render UNLESS the deterministic scorer already
    carries extremely high confidence in it (`verification_fast_path_min_score`).
    A confirmed "rejected" beat blocks unconditionally either way, via
    `max_severe_mismatch_count` above, regardless of this flag.
    """
    comp = composition
    style = (visual_style or "").strip().lower() or None
    minimalist = style == STYLE_MINIMALIST
    findings: list[VisualQualityFinding] = []

    if comp.total_beat_count == 0:
        findings.append(
            VisualQualityFinding(
                CODE_NO_BEATS,
                SEVERITY_BLOCKING,
                "The render has no visual beats to assess.",
            )
        )
        return VisualQualityAssessment(
            status=VQ_BLOCKED,
            composition=comp,
            findings=findings,
            visual_style=style,
            minimalism_applied=minimalist,
        )

    long_enough_for_gap_rules = comp.total_duration_ms >= thresholds.gap_rules_min_duration_ms

    # ── Blocking floors ──────────────────────────────────────────────────────
    # Ordered most-fundamental first so the summary line leads with the cause
    # rather than a symptom.

    if comp.unresolved_beat_count:
        findings.append(
            VisualQualityFinding(
                CODE_UNRESOLVED,
                SEVERITY_BLOCKING,
                f"{comp.unresolved_beat_count} beat(s) have no visual at all.",
                {"unresolved_beat_count": comp.unresolved_beat_count},
            )
        )

    if thresholds.require_any_meaningful_visual and comp.meaningful_beat_count == 0:
        findings.append(
            VisualQualityFinding(
                CODE_NO_MEANINGFUL,
                SEVERITY_BLOCKING,
                (
                    "Not one beat carries a meaningful visual — the entire video is "
                    "typeset narration."
                ),
                {"total_beat_count": comp.total_beat_count},
            )
        )

    # A minimalist treatment lowers this floor; it does not remove it.
    meaningful_floor = (
        thresholds.minimalist_min_meaningful_runtime_pct
        if minimalist
        else thresholds.min_meaningful_runtime_pct
    )
    if comp.meaningful_runtime_pct < meaningful_floor:
        findings.append(
            VisualQualityFinding(
                CODE_MEANINGFUL_FLOOR,
                SEVERITY_BLOCKING,
                (
                    f"Only {_pct(comp.meaningful_runtime_pct)} of runtime carries a "
                    f"meaningful visual (floor {_pct(meaningful_floor)}); "
                    f"{_pct(comp.text_card_runtime_pct)} is text-card runtime."
                ),
                {
                    "meaningful_runtime_pct": round(comp.meaningful_runtime_pct, 4),
                    "text_card_runtime_pct": round(comp.text_card_runtime_pct, 4),
                    "floor": meaningful_floor,
                    "minimalism_applied": minimalist,
                },
            )
        )

    if (
        thresholds.require_any_meaningful_visual
        and long_enough_for_gap_rules
        and comp.max_meaningful_gap_ms > thresholds.max_meaningful_gap_ms
    ):
        findings.append(
            VisualQualityFinding(
                CODE_LONG_GAP,
                SEVERITY_BLOCKING,
                (
                    f"The longest stretch with no meaningful visual is "
                    f"{comp.max_meaningful_gap_ms / 1000:.0f}s "
                    f"(limit {thresholds.max_meaningful_gap_ms / 1000:.0f}s)."
                ),
                {
                    "max_meaningful_gap_ms": comp.max_meaningful_gap_ms,
                    "avg_meaningful_gap_ms": round(comp.avg_meaningful_gap_ms, 1),
                    "limit_ms": thresholds.max_meaningful_gap_ms,
                },
            )
        )

    # A severe mismatch always blocks, regardless of how strong the rest of
    # the render is — never diluted into an aggregate percentage. Only
    # populated when the (optional) actual-content verification pass ran and
    # explicitly rejected an asset; composition.py never infers this from the
    # lexical score alone.
    if comp.severe_mismatch_count > thresholds.max_severe_mismatch_count:
        findings.append(
            VisualQualityFinding(
                CODE_SEVERE_MISMATCH,
                SEVERITY_BLOCKING,
                (
                    f"{comp.severe_mismatch_count} beat(s) failed actual-content "
                    "verification — a visual that does not depict what the narration "
                    "describes must never publish."
                ),
                {"severe_mismatch_count": comp.severe_mismatch_count},
            )
        )

    # §9's hold/repair-over-publish-unchecked policy: when verification was
    # actually turned on for this render, a meaningful beat verification
    # could not confirm (still None/"unavailable" after any repair attempt)
    # holds the render — UNLESS the deterministic scorer already carries
    # extremely high confidence in it, which is exempted as a fast path.
    if verification_required:
        unchecked = [
            b
            for b in comp.beats
            # Only a RETRIEVED asset is ever a verification candidate at all
            # (verification.py skips `provider == "programmatic"` entirely,
            # by design — a generated graphic is an exact deterministic
            # render of the beat's own text, not a claim about the world).
            if b.realized_family in RETRIEVED_FAMILIES
            and b.verification_status not in ("verified", "questionable", "rejected")
            and not (
                b.resolved_score is not None
                and b.resolved_score >= thresholds.verification_fast_path_min_score
            )
        ]
        if unchecked:
            findings.append(
                VisualQualityFinding(
                    CODE_VERIFICATION_INCOMPLETE,
                    SEVERITY_BLOCKING,
                    (
                        f"{len(unchecked)} beat(s) could not be content-verified and are not "
                        "high-confidence enough for the deterministic fast path — publishing "
                        "an unchecked visual is not safe while verification is enabled."
                    ),
                    {
                        "unverified_beat_indexes": [b.beat_index for b in unchecked],
                        "fast_path_min_score": thresholds.verification_fast_path_min_score,
                    },
                )
            )

    # Provider failure is never relaxed by creative intent. Choosing text for
    # some beats is a strategy; every image search failing is a broken
    # pipeline wearing that strategy's clothes.
    if comp.provider_fallback_rate > thresholds.max_provider_fallback_rate:
        findings.append(
            VisualQualityFinding(
                CODE_PROVIDER_FAILURE,
                SEVERITY_BLOCKING,
                (
                    f"{_pct(comp.provider_fallback_rate)} of beats fell back to a generated "
                    f"card because visual retrieval failed, not by design "
                    f"({comp.provider_fallback_beats} of {comp.total_beat_count} beats; "
                    f"only {comp.creative_fallback_beats} fell back by choice)."
                ),
                {
                    "provider_fallback_beats": comp.provider_fallback_beats,
                    "creative_fallback_beats": comp.creative_fallback_beats,
                    "planned_meaningful_beats": comp.planned_meaningful_beats,
                    "fallback_reasons": dict(comp.fallback_reasons),
                    "limit": thresholds.max_provider_fallback_rate,
                },
            )
        )

    # ── Warnings ─────────────────────────────────────────────────────────────

    if not minimalist and comp.meaningful_runtime_pct < thresholds.warn_meaningful_runtime_pct:
        findings.append(
            VisualQualityFinding(
                CODE_MEANINGFUL_LOW,
                SEVERITY_WARNING,
                (
                    f"{_pct(comp.meaningful_runtime_pct)} meaningful-visual runtime is below "
                    f"the {_pct(thresholds.warn_meaningful_runtime_pct)} comfort level."
                ),
                {"meaningful_runtime_pct": round(comp.meaningful_runtime_pct, 4)},
            )
        )

    if (
        not minimalist
        and comp.relevant_visual_runtime_pct < thresholds.warn_relevant_visual_runtime_pct
    ):
        findings.append(
            VisualQualityFinding(
                CODE_RELEVANT_COVERAGE_LOW,
                SEVERITY_WARNING,
                (
                    f"Only {_pct(comp.relevant_visual_runtime_pct)} of runtime is a "
                    "confidently-relevant visual, below the "
                    f"{_pct(thresholds.warn_relevant_visual_runtime_pct)} comfort level "
                    f"({comp.low_confidence_scene_count} low-confidence beat(s))."
                ),
                {
                    "relevant_visual_runtime_pct": round(comp.relevant_visual_runtime_pct, 4),
                    "low_confidence_scene_count": comp.low_confidence_scene_count,
                    "rejected_scene_count": comp.rejected_scene_count,
                },
            )
        )

    if not minimalist and comp.text_card_runtime_pct > thresholds.warn_text_card_runtime_pct:
        findings.append(
            VisualQualityFinding(
                CODE_TEXT_HEAVY,
                SEVERITY_WARNING,
                f"{_pct(comp.text_card_runtime_pct)} of runtime is text cards.",
                {"text_card_runtime_pct": round(comp.text_card_runtime_pct, 4)},
            )
        )

    if not comp.opening_meaningful_visual:
        findings.append(
            VisualQualityFinding(
                CODE_WEAK_OPENING,
                SEVERITY_WARNING,
                "No meaningful visual appears in the opening seconds.",
                {"opening_window_ms": 4_000},
            )
        )

    if (
        long_enough_for_gap_rules
        and thresholds.warn_meaningful_gap_ms
        < comp.max_meaningful_gap_ms
        <= thresholds.max_meaningful_gap_ms
    ):
        findings.append(
            VisualQualityFinding(
                CODE_NOTABLE_GAP,
                SEVERITY_WARNING,
                (f"The longest meaningful-visual gap is {comp.max_meaningful_gap_ms / 1000:.0f}s."),
                {"max_meaningful_gap_ms": comp.max_meaningful_gap_ms},
            )
        )

    # Dominance by a *meaningful* family is a style; dominance by text cards is
    # already covered above, so this only warns when it is not the text case.
    if (
        comp.dominant_family_share > thresholds.warn_dominant_family_share
        and comp.dominant_family != FAMILY_TEXT_CARD
    ):
        findings.append(
            VisualQualityFinding(
                CODE_FAMILY_DOMINANCE,
                SEVERITY_WARNING,
                (
                    f"{_pct(comp.dominant_family_share)} of runtime is a single visual "
                    f"family ({comp.dominant_family})."
                ),
                {
                    "dominant_family": comp.dominant_family,
                    "dominant_family_share": round(comp.dominant_family_share, 4),
                },
            )
        )

    if (
        long_enough_for_gap_rules
        and comp.visual_changes_per_minute < thresholds.warn_min_changes_per_minute
    ):
        findings.append(
            VisualQualityFinding(
                CODE_LOW_CADENCE,
                SEVERITY_WARNING,
                (
                    f"The visual changes {comp.visual_changes_per_minute:.1f} times per "
                    f"minute (below {thresholds.warn_min_changes_per_minute:.0f})."
                ),
                {"visual_changes_per_minute": round(comp.visual_changes_per_minute, 2)},
            )
        )

    if comp.asset_reuse_ratio > thresholds.warn_asset_reuse_ratio:
        findings.append(
            VisualQualityFinding(
                CODE_ASSET_REUSE,
                SEVERITY_WARNING,
                (
                    f"{_pct(comp.asset_reuse_ratio)} of beats reuse an asset already shown "
                    f"({comp.distinct_asset_count} distinct assets across "
                    f"{comp.total_beat_count} beats)."
                ),
                {
                    "asset_reuse_ratio": round(comp.asset_reuse_ratio, 4),
                    "distinct_asset_count": comp.distinct_asset_count,
                },
            )
        )

    if (
        thresholds.warn_provider_fallback_rate
        < comp.provider_fallback_rate
        <= thresholds.max_provider_fallback_rate
    ):
        findings.append(
            VisualQualityFinding(
                CODE_PROVIDER_DEGRADED,
                SEVERITY_WARNING,
                (
                    f"{_pct(comp.provider_fallback_rate)} of beats fell back because "
                    "retrieval returned nothing usable."
                ),
                {
                    "provider_fallback_beats": comp.provider_fallback_beats,
                    "fallback_reasons": dict(comp.fallback_reasons),
                },
            )
        )

    if any(f.severity == SEVERITY_BLOCKING for f in findings):
        status = VQ_BLOCKED
    elif findings:
        status = VQ_PASS_WITH_WARNINGS
    else:
        status = VQ_PASS

    return VisualQualityAssessment(
        status=status,
        composition=comp,
        findings=findings,
        visual_style=style,
        minimalism_applied=minimalist,
    )
