"""Phase 7A, §6 — rolling diversity guards.

Interpretable, not learned: plain counting over a rolling window of recent
selections. Two independent axes (domain and format) so "science explainer,
science explainer, science explainer" cannot satisfy diversity merely
because the format happens to vary, and a format monoculture cannot hide
behind varied domains either.

These guards SHAPE the portfolio allocator's candidate ranking (§4); they
never ban a domain/format outright — a guard that returns False for every
candidate would deadlock the allocator, which is exactly the trap the
no-eligible-candidate recovery path (§9) already exists to avoid elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.intelligence.exploration.cell import ExplorationCell

DEFAULT_DOMAIN_WINDOW: int = 6
DEFAULT_DOMAIN_MAX_REPEATS: int = 2
DEFAULT_DISTINCT_DOMAIN_WINDOW: int = 8
DEFAULT_DISTINCT_DOMAIN_MIN: int = 4
DEFAULT_FORMAT_WINDOW: int = 6
DEFAULT_FORMAT_MAX_REPEATS: int = 3


@dataclass(frozen=True)
class DiversityPolicy:
    """Calibrated defaults matching §6's conceptual examples — never
    hardcoded as magic numbers at the call site, always named here so a
    later calibration pass changes one place."""

    domain_window: int = DEFAULT_DOMAIN_WINDOW
    domain_max_repeats: int = DEFAULT_DOMAIN_MAX_REPEATS
    distinct_domain_window: int = DEFAULT_DISTINCT_DOMAIN_WINDOW
    distinct_domain_min: int = DEFAULT_DISTINCT_DOMAIN_MIN
    format_window: int = DEFAULT_FORMAT_WINDOW
    format_max_repeats: int = DEFAULT_FORMAT_MAX_REPEATS


@dataclass(frozen=True)
class DiversityAssessment:
    domain_repetition_ok: bool
    distinct_domain_floor_met: bool
    format_repetition_ok: bool
    domain_count_in_window: int
    distinct_domain_count: int
    format_count_in_window: int

    @property
    def ok(self) -> bool:
        return self.domain_repetition_ok and self.format_repetition_ok


def assess_candidate(
    candidate: ExplorationCell,
    recent: list[ExplorationCell],
    *,
    policy: DiversityPolicy | None = None,
) -> DiversityAssessment:
    """Would adding `candidate` next violate a rolling diversity guard?

    `recent` is ordered oldest-first, most-recent-last (matches how
    publication/selection history is naturally read from the DB). Only the
    guard's own window size of the tail is consulted per axis.
    """
    policy = policy or DiversityPolicy()
    candidate_domain = candidate.domain_key()
    candidate_format = candidate.format_key()

    domain_tail = [c.domain_key() for c in recent[-policy.domain_window :]]
    domain_count = domain_tail.count(candidate_domain)
    domain_repetition_ok = domain_count < policy.domain_max_repeats

    distinct_tail = [c.domain_key() for c in recent[-policy.distinct_domain_window :]]
    # The candidate itself would count toward distinctness once added.
    distinct_count = len(set(distinct_tail) | {candidate_domain})
    distinct_floor_met = distinct_count >= policy.distinct_domain_min

    format_tail = [c.format_key() for c in recent[-policy.format_window :]]
    format_count = format_tail.count(candidate_format)
    format_repetition_ok = format_count < policy.format_max_repeats

    return DiversityAssessment(
        domain_repetition_ok=domain_repetition_ok,
        distinct_domain_floor_met=distinct_floor_met,
        format_repetition_ok=format_repetition_ok,
        domain_count_in_window=domain_count,
        distinct_domain_count=distinct_count,
        format_count_in_window=format_count,
    )


def distinct_domain_count(recent: list[ExplorationCell], *, window: int) -> int:
    tail = recent[-window:]
    return len({c.domain_key() for c in tail})


def max_consecutive_same_domain(recent: list[ExplorationCell]) -> int:
    """The longest run of identical consecutive domains — used by the
    simulation report (§16) as a plain-language "did it get stuck" signal
    distinct from the windowed guard itself."""
    if not recent:
        return 0
    longest = current = 1
    for prev, cur in zip(recent, recent[1:], strict=False):
        if prev.domain_key() == cur.domain_key():
            current += 1
            longest = max(longest, current)
        else:
            current = 1
    return longest
