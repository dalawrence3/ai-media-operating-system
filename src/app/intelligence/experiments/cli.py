"""Phase 14A — ace experiment CLI.

Commands:
    ace experiment list   [--channel] [--status] [--type]   — list experiments
    ace experiment show   <experiment_id>                    — show experiment detail
    ace experiment lineage <experiment_id>                   — full production lineage
"""

from __future__ import annotations

import json
from typing import Annotated

import typer

from app.intelligence.experiments.models import ExperimentStatus, ExperimentType

experiment_app = typer.Typer(
    name="experiment",
    help="Production experiment ledger — lifecycle, metrics, and lineage.",
    no_args_is_help=True,
)


def _get_db():
    from app.core.config import get_config
    from app.core.database import open_db
    from app.core.logging import configure_logging

    cfg = get_config()
    configure_logging(cfg.log_level)
    return open_db(cfg.db_path)


@experiment_app.command("list")
def experiment_list(
    channel: Annotated[
        int | None, typer.Option("--channel", "-c", help="Filter by channel ID.")
    ] = None,
    status: Annotated[
        ExperimentStatus | None, typer.Option("--status", "-s", help="Filter by status.")
    ] = None,
    experiment_type: Annotated[
        ExperimentType | None, typer.Option("--type", "-t", help="Filter by type.")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max results.")] = 20,
) -> None:
    """List production experiments."""
    from app.intelligence.experiments.repository import list_experiments

    conn = _get_db()
    try:
        experiments = list_experiments(
            conn,
            channel_id=channel,
            status=status,
            experiment_type=experiment_type,
            limit=limit,
        )
        if not experiments:
            typer.echo("No experiments found.")
            return
        for exp in experiments:
            typer.echo(
                f"[{exp.id[:8]}] {exp.experiment_type.value:<12} {exp.status.value:<14} "
                f"channel={exp.channel_id}  opp={exp.opportunity_id}  "
                f'hypothesis="{exp.hypothesis[:60]}"'
            )
    finally:
        conn.close()


@experiment_app.command("show")
def experiment_show(
    experiment_id: Annotated[str, typer.Argument(help="Experiment ID (full UUID or prefix).")],
) -> None:
    """Show experiment detail including metric targets and factors."""
    from app.intelligence.experiments.repository import get_experiment, list_experiments

    conn = _get_db()
    try:
        try:
            exp = get_experiment(conn, experiment_id)
        except KeyError:
            # Try prefix match.
            all_exps = list_experiments(conn, limit=500)
            matches = [e for e in all_exps if e.id.startswith(experiment_id)]
            if len(matches) == 1:
                exp = get_experiment(conn, matches[0].id)
            elif len(matches) > 1:
                typer.echo(
                    f"Ambiguous prefix '{experiment_id}' — matches {len(matches)} experiments.",
                    err=True,
                )
                raise typer.Exit(1) from None
            else:
                typer.echo(f"Experiment '{experiment_id}' not found.", err=True)
                raise typer.Exit(1) from None

        typer.echo(f"ID:         {exp.id}")
        typer.echo(f"Type:       {exp.experiment_type.value}")
        typer.echo(f"Status:     {exp.status.value}")
        typer.echo(f"Channel:    {exp.channel_id}")
        typer.echo(f"Opportunity:{exp.opportunity_id}")
        typer.echo(f"Hypothesis: {exp.hypothesis}")
        if exp.hypothesis_null:
            typer.echo(f"Null H:     {exp.hypothesis_null}")
        typer.echo(f"Created:    {exp.created_at}")
        typer.echo(f"Updated:    {exp.updated_at}")
        if exp.publication_id:
            typer.echo(f"Publication:{exp.publication_id}")
        typer.echo("")
        typer.echo("Metric targets:")
        if not exp.metric_targets:
            typer.echo("  (none)")
        for t in exp.metric_targets:
            primary = " [primary]" if t.is_primary else ""
            typer.echo(f"  {t.metric_name:<30} {t.direction.value}{primary}")
        typer.echo("")
        typer.echo("Factors:")
        if not exp.factors:
            typer.echo("  (none)")
        for f in exp.factors:
            actual = f" → actual={f.actual_value!r}" if f.actual_value is not None else ""
            typer.echo(
                f"  [{f.factor_role.value:<10}] {f.factor_name:<25} "
                f"intended={f.intended_value!r}{actual}"
            )
    finally:
        conn.close()


@experiment_app.command("eligibility")
def experiment_eligibility(
    opportunity_id: Annotated[int, typer.Argument(help="Opportunity ID to assess.")],
    channel: Annotated[int, typer.Option("--channel", "-c", help="Channel ID.")],
    output_json: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
) -> None:
    """Assess experiment eligibility for an Opportunity (read-only, no live API calls)."""
    from app.intelligence.experiments.eligibility_service import assess_experiment_eligibility

    conn = _get_db()
    try:
        result = assess_experiment_eligibility(conn, opportunity_id, channel)

        if output_json:
            typer.echo(
                __import__("json").dumps(
                    {
                        "opportunity_id": result.opportunity_id,
                        "channel_id": result.channel_id,
                        "classification": result.classification.value,
                        "assessed_at": result.assessed_at,
                        "market_freshness_class": result.market_freshness_class.value
                        if result.market_freshness_class
                        else None,
                        "market_knowledge_age_hours": result.market_knowledge_age_hours,
                        "signal_maturity": result.signal_maturity,
                        "signal_confidence": result.signal_confidence,
                        "semantic_fit_score": result.semantic_fit_score,
                        "semantic_fit_label": result.semantic_fit_label,
                        "has_active_conflict": result.has_active_conflict,
                        "active_conflict_experiment_id": result.active_conflict_experiment_id,
                        "phase12c_publication_count": result.phase12c_publication_count,
                        "analytics_ready": result.analytics_ready,
                        "findings": [
                            {
                                "code": f.code,
                                "severity": f.severity,
                                "message": f.message,
                                "detail": f.detail,
                            }
                            for f in result.findings
                        ],
                    },
                    indent=2,
                )
            )
            return

        cls_symbol = {
            "general_eligible": "✓",
            "exploration_only": "~",
            "requires_refresh": "↻",
            "unresolved": "?",
            "ineligible": "✗",
        }.get(result.classification.value, " ")

        typer.echo(
            f"[{cls_symbol}] Opportunity #{opportunity_id}  →  "
            f"{result.classification.value.upper()}"
        )
        typer.echo(f"    Channel:    {result.channel_id}")
        typer.echo(f"    Assessed:   {result.assessed_at}")
        if result.market_freshness_class:
            age_str = (
                f"{result.market_knowledge_age_hours:.0f}h"
                if result.market_knowledge_age_hours
                else "?"
            )
            typer.echo(f"    Market:     {result.market_freshness_class.value}  (age={age_str})")
        if result.signal_maturity:
            typer.echo(
                f"    Signal:     {result.signal_maturity}  "
                f"confidence={result.signal_confidence:.3f}"
            )
        if result.semantic_fit_score is not None:
            typer.echo(
                f"    Fit score:  {result.semantic_fit_score:.3f}  ({result.semantic_fit_label})"
            )
        if result.has_active_conflict:
            typer.echo(f"    Conflict:   {result.active_conflict_experiment_id}")
        typer.echo("")
        if result.findings:
            typer.echo("Findings:")
            for f in result.findings:
                prefix = {"block": "[BLOCK]", "warn": "[warn] ", "info": "[info] "}.get(
                    f.severity, "       "
                )
                typer.echo(f"  {prefix} {f.message}")
    finally:
        conn.close()


@experiment_app.command("lineage")
def experiment_lineage(
    experiment_id: Annotated[str, typer.Argument(help="Experiment ID.")],
    output_json: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
) -> None:
    """Show full production lineage for an experiment."""
    from app.intelligence.experiments.repository import get_experiment_lineage

    conn = _get_db()
    try:
        try:
            lineage = get_experiment_lineage(conn, experiment_id)
        except KeyError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from None

        if output_json:
            typer.echo(json.dumps(lineage, indent=2))
            return

        exp = lineage["experiment"]
        typer.echo(f"Experiment: {exp['id']}  [{exp['type']}]  status={exp['status']}")
        typer.echo(f"  Hypothesis: {exp['hypothesis']}")
        typer.echo(
            f"  Channel: {exp['channel_id']}  Opportunity: {exp['opportunity_id']}  "
            f"Publication: {exp['publication_id']}"
        )

        if lineage["opportunity"]:
            opp = lineage["opportunity"]
            typer.echo(
                f"\nOpportunity #{opp['id']}: {opp['normalized_topic']}  "
                f"({opp['current_lifecycle_state']})"
            )

        typer.echo(f"\nProduction plans ({len(lineage['production_plans'])}):")
        for pp in lineage["production_plans"]:
            typer.echo(f"  plan #{pp['id']}  topic={pp['topic_id']}  status={pp['status']}")

        typer.echo(f"\nAnalytics snapshots ({len(lineage['analytics_snapshots'])}):")
        for snap in lineage["analytics_snapshots"]:
            typer.echo(f"  #{snap['id']}  pub={snap['publication_id']}")

        typer.echo(f"\nLifecycle events ({len(lineage['state_events'])}):")
        for ev in lineage["state_events"]:
            arrow = f"{ev['from_state']} → " if ev["from_state"] else ""
            typer.echo(f"  {ev['created_at']}  {arrow}{ev['to_state']}  actor={ev['actor']}")
    finally:
        conn.close()


@experiment_app.command("plan")
def experiment_plan(
    channel: Annotated[int, typer.Option("--channel", "-c", help="Channel ID to plan for.")],
    opportunity_ids: Annotated[
        str | None,
        typer.Option(
            "--opportunities",
            help="Comma-separated opportunity IDs to assess (default: all active).",
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Compute plan without persisting to DB.")
    ] = False,
    output_json: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max opportunities to assess.")] = 20,
) -> None:
    """Build an experiment portfolio plan for a channel (read-only, no live API calls).

    Assesses eligibility for each active opportunity, scores candidates,
    and produces a portfolio of exploration/exploitation experiments.
    Use --dry-run to preview without persisting.
    """
    from app.intelligence.experiments.eligibility_service import assess_experiment_eligibility
    from app.intelligence.experiments.planning_service import build_portfolio_plan

    conn = _get_db()
    try:
        # Determine opportunity IDs to assess
        if opportunity_ids:
            opp_id_list = [int(x.strip()) for x in opportunity_ids.split(",") if x.strip()]
        else:
            rows = conn.execute(
                """SELECT id FROM opportunities
                   WHERE channel_id = ?
                     AND current_lifecycle_state NOT IN ('rejected', 'archived', 'produced')
                   ORDER BY id DESC LIMIT ?""",
                (channel, limit),
            ).fetchall()
            opp_id_list = [r["id"] for r in rows]

        if not opp_id_list:
            typer.echo("No opportunities found for this channel.")
            return

        # Assess eligibility for each
        assessments = []
        for opp_id in opp_id_list:
            a = assess_experiment_eligibility(conn, opp_id, channel)
            assessments.append(a)

        plan = build_portfolio_plan(conn, channel, assessments, dry_run=dry_run)

        if output_json:
            typer.echo(
                json.dumps(
                    {
                        "run_id": plan.run_id,
                        "channel_id": plan.channel_id,
                        "planned_at": plan.planned_at,
                        "is_dry_run": plan.is_dry_run,
                        "eligible_count": plan.eligible_count,
                        "general_eligible_count": plan.general_eligible_count,
                        "exploration_only_count": plan.exploration_only_count,
                        "selected_count": plan.selected_count,
                        "deferred_count": plan.deferred_count,
                        "input_hash": plan.input_hash,
                        "decisions": [
                            {
                                "opportunity_id": d.opportunity_id,
                                "selected": d.selected,
                                "pool_type": d.pool_type,
                                "rank_in_pool": d.rank_in_pool,
                                "selection_reason": d.selection_reason,
                                "deferral_reason": d.deferral_reason,
                                "is_validation_repeat": d.is_validation_repeat,
                                "planning_intent": d.candidate.planning_intent.value,
                                "hypothesis_sketch": d.candidate.hypothesis_sketch,
                                "exploitation_value": d.candidate.score.exploitation_value,
                                "exploration_value": d.candidate.score.exploration_value,
                            }
                            for d in plan.decisions
                        ],
                    },
                    indent=2,
                )
            )
            return

        dry_tag = " [DRY RUN]" if plan.is_dry_run else ""
        typer.echo(f"Portfolio Plan{dry_tag}  channel={plan.channel_id}  run={plan.run_id[:8]}")
        typer.echo(
            f"  Assessed:  {len(opp_id_list)} opportunities  |  Eligible: {plan.eligible_count}"
        )
        typer.echo(f"  Selected:  {plan.selected_count}  |  Deferred: {plan.deferred_count}")
        typer.echo("")

        selected = [d for d in plan.decisions if d.selected]
        deferred = [d for d in plan.decisions if not d.selected]

        if selected:
            typer.echo("Selected:")
            for d in selected:
                score_str = ""
                if (
                    d.pool_type == "exploitation"
                    and d.candidate.score.exploitation_value is not None
                ):
                    score_str = f"  exploit={d.candidate.score.exploitation_value:.3f}"
                else:
                    score_str = f"  explore={d.candidate.score.exploration_value:.3f}"
                typer.echo(
                    f"  [{d.pool_type:<12}] opp={d.opportunity_id}  "
                    f"rank={d.rank_in_pool}{score_str}"
                    f"  intent={d.candidate.planning_intent.value}"
                )
                typer.echo(f"    {d.candidate.hypothesis_sketch}")
        if deferred:
            typer.echo("\nDeferred:")
            for d in deferred:
                typer.echo(f"  opp={d.opportunity_id}  reason={d.deferral_reason}")
    finally:
        conn.close()


@experiment_app.command("planning-run")
def experiment_planning_run(
    run_id: Annotated[str, typer.Argument(help="Planning run ID (full or prefix).")],
    output_json: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
) -> None:
    """Show detail for a stored planning run."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM experiment_planning_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            # Try prefix match
            rows = conn.execute(
                "SELECT * FROM experiment_planning_runs WHERE id LIKE ?",
                (f"{run_id}%",),
            ).fetchall()
            if len(rows) == 1:
                row = rows[0]
            elif len(rows) > 1:
                typer.echo(f"Ambiguous prefix '{run_id}' matches {len(rows)} runs.", err=True)
                raise typer.Exit(1)
            else:
                typer.echo(f"Planning run '{run_id}' not found.", err=True)
                raise typer.Exit(1)

        if output_json:
            typer.echo(
                json.dumps(
                    {
                        "id": row["id"],
                        "channel_id": row["channel_id"],
                        "status": row["status"],
                        "eligible_count": row["eligible_count"],
                        "exploration_only_count": row["exploration_only_count"],
                        "general_eligible_count": row["general_eligible_count"],
                        "selected_count": row["selected_count"],
                        "deferred_count": row["deferred_count"],
                        "input_hash": row["input_hash"],
                        "created_at": row["created_at"],
                    },
                    indent=2,
                )
            )
            return

        typer.echo(f"Planning Run: {row['id']}")
        typer.echo(f"  Channel:  {row['channel_id']}")
        typer.echo(f"  Status:   {row['status']}")
        typer.echo(f"  Created:  {row['created_at']}")
        typer.echo(
            f"  Eligible: {row['eligible_count']}  "
            f"(general={row['general_eligible_count']}, "
            f"exploration_only={row['exploration_only_count']})"
        )
        typer.echo(f"  Selected: {row['selected_count']}  Deferred: {row['deferred_count']}")
    finally:
        conn.close()


@experiment_app.command("candidates")
def experiment_candidates(
    run: Annotated[str, typer.Option("--run", "-r", help="Planning run ID.")],
    selected_only: Annotated[
        bool, typer.Option("--selected", help="Show only selected candidates.")
    ] = False,
    output_json: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
) -> None:
    """Show scored candidates for a planning run."""
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT s.*, d.selected, d.rank_in_pool, d.pool_type,
                      d.selection_reason, d.deferral_reason, d.is_validation_repeat
               FROM experiment_candidate_scores s
               JOIN experiment_selection_decisions d ON d.candidate_score_id = s.id
               WHERE s.planning_run_id = ?
               ORDER BY d.selected DESC, d.pool_type, d.rank_in_pool""",
            (run,),
        ).fetchall()
        if not rows:
            typer.echo(f"No candidates found for run '{run}'.")
            return

        if selected_only:
            rows = [r for r in rows if r["selected"]]

        if output_json:
            typer.echo(
                json.dumps(
                    [
                        {
                            "opportunity_id": r["opportunity_id"],
                            "selected": bool(r["selected"]),
                            "pool_type": r["pool_type"],
                            "rank_in_pool": r["rank_in_pool"],
                            "planning_intent": r["planning_intent"],
                            "eligibility_classification": r["eligibility_classification"],
                            "hypothesis_sketch": r["hypothesis_sketch"],
                            "exploitation_value": r["exploitation_value"],
                            "exploration_value": r["exploration_value"],
                            "information_gain": r["information_gain"],
                            "uncertainty": r["uncertainty"],
                            "cluster_coverage_need": r["cluster_coverage_need"],
                            "internal_evidence_strength": r["internal_evidence_strength"],
                            "deferral_reason": r["deferral_reason"],
                        }
                        for r in rows
                    ],
                    indent=2,
                )
            )
            return

        for r in rows:
            selected_mark = "✓" if r["selected"] else "○"
            pool = r["pool_type"] or "deferred"
            rank = f"#{r['rank_in_pool']}" if r["rank_in_pool"] else "  "
            ev = (
                f"{r['exploitation_value']:.3f}" if r["exploitation_value"] is not None else "  N/A"
            )
            xv = f"{r['exploration_value']:.3f}" if r["exploration_value"] is not None else "  N/A"
            typer.echo(
                f"[{selected_mark}] opp={r['opportunity_id']}  "
                f"pool={pool:<12} {rank}  "
                f"exploit={ev}  explore={xv}  "
                f"intent={r['planning_intent']}"
            )
            if not r["selected"] and r["deferral_reason"]:
                typer.echo(f"      deferred: {r['deferral_reason']}")
    finally:
        conn.close()


@experiment_app.command("execute")
def experiment_execute(
    experiment_id: Annotated[
        str, typer.Argument(help="Experiment ID to create an execution contract for.")
    ],
    brief_id: Annotated[
        str, typer.Option("--brief", help="Strategy brief ID to bind to this contract.")
    ],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--real",
            help=(
                "Dry run (default) persists contract for audit but does not inject "
                "into production. --real enables production override."
            ),
        ),
    ] = True,
    idea_id: Annotated[
        int | None, typer.Option("--idea", help="Idea candidate ID (optional).")
    ] = None,
    output_json: Annotated[bool, typer.Option("--json", help="Output JSON.")] = False,
) -> None:
    """Create an execution contract for an experiment.

    By default creates a DRY_RUN contract, which is persisted for audit but
    does not inject treatment values into production. Pass --real to enable
    production override (speaking_rate, etc.) via the contract.

    IMPORTANT: --real does NOT publish content. It only wires the contract
    so that the next narration stage reads the experiment's intended values.
    No content is generated by this command.
    """
    from app.intelligence.experiments.execution_contract import ExecutionMode
    from app.intelligence.experiments.execution_service import (
        ExecutionContractError,
        create_execution_contract,
    )

    conn = _get_db()
    try:
        mode = ExecutionMode.DRY_RUN if dry_run else ExecutionMode.REAL
        contract = create_execution_contract(
            conn,
            experiment_id,
            brief_id,
            mode=mode,
            idea_id=idea_id,
        )
        conn.commit()

        if output_json:
            typer.echo(
                json.dumps(
                    {
                        "id": contract.id,
                        "experiment_id": contract.experiment_id,
                        "brief_id": contract.brief_id,
                        "idea_id": contract.idea_id,
                        "execution_mode": contract.execution_mode.value,
                        "status": contract.status,
                        "eligibility_recheck_result": contract.eligibility_recheck_result,
                        "eligibility_blocked": contract.eligibility_blocked,
                        "narration_speaking_rate_override": (
                            contract.narration_speaking_rate_override
                        ),
                        "treatment_abs_valid": contract.treatment_abs_valid,
                        "treatment_delta_valid": contract.treatment_delta_valid,
                        "execution_policy_version": contract.execution_policy_version,
                        "treatment_factors": [
                            {
                                "factor_name": tc.factor_name,
                                "intended_value": tc.intended_value,
                                "abs_valid": tc.abs_valid,
                                "delta_valid": tc.delta_valid,
                            }
                            for tc in contract.treatment_configs
                        ],
                        "control_factors": [
                            {
                                "factor_name": cc.factor_name,
                                "baseline_value": cc.baseline_value,
                                "control_capability": cc.control_capability,
                            }
                            for cc in contract.control_configs
                        ],
                    },
                    indent=2,
                )
            )
            return

        typer.echo(f"Execution contract: {contract.id}")
        typer.echo(f"  Experiment : {contract.experiment_id}")
        typer.echo(f"  Brief      : {contract.brief_id}")
        typer.echo(f"  Mode       : {contract.execution_mode.value}")
        typer.echo(f"  Status     : {contract.status}")
        typer.echo(
            f"  Eligibility: {contract.eligibility_recheck_result or 'unknown'}"
            f" {'[BLOCKED]' if contract.eligibility_blocked else ''}"
        )
        typer.echo(f"  abs_valid  : {contract.treatment_abs_valid}")
        typer.echo(f"  delta_valid: {contract.treatment_delta_valid}")
        if contract.narration_speaking_rate_override is not None:
            typer.echo(f"  rate       : {contract.narration_speaking_rate_override:.3f}")
        if contract.treatment_configs:
            typer.echo("  Treatment factors:")
            for tc in contract.treatment_configs:
                typer.echo(
                    f"    {tc.factor_name} = {tc.intended_value!r}"
                    f" (abs={tc.abs_valid} delta={tc.delta_valid})"
                )
        if contract.control_configs:
            typer.echo("  Control factors:")
            for cc in contract.control_configs:
                typer.echo(
                    f"    {cc.factor_name} = {cc.baseline_value!r} [{cc.control_capability}]"
                )
    except ExecutionContractError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None
    finally:
        conn.close()


