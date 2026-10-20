"""Phase 7 scene-planning constants."""

MANIFEST_SCHEMA_VERSION = "1.0"
PLANNER_VERSION = "1.0"

# ── Shot types ────────────────────────────────────────────────────────────────

SHOT_TYPE_WIDE = "wide"
SHOT_TYPE_MEDIUM = "medium"
SHOT_TYPE_CLOSE_UP = "close_up"
SHOT_TYPE_EXTREME_CLOSE_UP = "extreme_close_up"
SHOT_TYPE_OVERHEAD = "overhead"
SHOT_TYPE_DRONE = "drone"
SHOT_TYPE_CUTAWAY = "cutaway"

SHOT_TYPES = frozenset(
    {
        SHOT_TYPE_WIDE,
        SHOT_TYPE_MEDIUM,
        SHOT_TYPE_CLOSE_UP,
        SHOT_TYPE_EXTREME_CLOSE_UP,
        SHOT_TYPE_OVERHEAD,
        SHOT_TYPE_DRONE,
        SHOT_TYPE_CUTAWAY,
    }
)

# ── Camera movements ──────────────────────────────────────────────────────────

CAMERA_STATIC = "static"
CAMERA_PAN = "pan"
CAMERA_TILT = "tilt"
CAMERA_ZOOM_IN = "zoom_in"
CAMERA_ZOOM_OUT = "zoom_out"
CAMERA_DOLLY = "dolly"
CAMERA_TRACKING = "tracking"
CAMERA_HANDHELD = "handheld"

CAMERA_MOVEMENTS = frozenset(
    {
        CAMERA_STATIC,
        CAMERA_PAN,
        CAMERA_TILT,
        CAMERA_ZOOM_IN,
        CAMERA_ZOOM_OUT,
        CAMERA_DOLLY,
        CAMERA_TRACKING,
        CAMERA_HANDHELD,
    }
)

# ── Transitions ───────────────────────────────────────────────────────────────

TRANSITION_CUT = "cut"
TRANSITION_DISSOLVE = "dissolve"
TRANSITION_FADE_TO_BLACK = "fade_to_black"
TRANSITION_FADE_FROM_BLACK = "fade_from_black"
TRANSITION_WIPE = "wipe"
TRANSITION_CROSS_DISSOLVE = "cross_dissolve"
TRANSITION_SMASH_CUT = "smash_cut"

TRANSITIONS = frozenset(
    {
        TRANSITION_CUT,
        TRANSITION_DISSOLVE,
        TRANSITION_FADE_TO_BLACK,
        TRANSITION_FADE_FROM_BLACK,
        TRANSITION_WIPE,
        TRANSITION_CROSS_DISSOLVE,
        TRANSITION_SMASH_CUT,
    }
)

# ── Asset categories ──────────────────────────────────────────────────────────

ASSET_STOCK_FOOTAGE = "stock_footage"
ASSET_PHOTOGRAPH = "photograph"
ASSET_ILLUSTRATION = "illustration"
ASSET_ICON = "icon"
ASSET_MAP = "map"
ASSET_CHART = "chart"
ASSET_DIAGRAM = "diagram"
ASSET_ANIMATION = "animation"
ASSET_LOGO = "logo"
ASSET_HISTORICAL_IMAGERY = "historical_imagery"
ASSET_SCREEN_RECORDING = "screen_recording"
ASSET_AI_GENERATED_IMAGE = "ai_generated_image"
ASSET_AI_GENERATED_BROLL = "ai_generated_broll"
ASSET_FUTURE_CLIP = "future_clip"

ASSET_CATEGORIES = frozenset(
    {
        ASSET_STOCK_FOOTAGE,
        ASSET_PHOTOGRAPH,
        ASSET_ILLUSTRATION,
        ASSET_ICON,
        ASSET_MAP,
        ASSET_CHART,
        ASSET_DIAGRAM,
        ASSET_ANIMATION,
        ASSET_LOGO,
        ASSET_HISTORICAL_IMAGERY,
        ASSET_SCREEN_RECORDING,
        ASSET_AI_GENERATED_IMAGE,
        ASSET_AI_GENERATED_BROLL,
        ASSET_FUTURE_CLIP,
    }
)

# ── License statuses ──────────────────────────────────────────────────────────

LICENSE_UNKNOWN = "unknown"
LICENSE_ROYALTY_FREE = "royalty_free"
LICENSE_CREATIVE_COMMONS = "creative_commons"
LICENSE_LICENSED = "licensed"
LICENSE_PUBLIC_DOMAIN = "public_domain"
LICENSE_PROPRIETARY = "proprietary"
LICENSE_REQUIRES_ATTRIBUTION = "requires_attribution"
LICENSE_COMMERCIAL_SAFE = "commercial_safe"

LICENSE_STATUSES = frozenset(
    {
        LICENSE_UNKNOWN,
        LICENSE_ROYALTY_FREE,
        LICENSE_CREATIVE_COMMONS,
        LICENSE_LICENSED,
        LICENSE_PUBLIC_DOMAIN,
        LICENSE_PROPRIETARY,
        LICENSE_REQUIRES_ATTRIBUTION,
        LICENSE_COMMERCIAL_SAFE,
    }
)

# ── Asset priority ────────────────────────────────────────────────────────────

PRIORITY_REQUIRED = "required"
PRIORITY_PREFERRED = "preferred"
PRIORITY_OPTIONAL = "optional"
PRIORITY_FALLBACK = "fallback"

ASSET_PRIORITIES = frozenset(
    {
        PRIORITY_REQUIRED,
        PRIORITY_PREFERRED,
        PRIORITY_OPTIONAL,
        PRIORITY_FALLBACK,
    }
)

