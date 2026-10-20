"""Phase 7A, §16 — exploratory-horizon simulation.

Drives the portfolio allocator forward N ticks entirely in memory (no DB,
no LLM calls) and reports the resulting distribution — this is the "would
this policy actually produce a diverse experimental channel?" check the
phase spec calls one of the most important validation outputs. Pure
function of its inputs, deterministic given a seed history and policy, so
a report is exactly reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.intelligence.exploration.cell import ExplorationCell
from app.intelligence.exploration.diversity import (
    distinct_domain_count,
    max_consecutive_same_domain,
)
from app.intelligence.exploration.portfolio import (
    AllocationPolicy,
    choose_next_cell,
    evidence_is_mature,
    exploit_ready_by_ratio,
)
from app.intelligence.exploration.taxonomy import AUDIENCE_VALUE_SEEDS, DOMAIN_SEEDS, FORMAT_SEEDS


def default_candidate_pool() -> list[ExplorationCell]:
    """Domain x format, one audience_value per cell rotated across the
    seed list so the simulated candidate pool exercises all three
    dimensions rather than pinning audience_value to a single constant."""
    pool: list[ExplorationCell] = []
    for i, domain in enumerate(DOMAIN_SEEDS):
        for j, fmt in enumerate(FORMAT_SEEDS):
            value = AUDIENCE_VALUE_SEEDS[(i + j) % len(AUDIENCE_VALUE_SEEDS)]
            pool.append(ExplorationCell(domain=domain, format=fmt, audience_value=value))
    return pool


@dataclass
class SimulationReport:
    selections: list[ExplorationCell] = field(default_factory=list)
    domain_distribution: dict[str, int] = field(default_factory=dict)
    format_distribution: dict[str, int] = field(default_factory=dict)
    exploration_count: int = 0
    exploitation_count: int = 0
    max_consecutive_same_domain: int = 0
    distinct_domain_counts_by_window: dict[int, int] = field(default_factory=dict)
    evidence_mature: bool = False
    max_domain_concentration: int = 0
    max_format_concentration: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "selections": [c.label() for c in self.selections],
            "domain_distribution": dict(self.domain_distribution),
            "format_distribution": dict(self.format_distribution),
            "exploration_count": self.exploration_count,
            "exploitation_count": self.exploitation_count,
            "max_consecutive_same_domain": self.max_consecutive_same_domain,
            "distinct_domain_counts_by_window": dict(self.distinct_domain_counts_by_window),
            "evidence_mature": self.evidence_mature,
            "max_domain_concentration": self.max_domain_concentration,
            "max_format_concentration": self.max_format_concentration,
        }


def simulate_next_n_cells(
    *,
    n: int = 20,
    seed_history: list[ExplorationCell] | None = None,
    candidates: list[ExplorationCell] | None = None,
    policy: AllocationPolicy | None = None,
    evidence_scores: dict[tuple[str, str], float] | None = None,
    start_tick: int = 0,
) -> SimulationReport:
    """Simulate the next `n` allocator picks.

    `seed_history` lets a caller start from real recent publication history
    (e.g. today's actual science-heavy run) to check whether the policy
    would correct course, rather than always starting from an empty,
    best-case channel.
    """
    policy = policy or AllocationPolicy()
    candidates = candidates if candidates is not None else default_candidate_pool()
    history: list[ExplorationCell] = list(seed_history or [])
    trial_counts: dict[tuple[str, str], int] = {}
    for cell in history:
        key = (cell.domain_key(), cell.format_key())
        trial_counts[key] = trial_counts.get(key, 0) + 1

    report = SimulationReport()
    for i in range(n):
        tick = start_tick + i
        exploit = exploit_ready_by_ratio(tick, policy=policy)
        decision = choose_next_cell(
            candidates,
            recent=history,
            trial_counts=trial_counts,
            evidence_scores=evidence_scores,
            policy=policy,
            exploit_ready=exploit,
        )
        chosen = decision.cell
        history.append(chosen)
        key = (chosen.domain_key(), chosen.format_key())
        trial_counts[key] = trial_counts.get(key, 0) + 1

        report.selections.append(chosen)
        report.domain_distribution[chosen.domain] = (
            report.domain_distribution.get(chosen.domain, 0) + 1
        )
        report.format_distribution[chosen.format] = (
            report.format_distribution.get(chosen.format, 0) + 1
        )
        if decision.designation == "exploitation":
            report.exploitation_count += 1
        else:
            report.exploration_count += 1

    report.max_consecutive_same_domain = max_consecutive_same_domain(report.selections)
    for window in (6, 8, 10):
        report.distinct_domain_counts_by_window[window] = distinct_domain_count(
            report.selections, window=window
        )
    report.evidence_mature = evidence_is_mature(evidence_scores, policy=policy)
    report.max_domain_concentration = max(report.domain_distribution.values(), default=0)
    report.max_format_concentration = max(report.format_distribution.values(), default=0)
    return report
