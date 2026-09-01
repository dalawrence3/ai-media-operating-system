"""Phase 18D — deterministic market-theme fidelity evaluation.

For a MARKET_EXPLORATION experiment the treatment variable is the topic
cluster itself: the hypothesis is "publishing about this cluster produces
measurable signal". Phase 14F.2 therefore evaluates such an experiment's
execution fidelity by asking whether the video that actually got produced is
about the cluster it was supposed to be about — via an injectable
`market_theme_evaluator`, so the fidelity engine stays free of LLM and
network calls.

Nothing ever injected one. `compare_intended_vs_actual` with no evaluator
correctly refuses to guess (absence must not default to VALID), so every
autonomously produced market-exploration experiment classified as UNRESOLVED,
which made its outcome INVALID_EXECUTION, which meant it could never mature,
never be analyzed, and never contribute to learning. The closed loop was
broken at its last link.

This module supplies the missing evaluator, deterministically:

  - No LLM. No network. Same inputs always give the same answer, so a
    fidelity verdict can be re-derived and audited later.
  - Lexical overlap between the cluster's canonical label and the produced
    script, using the same normalisation the opportunity deduplicator uses,
    so "market theme" means the same thing here as everywhere else.
  - Honest abstention. A missing cluster is `unresolved` and a script that
    does not exist yet is `not_yet_available` — neither is reported as a
    match. UNRESOLVED remains the right answer when we genuinely cannot
    tell; what changes is that we can now usually tell.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from app.core.logging import get_logger
from app.intelligence.dedup import normalize_topic

logger = get_logger(__name__)

# Fraction of the cluster's distinctive terms that must appear in the script
# for the produced video to count as on-theme.
#
# 0.5 rather than something stricter: a cluster label like "science technology
# explained" is a theme, not a required phrase, and a good script about it
# will naturally use some of those words and not others. Requiring every term
# would fail correct videos; requiring one term would pass off-topic ones that
# happen to say "science".
THEME_MATCH_MIN_COVERAGE = 0.5

# Result vocabulary expected by compare_intended_vs_actual.
MATCHED = "matched"
DEVIATED = "deviated"
NOT_YET_AVAILABLE = "not_yet_available"
UNRESOLVED = "unresolved"


def _cluster_terms(conn: sqlite3.Connection, cluster_id: int) -> set[str] | None:
    """The cluster's distinctive terms, or None when the cluster is unknown."""
    row = conn.execute(
        "SELECT canonical_label, normalized_label FROM market_canonical_clusters WHERE id = ?",
        (cluster_id,),
    ).fetchone()
    if row is None:
        return None
    label = row["normalized_label"] or row["canonical_label"] or ""
    terms = set(normalize_topic(label).split())
    return terms or None


def evaluate_market_theme(
    conn: sqlite3.Connection, cluster_id: int, script_body: str | None
) -> str:
    """Decide whether the produced script is on-theme for the cluster.

    Returns one of the four fidelity verdicts. Never raises: the caller
    treats an exception as `unresolved` anyway, and a fidelity assessment
    must not be able to fail a publication that already happened.
    """
    try:
        terms = _cluster_terms(conn, cluster_id)
        if terms is None:
            return UNRESOLVED

        if script_body is None:
            # Production has not reached a script yet — this is a "come back
            # later", not a deviation.
            return NOT_YET_AVAILABLE
        if not script_body.strip():
            return UNRESOLVED

        script_terms = set(normalize_topic(script_body).split())
        if not script_terms:
            return UNRESOLVED

        coverage = len(terms & script_terms) / len(terms)
        return MATCHED if coverage >= THEME_MATCH_MIN_COVERAGE else DEVIATED
    except Exception as exc:  # noqa: BLE001 — abstain rather than fail
        logger.warning(
            "market theme fidelity: could not evaluate cluster %s (treating as unresolved): %s",
            cluster_id,
            exc,
        )
        return UNRESOLVED


def build_market_theme_evaluator(
    conn: sqlite3.Connection,
) -> Callable[[int, str | None], str]:
    """Bind a connection into the evaluator signature the fidelity engine wants."""

    def _evaluator(cluster_id: int, script_body: str | None) -> str:
        return evaluate_market_theme(conn, cluster_id, script_body)

    return _evaluator


__all__ = [
    "THEME_MATCH_MIN_COVERAGE",
    "build_market_theme_evaluator",
    "evaluate_market_theme",
]
