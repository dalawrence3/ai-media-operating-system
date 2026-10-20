"""Phase 7A, §4/§5/§12 — the exploration portfolio allocator.

An interpretable policy, not a bandit: rank candidate cells by (a) whether
they pass the rolling diversity guard, (b) an information-value bonus for
under-tested cells, and (c) a configurable exploration/exploitation mix —
then pick the top-ranked survivor. No learned weights, no ML model; every
score component is a named, inspectable number so a human can read why a
choice was made from `AllocationDecision.rationale` alone.

This module is a pure function over its inputs (recent selections + trial
counts + candidate pool) — it never touches the database itself. Callers
assemble those inputs from `exploration_cell_selections` (and, later,
`topic_attribute_performance_observations` once enough evidence exists) and
pass them in, keeping this logic trivially unit-testable and reusable by
both the live decision path and the simulation harness (§16).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.intelligence.exploration.cell import (
    DESIGNATION_EXPLOITATION,
    DESIGNATION_EXPLORATION,
    ExplorationCell,
)
from app.intelligence.exploration.diversity import DiversityPolicy, assess_candidate
from app.intelligence.exploration.format_suitability import (
    format_suitability as _format_suitability_lookup,
)

# §5: "conceptually favor something around 70-80% exploration / 20-30%
# exploitation" for an early-stage channel — configurable, not hardcoded
# as the only legal value; a channel with a richer evidence base can be
# configured toward more exploitation later without touching this module.
DEFAULT_EXPLORATION_RATIO: float = 0.75

# Below this many prior trials, a cell counts as "under-tested" for the
# information-value bonus (§12). Small and named, not a magic number inline.
LOW_EVIDENCE_TRIAL_FLOOR: int = 2

# Phase 7B, §2 — cold-start/evidence-maturity rule. Exploitation is only
# EVER active once at least this many distinct cells carry real recorded
# evidence. Below this, "exploitation" would just mean "confidently revisit
# whatever cell happens to sit at evidence_score=0.0 first" — a fabricated
# confidence signal, not a real one. An interpretable count threshold, not a
# statistical-significance test: simple enough to reason about, strict
# enough that a handful of stray zeros can't fake readiness.
MIN_CELLS_WITH_EVIDENCE_FOR_EXPLOITATION: int = 3

# Phase 7C.1, Part B, §6 — weight for format_suitability inside
# CandidateScore.total_score (the single-number diagnostic summary used in
# rationale/reporting, e.g. simulation.py). The actual exploration RANKING
# in choose_next_cell does not use this weight at all: it places
# format_suitability as its own tuple position strictly AFTER
# information_value, so novelty always decides first and suitability only
# breaks ties among equally-novel candidates — no weight can make
# suitability outrank novelty there. This constant only tunes how visible
# suitability is in the diagnostic score, never the actual selection.
FORMAT_SUITABILITY_WEIGHT: float = 0.5


@dataclass(frozen=True)
class AllocationPolicy:
    exploration_ratio: float = DEFAULT_EXPLORATION_RATIO
    diversity: DiversityPolicy = field(default_factory=DiversityPolicy)
    low_evidence_trial_floor: int = LOW_EVIDENCE_TRIAL_FLOOR
    min_cells_with_evidence_for_exploitation: int = MIN_CELLS_WITH_EVIDENCE_FOR_EXPLOITATION
    format_suitability_weight: float = FORMAT_SUITABILITY_WEIGHT

    def __post_init__(self) -> None:
        if not 0.0 <= self.exploration_ratio <= 1.0:
            raise ValueError(f"exploration_ratio must be in [0, 1], got {self.exploration_ratio}")


def evidence_is_mature(
    evidence_scores: dict[tuple[str, str], float] | None, *, policy: AllocationPolicy | None = None
) -> bool:
    """Is there enough REAL recorded evidence to justify exploitation at
    all (Phase 7B, §2)? Deliberately simple: count distinct (domain,
    format) cells carrying a recorded evidence score, regardless of the
    score's own value — a 0.0 that was actually MEASURED still counts as
    evidence (a format that genuinely underperformed is real information);
    an absent entry does not. `evidence_scores=None`/`{}` (the shape every
    caller passes before any real publication evidence exists) always
    reads as immature.
    """
    policy = policy or AllocationPolicy()
    if not evidence_scores:
        return False
    return len(evidence_scores) >= policy.min_cells_with_evidence_for_exploitation


@dataclass(frozen=True)
class CandidateScore:
    candidate: ExplorationCell
    diversity_ok: bool
    trial_count: int
    domain_trial_total: int
    format_trial_total: int
    information_value: float
    evidence_score: float
    format_suitability: float
    total_score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class AllocationDecision:
    cell: ExplorationCell
    designation: str
    rationale: str
    scored_candidates: tuple[CandidateScore, ...]


def _trial_count(candidate: ExplorationCell, trial_counts: dict[tuple[str, str], int]) -> int:
    return trial_counts.get((candidate.domain_key(), candidate.format_key()), 0)


def _domain_trial_total(
    candidate: ExplorationCell, trial_counts: dict[tuple[str, str], int]
) -> int:
    """Total trials across every FORMAT within this candidate's domain —
    the fairness signal that keeps a many-format domain from monopolizing
    "freshness" by cycling through its own untried formats while a
    single-format-tested-out domain (or one with fewer format variants)
    never gets its turn. See choose_next_cell's exploration ranking."""
    domain_key = candidate.domain_key()
    return sum(count for (domain, _fmt), count in trial_counts.items() if domain == domain_key)


