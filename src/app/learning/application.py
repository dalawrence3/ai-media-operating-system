"""Phase 12A — Recommendation Application Framework.

Closes the learning loop by translating accepted recommendations into
concrete parameter adjustments.  No recommendation is applied automatically;
all applications are operator-initiated via explicit direction and magnitude.

Lifecycle:
    proposed → applied → (superseded | reverted)
              ↘ failed

Design invariants:
  - Only 'accepted' + 'actionable' recommendations may have applications.
  - 'exploratory' recommendations must NOT mutate parameters.
  - Intent is structured (direction + magnitude dataclass) — never inferred
    from string content.  The operator supplies direction from their reading
    of the recommendation's observational evidence.
  - Each (recommendation_id, topic_id, parameter_name) has at most one active
    application (UNIQUE constraint).  Idempotent: a duplicate raises
    DuplicateApplicationError rather than inserting a second row.
  - All parameter adjustments are bounded: min, max, and max_delta are checked
    at proposal time and recorded for auditability.
  - Scope is anchored to topic_id and never leaks across topics.
  - Full provenance: recommendation → learning_run → publication → snapshots.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel

from app.learning.constants import (
    APPLICATION_DIRECTIONS,
    APPLICATION_ENGINE_VERSION,
    APPLICATION_SCHEMA_VERSION,
    APPLICATION_STATUS_APPLIED,
    APPLICATION_STATUS_PROPOSED,
    APPLICATION_STATUS_REVERTED,
    DIRECTION_DECREASE,
    DIRECTION_INCREASE,
    NARRATION_PACE_MAX_DELTA,
    NARRATION_PACE_PARAMETER,
    NARRATION_PACE_SPEAKING_RATE_MAX,
    NARRATION_PACE_SPEAKING_RATE_MIN,
    STATUS_ACCEPTED,
    STRENGTH_ACTIONABLE,
)
from app.learning.errors import (
    ApplicationNotFoundError,
    ApplicationParameterError,
    DuplicateApplicationError,
    InvalidApplicationTransitionError,
    RecommendationNotApplicableError,
)
from app.learning.repository import get_recommendation


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


# ── Domain models ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ApplicationIntent:
    """Structured parameter adjustment intent.  Never parsed from strings.

    All fields are supplied explicitly by the operator.  The framework validates
    bounds and records the intent as-is for full provenance.
    """

    domain: str  # e.g. 'narration'
    subsystem: str  # e.g. 'narration_pace'
    parameter_name: str  # e.g. 'speaking_rate'
    direction: str  # 'increase' | 'decrease' | 'maintain'
    magnitude: float  # absolute delta (e.g. 0.1 means ±0.1 to current value)
    value_before: float  # current parameter value at proposal time
    target_value: float  # computed: value_before ± magnitude
    safety_min: float  # lower bound enforced at proposal
    safety_max: float  # upper bound enforced at proposal
    safety_max_delta: float  # max |target - before| enforced at proposal


@dataclass
class RecommendationApplicationDraft:
    """Mutable draft for insertion into recommendation_applications."""

    recommendation_id: int
    learning_run_id: int
    domain: str
    subsystem: str
    parameter_name: str
    intent_direction: str
    intent_magnitude: float
    intent_target_value: float
    topic_id: int
    channel_id: int | None
    workspace_id: str | None
    publication_id: int | None
    source_snapshot_ids_json: str
    value_before: float
    safety_min: float
    safety_max: float
    safety_max_delta: float
    input_hash: str


class RecommendationApplication(BaseModel):
    model_config = {"frozen": True}

    id: int
    recommendation_id: int
    learning_run_id: int
    domain: str
    subsystem: str
    parameter_name: str
    intent_direction: str
    intent_magnitude: float
    intent_target_value: float
    topic_id: int
    channel_id: int | None
    workspace_id: str | None
    publication_id: int | None
    source_snapshot_ids_json: str
    value_before: float | None
    value_applied: float | None
    narration_run_id: int | None
    status: str
    safety_min: float
    safety_max: float
    safety_max_delta: float
    input_hash: str
    error_message: str | None
    proposed_at: str
    applied_at: str | None
    reverted_at: str | None
    superseded_at: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> RecommendationApplication:
        return cls(**dict(row))


# ── Hashing ───────────────────────────────────────────────────────────────────


def _compute_application_hash(
    *,
    recommendation_id: int,
    topic_id: int,
    parameter_name: str,
    direction: str,
    magnitude: float,
    value_before: float,
) -> str:
    """Deterministic SHA-256 hash identifying a unique application attempt."""
    payload = json.dumps(
        {
            "recommendation_id": recommendation_id,
            "topic_id": topic_id,
            "parameter_name": parameter_name,
            "direction": direction,
            "magnitude": magnitude,
            "value_before": value_before,
            "engine_version": APPLICATION_ENGINE_VERSION,
            "schema_version": APPLICATION_SCHEMA_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# ── Narration pace adapter ────────────────────────────────────────────────────


def build_narration_pace_intent(
    *,
    direction: str,
    magnitude: float,
    current_speaking_rate: float,
) -> ApplicationIntent:
    """Compute a speaking_rate ApplicationIntent from operator-supplied parameters.

    The operator supplies direction and magnitude based on their reading of the
    recommendation's observational evidence.  This function does NOT infer
    direction from analytics data — it only validates bounds and computes the
    target value.

    Args:
        direction: 'increase', 'decrease', or 'maintain' — operator-provided
        magnitude: absolute delta (e.g. 0.1 → adjust current rate by ±0.1)
        current_speaking_rate: voice_profile.speaking_rate at proposal time
    """
    if direction not in APPLICATION_DIRECTIONS:
        raise ApplicationParameterError(
            f"Invalid direction {direction!r}; must be one of {sorted(APPLICATION_DIRECTIONS)}"
        )
    if magnitude < 0.0:
        raise ApplicationParameterError(f"Magnitude must be >= 0, got {magnitude}")
    if magnitude > NARRATION_PACE_MAX_DELTA:
        raise ApplicationParameterError(
            f"Magnitude {magnitude} exceeds safety max_delta "
            f"{NARRATION_PACE_MAX_DELTA}. Reduce magnitude or split across "
            "multiple applications with re-evaluation between steps."
        )

    if direction == DIRECTION_INCREASE:
        target = current_speaking_rate + magnitude
    elif direction == DIRECTION_DECREASE:
        target = current_speaking_rate - magnitude
    else:  # maintain
        target = current_speaking_rate

    target = round(target, 6)

    if target < NARRATION_PACE_SPEAKING_RATE_MIN:
        raise ApplicationParameterError(
            f"Target speaking_rate {target:.4f} is below safety minimum "
            f"{NARRATION_PACE_SPEAKING_RATE_MIN}. Reduce magnitude."
        )
    if target > NARRATION_PACE_SPEAKING_RATE_MAX:
        raise ApplicationParameterError(
            f"Target speaking_rate {target:.4f} exceeds safety maximum "
            f"{NARRATION_PACE_SPEAKING_RATE_MAX}. Reduce magnitude."
        )

    return ApplicationIntent(
        domain="narration",
        subsystem="narration_pace",
        parameter_name=NARRATION_PACE_PARAMETER,
        direction=direction,
        magnitude=magnitude,
        value_before=current_speaking_rate,
        target_value=target,
        safety_min=NARRATION_PACE_SPEAKING_RATE_MIN,
        safety_max=NARRATION_PACE_SPEAKING_RATE_MAX,
        safety_max_delta=NARRATION_PACE_MAX_DELTA,
    )


# ── DB operations ─────────────────────────────────────────────────────────────


def propose_application(
    conn: sqlite3.Connection,
    *,
    recommendation_id: int,
    intent: ApplicationIntent,
) -> int:
    """Persist a proposed application for an accepted + actionable recommendation.

    Validates recommendation state before writing.  Raises
    RecommendationNotApplicableError if the recommendation is not accepted+actionable.
    Raises DuplicateApplicationError if an active application for
    (recommendation_id, topic_id, parameter_name) already exists.

    Returns the new application ID.
    """
    rec = get_recommendation(conn, recommendation_id)

    # Derive authoritative channel_id from recommendation → publication → bridge.
    # The bridge maps cp_channels.id (UUID) to channels.id (INTEGER).
    # If any link in the chain is absent (legacy rows, no bridge yet), channel_id stays NULL.
    _channel_id: int | None = None
    if rec.publication_id is not None:
        _pub_row = conn.execute(
            "SELECT channel_id FROM publications WHERE id = ?",
            (rec.publication_id,),
        ).fetchone()
        if _pub_row and _pub_row["channel_id"]:
            _bridge_row = conn.execute(
                "SELECT id FROM channels WHERE cp_channel_id = ?",
                (_pub_row["channel_id"],),
            ).fetchone()
            if _bridge_row:
                _channel_id = int(_bridge_row["id"])

    if rec.status != STATUS_ACCEPTED:
        raise RecommendationNotApplicableError(
            f"Recommendation {recommendation_id} has status={rec.status!r}; "
            "only 'accepted' recommendations may be applied."
        )
    if rec.recommendation_strength != STRENGTH_ACTIONABLE:
        raise RecommendationNotApplicableError(
            f"Recommendation {recommendation_id} has "
            f"strength={rec.recommendation_strength!r}; only 'actionable' "
            "recommendations may be applied. 'exploratory' recommendations "
            "must not automatically mutate parameters."
        )

    input_hash = _compute_application_hash(
        recommendation_id=recommendation_id,
        topic_id=rec.topic_id,
        parameter_name=intent.parameter_name,
        direction=intent.direction,
        magnitude=intent.magnitude,
        value_before=intent.value_before,
    )

    now = _now()
    try:
        cursor = conn.execute(
            """
            INSERT INTO recommendation_applications (
                recommendation_id, learning_run_id,
                domain, subsystem, parameter_name,
                intent_direction, intent_magnitude, intent_target_value,
                topic_id, channel_id, workspace_id,
                publication_id, source_snapshot_ids_json,
                value_before, value_applied, narration_run_id,
                status, safety_min, safety_max, safety_max_delta,
                input_hash, error_message,
                proposed_at, applied_at, reverted_at, superseded_at,
                created_at, updated_at
            ) VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,?,?,?,?,?,NULL,?,NULL,NULL,NULL,?,?
            )
            """,
            (
                recommendation_id,
                rec.learning_run_id,
                intent.domain,
                intent.subsystem,
                intent.parameter_name,
                intent.direction,
                intent.magnitude,
                intent.target_value,
                rec.topic_id,
                _channel_id,
                None,  # workspace_id
                rec.publication_id,
                "[]",  # source_snapshot_ids_json
                intent.value_before,
                APPLICATION_STATUS_PROPOSED,
                intent.safety_min,
                intent.safety_max,
                intent.safety_max_delta,
                input_hash,
                now,  # proposed_at
                now,  # created_at
                now,  # updated_at
            ),
        )
        conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" in str(exc).upper():
            raise DuplicateApplicationError(
                f"An application for recommendation={recommendation_id}, "
                f"topic_id={rec.topic_id}, parameter={intent.parameter_name!r} "
                "already exists. Only one active application per "
                "(recommendation, topic, parameter) is allowed."
            ) from exc
        raise


def apply_application(
    conn: sqlite3.Connection,
    application_id: int,
    *,
    value_applied: float,
    narration_run_id: int | None = None,
) -> None:
    """Transition a proposed application to applied.

    Records value_applied (the exact value used) and optionally the
    narration_run_id that consumed this application.
    """
    row = conn.execute(
        "SELECT status FROM recommendation_applications WHERE id = ?",
        (application_id,),
    ).fetchone()
    if row is None:
        raise ApplicationNotFoundError(f"No application with id={application_id}")
    if row["status"] != APPLICATION_STATUS_PROPOSED:
        raise InvalidApplicationTransitionError(
            f"Cannot apply application {application_id}: "
            f"status is {row['status']!r}, expected 'proposed'."
        )

    now = _now()
    conn.execute(
        """
        UPDATE recommendation_applications
           SET status           = ?,
               value_applied    = ?,
               narration_run_id = ?,
               applied_at       = ?,
               updated_at       = ?
         WHERE id = ?
        """,
        (APPLICATION_STATUS_APPLIED, value_applied, narration_run_id, now, now, application_id),
    )
    conn.commit()


def revert_application(
    conn: sqlite3.Connection,
    application_id: int,
) -> None:
    """Transition an applied application to reverted."""
    row = conn.execute(
        "SELECT status FROM recommendation_applications WHERE id = ?",
        (application_id,),
    ).fetchone()
    if row is None:
        raise ApplicationNotFoundError(f"No application with id={application_id}")
    if row["status"] != APPLICATION_STATUS_APPLIED:
        raise InvalidApplicationTransitionError(
            f"Cannot revert application {application_id}: "
            f"status is {row['status']!r}, expected 'applied'."
        )

    now = _now()
    conn.execute(
        """
        UPDATE recommendation_applications
           SET status      = ?,
               reverted_at = ?,
               updated_at  = ?
         WHERE id = ?
        """,
        (APPLICATION_STATUS_REVERTED, now, now, application_id),
    )
    conn.commit()


def get_application(
    conn: sqlite3.Connection,
    application_id: int,
) -> RecommendationApplication:
    """Fetch a recommendation application by ID."""
    row = conn.execute(
        "SELECT * FROM recommendation_applications WHERE id = ?",
        (application_id,),
    ).fetchone()
    if row is None:
        raise ApplicationNotFoundError(f"No application with id={application_id}")
    return RecommendationApplication.from_row(row)


def get_active_application_for_parameter(
    conn: sqlite3.Connection,
    *,
    topic_id: int,
    parameter_name: str,
    channel_id: int | None = None,
) -> RecommendationApplication | None:
    """Return the most recent proposed or applied application for topic + parameter.

    Fail-safe channel semantics:
      - channel_id supplied → filter AND channel_id = ? (excludes NULL/legacy rows;
        Channel B cannot consume Channel A's application)
      - channel_id=None → filter AND channel_id IS NULL (legacy single-channel path;
        never crosses into channel-scoped rows)

    This ensures no cross-channel contamination in either direction.
    """
    if channel_id is not None:
        row = conn.execute(
            """
            SELECT * FROM recommendation_applications
             WHERE topic_id       = ?
               AND parameter_name = ?
               AND channel_id     = ?
               AND status         IN ('proposed', 'applied')
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (topic_id, parameter_name, channel_id),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT * FROM recommendation_applications
             WHERE topic_id       = ?
               AND parameter_name = ?
               AND channel_id     IS NULL
               AND status         IN ('proposed', 'applied')
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (topic_id, parameter_name),
        ).fetchone()
    if row is None:
        return None
    return RecommendationApplication.from_row(row)


