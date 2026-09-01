"""Repository layer for Phase 14A production experiment ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from app.intelligence.experiments.models import (
    _TRANSITION_TIMESTAMP,
    ALLOWED_TRANSITIONS,
    Experiment,
    ExperimentFactor,
    ExperimentMetricTarget,
    ExperimentStateEvent,
    ExperimentStatus,
    ExperimentType,
    FactorRole,
    MaturityPolicy,
    MetricDirection,
)


def _row_to_experiment(row: sqlite3.Row) -> Experiment:
    return Experiment(
        id=row["id"],
        channel_id=row["channel_id"],
        opportunity_id=row["opportunity_id"],
        experiment_type=ExperimentType(row["experiment_type"]),
        status=ExperimentStatus(row["status"]),
        hypothesis=row["hypothesis"],
        hypothesis_null=row["hypothesis_null"],
        hypothesis_metric=row["hypothesis_metric"],
        input_hash=row["input_hash"],
        maturity_policy_json=row["maturity_policy_json"],
        policy_snapshot_json=row["policy_snapshot_json"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        planned_at=row["planned_at"],
        in_production_at=row["in_production_at"],
        published_at=row["published_at"],
        observing_at=row["observing_at"],
        matured_at=row["matured_at"],
        analyzed_at=row["analyzed_at"],
        completed_at=row["completed_at"],
        cancelled_at=row["cancelled_at"],
        cancelled_reason=row["cancelled_reason"],
        publication_id=row["publication_id"],
    )


def _row_to_metric_target(row: sqlite3.Row) -> ExperimentMetricTarget:
    return ExperimentMetricTarget(
        id=row["id"],
        experiment_id=row["experiment_id"],
        metric_name=row["metric_name"],
        direction=MetricDirection(row["direction"]),
        target_value=row["target_value"],
        target_min=row["target_min"],
        target_max=row["target_max"],
        is_primary=bool(row["is_primary"]),
    )


def _row_to_factor(row: sqlite3.Row) -> ExperimentFactor:
    return ExperimentFactor(
        id=row["id"],
        experiment_id=row["experiment_id"],
        factor_name=row["factor_name"],
        factor_role=FactorRole(row["factor_role"]),
        intended_value=row["intended_value"],
        actual_value=row["actual_value"],
        value_type=row["value_type"],
    )


def _row_to_state_event(row: sqlite3.Row) -> ExperimentStateEvent:
    return ExperimentStateEvent(
        id=row["id"],
        experiment_id=row["experiment_id"],
        from_state=row["from_state"],
        to_state=row["to_state"],
        actor=row["actor"],
        reason=row["reason"],
        created_at=row["created_at"],
    )


def _compute_input_hash(
    channel_id: int,
    experiment_type: ExperimentType,
    hypothesis: str,
    opportunity_id: int | None,
) -> str:
    payload = json.dumps(
        {
            "channel_id": channel_id,
            "experiment_type": experiment_type.value,
            "hypothesis": hypothesis,
            "opportunity_id": opportunity_id,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def create_experiment(
    conn: sqlite3.Connection,
    *,
    experiment_id: str,
    channel_id: int,
    experiment_type: ExperimentType,
    hypothesis: str,
    maturity_policy: MaturityPolicy | None = None,
    opportunity_id: int | None = None,
    hypothesis_null: str = "",
    hypothesis_metric: str | None = None,
    policy_snapshot: dict | None = None,
    actor: str = "system",
) -> Experiment:
    """Create a new experiment in draft status. Idempotent via input_hash."""
    policy = maturity_policy or MaturityPolicy.default()
    input_hash = _compute_input_hash(channel_id, experiment_type, hypothesis, opportunity_id)

    existing = conn.execute(
        "SELECT id FROM experiments WHERE input_hash = ?", (input_hash,)
    ).fetchone()
    if existing:
        return get_experiment(conn, existing["id"])

    policy_snapshot_json = json.dumps(policy_snapshot or {})

    conn.execute(
        """
        INSERT INTO experiments (
            id, channel_id, opportunity_id, experiment_type, status,
            hypothesis, hypothesis_null, hypothesis_metric,
            input_hash, maturity_policy_json, policy_snapshot_json
        ) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?)
        """,
        (
            experiment_id,
            channel_id,
            opportunity_id,
            experiment_type.value,
            hypothesis,
            hypothesis_null,
            hypothesis_metric,
            input_hash,
            policy.to_json(),
            policy_snapshot_json,
        ),
    )
    conn.execute(
        """
        INSERT INTO experiment_state_events
            (experiment_id, from_state, to_state, actor, reason)
        VALUES (?, NULL, 'draft', ?, 'created')
        """,
        (experiment_id, actor),
    )
    return get_experiment(conn, experiment_id)


def get_experiment(
    conn: sqlite3.Connection,
    experiment_id: str,
    *,
    include_targets: bool = True,
    include_factors: bool = True,
) -> Experiment:
    row = conn.execute("SELECT * FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
    if row is None:
        raise KeyError(f"Experiment {experiment_id!r} not found")
    exp = _row_to_experiment(row)
    if include_targets:
        exp.metric_targets = [
            _row_to_metric_target(r)
            for r in conn.execute(
                "SELECT * FROM experiment_metric_targets WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchall()
        ]
    if include_factors:
        exp.factors = [
            _row_to_factor(r)
            for r in conn.execute(
                "SELECT * FROM experiment_factors WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchall()
        ]
    return exp


def list_experiments(
    conn: sqlite3.Connection,
    *,
    channel_id: int | None = None,
    status: ExperimentStatus | None = None,
    experiment_type: ExperimentType | None = None,
    limit: int = 50,
) -> list[Experiment]:
    clauses = []
    params: list = []
    if channel_id is not None:
        clauses.append("channel_id = ?")
        params.append(channel_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status.value)
    if experiment_type is not None:
        clauses.append("experiment_type = ?")
        params.append(experiment_type.value)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM experiments {where} ORDER BY created_at DESC LIMIT ?",
        params,
    ).fetchall()
    return [_row_to_experiment(r) for r in rows]


def transition_experiment_state(
    conn: sqlite3.Connection,
    experiment_id: str,
    to_state: ExperimentStatus,
    *,
    actor: str = "system",
    reason: str = "",
    cancelled_reason: str | None = None,
) -> Experiment:
    """Advance the experiment lifecycle. Raises ValueError for invalid transitions."""
    row = conn.execute("SELECT status FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
    if row is None:
        raise KeyError(f"Experiment {experiment_id!r} not found")
    from_status = ExperimentStatus(row["status"])
    if to_state not in ALLOWED_TRANSITIONS.get(from_status, set()):
        raise ValueError(
            f"Invalid transition {from_status.value!r} → {to_state.value!r} "
            f"for experiment {experiment_id!r}"
        )

    ts_col = _TRANSITION_TIMESTAMP.get(to_state)
    extra_set = f", {ts_col} = strftime('%Y-%m-%dT%H:%M:%S', 'now')" if ts_col else ""
    cancelled_set = ""
    cancelled_params: list = []
    if to_state == ExperimentStatus.cancelled and cancelled_reason:
        cancelled_set = ", cancelled_reason = ?"
        cancelled_params.append(cancelled_reason)

    conn.execute(
        f"UPDATE experiments SET status = ?, "
        f"updated_at = strftime('%Y-%m-%dT%H:%M:%S', 'now'){extra_set}{cancelled_set} "
        f"WHERE id = ?",
        [to_state.value] + cancelled_params + [experiment_id],
    )
    conn.execute(
        """
        INSERT INTO experiment_state_events
            (experiment_id, from_state, to_state, actor, reason)
        VALUES (?, ?, ?, ?, ?)
        """,
        (experiment_id, from_status.value, to_state.value, actor, reason),
    )
    return get_experiment(conn, experiment_id)


def add_metric_target(
    conn: sqlite3.Connection,
    experiment_id: str,
    *,
    metric_name: str,
    direction: MetricDirection,
    is_primary: bool = False,
    target_value: float | None = None,
    target_min: float | None = None,
    target_max: float | None = None,
) -> ExperimentMetricTarget:
    conn.execute(
        """
        INSERT INTO experiment_metric_targets
            (experiment_id, metric_name, direction, target_value, target_min,
             target_max, is_primary)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(experiment_id, metric_name) DO UPDATE SET
            direction = excluded.direction,
            target_value = excluded.target_value,
            target_min = excluded.target_min,
            target_max = excluded.target_max,
            is_primary = excluded.is_primary
        """,
        (
            experiment_id,
            metric_name,
            direction.value,
            target_value,
            target_min,
            target_max,
            int(is_primary),
        ),
    )
    row = conn.execute(
        "SELECT * FROM experiment_metric_targets WHERE experiment_id = ? AND metric_name = ?",
        (experiment_id, metric_name),
    ).fetchone()
    return _row_to_metric_target(row)


def add_factor(
    conn: sqlite3.Connection,
    experiment_id: str,
    *,
    factor_name: str,
    factor_role: FactorRole,
    intended_value: str | None = None,
    value_type: str = "string",
) -> ExperimentFactor:
    conn.execute(
        """
        INSERT INTO experiment_factors
            (experiment_id, factor_name, factor_role, intended_value, value_type)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(experiment_id, factor_name) DO UPDATE SET
            factor_role = excluded.factor_role,
            intended_value = excluded.intended_value,
            value_type = excluded.value_type
        """,
        (experiment_id, factor_name, factor_role.value, intended_value, value_type),
    )
    row = conn.execute(
        "SELECT * FROM experiment_factors WHERE experiment_id = ? AND factor_name = ?",
        (experiment_id, factor_name),
    ).fetchone()
    return _row_to_factor(row)


def set_factor_actual(
    conn: sqlite3.Connection,
    experiment_id: str,
    factor_name: str,
    actual_value: str,
) -> ExperimentFactor:
    result = conn.execute(
        "UPDATE experiment_factors SET actual_value = ? "
        "WHERE experiment_id = ? AND factor_name = ?",
        (actual_value, experiment_id, factor_name),
    )
    if result.rowcount == 0:
        raise KeyError(f"Factor {factor_name!r} not found on experiment {experiment_id!r}")
    row = conn.execute(
        "SELECT * FROM experiment_factors WHERE experiment_id = ? AND factor_name = ?",
        (experiment_id, factor_name),
    ).fetchone()
    return _row_to_factor(row)


def bind_experiment_to_production_plan(
    conn: sqlite3.Connection,
    experiment_id: str,
    production_plan_id: int,
    *,
    actor: str = "system",
) -> None:
    """Bind an experiment to a production plan — the authoritative attachment point.

    Validates experiment lifecycle, plan existence, idempotency, conflict detection,
    and opportunity/topic lineage compatibility when available.
    """
    exp_row = conn.execute(
        "SELECT id, channel_id, opportunity_id, status FROM experiments WHERE id = ?",
        (experiment_id,),
    ).fetchone()
    if exp_row is None:
        raise KeyError(f"Experiment {experiment_id!r} not found")

    from_status = ExperimentStatus(exp_row["status"])
    _non_bindable = {ExperimentStatus.cancelled, ExperimentStatus.completed}
    if from_status in _non_bindable:
        raise ValueError(
            f"Experiment {experiment_id!r} has status {from_status.value!r} "
            "and cannot be bound to a production plan"
        )

    plan_row = conn.execute(
        "SELECT id, experiment_id, topic_id FROM production_plans WHERE id = ?",
        (production_plan_id,),
    ).fetchone()
    if plan_row is None:
        raise KeyError(f"Production plan {production_plan_id} not found")

    if plan_row["experiment_id"] == experiment_id:
        return  # idempotent: same binding already in place

    if plan_row["experiment_id"] is not None:
        raise ValueError(
            f"Production plan {production_plan_id} is already bound to experiment "
            f"{plan_row['experiment_id']!r}; cannot rebind to {experiment_id!r}"
        )

    opportunity_id = exp_row["opportunity_id"]
    if opportunity_id is not None and plan_row["topic_id"] is not None:
        topic_row = conn.execute(
            "SELECT promoted_opportunity_id FROM topics WHERE id = ?",
            (plan_row["topic_id"],),
        ).fetchone()
        if (
            topic_row is not None
            and topic_row["promoted_opportunity_id"] is not None
            and topic_row["promoted_opportunity_id"] != opportunity_id
        ):
            raise ValueError(
                f"Opportunity lineage mismatch: experiment {experiment_id!r} has "
                f"opportunity_id={opportunity_id} but plan {production_plan_id} has "
                f"topic {plan_row['topic_id']} with promoted_opportunity_id="
                f"{topic_row['promoted_opportunity_id']}"
            )

        opp_row = conn.execute(
            "SELECT channel_id FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()
        if opp_row is not None and opp_row["channel_id"] != exp_row["channel_id"]:
            raise ValueError(
                f"Channel mismatch: experiment {experiment_id!r} has channel_id="
                f"{exp_row['channel_id']} but opportunity {opportunity_id} belongs to "
                f"channel_id={opp_row['channel_id']}"
            )

    conn.execute(
        "UPDATE production_plans SET experiment_id = ? WHERE id = ?",
        (experiment_id, production_plan_id),
    )
    conn.execute(
        """
        INSERT INTO experiment_state_events
            (experiment_id, from_state, to_state, actor, reason)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            experiment_id,
            from_status.value,
            from_status.value,
            actor,
            f"bound to production_plan {production_plan_id}",
        ),
    )


