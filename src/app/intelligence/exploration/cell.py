"""Phase 7A, §3 — the exploration cell: domain x format x audience_value.

A cell is a HYPOTHESIS the portfolio allocator chooses to pursue, before any
concrete topic exists. "language_learning x interactive_recall_quiz x
skill_building" is a cell; "English -> Spanish: how do you say 'thank you'?"
is a topic that could later be discovered inside that cell. Level B market
intelligence (§7) discovers topics inside an already-chosen cell — it never
invents the cell itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.intelligence.exploration.taxonomy import (
    is_known_audience_value,
    is_seed_domain,
    is_seed_format,
    normalize_domain,
    normalize_format,
)

DESIGNATION_EXPLORATION = "exploration"
DESIGNATION_EXPLOITATION = "exploitation"


@dataclass(frozen=True)
class ExplorationCell:
    """One (domain, format, audience_value) exploration hypothesis.

    Immutable and hashable so it can be used as a dict/set key for rolling
    diversity and evidence bookkeeping. Values are stored exactly as given
    (original casing/text preserved for display and for the persisted
    ledger row); `key()` is the normalized form used for comparison.
    """

    domain: str
    format: str
    audience_value: str
    designation: str = DESIGNATION_EXPLORATION
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.domain.strip():
            raise ValueError("ExplorationCell.domain must not be empty")
        if not self.format.strip():
            raise ValueError("ExplorationCell.format must not be empty")
        if not self.audience_value.strip():
            raise ValueError("ExplorationCell.audience_value must not be empty")
        if self.designation not in (DESIGNATION_EXPLORATION, DESIGNATION_EXPLOITATION):
            raise ValueError(
                f"designation must be {DESIGNATION_EXPLORATION!r} or "
                f"{DESIGNATION_EXPLOITATION!r}, got {self.designation!r}"
            )

    def key(self) -> tuple[str, str, str]:
        """Normalized identity for dedup/comparison — never for display."""
        return (
            normalize_domain(self.domain),
            normalize_format(self.format),
            self.audience_value.strip().casefold(),
        )

    def domain_key(self) -> str:
        return normalize_domain(self.domain)

    def format_key(self) -> str:
        return normalize_format(self.format)

    @property
    def is_seed_domain(self) -> bool:
        return is_seed_domain(self.domain)

    @property
    def is_seed_format(self) -> bool:
        return is_seed_format(self.format)

    @property
    def is_known_audience_value(self) -> bool:
        return is_known_audience_value(self.audience_value)

    def label(self) -> str:
        return f"{self.domain} × {self.format} × {self.audience_value}"