@experiment_app.command("evaluate")
def experiment_evaluate(
    experiment_id: Annotated[str, typer.Argument(help="Experiment ID to evaluate.")],
    prior: Annotated[
        str | None,
        typer.Option("--prior", help="Prior experiment ID for validation/sequential baseline."),
    ] = None,
    persist: Annotated[
        bool, typer.Option("--persist/--no-persist", help="Persist result to DB.")
    ] = False,
    output_json: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
) -> None:
    """Evaluate experiment outcome (read-only by default; --persist to save).

    No YouTube calls, no content generation, no live API calls.
    """
    from app.intelligence.experiments.outcome_service import (
        evaluate_experiment_outcome,
        persist_outcome,
    )

    conn = _get_db()
    try:
        try:
            ev = evaluate_experiment_outcome(conn, experiment_id, prior_experiment_id=prior)
        except KeyError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from None

        if persist:
            persist_outcome(conn, ev)
            conn.commit()

        if output_json:
            typer.echo(
                json.dumps(
                    {
                        "id": ev.id,
                        "experiment_id": ev.experiment_id,
                        "readiness": ev.readiness.value,
                        "classification": ev.classification.value if ev.classification else None,
                        "evidence_maturity": ev.evidence_maturity.value
                        if ev.evidence_maturity
                        else None,
                        "baseline_source_type": ev.baseline_source_type.value,
                        "baseline_experiment_id": ev.baseline_experiment_id,
                        "target_metric_name": ev.target_metric_name,
                        "target_metric_direction": ev.target_metric_direction,
                        "treatment_metric_value": ev.treatment_metric_value,
                        "baseline_metric_value": ev.baseline_metric_value,
                        "absolute_delta": ev.absolute_delta,
                        "relative_delta": ev.relative_delta,
                        "is_mature": ev.is_mature,
                        "publication_age_hours": ev.publication_age_hours,
                        "observed_views": ev.observed_views,
                        "reasons": ev.reasons,
                        "warnings": ev.warnings,
                        "outcome_policy_version": ev.outcome_policy_version,
                        "input_hash": ev.input_hash,
                        "evaluated_at": ev.evaluated_at,
                    },
                    indent=2,
                )
            )
            return

        typer.echo(f"Readiness:    {ev.readiness.value.upper()}")
        if ev.classification:
            typer.echo(f"Outcome:      {ev.classification.value.upper()}")
        if ev.evidence_maturity:
            typer.echo(f"Evidence:     {ev.evidence_maturity.value}")
        if ev.target_metric_name:
            typer.echo(f"Metric:       {ev.target_metric_name}  ({ev.target_metric_direction})")
        if ev.treatment_metric_value is not None:
            typer.echo(f"Treatment:    {ev.treatment_metric_value:.4f}")
        if ev.baseline_metric_value is not None:
            typer.echo(
                f"Baseline:     {ev.baseline_metric_value:.4f}  [{ev.baseline_source_type.value}]"
            )
        if ev.absolute_delta is not None:
            rel_str = f"  ({ev.relative_delta:+.1%})" if ev.relative_delta is not None else ""
            typer.echo(f"Delta:        {ev.absolute_delta:+.4f}{rel_str}")
        if ev.is_mature:
            typer.echo("Mature:       yes")
        if ev.publication_age_hours is not None:
            typer.echo(f"Age:          {ev.publication_age_hours:.1f}h")
        if ev.observed_views is not None:
            typer.echo(f"Views:        {ev.observed_views:.0f}")
        for r in ev.reasons:
            typer.echo(f"  reason:     {r}")
        for w in ev.warnings:
            typer.echo(f"  warning:    {w}")
        if persist:
            typer.echo(f"Persisted:    {ev.id}")
    finally:
        conn.close()