def derive_experiment_id_from_publication(
    conn: sqlite3.Connection,
    publication_id: int,
) -> str | None:
    """Return the experiment_id linked to this publication via publishing_plan."""
    row = conn.execute(
        """
        SELECT pp.experiment_id
        FROM publications pub
        JOIN publishing_plans pp ON pp.id = pub.publishing_plan_id
        WHERE pub.id = ?
        """,
        (publication_id,),
    ).fetchone()
    return row["experiment_id"] if row else None


def derive_topic_id_from_publication(
    conn: sqlite3.Connection,
    publication_id: int,
) -> int | None:
    """Return the topic_id linked to this publication via publishing_plan."""
    row = conn.execute(
        """
        SELECT pp.topic_id
        FROM publications pub
        JOIN publishing_plans pp ON pp.id = pub.publishing_plan_id
        WHERE pub.id = ?
        """,
        (publication_id,),
    ).fetchone()
    return row["topic_id"] if row else None


def attach_publication(
    conn: sqlite3.Connection,
    experiment_id: str,
    publication_id: int,
) -> None:
    """Record which publication realised this experiment.

    Phase 14B hardening: validates experiment and publication existence, enforces
    idempotency, and rejects conflicting attachments.
    """
    exp_row = conn.execute(
        "SELECT id, status, publication_id FROM experiments WHERE id = ?",
        (experiment_id,),
    ).fetchone()
    if exp_row is None:
        raise KeyError(f"Experiment {experiment_id!r} not found")

    from_status = ExperimentStatus(exp_row["status"])
    if from_status in {ExperimentStatus.cancelled, ExperimentStatus.completed}:
        raise ValueError(
            f"Experiment {experiment_id!r} has status {from_status.value!r} "
            "and cannot have a publication attached"
        )

    pub_row = conn.execute("SELECT id FROM publications WHERE id = ?", (publication_id,)).fetchone()
    if pub_row is None:
        raise KeyError(f"Publication {publication_id} not found")

    if exp_row["publication_id"] == publication_id:
        return  # idempotent

    if exp_row["publication_id"] is not None:
        raise ValueError(
            f"Experiment {experiment_id!r} is already attached to publication "
            f"{exp_row['publication_id']}; cannot attach to {publication_id}"
        )

    # Phase 14B.1: verify publication's lineage is consistent with the experiment.
    # Publication → Publishing Plan → experiment_id must match if the plan carries one.
    _pub_lineage_row = conn.execute(
        """
        SELECT pp.experiment_id
        FROM publications pub
        JOIN publishing_plans pp ON pp.id = pub.publishing_plan_id
        WHERE pub.id = ?
        """,
        (publication_id,),
    ).fetchone()
    if _pub_lineage_row is not None and _pub_lineage_row["experiment_id"] is not None:
        if _pub_lineage_row["experiment_id"] != experiment_id:
            raise ValueError(
                f"Publication {publication_id} belongs to experiment "
                f"{_pub_lineage_row['experiment_id']!r} via publishing_plan lineage; "
                f"cannot attach to experiment {experiment_id!r}"
            )

    # Phase 14B.2: also check production_plan experiment_id via the deeper lineage path.
    # Covers the gap where publishing_plan.experiment_id is NULL but the upstream
    # production_plan is already bound to a different experiment.
    _prod_lineage_row = conn.execute(
        """
        SELECT pp2.experiment_id
        FROM publications pub
        JOIN publishing_plans pp ON pp.id = pub.publishing_plan_id
        JOIN production_plans pp2 ON pp2.id = pp.production_plan_id
        WHERE pub.id = ?
        """,
        (publication_id,),
    ).fetchone()
    if _prod_lineage_row is not None and _prod_lineage_row["experiment_id"] is not None:
        if _prod_lineage_row["experiment_id"] != experiment_id:
            raise ValueError(
                f"Publication {publication_id} belongs to experiment "
                f"{_prod_lineage_row['experiment_id']!r} via production_plan lineage; "
                f"cannot attach to experiment {experiment_id!r}"
            )

    conn.execute(
        "UPDATE experiments SET publication_id = ?, "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%S', 'now') WHERE id = ?",
        (publication_id, experiment_id),
    )