def _format_trial_total(
    candidate: ExplorationCell, trial_counts: dict[tuple[str, str], int]
) -> int:
    """Total trials across every DOMAIN for this candidate's format — the
    mirror-image fairness signal (Phase 7B, §1). Without this, a format
    with many domain seeds (e.g. interactive_recall_quiz, tried against 12
    different domains) can look "fresh" every time a new domain is reached,
    consuming a disproportionate share of the exploration horizon even
    though THAT format specifically has already been exercised many times
    overall. Ranked alongside domain-level fairness so neither axis can be
    starved by the other — see choose_next_cell's exploration ranking."""
    format_key = candidate.format_key()
    return sum(count for (_domain, fmt), count in trial_counts.items() if fmt == format_key)


def _information_value(trials: int, *, floor: int) -> float:
    """0..1, higher for less-tried cells. A cell with zero trials gets the
    maximum bonus; the bonus decays to 0 once a cell has cleared `floor`
    trials — deliberately simple (no smoothing/decay curve) per the "no
    unnecessary ML" instruction."""
    if trials >= floor:
        return 0.0
    return 1.0 - (trials / floor if floor else 1.0)


def score_candidates(
    candidates: list[ExplorationCell],
    *,
    recent: list[ExplorationCell],
    trial_counts: dict[tuple[str, str], int] | None = None,
    evidence_scores: dict[tuple[str, str], float] | None = None,
    policy: AllocationPolicy | None = None,
) -> list[CandidateScore]:
    """Score every candidate cell; does not choose one. Exposed separately
    from `choose_next_cell` so the simulation report (§16) and tests can
    inspect the full ranking, not just the winner."""
    policy = policy or AllocationPolicy()
    trial_counts = trial_counts or {}
    evidence_scores = evidence_scores or {}

    scored: list[CandidateScore] = []
    for candidate in candidates:
        diversity = assess_candidate(candidate, recent, policy=policy.diversity)
        trials = _trial_count(candidate, trial_counts)
        domain_total = _domain_trial_total(candidate, trial_counts)
        format_total = _format_trial_total(candidate, trial_counts)
        info_value = _information_value(trials, floor=policy.low_evidence_trial_floor)
        evidence = evidence_scores.get((candidate.domain_key(), candidate.format_key()), 0.0)
        suitability = _format_suitability_lookup(candidate.format_key(), candidate.audience_value)

        reasons: list[str] = []
        if not diversity.domain_repetition_ok:
            reasons.append(
                f"domain '{candidate.domain}' repeated "
                f"{diversity.domain_count_in_window}x in the diversity window"
            )
        if not diversity.format_repetition_ok:
            reasons.append(
                f"format '{candidate.format}' repeated "
                f"{diversity.format_count_in_window}x in the diversity window"
            )
        if trials == 0:
            reasons.append("never tried before — maximum information value")
        elif trials < policy.low_evidence_trial_floor:
            reasons.append(f"under-tested ({trials} prior trial(s))")
        if suitability >= 0.7:
            reasons.append(
                f"format well-suited to '{candidate.audience_value}' "
                f"(suitability={suitability:.2f})"
            )

        total = info_value + evidence + suitability * policy.format_suitability_weight
        scored.append(
            CandidateScore(
                candidate=candidate,
                diversity_ok=diversity.ok,
                trial_count=trials,
                domain_trial_total=domain_total,
                format_trial_total=format_total,
                information_value=info_value,
                evidence_score=evidence,
                format_suitability=suitability,
                total_score=total,
                reasons=tuple(reasons),
            )
        )
    return scored


