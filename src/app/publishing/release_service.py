"""Phase 18C — reusable public-release logic.

Before this module the entire release sequence (gates, scope check,
read-before-write, ground-truth reconcile, videos.update, local persistence,
audit) lived inside the `POST /publications/{id}/release-public` route
handler, expressed in HTTPExceptions. That made it unreachable from a
background worker, which is exactly what autonomous scheduled publishing
needs.

The logic is extracted here unchanged in substance and re-expressed as a
typed result rather than HTTP semantics. The API route keeps its behaviour by
delegating here and mapping the outcome back to status codes; the autonomous
publishing cycle calls the same function. One implementation, two callers —
so the safety properties cannot drift apart.

Preserved exactly from the original:
  - read-before-write via videos.list (never blind-update)
  - if YouTube already reports public, reconcile locally and do NOT re-update
  - local DB is written only AFTER YouTube confirms success
  - a failed local write after a successful YouTube update is CRITICAL-logged
    and surfaced, never silently swallowed
  - read-only status fields are stripped before videos.update
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

# Read-only fields returned by videos.list that must not be echoed back in a
# videos.update call — YouTube rejects the request with 400 if they are.
STATUS_READ_ONLY_FIELDS = frozenset(
    {"uploadStatus", "failureReason", "rejectionReason", "madeForKids"}
)


class ReleaseOutcome(StrEnum):
    released = "released"  # we made it public this call
    already_public_reconciled = "already_public_reconciled"  # YouTube was already public
    provider_read_failed = "provider_read_failed"
    provider_update_failed = "provider_update_failed"
    local_persist_failed = "local_persist_failed"  # video IS public, DB write failed
    video_not_found = "video_not_found"


@dataclass
class ReleaseResult:
    outcome: ReleaseOutcome
    publication_id: int
    provider_video_id: str
    detail: str = ""
    reconciled: bool = False

    @property
    def is_public(self) -> bool:
        """True when the video is public on YouTube, regardless of local state.

        `local_persist_failed` counts as public: the external side effect
        succeeded and callers must not retry the update.
        """
        return self.outcome in {
            ReleaseOutcome.released,
            ReleaseOutcome.already_public_reconciled,
            ReleaseOutcome.local_persist_failed,
        }

    @property
    def ok(self) -> bool:
        """True when the video is public AND local state agrees."""
        return self.outcome in {
            ReleaseOutcome.released,
            ReleaseOutcome.already_public_reconciled,
        }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _emit(
    conn: Any,
    *,
    event_type: str,
    workspace_id: str,
    actor: str,
    platform_account_id: str | None,
    publication_id: int,
    payload: dict[str, Any],
) -> None:
    """Best-effort cp_events audit write."""
    from app.control_plane.models import ControlEventDraft
    from app.control_plane.repository import create_event

    try:
        create_event(
            conn,
            ControlEventDraft(
                id=str(uuid.uuid4()),
                event_type=event_type,
                workspace_id=workspace_id,
                actor=actor,
                platform_account_id=platform_account_id,
                source_entity_id=str(publication_id),
                payload=payload,
            ),
        )
        conn.commit()
    except Exception as exc:  # pragma: no cover - audit is best-effort
        logger.warning("release audit event write failed (non-fatal): %s", exc)


def release_publication_to_public(
    conn: Any,
    *,
    publication_id: int,
    provider_video_id: str,
    workspace_id: str,
    platform_account_id: str | None,
    actor: str,
    yt_client: Any,
) -> ReleaseResult:
    """Make an uploaded-private YouTube video public, idempotently.

    Callers are responsible for having already checked authorization (both
    global env gates and channel authorization) and OAuth release scope. This
    function owns the provider interaction and local state transition only.

    Idempotent by construction: it reads YouTube's current privacyStatus
    first and treats an already-public video as success without issuing a
    second update.
    """
    # ── Read-before-write: YouTube is the ground truth ────────────────────
    try:
        list_resp = yt_client.get_video(provider_video_id, ["status"])
    except Exception as exc:
        return ReleaseResult(
            outcome=ReleaseOutcome.provider_read_failed,
            publication_id=publication_id,
            provider_video_id=provider_video_id,
            detail=f"Failed to read current video status from YouTube: {exc}",
        )

    items = list_resp.get("items", [])
    if not items:
        return ReleaseResult(
            outcome=ReleaseOutcome.video_not_found,
            publication_id=publication_id,
            provider_video_id=provider_video_id,
            detail=f"Video {provider_video_id!r} not found on YouTube.",
        )
    current_status: dict = items[0].get("status", {})

    # ── Already public: reconcile local state, issue no provider write ────
    if current_status.get("privacyStatus") == "public":
        row = conn.execute(
            "SELECT visibility FROM publications WHERE id = ?", (publication_id,)
        ).fetchone()
        previous = row["visibility"] if row else None
        if previous != "public":
            conn.execute(
                "UPDATE publications SET visibility='public', updated_at=? WHERE id=?",
                (_now_iso(), publication_id),
            )
            conn.commit()
            logger.info(
                "release: reconciled local visibility for publication %d "
                "(YouTube ground truth was already public)",
                publication_id,
            )
            _emit(
                conn,
                event_type="publication.visibility_reconciled_public",
                workspace_id=workspace_id,
                actor=actor,
                platform_account_id=platform_account_id,
                publication_id=publication_id,
                payload={
                    "publication_id": publication_id,
                    "provider_video_id": provider_video_id,
                    "previous_local_visibility": previous,
                    "observed_provider_visibility": "public",
                },
            )
        return ReleaseResult(
            outcome=ReleaseOutcome.already_public_reconciled,
            publication_id=publication_id,
            provider_video_id=provider_video_id,
            detail="YouTube already reported this video as public; local state reconciled.",
            reconciled=True,
        )

    # ── Build the update body, stripping read-only fields ─────────────────
    update_status = {k: v for k, v in current_status.items() if k not in STATUS_READ_ONLY_FIELDS}
    update_status["privacyStatus"] = "public"
    update_status.pop("publishAt", None)  # clear any scheduled release

    # ── The external side effect. Local DB untouched until this succeeds ──
    try:
        yt_client.update_video(provider_video_id, snippet={}, status=update_status)
    except Exception as exc:
        return ReleaseResult(
            outcome=ReleaseOutcome.provider_update_failed,
            publication_id=publication_id,
            provider_video_id=provider_video_id,
            detail=f"YouTube videos.update failed: {exc}",
        )

    # ── Persist locally. The video is ALREADY public from here on. ────────
    try:
        conn.execute(
            "UPDATE publications SET visibility='public', updated_at=? WHERE id=?",
            (_now_iso(), publication_id),
        )
        conn.commit()
    except Exception as exc:
        logger.critical(
            "CRITICAL: YouTube reports publication %d (%s) is now public but the local DB "
            "update failed: %s. Manual reconciliation required.",
            publication_id,
            provider_video_id,
            exc,
        )
        return ReleaseResult(
            outcome=ReleaseOutcome.local_persist_failed,
            publication_id=publication_id,
            provider_video_id=provider_video_id,
            detail=(
                "YouTube update succeeded but the local DB update failed. "
                "The video IS public — do not retry the update; reconcile local state."
            ),
        )

    _emit(
        conn,
        event_type="publication.released_public",
        workspace_id=workspace_id,
        actor=actor,
        platform_account_id=platform_account_id,
        publication_id=publication_id,
        # Payload shape preserved exactly from the pre-extraction route so
        # existing consumers and audit queries keep working.
        payload={
            "publication_id": publication_id,
            "provider_video_id": provider_video_id,
            "visibility_before": "private",
            "visibility_after": "public",
        },
    )
    return ReleaseResult(
        outcome=ReleaseOutcome.released,
        publication_id=publication_id,
        provider_video_id=provider_video_id,
        detail="Video released publicly.",
    )