def get_experiment_lineage(
    conn: sqlite3.Connection,
    experiment_id: str,
) -> dict:
    """Return a lineage dict linking the experiment to its full production pipeline."""
    exp = get_experiment(conn, experiment_id)

    production_plans = conn.execute(
        "SELECT id, topic_id, status FROM production_plans WHERE experiment_id = ?",
        (experiment_id,),
    ).fetchall()

    analytics = conn.execute(
        "SELECT id, publication_id FROM analytics_snapshots WHERE experiment_id = ?",
        (experiment_id,),
    ).fetchall()

    state_events = conn.execute(
        "SELECT from_state, to_state, actor, reason, created_at "
        "FROM experiment_state_events WHERE experiment_id = ? ORDER BY id",
        (experiment_id,),
    ).fetchall()

    opportunity = None
    if exp.opportunity_id:
        opp_row = conn.execute(
            "SELECT id, normalized_topic, current_lifecycle_state FROM opportunities WHERE id = ?",
            (exp.opportunity_id,),
        ).fetchone()
        if opp_row:
            opportunity = dict(opp_row)

    # Derive topic from production plan → topics
    topic = None
    plan_ids = [r["id"] for r in production_plans]
    if plan_ids:
        topic_row = conn.execute(
            f"SELECT t.id, t.title FROM topics t "
            f"JOIN production_plans pp ON pp.topic_id = t.id "
            f"WHERE pp.id IN ({','.join('?' * len(plan_ids))})",
            plan_ids,
        ).fetchone()
        if topic_row:
            topic = dict(topic_row)

    # Feature snapshots linked via production_plan experiment_id
    feature_snapshots: list[dict] = []
    if plan_ids:
        fs_rows = conn.execute(
            f"SELECT id, publication_id, feature_schema_version, extracted_at "
            f"FROM content_feature_snapshots "
            f"WHERE production_plan_id IN ({','.join('?' * len(plan_ids))})",
            plan_ids,
        ).fetchall()
        feature_snapshots = [dict(r) for r in fs_rows]

    # Learning runs linked via publication_id on the experiment
    learning_runs: list[dict] = []
    if exp.publication_id:
        lr_rows = conn.execute(
            "SELECT id, status, engine_version, input_hash, created_at "
            "FROM learning_runs WHERE publication_id = ?",
            (exp.publication_id,),
        ).fetchall()
        learning_runs = [dict(r) for r in lr_rows]

    return {
        "experiment": {
            "id": exp.id,
            "type": exp.experiment_type.value,
            "status": exp.status.value,
            "hypothesis": exp.hypothesis,
            "channel_id": exp.channel_id,
            "opportunity_id": exp.opportunity_id,
            "publication_id": exp.publication_id,
        },
        "opportunity": opportunity,
        "topic": topic,
        "production_plans": [dict(r) for r in production_plans],
        "analytics_snapshots": [dict(r) for r in analytics],
        "feature_snapshots": feature_snapshots,
        "learning_runs": learning_runs,
        "metric_targets": [
            {
                "metric_name": t.metric_name,
                "direction": t.direction.value,
                "is_primary": t.is_primary,
                "target_value": t.target_value,
            }
            for t in exp.metric_targets
        ],
        "factors": [
            {
                "factor_name": f.factor_name,
                "role": f.factor_role.value,
                "intended_value": f.intended_value,
                "actual_value": f.actual_value,
            }
            for f in exp.factors
        ],
        "state_events": [dict(r) for r in state_events],
    }
