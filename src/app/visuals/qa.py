"""Visual QA gate.

Separates two things the pipeline previously conflated: a render that FFmpeg
produced successfully, and a render that is good enough to release.

The gate never blocks on a single low-confidence decision — a few weak beats
in an otherwise strong video are a review note.  It blocks on the failure
modes that make a video unpublishable: one clip dominating the runtime,
unresolved beats, unsafe licensing, or a visual track that barely changes.
"""

from __future__ import annotations

from collections import Counter

from app.visuals.constants import (
    LICENSE_UNSAFE,
    LOW_CONFIDENCE_SCORE,
    QA_FAIL,
    QA_MAX_DESCRIPTOR_OVERLAP,
    QA_MAX_LOW_CONFIDENCE_SHARE,
    QA_MAX_PLACEHOLDER_SHARE,
    QA_MAX_SINGLE_ASSET_SHARE,
    QA_MIN_BEATS_PER_MINUTE,
    QA_MIN_DISTINCT_ASSET_SHARE,
    QA_PASS,
    QA_REVIEW_NEEDED,
    QA_VERSION,
)
from app.visuals.models import BeatResolution, QAFinding, VisualPlan, VisualQAReport

# Finding codes — stable identifiers for downstream review tooling.
CODE_NO_BEATS = "no_beats"
CODE_UNRESOLVED_BEATS = "unresolved_beats"
CODE_DOMINANT_ASSET = "dominant_asset"
CODE_LOW_VISUAL_CHANGE = "low_visual_change"
CODE_PLACEHOLDER_HEAVY = "placeholder_heavy"
CODE_LOW_DIVERSITY = "low_asset_diversity"
CODE_LOW_CONFIDENCE = "low_confidence_retrieval"
CODE_UNSAFE_LICENSE = "unsafe_license"
CODE_MISSING_FILE = "missing_asset_file"
CODE_ATTRIBUTION_MISSING = "attribution_missing"
CODE_VISUAL_MONOTONY = "visual_monotony"


def _dominant(resolutions: list[BeatResolution]) -> tuple[str | None, int]:
    totals: Counter[str] = Counter()
    for resolution in resolutions:
        if resolution.asset_key:
            totals[resolution.asset_key] += resolution.beat.duration_ms
    if not totals:
        return None, 0
    key, ms = totals.most_common(1)[0]
    return key, ms


def _mean_descriptor_overlap(resolutions: list[BeatResolution]) -> float:
    """Mean pairwise Jaccard overlap across retrieved assets' descriptions.

    High overlap with a high distinct-asset count is the signature of a video
    that is technically varied and visually repetitive.
    """
    sets = [
        {t.lower() for t in r.descriptors if len(t) > 2}
        for r in resolutions
        if r.provider != "programmatic" and r.descriptors
    ]
    sets = [s for s in sets if s]
    if len(sets) < 2:
        return 0.0
    scores: list[float] = []
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            union = sets[i] | sets[j]
            if union:
                scores.append(len(sets[i] & sets[j]) / len(union))
    return sum(scores) / len(scores) if scores else 0.0