@experiment_app.command("outcome")
def experiment_outcome(
    experiment_id: Annotated[str, typer.Argument(help="Experiment ID.")],
    output_json: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
) -> None:
    """Show the latest persisted outcome for an experiment."""
    from app.intelligence.experiments.outcome_service import get_latest_experiment_outcome

    conn = _get_db()
    try:
        ev = get_latest_experiment_outcome(conn, experiment_id)
        if ev is None:
            typer.echo("No persisted outcome found for this experiment.")
            return

        if output_json:
            typer.echo(
                json.dumps(
                    {
                        "id": ev.id,
                        "experiment_id": ev.experiment_id,
                        "readiness": ev.readiness.value,
                        "classification": ev.classification.value if ev.classification else None,
                        "evidence_maturity": ev.evidence_maturity.value
                        if ev.evidence_maturity
                        else None,
                        "baseline_source_type": ev.baseline_source_type.value,
                        "absolute_delta": ev.absolute_delta,
                        "relative_delta": ev.relative_delta,
                        "is_mature": ev.is_mature,
                        "evaluated_at": ev.evaluated_at,
                    },
                    indent=2,
                )
            )
            return

        typer.echo(f"[{ev.evaluated_at}]  {ev.readiness.value.upper()}")
        if ev.classification:
            typer.echo(f"  Outcome:  {ev.classification.value}")
        if ev.absolute_delta is not None:
            typer.echo(f"  Delta:    {ev.absolute_delta:+.4f}")
        if ev.is_mature:
            typer.echo("  Mature:   yes")
    finally:
        conn.close()


@experiment_app.command("outcomes")
def experiment_outcomes(
    experiment_id: Annotated[str, typer.Argument(help="Experiment ID.")],
    output_json: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
) -> None:
    """List all persisted outcome evaluations for an experiment."""
    from app.intelligence.experiments.outcome_service import list_experiment_outcomes

    conn = _get_db()
    try:
        outcomes = list_experiment_outcomes(conn, experiment_id)
        if not outcomes:
            typer.echo("No persisted outcomes found.")
            return

        if output_json:
            typer.echo(
                json.dumps(
                    [
                        {
                            "id": ev.id,
                            "readiness": ev.readiness.value,
                            "classification": ev.classification.value
                            if ev.classification
                            else None,
                            "is_mature": ev.is_mature,
                            "evaluated_at": ev.evaluated_at,
                        }
                        for ev in outcomes
                    ],
                    indent=2,
                )
            )
            return

        for ev in outcomes:
            cls_str = ev.classification.value if ev.classification else "—"
            mature_str = " [mature]" if ev.is_mature else ""
            typer.echo(f"[{ev.evaluated_at}]  {ev.readiness.value:<22}  {cls_str}{mature_str}")
    finally:
        conn.close()