# ── Production integration helpers ────────────────────────────────────────────


def resolve_speaking_rate_override(
    conn: sqlite3.Connection,
    *,
    topic_id: int,
    channel_id: int | None = None,
) -> tuple[RecommendationApplication | None, float | None]:
    """Look up any active narration pace application and return the effective rate.

    Returns (application, override_value):
      - (None, None)   — no active application; narrate_plan uses voice profile default.
      - (app, float)   — app.status='proposed': use app.intent_target_value.
      - (app, float)   — app.status='applied':  use app.value_applied (persistent, no compounding).

    channel_id: when provided, only channel-scoped applications for that channel are
    returned; when None, only legacy NULL-channel applications are returned.

    Call this before narrate_plan().  After successful synthesis, call
    consume_proposed_application() to transition a proposed application to applied.
    """
    app = get_active_application_for_parameter(
        conn,
        topic_id=topic_id,
        parameter_name=NARRATION_PACE_PARAMETER,
        channel_id=channel_id,
    )
    if app is None:
        return None, None
    if app.status == APPLICATION_STATUS_PROPOSED:
        return app, app.intent_target_value
    # status == 'applied' — reuse the stored value; do NOT recompute the delta
    return app, app.value_applied


def consume_proposed_application(
    conn: sqlite3.Connection,
    application: RecommendationApplication,
    *,
    narration_run_id: int,
    value_applied: float,
) -> None:
    """Transition a proposed application to applied after successful narration.

    No-ops silently when the application is already applied, reverted, or failed.
    This makes callers safe under idempotent narration re-runs where the application
    was already consumed in a prior run.

    Call this ONLY after narrate_plan() returns successfully.  If narration fails
    with an exception, do not call this — the application must remain proposed so
    the next narration attempt still picks up the override.
    """
    if application.status == APPLICATION_STATUS_PROPOSED:
        apply_application(
            conn,
            application.id,
            value_applied=value_applied,
            narration_run_id=narration_run_id,
        )
