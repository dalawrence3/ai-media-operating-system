"""Candidate-supply diagnostics (Phase 5, §13).

Answers "did a recent render's weakness originate in query generation (too
few/wrong candidates ever retrieved) or in scoring (candidates retrieved but
rejected)?" — a read-only aggregation over data that is ALREADY persisted:
`visual_candidate_evidence` (Phase 18G) already records every candidate a
beat considered, three-way classified, with the verbatim rejection reason.
No new table, no new migration — this module only ever reads.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

# Mirrors scoring.REASON_NEEDS_VERIFICATION's literal value — duplicated
# rather than imported, matching the existing pattern in composition.py's
# RELEVANCE_REJECTION_REASONS and models.py's ScoredCandidate.verify_eligible
# (scoring.py is not a dependency of this module; both already document why).
_NEEDS_VERIFICATION_REASON = "needs_verification"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
    )


@dataclass
class CandidateSupplyReport:
    """One render's candidate-supply picture, beat-by-beat evidence rolled up."""

    total_beats: int = 0
    beats_with_any_candidate: int = 0
    candidates_retrieved: int = 0
    candidates_accept: int = 0
    candidates_verify: int = 0
    candidates_reject: int = 0
    # rejection_reason (scoring.REASON_* string, "needs_verification" split
    # out separately above since it is not a hard reject) -> count.
    rejection_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def candidates_retrieved_per_beat(self) -> float:
        return self.candidates_retrieved / self.total_beats if self.total_beats else 0.0

    @property
    def candidates_accept_per_beat(self) -> float:
        return self.candidates_accept / self.total_beats if self.total_beats else 0.0

    @property
    def candidates_verify_per_beat(self) -> float:
        return self.candidates_verify / self.total_beats if self.total_beats else 0.0

    @property
    def candidates_reject_per_beat(self) -> float:
        return self.candidates_reject / self.total_beats if self.total_beats else 0.0

    @property
    def beats_with_zero_candidates(self) -> int:
        return self.total_beats - self.beats_with_any_candidate

    def as_dict(self) -> dict[str, object]:
        return {
            "total_beats": self.total_beats,
            "beats_with_any_candidate": self.beats_with_any_candidate,
            "beats_with_zero_candidates": self.beats_with_zero_candidates,
            "candidates_retrieved": self.candidates_retrieved,
            "candidates_retrieved_per_beat": round(self.candidates_retrieved_per_beat, 2),
            "candidates_accept": self.candidates_accept,
            "candidates_accept_per_beat": round(self.candidates_accept_per_beat, 2),
            "candidates_verify": self.candidates_verify,
            "candidates_verify_per_beat": round(self.candidates_verify_per_beat, 2),
            "candidates_reject": self.candidates_reject,
            "candidates_reject_per_beat": round(self.candidates_reject_per_beat, 2),
            "rejection_reasons": dict(self.rejection_reasons),
        }


def candidate_supply_report(
    conn: sqlite3.Connection, scene_manifest_id: int
) -> CandidateSupplyReport:
    """Aggregate `visual_candidate_evidence` for one render's beats.

    "Retrieved" is a lower bound, not a true provider-call count: evidence
    persistence is bounded to `MAX_REJECTED_CANDIDATES_PERSISTED` non-selected
    entries per beat (repository.py), so a beat that genuinely saw many more
    candidates than that only ever contributes the bounded, persisted subset
    here — enough to see WHICH failure mode dominates, not an exact provider
    quota count.
    """
    report = CandidateSupplyReport()
    beats = conn.execute(
        "SELECT id FROM visual_beats WHERE scene_manifest_id = ?", (scene_manifest_id,)
    ).fetchall()
    report.total_beats = len(beats)
    if not beats or not _table_exists(conn, "visual_candidate_evidence"):
        return report

    beat_ids = [row["id"] for row in beats]
    placeholders = ",".join("?" * len(beat_ids))
    rows = conn.execute(
        f"SELECT visual_beat_id, decision, rejection_reason FROM visual_candidate_evidence "
        f"WHERE visual_beat_id IN ({placeholders})",
        beat_ids,
    ).fetchall()

    beats_with_candidates: set[int] = set()
    for row in rows:
        beats_with_candidates.add(row["visual_beat_id"])
        report.candidates_retrieved += 1
        decision = row["decision"]
        reason = row["rejection_reason"]
        if decision != "rejected":
            report.candidates_accept += 1
        elif reason == _NEEDS_VERIFICATION_REASON:
            report.candidates_verify += 1
        else:
            report.candidates_reject += 1
            key = reason or "unknown"
            report.rejection_reasons[key] = report.rejection_reasons.get(key, 0) + 1

    report.beats_with_any_candidate = len(beats_with_candidates)
    return report
