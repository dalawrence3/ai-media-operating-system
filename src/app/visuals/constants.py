"""Semantic visual engine — versioned constants.

Nothing in this module is channel-, niche-, or topic-specific.  Domain
knowledge belongs in the narration being planned, never here.
"""

from __future__ import annotations

from typing import Final

VISUAL_ENGINE_VERSION: Final[str] = "1.0"
BEAT_PLANNER_VERSION: Final[str] = "1.0"
SCORING_VERSION: Final[str] = "1.0"
QA_VERSION: Final[str] = "1.0"

# ── Beat intents ─────────────────────────────────────────────────────────────
# What the narration is *doing* at this moment.  Drives media-type preference,
# query construction, and the programmatic-graphic renderer.

INTENT_ENTITY: Final[str] = "entity"
INTENT_ACTION: Final[str] = "action"
INTENT_PROCESS: Final[str] = "process"
INTENT_COMPARISON: Final[str] = "comparison"
INTENT_DIAGRAM: Final[str] = "diagram"
INTENT_NUMBER: Final[str] = "number"
INTENT_QUOTE: Final[str] = "quote"
INTENT_LOCATION: Final[str] = "location"
INTENT_TIMELINE: Final[str] = "timeline"
INTENT_CONCEPT: Final[str] = "concept"
INTENT_EMPHASIS: Final[str] = "emphasis"
INTENT_TRANSITION: Final[str] = "transition"
INTENT_CTA: Final[str] = "cta"

ALL_INTENTS: Final[tuple[str, ...]] = (
    INTENT_ENTITY,
    INTENT_ACTION,
    INTENT_PROCESS,
    INTENT_COMPARISON,
    INTENT_DIAGRAM,
    INTENT_NUMBER,
    INTENT_QUOTE,
    INTENT_LOCATION,
    INTENT_TIMELINE,
    INTENT_CONCEPT,
    INTENT_EMPHASIS,
    INTENT_TRANSITION,
    INTENT_CTA,
)

# ── Media types ──────────────────────────────────────────────────────────────

MEDIA_VIDEO: Final[str] = "video"
MEDIA_PHOTO: Final[str] = "photo"
MEDIA_ILLUSTRATION: Final[str] = "illustration"
MEDIA_GRAPHIC: Final[str] = "graphic"  # locally generated, zero marginal cost

ALL_MEDIA_TYPES: Final[tuple[str, ...]] = (
    MEDIA_VIDEO,
    MEDIA_PHOTO,
    MEDIA_ILLUSTRATION,
    MEDIA_GRAPHIC,
)

# Intents whose meaning lives in *structure* rather than in footage.  Stock
# retrieval for these is still attempted, but must clear a higher bar than a
# locally generated explanatory graphic before it wins.
STRUCTURAL_INTENTS: Final[frozenset[str]] = frozenset(
    {
        INTENT_PROCESS,
        INTENT_COMPARISON,
        INTENT_DIAGRAM,
        INTENT_NUMBER,
        INTENT_TIMELINE,
        INTENT_QUOTE,
    }
)

# Preferred media types per intent, most preferred first.
INTENT_MEDIA_PREFERENCES: Final[dict[str, tuple[str, ...]]] = {
    INTENT_ENTITY: (MEDIA_VIDEO, MEDIA_PHOTO, MEDIA_GRAPHIC),
    INTENT_ACTION: (MEDIA_VIDEO, MEDIA_PHOTO, MEDIA_GRAPHIC),
    INTENT_LOCATION: (MEDIA_VIDEO, MEDIA_PHOTO, MEDIA_GRAPHIC),
    INTENT_CONCEPT: (MEDIA_GRAPHIC, MEDIA_VIDEO, MEDIA_PHOTO),
    INTENT_EMPHASIS: (MEDIA_GRAPHIC, MEDIA_VIDEO, MEDIA_PHOTO),
    INTENT_TRANSITION: (MEDIA_VIDEO, MEDIA_PHOTO, MEDIA_GRAPHIC),
    INTENT_PROCESS: (MEDIA_GRAPHIC, MEDIA_ILLUSTRATION, MEDIA_VIDEO),
    INTENT_COMPARISON: (MEDIA_GRAPHIC, MEDIA_ILLUSTRATION, MEDIA_PHOTO),
    INTENT_DIAGRAM: (MEDIA_GRAPHIC, MEDIA_ILLUSTRATION, MEDIA_PHOTO),
    INTENT_NUMBER: (MEDIA_GRAPHIC, MEDIA_VIDEO, MEDIA_PHOTO),
    INTENT_TIMELINE: (MEDIA_GRAPHIC, MEDIA_ILLUSTRATION, MEDIA_PHOTO),
    INTENT_QUOTE: (MEDIA_GRAPHIC, MEDIA_PHOTO, MEDIA_VIDEO),
    INTENT_CTA: (MEDIA_GRAPHIC, MEDIA_VIDEO, MEDIA_PHOTO),
}

