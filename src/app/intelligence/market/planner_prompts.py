"""Phase 13D-B — Prompt names, versions, and cold-start priority policy constants.

All prompt text lives in the TOML registry under:
    src/app/ai/prompts/<name>/v<version>.toml

Do not embed system/user prompt strings here.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Prompt identity
# ---------------------------------------------------------------------------

COLD_START_PROMPT_NAME: str = "market-exploration-cold-start"
COLD_START_PROMPT_VERSION: str = "1"

# ---------------------------------------------------------------------------
# LLM expansion bounds
# ---------------------------------------------------------------------------

# Maximum candidates the LLM may propose in one call.
# The planner passes this into the prompt template.
COLD_START_MAX_LLM_CANDIDATES: int = 10

# Maximum candidates we will accept from the LLM even if the schema allows more.
COLD_START_LLM_CANDIDATE_HARD_CAP: int = 12

# ---------------------------------------------------------------------------
# V1 cold-start priority weights
# (must match PriorityComponents field names; weights sum to 1.0)
# ---------------------------------------------------------------------------

COLD_START_PRIORITY_WEIGHTS: dict[str, float] = {
    "niche_fit": 0.40,
    "novelty": 0.35,
    "evidence_strength": 0.00,  # no external evidence at cold start
    "velocity_trigger": 0.00,  # no velocity at cold start
    "corroboration": 0.00,  # no corroboration at cold start
    "depth_factor": 0.25,  # always 1.0 at depth 0 → full weight
}

# ---------------------------------------------------------------------------
# V1 cold-start eligibility thresholds
# ---------------------------------------------------------------------------

# Jaccard similarity above this → near-duplicate, deferred.
# Callers may override with the channel profile's duplicate_similarity_threshold.
DEFAULT_JACCARD_DEDUP_THRESHOLD: float = 0.70

# Jaccard similarity against excluded topics above this → rejected.
EXCLUDED_TOPIC_JACCARD_THRESHOLD: float = 0.60

# ---------------------------------------------------------------------------
# V1 cold-start portfolio allocation
# ---------------------------------------------------------------------------

# Maximum CHANNEL_BOOTSTRAP probes selected per run regardless of how many
# secondary niches the channel profile contains.  Secondary niches beyond this
# cap are persisted as DEFERRED (portfolio_allocation) so semantic MARKET_REGION
# probes retain meaningful capacity even with large profiles.
#
# Allocation by max_probes:
#   max_probes=1 → 1 anchor (primary niche only), 0 semantic
#   max_probes=2 → 1 anchor, 1 semantic
#   max_probes=3 → 2 anchors, 1 semantic
#   max_probes=N → 2 anchors, N-2 semantic  (N ≥ 4)
COLD_START_V1_ANCHOR_CAP: int = 2

# ---------------------------------------------------------------------------
# Phase 13D-C — Adjacent concept expansion
# ---------------------------------------------------------------------------

ADJACENT_PROMPT_NAME: str = "market-exploration-adjacent"
ADJACENT_PROMPT_VERSION: str = "1"

ADJACENT_MAX_LLM_CANDIDATES: int = 8
ADJACENT_LLM_HARD_CAP: int = 10

ADJACENT_PRIORITY_WEIGHTS: dict[str, float] = {
    "niche_fit": 0.30,
    "novelty": 0.25,
    "evidence_strength": 0.25,
    "velocity_trigger": 0.15,
    "corroboration": 0.05,
    "depth_factor": 0.00,
}

# Minimum distinct videos observed before a probe is eligible for adjacent expansion.
ADJACENT_MIN_VIDEOS_FOR_EXPANSION: int = 3

# Minimum distinct video titles (VIDEO_TITLE observations) required to perform
# semantically-grounded adjacent expansion.  Probes with enough videos but no
# semantic evidence are classified SEMANTICALLY_UNGROUNDED and expansion is aborted
# rather than allowing the LLM to invent adjacent concepts from pretrained knowledge.
ADJACENT_MIN_SEMANTIC_EVIDENCE: int = 1

# Maximum video evidence items passed to the adjacent LLM prompt.
ADJACENT_MAX_VIDEO_EVIDENCE: int = 10

# ---------------------------------------------------------------------------
# V1 cold-start predicate: evidence-sparse definition
# ---------------------------------------------------------------------------

# A channel is evidence-sparse if it has fewer than this many dispatched probes
# across all prior exploration runs.
COLD_START_DISPATCHED_PROBE_THRESHOLD: int = 3

# ---------------------------------------------------------------------------
# Phase 13D-D — Selector / Semantic Niche Guard
# ---------------------------------------------------------------------------

SELECTOR_PROMPT_NAME: str = "market-exploration-niche-guard"
SELECTOR_PROMPT_VERSION: str = "1"

# Versioned policy IDs — used in diagnostics and policy snapshots.
SELECTOR_POLICY_VERSION: str = "v1"
SELECTOR_DIVERSITY_POLICY_VERSION: str = "v1"
SELECTOR_VELOCITY_NORM_VERSION: str = "v1"

# Hard cap on candidate probes per semantic evaluation batch (one LLM call).
SELECTOR_MAX_BATCH_SIZE: int = 20

# Diversity guard: max probes selected from one market-region Jaccard cluster.
SELECTOR_MAX_REGION_PER_CLUSTER: int = 2

# Jaccard threshold for clustering candidates into the same market region.
SELECTOR_REGION_CLUSTER_JACCARD: float = 0.40

# Fraction of selection slots reserved for exploration probes
# (CHANNEL_BOOTSTRAP + MARKET_REGION) vs evidence-following probes
# (ADJACENT_TOPIC + VELOCITY_FOLLOWUP).
SELECTOR_EXPLORATION_SLOT_RATIO: float = 0.50

# V1 base priority weights. Sum to 1.0.
# Weights are used with applicable-component normalization: components
# that are None for a given probe class are excluded from the weighted
# average and their weight is redistributed to applicable components.
SELECTOR_PRIORITY_WEIGHTS: dict[str, float] = {
    "niche_fit": 0.30,
    "novelty": 0.25,
    "evidence_strength": 0.20,
    "velocity_trigger": 0.10,
    "corroboration": 0.10,
    "depth_factor": 0.05,
}

# Velocity log-normalization reference band (V1).
# peak_velocity_per_day = SELECTOR_VELOCITY_REF_VIEWS_PER_DAY → velocity_trigger = 1.0
SELECTOR_VELOCITY_REF_VIEWS_PER_DAY: float = 50_000.0

# Evidence strength normalization reference for video count (V1).
SELECTOR_EVIDENCE_VIDEO_REF: int = 20