# ── Verification statuses ─────────────────────────────────────────────────────

VERIFICATION_UNVERIFIED = "unverified"
VERIFICATION_VERIFIED = "verified"
VERIFICATION_DISPUTED = "disputed"
VERIFICATION_PENDING = "pending"

VERIFICATION_STATUSES = frozenset(
    {
        VERIFICATION_UNVERIFIED,
        VERIFICATION_VERIFIED,
        VERIFICATION_DISPUTED,
        VERIFICATION_PENDING,
    }
)

# ── Review event types ────────────────────────────────────────────────────────

EVENT_APPROVED = "approved"
EVENT_REJECTED = "rejected"
EVENT_SCENE_REJECTED = "scene_rejected"

REVIEW_EVENT_TYPES = frozenset(
    {
        EVENT_APPROVED,
        EVENT_REJECTED,
        EVENT_SCENE_REJECTED,
    }
)

# ── Section-type → shot-type mapping ─────────────────────────────────────────

SECTION_SHOT_TYPE_MAP: dict[str, str] = {
    "hook": SHOT_TYPE_CLOSE_UP,
    "intro": SHOT_TYPE_MEDIUM,
    "body": SHOT_TYPE_WIDE,
    "evidence": SHOT_TYPE_CUTAWAY,
    "outro": SHOT_TYPE_MEDIUM,
    "cta": SHOT_TYPE_CLOSE_UP,
    # Phase 7B, §10 — interactive_recall_quiz: the question and its answer
    # both get the same close-up intimacy as "hook"/"cta" (they are the
    # moments the viewer must focus on); "next_challenge" is transitional,
    # like "intro".
    "prompt": SHOT_TYPE_CLOSE_UP,
    "reveal": SHOT_TYPE_CLOSE_UP,
    "next_challenge": SHOT_TYPE_MEDIUM,
    # Phase 7B, §10 — practical_micro_lesson: "problem" mirrors "hook"
    # (personally relevant), "explanation" mirrors "body", "example"
    # mirrors "evidence" (a concrete, specific instance), "takeaway"
    # mirrors "outro".
    "problem": SHOT_TYPE_CLOSE_UP,
    "explanation": SHOT_TYPE_WIDE,
    "example": SHOT_TYPE_CUTAWAY,
    "takeaway": SHOT_TYPE_MEDIUM,
}
DEFAULT_SHOT_TYPE = SHOT_TYPE_MEDIUM

# ── Section-type → camera movement mapping ────────────────────────────────────

SECTION_CAMERA_MAP: dict[str, str] = {
    "hook": CAMERA_ZOOM_IN,
    "intro": CAMERA_PAN,
    "body": CAMERA_STATIC,
    "evidence": CAMERA_STATIC,
    "outro": CAMERA_ZOOM_OUT,
    "cta": CAMERA_STATIC,
    # Phase 7B, §10 — "prompt" zooms in to build anticipation during the
    # think window (mirrors "hook"); "reveal" holds static so the answer
    # text is easy to read without competing motion; "next_challenge"
    # pans forward into the next round (mirrors "intro").
    "prompt": CAMERA_ZOOM_IN,
    "reveal": CAMERA_STATIC,
    "next_challenge": CAMERA_PAN,
    # Phase 7B, §10 — micro-lesson mirrors explainer beats: "problem"
    # zooms in like "hook", "explanation"/"example" hold static, "takeaway"
    # zooms out like "outro".
    "problem": CAMERA_ZOOM_IN,
    "explanation": CAMERA_STATIC,
    "example": CAMERA_STATIC,
    "takeaway": CAMERA_ZOOM_OUT,
}
DEFAULT_CAMERA_MOVEMENT = CAMERA_STATIC

# ── Section-type → asset category preferences ─────────────────────────────────

SECTION_ASSET_PREFERENCES: dict[str, list[str]] = {
    "hook": [ASSET_STOCK_FOOTAGE, ASSET_AI_GENERATED_BROLL],
    "intro": [ASSET_STOCK_FOOTAGE, ASSET_ILLUSTRATION],
    "body": [ASSET_STOCK_FOOTAGE, ASSET_PHOTOGRAPH, ASSET_CHART],
    "evidence": [ASSET_PHOTOGRAPH, ASSET_CHART, ASSET_DIAGRAM, ASSET_HISTORICAL_IMAGERY],
    "outro": [ASSET_STOCK_FOOTAGE, ASSET_ANIMATION],
    "cta": [ASSET_ICON, ASSET_ANIMATION],
    # Phase 7B, §10 — quiz prompt/reveal favor icon/animation overlays
    # (a question-mark / checkmark motif) over stock footage, same
    # category set as "cta" since both are graphic-forward moments.
    "prompt": [ASSET_ICON, ASSET_ANIMATION],
    "reveal": [ASSET_ICON, ASSET_ANIMATION],
    "next_challenge": [ASSET_STOCK_FOOTAGE, ASSET_ILLUSTRATION],
    # Phase 7B, §10 — micro-lesson: "explanation" favors diagrams/
    # illustration for the concept, "example" favors concrete photograph/
    # chart evidence, "problem"/"takeaway" mirror hook/outro.
    "problem": [ASSET_STOCK_FOOTAGE, ASSET_AI_GENERATED_BROLL],
    "explanation": [ASSET_ILLUSTRATION, ASSET_DIAGRAM],
    "example": [ASSET_PHOTOGRAPH, ASSET_CHART],
    "takeaway": [ASSET_STOCK_FOOTAGE, ASSET_ANIMATION],
}
DEFAULT_ASSET_PREFERENCES = [ASSET_STOCK_FOOTAGE, ASSET_AI_GENERATED_IMAGE]