# ── Beat pacing ──────────────────────────────────────────────────────────────
# Targets, not hard rules.  The planner adapts to narration semantics; these
# only bound the adaptation so a beat can never be subliminal or interminable.

DEFAULT_TARGET_BEAT_MS: Final[int] = 3400
MIN_BEAT_MS: Final[int] = 1500
MAX_BEAT_MS: Final[int] = 6500

# ── Diversity / repetition safeguards ────────────────────────────────────────

# One asset may not occupy more than this share of the finished video.
MAX_ASSET_VIDEO_SHARE: Final[float] = 0.25
# ...nor more than this many wall-clock seconds in total.
MAX_ASSET_TOTAL_MS: Final[int] = 12_000
# ...nor appear on more than this many beats.
MAX_ASSET_USES_PER_VIDEO: Final[int] = 2
# Reuse of an asset already seen on this channel decays over this many days.
CHANNEL_REUSE_DECAY_DAYS: Final[int] = 120

# ── Scoring ──────────────────────────────────────────────────────────────────

# A candidate below this floor is rejected outright: a locally generated
# explanatory graphic is a better product than irrelevant footage.
#
# These floors are calibrated against scores computed from provider metadata
# ONLY. An earlier revision also credited a candidate for the query used to
# retrieve it, which inflated every score toward 1.0 and made these thresholds
# meaningless — a candidate cannot be evidence for its own relevance.
MIN_ACCEPTABLE_SCORE: Final[float] = 0.40
# Structural intents demand more from stock before it beats a graphic.
STRUCTURAL_STOCK_SCORE_FLOOR: Final[float] = 0.55
# A numeric claim is the case stock footage can never actually depict: the
# figure itself is the visual. Only an exceptionally on-topic clip wins here.
NUMBER_STOCK_SCORE_FLOOR: Final[float] = 0.68
# Minimum total specificity a candidate's matched terms must carry.  0.9 means
# one named-entity match, or two mid-length ordinary words — but never a single
# common word, which is how "repair" retrieved a car garage.
MIN_EVIDENCE_WEIGHT: Final[float] = 0.9

# Below this, the beat is flagged low-confidence for QA even if it resolved.
LOW_CONFIDENCE_SCORE: Final[float] = 0.48

# ── License / commercial safety ──────────────────────────────────────────────

LICENSE_VERIFIED: Final[str] = "verified"
LICENSE_UNKNOWN: Final[str] = "unknown"
LICENSE_NOT_REQUIRED: Final[str] = "not_required"
LICENSE_UNSAFE: Final[str] = "unsafe"

# ── Provider cost tiers (relative, not currency) ─────────────────────────────

COST_FREE: Final[int] = 0
COST_LOCAL: Final[int] = 0
COST_LOW: Final[int] = 1
COST_HIGH: Final[int] = 3

# ── Motion treatments for stills ─────────────────────────────────────────────

MOTION_NONE: Final[str] = "none"
MOTION_ZOOM_IN: Final[str] = "zoom_in"
MOTION_ZOOM_OUT: Final[str] = "zoom_out"
MOTION_PAN_LEFT: Final[str] = "pan_left"
MOTION_PAN_RIGHT: Final[str] = "pan_right"

# How a source is fitted into the target frame.
FIT_COVER: Final[str] = "cover"  # fill the frame, centre-crop the excess
FIT_CONTAIN: Final[str] = "contain"  # fit whole image, fill behind it

# Centre-cropping below this fraction of the source area destroys the content.
# A wide labelled diagram cropped to 9:16 keeps ~36% of its width and becomes
# unreadable — exactly the case where the asset was chosen *for* its labels.
MIN_RETAINED_AREA_FOR_CROP: Final[float] = 0.6

STILL_MOTIONS: Final[tuple[str, ...]] = (
    MOTION_ZOOM_IN,
    MOTION_PAN_RIGHT,
    MOTION_ZOOM_OUT,
    MOTION_PAN_LEFT,
)

# ── QA verdicts ──────────────────────────────────────────────────────────────

QA_PASS: Final[str] = "pass"
QA_REVIEW_NEEDED: Final[str] = "review_needed"
QA_FAIL: Final[str] = "fail"

# QA thresholds
QA_MIN_BEATS_PER_MINUTE: Final[float] = 8.0
QA_MAX_PLACEHOLDER_SHARE: Final[float] = 0.15
QA_MAX_UNRESOLVED_SHARE: Final[float] = 0.0
QA_MAX_LOW_CONFIDENCE_SHARE: Final[float] = 0.4
QA_MAX_SINGLE_ASSET_SHARE: Final[float] = 0.30
QA_MIN_DISTINCT_ASSET_SHARE: Final[float] = 0.55
# Mean pairwise descriptive overlap above which the retrieved visuals read as
# one repeated idea, however many distinct asset ids they carry.
QA_MAX_DESCRIPTOR_OVERLAP: Final[float] = 0.34