def audit_visual_plan(
    plan: VisualPlan,
    *,
    require_commercial_safe: bool = True,
    check_files_exist: bool = True,
) -> VisualQAReport:
    """Audit a resolved visual plan and return a release verdict."""
    from pathlib import Path

    resolutions = plan.resolutions
    report = VisualQAReport(qa_version=QA_VERSION)
    report.beat_count = len(resolutions)

    if not resolutions:
        report.status = QA_FAIL
        report.findings.append(
            QAFinding(CODE_NO_BEATS, "blocking", "The visual plan contains no beats.")
        )
        return report

    total_ms = sum(r.beat.duration_ms for r in resolutions) or 1

    report.resolved_count = sum(1 for r in resolutions if r.resolved)
    report.placeholder_count = sum(1 for r in resolutions if r.is_placeholder)
    report.media_type_distribution = dict(Counter(r.media_type for r in resolutions))
    report.provider_distribution = dict(Counter(r.provider for r in resolutions))
    report.distinct_asset_count = len({r.asset_key for r in resolutions if r.asset_key})
    report.beats_per_minute = len(resolutions) / (total_ms / 60_000.0)
    # Only retrieved assets can be low-confidence. A programmatic graphic
    # carries the score of the candidates it beat, which says nothing about
    # the graphic's own fitness — counting it would penalise the correct call.
    report.low_confidence_count = sum(
        1
        for r in resolutions
        if r.provider != "programmatic" and r.score and r.score < LOW_CONFIDENCE_SCORE
    )

    dominant_key, dominant_ms = _dominant(resolutions)
    report.dominant_asset_key = dominant_key
    report.dominant_asset_ms = dominant_ms
    report.dominant_asset_share = dominant_ms / total_ms

    # ── Blocking conditions ─────────────────────────────────────────────────

    unresolved = [r for r in resolutions if not r.resolved]
    if unresolved:
        report.findings.append(
            QAFinding(
                CODE_UNRESOLVED_BEATS,
                "blocking",
                f"{len(unresolved)} beat(s) have no visual asset.",
                {"beat_indexes": [r.beat.beat_index for r in unresolved]},
            )
        )

    if check_files_exist:
        missing = [r for r in resolutions if r.local_path and not Path(r.local_path).exists()]
        if missing:
            report.findings.append(
                QAFinding(
                    CODE_MISSING_FILE,
                    "blocking",
                    f"{len(missing)} resolved asset file(s) are missing on disk.",
                    {"beat_indexes": [r.beat.beat_index for r in missing]},
                )
            )

    unsafe = [
        r
        for r in resolutions
        if r.license_status == LICENSE_UNSAFE or (require_commercial_safe and not r.commercial_safe)
    ]
    if unsafe:
        report.findings.append(
            QAFinding(
                CODE_UNSAFE_LICENSE,
                "blocking",
                f"{len(unsafe)} beat(s) use assets that are not cleared for commercial use.",
                {"beat_indexes": [r.beat.beat_index for r in unsafe]},
            )
        )

    if report.dominant_asset_share > QA_MAX_SINGLE_ASSET_SHARE:
        report.findings.append(
            QAFinding(
                CODE_DOMINANT_ASSET,
                "blocking",
                (
                    f"One asset covers {report.dominant_asset_share:.0%} of the video "
                    f"({dominant_ms / 1000:.1f}s)."
                ),
                {"asset_key": dominant_key, "duration_ms": dominant_ms},
            )
        )

    if report.beats_per_minute < QA_MIN_BEATS_PER_MINUTE:
        report.findings.append(
            QAFinding(
                CODE_LOW_VISUAL_CHANGE,
                "blocking",
                (
                    f"The visual track changes {report.beats_per_minute:.1f} times per "
                    f"minute; short-form pacing needs at least {QA_MIN_BEATS_PER_MINUTE:.0f}."
                ),
            )
        )

    placeholder_share = report.placeholder_count / len(resolutions)
    if placeholder_share > QA_MAX_PLACEHOLDER_SHARE:
        report.findings.append(
            QAFinding(
                CODE_PLACEHOLDER_HEAVY,
                "blocking",
                f"{placeholder_share:.0%} of beats are bare placeholders.",
                {"placeholder_count": report.placeholder_count},
            )
        )

    # ── Review-level conditions ─────────────────────────────────────────────

    distinct_share = report.distinct_asset_count / len(resolutions)
    if distinct_share < QA_MIN_DISTINCT_ASSET_SHARE:
        report.findings.append(
            QAFinding(
                CODE_LOW_DIVERSITY,
                "warning",
                (
                    f"Only {report.distinct_asset_count} distinct assets across "
                    f"{len(resolutions)} beats."
                ),
            )
        )

    low_confidence_share = report.low_confidence_count / len(resolutions)
    if low_confidence_share > QA_MAX_LOW_CONFIDENCE_SHARE:
        report.findings.append(
            QAFinding(
                CODE_LOW_CONFIDENCE,
                "warning",
                f"{low_confidence_share:.0%} of beats resolved with low relevance confidence.",
            )
        )

    monotony = _mean_descriptor_overlap(resolutions)
    if monotony > QA_MAX_DESCRIPTOR_OVERLAP:
        report.findings.append(
            QAFinding(
                CODE_VISUAL_MONOTONY,
                "warning",
                (
                    f"Retrieved visuals average {monotony:.0%} descriptive overlap; "
                    "the video may read as one repeated visual idea."
                ),
                {"mean_overlap": round(monotony, 4)},
            )
        )

    missing_attribution = [
        r for r in resolutions if r.attribution_required and not r.attribution_text
    ]
    if missing_attribution:
        report.findings.append(
            QAFinding(
                CODE_ATTRIBUTION_MISSING,
                "warning",
                f"{len(missing_attribution)} asset(s) require attribution but carry none.",
                {"beat_indexes": [r.beat.beat_index for r in missing_attribution]},
            )
        )

    if report.blocking:
        report.status = QA_FAIL
    elif any(f.severity == "warning" for f in report.findings):
        report.status = QA_REVIEW_NEEDED
    else:
        report.status = QA_PASS
    return report
