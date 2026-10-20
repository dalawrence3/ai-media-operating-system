"""Persistence for actual-content verification (Phase 3, §7).

Kept separate from `verification.py` on purpose, matching this codebase's
existing split between pure computation and persistence (`engine.py` vs.
`repository.py`; `quality.py` vs. `assessment_repository.py`): `verify_plan`
and `ContentVerifier` stay testable and DB-free, and this module owns the
one thing that genuinely needs a connection — reusing a verdict across a
restart, a rerender, or a repeated preflight rather than paying for the same
verification twice.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

from app.visuals.models import BeatResolution
from app.visuals.verification import (
    VERIFICATION_VERSION,
    BeatVerification,
    ContentVerifier,
    verification_cache_key,
    verify_plan,
)

logger = logging.getLogger(__name__)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
    )


@dataclass(frozen=True)
class _CachedVerdict:
    status: str
    reason: str | None
    confidence: float | None


def _get_cached(conn: sqlite3.Connection, cache_keys: list[str]) -> dict[str, _CachedVerdict]:
    if not cache_keys or not _table_exists(conn, "visual_verification_cache"):
        return {}
    placeholders = ",".join("?" * len(cache_keys))
    rows = conn.execute(
        f"SELECT cache_key, status, reason, confidence FROM visual_verification_cache "
        f"WHERE cache_key IN ({placeholders})",
        cache_keys,
    ).fetchall()
    return {
        row["cache_key"]: _CachedVerdict(row["status"], row["reason"], row["confidence"])
        for row in rows
    }


def _save_cache_entries(
    conn: sqlite3.Connection,
    entries: list[tuple[str, str, str, BeatVerification]],
) -> None:
    """entries: (cache_key, asset_key, model, verdict)."""
    if not entries or not _table_exists(conn, "visual_verification_cache"):
        return
    conn.executemany(
        """
        INSERT INTO visual_verification_cache (
            cache_key, asset_key, model, verifier_version, status, reason, confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            status = excluded.status,
            reason = excluded.reason,
            confidence = excluded.confidence
        """,
        [
            (
                cache_key,
                asset_key,
                model,
                VERIFICATION_VERSION,
                verdict.status,
                verdict.reason,
                verdict.confidence,
            )
            for cache_key, asset_key, model, verdict in entries
            # A verdict that never ran (STATUS_UNAVAILABLE) is never cached —
            # caching "we couldn't check" would make a transient provider
            # outage permanent for that asset/intent pair.
            if verdict.status != "unavailable"
        ],
    )
    conn.commit()


@dataclass
class VerificationCacheStats:
    """Non-secret run telemetry (§14), split by cache hit vs. a real call."""

    candidates: int = 0
    cache_hits: int = 0
    verified_via_calls: int = 0
    verifier_stats: list[dict] = field(default_factory=list)


def verify_plan_with_cache(
    conn: sqlite3.Connection,
    resolutions: list[BeatResolution],
    verifier: ContentVerifier,
    *,
    model: str,
    ffmpeg_bin: str = "ffmpeg",
) -> VerificationCacheStats:
    """Verify a plan's beats, reusing any cached verdict for an unchanged
    (asset, intent, model, verifier version) identity (§7).

    A beat whose cache key already has an entry is written directly from
    the cache — no frame extraction, no contact sheet, no verifier call.
    Everything else goes through `verify_plan` exactly as before, and any
    newly-produced verdict is persisted for next time.

    A beat that already carries a `verification_status` — Phase 4's
    `engine.VisualEngine.vision_rescue()` already verified its own
    replacement asset before this ever runs — is skipped for calling, but
    its already-real verdict is still written to the cache so a later
    rerun/reverification can reuse it too.
    """
    stats = VerificationCacheStats()
    already_verified = [
        r for r in resolutions if r.provider != "programmatic" and r.verification_status
    ]
    if already_verified:
        _save_cache_entries(
            conn,
            [
                (
                    verification_cache_key(r, model=model),
                    r.asset_key or "",
                    model,
                    BeatVerification(
                        r.beat.beat_index,
                        r.verification_status,
                        r.verification_reason,
                        r.visual_relevance_confidence,
                    ),
                )
                for r in already_verified
            ],
        )

    candidates = [
        r
        for r in resolutions
        if r.provider != "programmatic" and r.local_path and r.verification_status is None
    ]
    stats.candidates = len(candidates)
    if not candidates:
        return stats

    keyed = [(verification_cache_key(r, model=model), r) for r in candidates]
    cached = _get_cached(conn, [key for key, _ in keyed])

    to_verify: list[BeatResolution] = []
    for cache_key, resolution in keyed:
        hit = cached.get(cache_key)
        if hit is None:
            to_verify.append(resolution)
            continue
        resolution.verification_status = hit.status
        resolution.verification_reason = hit.reason
        resolution.visual_relevance_confidence = hit.confidence
        stats.cache_hits += 1

    if to_verify:
        verify_plan(to_verify, verifier, ffmpeg_bin=ffmpeg_bin)
        stats.verified_via_calls = len(to_verify)
        new_entries = [
            (
                verification_cache_key(r, model=model),
                r.asset_key or "",
                model,
                BeatVerification(
                    r.beat.beat_index,
                    r.verification_status,
                    r.verification_reason,
                    r.visual_relevance_confidence,
                ),
            )
            for r in to_verify
            if r.verification_status is not None
        ]
        _save_cache_entries(conn, new_entries)
        if hasattr(verifier, "last_run_stats"):
            stats.verifier_stats = list(verifier.last_run_stats)  # type: ignore[attr-defined]

    logger.info(
        "visual content verification: %d candidate(s), %d cache hit(s), %d verified via call(s)",
        stats.candidates,
        stats.cache_hits,
        stats.verified_via_calls,
    )
    return stats
