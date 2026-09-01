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

    # An explicitly minimalist treatment lowers the CREATIVE floor only.
    minimalist_min_meaningful_runtime_pct: float = 0.15


DEFAULT_THRESHOLDS = VisualQualityThresholds()


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
) -> VisualQualityAssessment:
    """Apply the quality floors to a measured composition.

    Pure and deterministic: the same composition and style always produce the
    same verdict, findings and ordering.
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

    if comp.meaningful_beat_count == 0:
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

    if long_enough_for_gap_rules and comp.max_meaningful_gap_ms > thresholds.max_meaningful_gap_ms:
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