def choose_next_cell(
    candidates: list[ExplorationCell],
    *,
    recent: list[ExplorationCell],
    trial_counts: dict[tuple[str, str], int] | None = None,
    evidence_scores: dict[tuple[str, str], float] | None = None,
    policy: AllocationPolicy | None = None,
    exploit_ready: bool = False,
) -> AllocationDecision:
    """Pick the next exploration cell to pursue.

    `exploit_ready` is the caller's own judgement of whether this tick
    should lean exploitation (e.g. a deterministic counter/RNG gated by
    `policy.exploration_ratio`) — this function does not roll dice itself,
    it only changes HOW it ranks candidates once told which regime applies,
    keeping the allocator itself deterministic and easy to test.

    That signal is still only a SCHEDULE, not a guarantee: Phase 7B, §2's
    cold-start/evidence-maturity rule is enforced here as a second, always-
    on gate — `exploit_ready=True` is downgraded back to exploration
    whenever `evidence_scores` is not yet mature (see `evidence_is_mature`),
    so an early-stage channel with no real performance evidence stays
    exploration-dominant (up to 100% exploration) regardless of what ratio
    a caller configured. As real evidence accumulates past the maturity
    floor, the configured ratio activates on its own — no code change, no
    separate "graduation" step.

    Diversity-guard failures are a strong penalty, not an outright ban: a
    candidate that fails the guard can still be chosen if every other
    candidate ALSO fails it (never deadlock a channel with zero legal
    choices), but is always ranked below any candidate that passes.
    """
    if not candidates:
        raise ValueError("choose_next_cell requires at least one candidate")

    policy = policy or AllocationPolicy()
    scored = score_candidates(
        candidates,
        recent=recent,
        trial_counts=trial_counts,
        evidence_scores=evidence_scores,
        policy=policy,
    )

    effective_exploit = exploit_ready and evidence_is_mature(evidence_scores, policy=policy)
    designation = DESIGNATION_EXPLOITATION if effective_exploit else DESIGNATION_EXPLORATION

    if designation == DESIGNATION_EXPLOITATION:
        # Exploitation ranks by evidence first (revisit what has worked),
        # information value only as a tiebreaker.
        ranked = sorted(
            scored,
            key=lambda s: (s.diversity_ok, s.evidence_score, s.information_value),
            reverse=True,
        )
    else:
        # Exploration ranks by DOMAIN-level fairness AND format-level
        # fairness before per-cell novelty (Phase 7B, §1 adds the format
        # axis — Phase 7A only had the domain one). Combining both as a
        # summed rank penalty (rather than strictly domain-then-format)
        # means a candidate whose format is very over-tried can still lose
        # to one whose domain is more over-tried, and vice versa — neither
        # axis categorically dominates the other. See _domain_trial_total /
        # _format_trial_total's docstrings for why each alone is necessary.
        #
        # Phase 7C.1, Part B, §6: format_suitability sits AFTER
        # information_value in this tuple, never before it — Python's
        # lexicographic tuple comparison means novelty always decides
        # first, and suitability only breaks ties among candidates that
        # are equally novel (the common case at cold start, where many
        # cells sit at 0 trials together). A more-novel-but-worse-fitting
        # candidate can never lose to a more-suitable-but-already-tried
        # one — suitability nudges, it never overrides exploration.
        ranked = sorted(
            scored,
            key=lambda s: (
                s.diversity_ok,
                -(s.domain_trial_total + s.format_trial_total),
                s.information_value,
                s.format_suitability,
                s.evidence_score,
            ),
            reverse=True,
        )

    winner = ranked[0]
    cell = ExplorationCell(
        domain=winner.candidate.domain,
        format=winner.candidate.format,
        audience_value=winner.candidate.audience_value,
        designation=designation,
        rationale="; ".join(winner.reasons) or "baseline candidate, no distinguishing signal",
    )
    downgrade_note = (
        " (exploit_ready requested but evidence is not yet mature — downgraded to exploration)"
        if exploit_ready and not effective_exploit
        else ""
    )
    rationale = (
        f"[{designation}] chose {cell.label()} "
        f"(diversity_ok={winner.diversity_ok}, trials={winner.trial_count}, "
        f"information_value={winner.information_value:.2f}, "
        f"evidence_score={winner.evidence_score:.2f}) — {cell.rationale}{downgrade_note}"
    )
    return AllocationDecision(
        cell=cell,
        designation=designation,
        rationale=rationale,
        scored_candidates=tuple(scored),
    )


def exploit_ready_by_ratio(tick: int, *, policy: AllocationPolicy | None = None) -> bool:
    """Deterministic exploration/exploitation scheduler (§5).

    A simple, inspectable counter-based split rather than randomness, so a
    simulation run is reproducible: across every `1 / (1 - exploration_ratio)`
    ticks, exactly one is exploitation. exploration_ratio=0.75 -> exploit on
    every 4th tick (index 3, 7, 11, ...).
    """
    policy = policy or AllocationPolicy()
    if policy.exploration_ratio >= 1.0:
        return False
    exploit_every = round(1.0 / (1.0 - policy.exploration_ratio))
    if exploit_every <= 0:
        return False
    return (tick + 1) % exploit_every == 0
