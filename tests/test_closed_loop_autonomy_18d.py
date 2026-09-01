"""Phase 18D — closed-loop autonomy: publication → observation → outcome → learning → decision.

Phase 18C proved a video could be produced and published autonomously. It did
not close the loop: the experiment ledger stopped at `in_production`, nothing
extracted content features, nothing computed an outcome, and the planner could
not see any of the channel's own learning. This suite covers the bridges that
close it, and the defects found while doing so.

Grouped by the property under test:

  Lifecycle       publication → experiment handoff, idempotency, reconciliation
  Observation     the `observing` period, and honest immaturity
  Outcome         maturity gating, seed exclusion, ledger advancement
  Learning bridge channel evidence actually reaching the planner
  Queue           terminal slots leaving the queue and production eligibility
  Scheduler       interval cadence
  Analytics       cumulative-window aggregation
  Isolation       nothing crosses channel boundaries

No network calls; no provider is contacted anywhere in this file.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.database import open_db


def _uid() -> str:
    return str(uuid.uuid4())


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _now() -> str:
    return _iso(datetime.now(UTC))


@pytest.fixture()
def db(tmp_path: Path):
    conn = open_db(tmp_path / "closed_loop_18d.db")
    conn.execute("PRAGMA foreign_keys = OFF")
    yield conn
    conn.close()


# ── Lineage seeding ──────────────────────────────────────────────────────────


def _seed_lineage(
    conn: sqlite3.Connection,
    *,
    cp_channel_id: str,
    intel_channel_id: int = 1,
    experiment_id: str | None = None,
    visibility: str = "public",
    status: str = "published",
    published_at: str | None = None,
    topic_id: int = 1,
    opportunity_id: int = 1,
) -> tuple[str, int]:
    """Build the minimal real chain the bridges walk.

    experiment → production_plan → publishing_plan → publication, plus the
    channel identity bridge. Deliberately built from the actual columns each
    bridge reads rather than from a helper that could drift from them.
    """
    exp_id = experiment_id or f"exp-{_uid()[:8]}"
    now = _now()

    conn.execute(
        "INSERT OR IGNORE INTO channels "
        "(id, platform, channel_name, platform_channel_id, cp_channel_id) "
        "VALUES (?, 'youtube', 'Test Channel', ?, ?)",
        (intel_channel_id, f"UC{intel_channel_id}test", cp_channel_id),
    )
    conn.execute(
        "UPDATE channels SET cp_channel_id = ? WHERE id = ?", (cp_channel_id, intel_channel_id)
    )
    conn.execute(
        "INSERT OR IGNORE INTO opportunities "
        "(id, channel_id, discovery_run_id, normalized_topic, raw_topic, "
        " current_lifecycle_state, created_at, updated_at) "
        "VALUES (?, ?, 0, 'test topic', 'test topic', 'approved', ?, ?)",
        (opportunity_id, intel_channel_id, now, now),
    )
    conn.execute(
        "INSERT OR IGNORE INTO topics "
        "(id, title, angle, status, promoted_opportunity_id, created_at, updated_at) "
        "VALUES (?, 'Test Topic', 'angle', 'approved', ?, ?, ?)",
        (topic_id, opportunity_id, now, now),
    )
    conn.execute(
        """INSERT INTO experiments
           (id, channel_id, opportunity_id, experiment_type, status, hypothesis,
            maturity_policy_json, created_at, updated_at, in_production_at)
           VALUES (?, ?, ?, 'exploration', 'in_production', 'test hypothesis',
                   '{}', ?, ?, ?)""",
        (exp_id, intel_channel_id, opportunity_id, now, now, now),
    )
    conn.execute(
        """INSERT INTO production_plans
           (topic_id, script_id, script_version, input_hash, script_body_hash,
            plan_schema_version, renderer_version, duration_algorithm_version,
            experiment_id, status, created_at, updated_at)
           VALUES (?, 1, 1, ?, ?, 'v1', 'v1', 'v1', ?, 'approved', ?, ?)""",
        (topic_id, _uid()[:16], _uid()[:16], exp_id, now, now),
    )
    prod_plan_id = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    conn.execute(
        """INSERT INTO publishing_plans
           (render_manifest_id, render_job_id, topic_id, production_plan_id, script_id,
            scene_manifest_id, narration_run_id, caption_run_id, experiment_id,
            input_hash, publishing_engine_version, metadata_version,
            provider, provider_version, title, description, tags_json,
            language, visibility, schedule_type, status, created_at, updated_at)
           VALUES (1, 1, ?, ?, 1, 1, 1, 1, ?, ?, 'v1', 'v1', 'youtube', 'v1',
                   'Test', 'desc', '[]', 'en', 'private', 'immediate', 'approved', ?, ?)""",
        (topic_id, prod_plan_id, exp_id, _uid()[:16], now, now),
    )
    pub_plan_id = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    conn.execute(
        """INSERT INTO publications
           (publishing_plan_id, publishing_job_id, provider, provider_version,
            provider_video_id, provider_url, status, visibility, published_at,
            publishing_engine_version, input_hash, output_sha256,
            created_at, updated_at, workspace_id, channel_id, platform_account_id)
           VALUES (?, 1, 'youtube', 'v1', ?, 'https://youtu.be/x', ?, ?, ?,
                   'v1', ?, ?, ?, ?, 'local-dev', ?, 'acct-1')""",
        (
            pub_plan_id,
            f"vid_{_uid()[:8]}",
            status,
            visibility,
            published_at or now,
            _uid()[:16],
            "a" * 64,
            now,
            now,
            cp_channel_id,
        ),
    )
    pub_id = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    conn.commit()
    return exp_id, pub_id


def _activate_observation(
    conn: sqlite3.Connection, publication_id: int, cp_channel_id: str
) -> None:
    conn.execute(
        """INSERT INTO analytics_observation_state
           (publication_id, workspace_id, channel_id, platform_account_id, schedule_id,
            observation_status, retention_acquired, consecutive_no_data, failure_count,
            created_at, updated_at)
           VALUES (?, 'local-dev', ?, 'acct-1', ?, 'active', 0, 0, 0, ?, ?)""",
        (publication_id, cp_channel_id, _uid(), _now(), _now()),
    )
    conn.commit()


def _experiment_status(conn: sqlite3.Connection, experiment_id: str) -> str:
    return conn.execute("SELECT status FROM experiments WHERE id = ?", (experiment_id,)).fetchone()[
        "status"
    ]


# ═════════════════════════════════════════════════════════════════════════════
# Lifecycle: publication → experiment handoff
# ═════════════════════════════════════════════════════════════════════════════


def test_public_publication_advances_experiment_past_in_production(db):
    """The Phase 18D gap itself: a public video must not leave its experiment
    stuck in `in_production` forever."""
    from app.intelligence.experiments.lifecycle import advance_experiment_for_publication

    cp = _uid()
    exp_id, pub_id = _seed_lineage(db, cp_channel_id=cp)
    assert _experiment_status(db, exp_id) == "in_production"

    result = advance_experiment_for_publication(db, pub_id)

    assert result.experiment_id == exp_id
    assert result.changed is True
    assert _experiment_status(db, exp_id) == "published"
    assert (
        db.execute("SELECT publication_id FROM experiments WHERE id = ?", (exp_id,)).fetchone()[
            "publication_id"
        ]
        == pub_id
    )


def test_handoff_lands_on_observing_when_observation_is_already_active(db):
    """Publication is an event, observation is a period. When the observer is
    already registered the ledger goes straight to the period."""
    from app.intelligence.experiments.lifecycle import advance_experiment_for_publication

    cp = _uid()
    exp_id, pub_id = _seed_lineage(db, cp_channel_id=cp)
    _activate_observation(db, pub_id, cp)

    advance_experiment_for_publication(db, pub_id)
    assert _experiment_status(db, exp_id) == "observing"


def test_handoff_is_idempotent(db):
    """Repeating the handoff — a retried cycle, a restart, a reconciliation
    pass — must not duplicate transitions or re-attach the publication."""
    from app.intelligence.experiments.lifecycle import advance_experiment_for_publication

    cp = _uid()
    exp_id, pub_id = _seed_lineage(db, cp_channel_id=cp)

    first = advance_experiment_for_publication(db, pub_id)
    second = advance_experiment_for_publication(db, pub_id)
    third = advance_experiment_for_publication(db, pub_id)

    assert first.changed is True
    assert second.changed is False
    assert third.changed is False
    assert _experiment_status(db, exp_id) == "published"

    events = db.execute(
        "SELECT COUNT(*) AS n FROM experiment_state_events "
        "WHERE experiment_id = ? AND to_state = 'published'",
        (exp_id,),
    ).fetchone()["n"]
    assert events == 1


def test_private_publication_never_advances_the_ledger(db):
    """A private upload is not a publication event. Recording one would make
    the ledger claim something that has not happened."""
    from app.intelligence.experiments.lifecycle import advance_experiment_for_publication

    cp = _uid()
    exp_id, pub_id = _seed_lineage(db, cp_channel_id=cp, visibility="private")

    result = advance_experiment_for_publication(db, pub_id)

    assert result.changed is False
    assert "not public" in (result.skipped_reason or "")
    assert _experiment_status(db, exp_id) == "in_production"


def test_experiment_is_derived_from_lineage_not_supplied_by_caller(db):
    """No caller can name an experiment — the derivation is the only path in."""
    from app.intelligence.experiments.lifecycle import (
        advance_experiment_for_publication,
        derive_experiment_for_publication,
    )

    cp = _uid()
    exp_a, pub_a = _seed_lineage(db, cp_channel_id=cp, topic_id=1, opportunity_id=1)
    exp_b, pub_b = _seed_lineage(db, cp_channel_id=cp, topic_id=2, opportunity_id=2)

    assert derive_experiment_for_publication(db, pub_a) == exp_a
    assert derive_experiment_for_publication(db, pub_b) == exp_b

    advance_experiment_for_publication(db, pub_a)
    assert _experiment_status(db, exp_a) == "published"
    assert _experiment_status(db, exp_b) == "in_production"


def test_derivation_falls_back_to_production_plan_lineage(db):
    """Older publishing plans carry no experiment_id; the deeper path covers them."""
    from app.intelligence.experiments.lifecycle import derive_experiment_for_publication

    cp = _uid()
    exp_id, pub_id = _seed_lineage(db, cp_channel_id=cp)
    db.execute(
        "UPDATE publishing_plans SET experiment_id = NULL "
        "WHERE id = (SELECT publishing_plan_id FROM publications WHERE id = ?)",
        (pub_id,),
    )
    db.commit()

    assert derive_experiment_for_publication(db, pub_id) == exp_id


def test_cancelled_experiment_is_never_advanced(db):
    from app.intelligence.experiments.lifecycle import advance_experiment_for_publication

    cp = _uid()
    exp_id, pub_id = _seed_lineage(db, cp_channel_id=cp)
    db.execute("UPDATE experiments SET status = 'cancelled' WHERE id = ?", (exp_id,))
    db.commit()

    result = advance_experiment_for_publication(db, pub_id)
    assert result.changed is False
    assert _experiment_status(db, exp_id) == "cancelled"


def test_handoff_walks_forward_through_skipped_stages(db):
    """An experiment left at `draft` by an interrupted production run still
    reaches `published` — the ledger only permits single steps, so the bridge
    walks them rather than requiring each call site to know the sequence."""
    from app.intelligence.experiments.lifecycle import advance_experiment_for_publication

    cp = _uid()
    exp_id, pub_id = _seed_lineage(db, cp_channel_id=cp)
    db.execute("UPDATE experiments SET status = 'draft' WHERE id = ?", (exp_id,))
    db.commit()

    result = advance_experiment_for_publication(db, pub_id)
    assert result.transitions == ["planned", "in_production", "published"]
    assert _experiment_status(db, exp_id) == "published"


def test_handoff_emits_an_audit_event(db):
    from app.intelligence.experiments.lifecycle import advance_experiment_for_publication

    cp = _uid()
    exp_id, pub_id = _seed_lineage(db, cp_channel_id=cp)
    advance_experiment_for_publication(db, pub_id)

    row = db.execute(
        "SELECT COUNT(*) AS n FROM cp_events WHERE event_type = 'experiment.published' "
        "AND experiment_id = ?",
        (exp_id,),
    ).fetchone()
    assert row["n"] == 1


# ═════════════════════════════════════════════════════════════════════════════
# Reconciliation / restart safety
# ═════════════════════════════════════════════════════════════════════════════


def test_reconciliation_repairs_a_publication_whose_handoff_never_ran(db):
    """The restart safety net: a crash between "provider confirmed public" and
    "ledger updated" must heal without an operator."""
    from app.intelligence.experiments.lifecycle import reconcile_experiment_lifecycle

    cp = _uid()
    exp_id, _pub_id = _seed_lineage(db, cp_channel_id=cp)

    repaired = reconcile_experiment_lifecycle(db)

    assert [r.experiment_id for r in repaired] == [exp_id]
    assert _experiment_status(db, exp_id) == "published"


def test_reconciliation_is_a_noop_once_the_ledger_has_caught_up(db):
    from app.intelligence.experiments.lifecycle import reconcile_experiment_lifecycle

    cp = _uid()
    _seed_lineage(db, cp_channel_id=cp)

    assert len(reconcile_experiment_lifecycle(db)) == 1
    assert reconcile_experiment_lifecycle(db) == []
    assert reconcile_experiment_lifecycle(db) == []


def test_reconciliation_ignores_private_publications(db):
    from app.intelligence.experiments.lifecycle import reconcile_experiment_lifecycle

    cp = _uid()
    exp_id, _ = _seed_lineage(db, cp_channel_id=cp, visibility="private")
    assert reconcile_experiment_lifecycle(db) == []
    assert _experiment_status(db, exp_id) == "in_production"


# ═════════════════════════════════════════════════════════════════════════════
# Observation
# ═════════════════════════════════════════════════════════════════════════════


def test_marking_observing_is_idempotent_across_ticks(db):
    """The observer calls this every tick; only the first one may transition."""
    from app.intelligence.experiments.lifecycle import mark_experiment_observing

    cp = _uid()
    exp_id, pub_id = _seed_lineage(db, cp_channel_id=cp)
    _activate_observation(db, pub_id, cp)

    for _ in range(5):
        mark_experiment_observing(db, pub_id)

    assert _experiment_status(db, exp_id) == "observing"
    n = db.execute(
        "SELECT COUNT(*) AS n FROM experiment_state_events "
        "WHERE experiment_id = ? AND to_state = 'observing'",
        (exp_id,),
    ).fetchone()["n"]
    assert n == 1


# ═════════════════════════════════════════════════════════════════════════════
# Outcome maturity
# ═════════════════════════════════════════════════════════════════════════════


def _seed_contract(
    conn: sqlite3.Connection,
    experiment_id: str,
    *,
    classification: str = "valid",
    channel_id: int = 1,
    opportunity_id: int = 1,
) -> None:
    """An execution contract with fidelity already assessed.

    The outcome evaluator gates on fidelity, so an experiment with no
    contract can never be scored — which is exactly what happened to every
    autonomously produced experiment before Phase 18D made the production
    cycle create one.
    """
    conn.execute(
        """INSERT INTO experiment_execution_contracts
           (id, experiment_id, brief_id, channel_id, opportunity_id,
            execution_mode, status, execution_policy_version,
            treatment_factors_json, control_factors_json, fidelity_json,
            valid_for_learning, created_at, completed_at)
           VALUES (?, ?, ?, ?, ?, 'real', 'completed', 'v1', '[]', '[]', ?, 1, ?, ?)""",
        (
            _uid(),
            experiment_id,
            _uid(),
            channel_id,
            opportunity_id,
            json.dumps({"classification": classification, "valid_for_learning": True}),
            _now(),
            _now(),
        ),
    )
    conn.commit()


def _seed_aggregate(
    conn: sqlite3.Connection,
    *,
    publication_id: int,
    topic_id: int,
    metric_name: str,
    value: float,
    is_seed: bool = False,
) -> None:
    conn.execute(
        """INSERT INTO analytics_aggregates
           (publication_id, topic_id, provider, period_type, period_key,
            metric_name, metric_value, snapshot_count, calculation_method,
            source_snapshot_ids_json, input_hash, created_at)
           VALUES (?, ?, ?, 'lifetime', 'lifetime', ?, ?, 1, 'sum', '[]', ?, ?)""",
        (
            publication_id,
            topic_id,
            "youtube_dev_seed" if is_seed else "youtube",
            metric_name,
            value,
            ("seed-" if is_seed else "real-") + _uid()[:12],
            _now(),
        ),
    )
    conn.commit()


def test_young_publication_with_no_views_stays_honestly_immature(db):
    """A video published minutes ago has no evidence. It must be represented
    as insufficient and left observing — never promoted, never fabricated."""
    from app.intelligence.experiments.lifecycle import advance_experiment_for_publication
    from app.intelligence.experiments.outcome_bridge import run_outcome_bridge

    cp = _uid()
    exp_id, pub_id = _seed_lineage(db, cp_channel_id=cp, published_at=_now())
    _activate_observation(db, pub_id, cp)
    _seed_contract(db, exp_id)
    advance_experiment_for_publication(db, pub_id)

    result = run_outcome_bridge(db, publication_id=pub_id)

    assert result.outcome_readiness == "insufficient_analytics"
    assert result.outcome_persisted is False
    assert _experiment_status(db, exp_id) == "observing"
    assert db.execute("SELECT COUNT(*) AS n FROM experiment_outcomes").fetchone()["n"] == 0


def test_mature_evidence_advances_the_experiment_to_analyzed(db):
    """Enough age and enough views: the outcome is persisted and the ledger
    moves on. `mature` is claimed only when the evaluator certifies it."""
    from app.intelligence.experiments.lifecycle import advance_experiment_for_publication
    from app.intelligence.experiments.outcome_bridge import run_outcome_bridge

    cp = _uid()
    old = _iso(datetime.now(UTC) - timedelta(days=5))
    exp_id, pub_id = _seed_lineage(db, cp_channel_id=cp, published_at=old)
    _activate_observation(db, pub_id, cp)
    _seed_contract(db, exp_id)
    _seed_aggregate(db, publication_id=pub_id, topic_id=1, metric_name="views", value=500.0)
    _seed_aggregate(
        db,
        publication_id=pub_id,
        topic_id=1,
        metric_name="average_view_percentage",
        value=42.0,
    )
    advance_experiment_for_publication(db, pub_id)

    result = run_outcome_bridge(db, publication_id=pub_id)

    assert result.outcome_readiness == "evaluable_mature"
    assert result.outcome_persisted is True
    assert _experiment_status(db, exp_id) == "analyzed"


def test_outcome_bridge_is_idempotent(db):
    """Every observation tick runs the bridge; repeats must not double-count."""
    from app.intelligence.experiments.lifecycle import advance_experiment_for_publication
    from app.intelligence.experiments.outcome_bridge import run_outcome_bridge

    cp = _uid()
    old = _iso(datetime.now(UTC) - timedelta(days=5))
    exp_id, pub_id = _seed_lineage(db, cp_channel_id=cp, published_at=old)
    _activate_observation(db, pub_id, cp)
    _seed_contract(db, exp_id)
    _seed_aggregate(db, publication_id=pub_id, topic_id=1, metric_name="views", value=500.0)
    _seed_aggregate(
        db,
        publication_id=pub_id,
        topic_id=1,
        metric_name="average_view_percentage",
        value=42.0,
    )
    advance_experiment_for_publication(db, pub_id)

    for _ in range(3):
        run_outcome_bridge(db, publication_id=pub_id)

    status_after = _experiment_status(db, exp_id)
    assert status_after == "analyzed"
    n_analyzed = db.execute(
        "SELECT COUNT(*) AS n FROM experiment_state_events "
        "WHERE experiment_id = ? AND to_state = 'analyzed'",
        (exp_id,),
    ).fetchone()["n"]
    assert n_analyzed == 1


def test_seed_analytics_never_count_as_evidence(db):
    """Development fixtures must not be scored as observations.

    Cross-publication learning already excludes `seed-%` aggregates; the
    outcome evaluator did not, so a channel carrying dev seed data would have
    scored experiments against 42 000 fabricated views.
    """
    from app.intelligence.experiments.lifecycle import advance_experiment_for_publication
    from app.intelligence.experiments.outcome_bridge import run_outcome_bridge

    cp = _uid()
    old = _iso(datetime.now(UTC) - timedelta(days=5))
    exp_id, pub_id = _seed_lineage(db, cp_channel_id=cp, published_at=old)
    _activate_observation(db, pub_id, cp)
    _seed_contract(db, exp_id)
    # Only seed data exists — there is genuinely nothing to learn from.
    _seed_aggregate(
        db,
        publication_id=pub_id,
        topic_id=1,
        metric_name="views",
        value=42000.0,
        is_seed=True,
    )
    advance_experiment_for_publication(db, pub_id)

    result = run_outcome_bridge(db, publication_id=pub_id)

    assert result.outcome_readiness == "insufficient_analytics"
    assert _experiment_status(db, exp_id) == "observing"


def test_provisional_outcome_matures_once_the_publication_is_old_enough(db):
    """Maturity depends on wall-clock age, not only on metrics.

    A video that collected its views on day one and nothing since must still
    be re-evaluated when it crosses the minimum-age threshold. This is why
    the bridge runs on every successful observation tick rather than only on
    ticks that brought new data — gating it on a changed snapshot would
    strand exactly these experiments in `observing` forever.
    """
    from app.intelligence.experiments.lifecycle import advance_experiment_for_publication
    from app.intelligence.experiments.outcome_bridge import run_outcome_bridge

    cp = _uid()
    young = _iso(datetime.now(UTC) - timedelta(hours=2))
    exp_id, pub_id = _seed_lineage(db, cp_channel_id=cp, published_at=young)
    _activate_observation(db, pub_id, cp)
    _seed_contract(db, exp_id)
    _seed_aggregate(db, publication_id=pub_id, topic_id=1, metric_name="views", value=500.0)
    _seed_aggregate(
        db,
        publication_id=pub_id,
        topic_id=1,
        metric_name="average_view_percentage",
        value=42.0,
    )
    advance_experiment_for_publication(db, pub_id)

    # Plenty of views, but only two hours old: real evidence, not yet mature.
    first = run_outcome_bridge(db, publication_id=pub_id)
    assert first.outcome_readiness == "evaluable_provisional"
    assert _experiment_status(db, exp_id) == "observing"

    # The same metrics, three days later — no new data at all, only age.
    old = _iso(datetime.now(UTC) - timedelta(days=3))
    db.execute("UPDATE publications SET published_at = ? WHERE id = ?", (old, pub_id))
    db.commit()

    second = run_outcome_bridge(db, publication_id=pub_id)
    assert second.outcome_readiness == "evaluable_mature"
    assert _experiment_status(db, exp_id) == "analyzed"


def test_missing_execution_contract_reports_unevaluable_rather_than_crashing(db):
    from app.intelligence.experiments.lifecycle import advance_experiment_for_publication
    from app.intelligence.experiments.outcome_bridge import run_outcome_bridge

    cp = _uid()
    old = _iso(datetime.now(UTC) - timedelta(days=5))
    exp_id, pub_id = _seed_lineage(db, cp_channel_id=cp, published_at=old)
    _activate_observation(db, pub_id, cp)
    _seed_aggregate(db, publication_id=pub_id, topic_id=1, metric_name="views", value=500.0)
    advance_experiment_for_publication(db, pub_id)

    result = run_outcome_bridge(db, publication_id=pub_id)

    assert result.outcome_readiness == "invalid_execution"
    assert result.outcome_persisted is False
    assert _experiment_status(db, exp_id) == "observing"


# ═════════════════════════════════════════════════════════════════════════════
# Execution fidelity — the last blocker on the loop
# ═════════════════════════════════════════════════════════════════════════════


def _seed_cluster(conn: sqlite3.Connection, cluster_id: int, label: str) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO market_canonical_clusters
           (id, platform, provider, canonical_label, normalized_label,
            semantic_fingerprint, identity_version, created_at, updated_at)
           VALUES (?, 'youtube', 'youtube_data_api', ?, ?, ?, 'v1', ?, ?)""",
        (cluster_id, label, label, _uid()[:16], _now(), _now()),
    )
    conn.commit()


def test_market_theme_evaluator_matches_an_on_theme_script(db):
    """A market-exploration experiment's treatment IS its cluster, so fidelity
    has to be able to ask whether the produced video is about that cluster.

    Nothing ever supplied an evaluator, so the engine abstained (correctly —
    absence must not default to VALID) and every autonomous market experiment
    was UNRESOLVED, hence INVALID_EXECUTION, hence unable to ever mature.
    """
    from app.intelligence.experiments.market_theme_fidelity import evaluate_market_theme

    _seed_cluster(db, 1, "science technology explained")
    body = (
        "# Science & Technology Explained in 60 Seconds\n"
        "Ever wonder how the technology in your pocket actually works? "
        "Science and tech shape everything around us."
    )
    assert evaluate_market_theme(db, 1, body) == "matched"


def test_market_theme_evaluator_flags_an_off_theme_script(db):
    from app.intelligence.experiments.market_theme_fidelity import evaluate_market_theme

    _seed_cluster(db, 1, "science technology explained")
    body = "A gentle guide to sourdough starters, hydration ratios and oven spring."
    assert evaluate_market_theme(db, 1, body) == "deviated"


def test_market_theme_evaluator_abstains_rather_than_guessing(db):
    """Honest abstention: unknown cluster is unresolved, absent script is
    'not yet available'. Neither may be reported as a match."""
    from app.intelligence.experiments.market_theme_fidelity import evaluate_market_theme

    _seed_cluster(db, 1, "science technology explained")
    assert evaluate_market_theme(db, 999, "anything") == "unresolved"
    assert evaluate_market_theme(db, 1, None) == "not_yet_available"
    assert evaluate_market_theme(db, 1, "   ") == "unresolved"


def test_market_theme_evaluation_is_deterministic(db):
    """No LLM, no network — the same inputs must always give the same verdict,
    so a fidelity decision can be re-derived and audited later."""
    from app.intelligence.experiments.market_theme_fidelity import evaluate_market_theme

    _seed_cluster(db, 1, "science technology explained")
    body = "Science and technology explained simply."
    verdicts = {evaluate_market_theme(db, 1, body) for _ in range(10)}
    assert verdicts == {"matched"}


def test_control_factor_without_a_baseline_is_a_warning_not_unresolved():
    """A control claim that was never made cannot have been violated.

    On a new channel most controlled factors legitimately have no baseline
    (baseline_source='unknown'). Treating that identically to "we cannot
    observe what happened" classified every experiment on a bootstrapping
    channel as UNRESOLVED — permanently, since a channel with no history is
    exactly the one with no baselines.
    """
    from app.intelligence.experiments.execution_contract import FidelityOutcome
    from app.intelligence.experiments.execution_service import (
        NO_BASELINE_DECLARED,
        _evaluate_fidelity_outcome,
    )

    outcome, reason = _evaluate_fidelity_outcome("narration_speaking_rate", None, True, "1.0")
    assert outcome == FidelityOutcome.NOT_OBSERVABLE
    assert reason == NO_BASELINE_DECLARED


def test_a_declared_baseline_that_cannot_be_read_is_still_unresolved():
    """The distinction must not become a blanket excuse: an experiment that
    DID declare a baseline and cannot verify it is genuinely unassessable."""
    from app.intelligence.experiments.execution_contract import FidelityOutcome
    from app.intelligence.experiments.execution_service import (
        NO_BASELINE_DECLARED,
        _evaluate_fidelity_outcome,
    )

    outcome, reason = _evaluate_fidelity_outcome("narration_speaking_rate", "1.0", True, None)
    assert outcome == FidelityOutcome.NOT_OBSERVABLE
    assert reason != NO_BASELINE_DECLARED


def test_declared_baseline_drift_is_still_detected():
    from app.intelligence.experiments.execution_contract import FidelityOutcome
    from app.intelligence.experiments.execution_service import _evaluate_fidelity_outcome

    outcome, _reason = _evaluate_fidelity_outcome("narration_speaking_rate", "1.0", True, "1.4")
    assert outcome == FidelityOutcome.DEVIATED


# ═════════════════════════════════════════════════════════════════════════════
# Learning → planner bridge
# ═════════════════════════════════════════════════════════════════════════════


def _seed_feature_observation(
    conn: sqlite3.Connection,
    *,
    cp_channel_id: str,
    feature_name: str = "narration_speaking_rate",
    bucket: str = "1–1.1",
    maturity: str = "directional",
) -> None:
    conn.execute(
        """INSERT INTO feature_performance_observations
           (channel_id, workspace_id, feature_name, feature_bucket, metric_name,
            observation_type, publication_count, mean, median, min_value, max_value,
            std_dev, sample_maturity, source_publication_ids_json, source_snapshot_ids_json,
            comparison_schema_version, observer_version, input_hash, created_at, updated_at)
           VALUES (?, 'ws', ?, ?, 'average_view_percentage', 'feature_bucket', 5,
                   50.0, 50.0, 40.0, 60.0, 5.0, ?, '[]', '[]', 'v1', 'v1', ?, ?, ?)""",
        (cp_channel_id, feature_name, bucket, maturity, _uid()[:16], _now(), _now()),
    )
    conn.commit()


def test_planner_reads_learning_evidence_through_the_channel_bridge(db):
    """The namespace defect: learning is keyed by the control-plane UUID and
    the planner runs in the intelligence integer namespace.

    Passing str(intel_channel_id) matched nothing, so every planning run saw
    an empty coverage map no matter how much the channel had learned.
    """
    from app.learning.cross_publication import get_exploration_coverage

    cp = _uid()
    _seed_lineage(db, cp_channel_id=cp)
    _seed_feature_observation(db, cp_channel_id=cp)

    # The pre-fix lookup — the intelligence id stringified — finds nothing.
    assert get_exploration_coverage(db, channel_id="1") == {}
    # The real key finds the channel's evidence.
    assert "narration_speaking_rate" in get_exploration_coverage(db, channel_id=cp)

    from app.intelligence.experiments.planning_service import _cp_channel_id_for

    assert _cp_channel_id_for(db, 1) == cp


def test_channel_evidence_maturity_comes_from_the_channels_own_baseline(db):
    from app.intelligence.experiments.planning_service import _channel_evidence_maturity

    cp = _uid()
    _seed_lineage(db, cp_channel_id=cp)

    # No baseline at all is 'insufficient', never an optimistic default.
    assert _channel_evidence_maturity(db, cp, "average_view_percentage") == "insufficient"

    db.execute(
        """INSERT INTO channel_performance_baselines
           (channel_id, workspace_id, metric_name, period_type, publication_count,
            mean, median, min_value, max_value, std_dev, sample_maturity,
            source_publication_ids_json, source_snapshot_ids_json,
            comparison_schema_version, observer_version, input_hash, created_at, updated_at)
           VALUES (?, 'ws', 'average_view_percentage', 'lifetime', 6, 50.0, 50.0,
                   40.0, 60.0, 5.0, 'directional', '[]', '[]', 'v1', 'v1', ?, ?, ?)""",
        (cp, _uid()[:16], _now(), _now()),
    )
    db.commit()
    assert _channel_evidence_maturity(db, cp, "average_view_percentage") == "directional"


def test_learning_consumption_gates_experiment_completion(db):
    """`completed` means the evidence reached learning, not merely that an
    outcome was computed. Without that gate an experiment would either never
    complete — permanently blocking its opportunity — or complete before its
    evidence was usable."""
    from app.intelligence.experiments.outcome_bridge import learning_has_consumed

    cp = _uid()
    _exp_id, pub_id = _seed_lineage(db, cp_channel_id=cp)

    assert learning_has_consumed(db, cp_channel_id=cp, publication_id=pub_id) is False

    db.execute(
        """INSERT INTO channel_performance_baselines
           (channel_id, workspace_id, metric_name, period_type, publication_count,
            mean, median, min_value, max_value, std_dev, sample_maturity,
            source_publication_ids_json, source_snapshot_ids_json,
            comparison_schema_version, observer_version, input_hash, created_at, updated_at)
           VALUES (?, 'ws', 'views', 'lifetime', 1, 10.0, 10.0, 10.0, 10.0, 0.0,
                   'insufficient', ?, '[]', 'v1', 'v1', ?, ?, ?)""",
        (cp, json.dumps([pub_id]), _uid()[:16], _now(), _now()),
    )
    db.commit()

    assert learning_has_consumed(db, cp_channel_id=cp, publication_id=pub_id) is True


# ═════════════════════════════════════════════════════════════════════════════
# Queue continuity
# ═════════════════════════════════════════════════════════════════════════════


def _insert_slot(
    conn: sqlite3.Connection,
    *,
    cp_channel_id: str,
    slot_key: str,
    publish_status: str | None = None,
    production_status: str | None = None,
    state: str = "filled",
) -> int:
    now = _now()
    conn.execute(
        """INSERT INTO publishing_slots
           (channel_id, workspace_id, slot_key, scheduled_for_local, timezone,
            scheduled_for_utc, state, reserved_at, filled_at, created_at, updated_at,
            production_status, publish_status)
           VALUES (?, 'local-dev', ?, ?, 'UTC', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            cp_channel_id,
            slot_key,
            now,
            now,
            state,
            now,
            now,
            now,
            now,
            production_status,
            publish_status,
        ),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]


def test_released_slot_leaves_the_queue(db):
    """The deadlock: a released slot kept state='filled' forever, so with a
    queue target of 1 the decision cycle returned QUEUE_ALREADY_SATISFIED for
    the rest of the channel's life and no further experiment was ever planned.
    """
    from app.intelligence.autonomy.repository import list_active_slots

    cp = _uid()
    _insert_slot(
        db, cp_channel_id=cp, slot_key="done", publish_status="released", production_status="ready"
    )

    assert list_active_slots(db, cp) == []


def test_missed_slot_leaves_the_queue_but_stays_on_the_record(db):
    """A missed slot must stop consuming capacity without being erased —
    the historical record of what the channel did has to survive."""
    from app.intelligence.autonomy.repository import list_active_slots, list_slots_for_channel

    cp = _uid()
    slot_id = _insert_slot(db, cp_channel_id=cp, slot_key="missed", publish_status="skipped_missed")

    assert list_active_slots(db, cp) == []
    assert [s.id for s in list_slots_for_channel(db, cp)] == [slot_id]
    assert (
        db.execute("SELECT state FROM publishing_slots WHERE id = ?", (slot_id,)).fetchone()[
            "state"
        ]
        == "filled"
    )


def test_in_flight_slot_still_occupies_the_queue(db):
    from app.intelligence.autonomy.repository import list_active_slots

    cp = _uid()
    _insert_slot(db, cp_channel_id=cp, slot_key="working", production_status="producing")
    assert len(list_active_slots(db, cp)) == 1


def test_retired_missed_slot_is_not_eligible_for_production(db):
    """The production-storm bug: handing a missed slot's lineage to its
    replacement clears the old slot's production columns, which made it look
    like fresh work forever after."""
    from app.intelligence.autonomy.repository import find_slot_needing_production

    cp = _uid()
    _insert_slot(
        db,
        cp_channel_id=cp,
        slot_key="retired",
        publish_status="skipped_missed",
        production_status=None,
    )

    assert find_slot_needing_production(db, cp) is None


def test_released_slot_frees_capacity_for_the_next_decision(db):
    """Steady state: one publication observing, one future slot selectable."""
    from app.intelligence.autonomy.repository import list_active_slots

    cp = _uid()
    _insert_slot(
        db,
        cp_channel_id=cp,
        slot_key="published",
        publish_status="released",
        production_status="ready",
    )
    active = list_active_slots(db, cp)
    assert len(active) == 0, "a released slot must not count toward queue_target"

    _insert_slot(db, cp_channel_id=cp, slot_key="next", state="reserved")
    assert len(list_active_slots(db, cp)) == 1


def test_queue_queries_are_channel_scoped(db):
    from app.intelligence.autonomy.repository import list_active_slots

    cp_a, cp_b = _uid(), _uid()
    _insert_slot(db, cp_channel_id=cp_a, slot_key="a-1", production_status="producing")
    _insert_slot(db, cp_channel_id=cp_b, slot_key="b-1", production_status="producing")

    assert len(list_active_slots(db, cp_a)) == 1
    assert len(list_active_slots(db, cp_b)) == 1


# ═════════════════════════════════════════════════════════════════════════════
# Scheduler cadence
# ═════════════════════════════════════════════════════════════════════════════


def test_interval_schedules_use_their_configured_interval():
    """The cadence defect: compute_next_run_at read `seconds` while every
    stored schedule writes `interval_seconds`, so every interval schedule
    silently fell back to daily — an hourly decision cycle ran once a day and
    a 10-minute publishing cycle could sleep through its slot's entire grace
    window."""
    from app.workers.scheduler import compute_next_run_at

    now = datetime.now(UTC)
    result = compute_next_run_at({"interval_seconds": 600}, "interval")
    delta = datetime.fromisoformat(result).replace(tzinfo=UTC) - now
    assert timedelta(seconds=595) <= delta <= timedelta(seconds=605)


def test_interval_schedules_still_accept_the_legacy_key():
    from app.workers.scheduler import compute_next_run_at

    now = datetime.now(UTC)
    result = compute_next_run_at({"seconds": 3600}, "interval")
    delta = datetime.fromisoformat(result).replace(tzinfo=UTC) - now
    assert timedelta(seconds=3595) <= delta <= timedelta(seconds=3605)


def test_interval_schedule_without_any_key_falls_back_to_daily():
    from app.workers.scheduler import compute_next_run_at

    now = datetime.now(UTC)
    result = compute_next_run_at({}, "interval")
    delta = datetime.fromisoformat(result).replace(tzinfo=UTC) - now
    assert timedelta(hours=23, minutes=59) <= delta <= timedelta(hours=24, minutes=1)


# ═════════════════════════════════════════════════════════════════════════════
# Analytics aggregation
# ═════════════════════════════════════════════════════════════════════════════


def _metric(value: float, *, snapshot_id: int, start: str, end: str):
    from app.analytics.models import AnalyticsMetric

    return AnalyticsMetric(
        id=snapshot_id,
        snapshot_id=snapshot_id,
        publication_id=1,
        topic_id=1,
        provider="youtube",
        metric_name="views",
        metric_value=value,
        period_start=start,
        period_end=end,
        input_hash=f"h{snapshot_id}",
        created_at="2026-08-29T00:00:00",
    )


def test_cumulative_observation_windows_are_not_double_counted():
    """The observer always queries published_at → today, so successive
    observations produce nested windows sharing a period_start. Summing them
    inflated a 474-view video to 948, which then corrupted channel baselines,
    min-views maturity gates, and every outcome computed from them."""
    from app.analytics.aggregation import _reduce_metrics

    rows = [
        _metric(474.0, snapshot_id=7, start="2026-08-17", end="2026-08-28"),
        _metric(474.0, snapshot_id=8, start="2026-08-17", end="2026-08-29"),
    ]
    result = _reduce_metrics("views", rows, {})
    assert result.value == 474.0


def test_genuinely_disjoint_periods_are_still_summed():
    """The fix must not break real additive aggregation across distinct periods."""
    from app.analytics.aggregation import _reduce_metrics

    rows = [
        _metric(100.0, snapshot_id=1, start="2026-08-01", end="2026-08-07"),
        _metric(150.0, snapshot_id=2, start="2026-08-08", end="2026-08-14"),
    ]
    result = _reduce_metrics("views", rows, {})
    assert result.value == 250.0


def test_latest_observation_of_a_window_supersedes_the_earlier_one():
    from app.analytics.aggregation import _reduce_metrics

    rows = [
        _metric(10.0, snapshot_id=1, start="2026-08-17", end="2026-08-20"),
        _metric(88.0, snapshot_id=2, start="2026-08-17", end="2026-08-29"),
        _metric(45.0, snapshot_id=3, start="2026-08-17", end="2026-08-25"),
    ]
    result = _reduce_metrics("views", rows, {})
    assert result.value == 88.0


# ═════════════════════════════════════════════════════════════════════════════
# Multi-channel isolation
# ═════════════════════════════════════════════════════════════════════════════


def test_reconciliation_does_not_cross_channel_boundaries(db):
    """Each channel's ledger advances only from its own publications."""
    from app.intelligence.experiments.lifecycle import reconcile_experiment_lifecycle

    cp_a, cp_b = _uid(), _uid()
    exp_a, _ = _seed_lineage(
        db, cp_channel_id=cp_a, intel_channel_id=1, topic_id=1, opportunity_id=1
    )
    exp_b, _ = _seed_lineage(
        db,
        cp_channel_id=cp_b,
        intel_channel_id=2,
        topic_id=2,
        opportunity_id=2,
        visibility="private",
    )

    reconcile_experiment_lifecycle(db)

    assert _experiment_status(db, exp_a) == "published"
    assert _experiment_status(db, exp_b) == "in_production"


def test_learning_evidence_is_channel_scoped(db):
    """One channel's feature observations must never appear in another's."""
    from app.learning.cross_publication import get_exploration_coverage

    cp_a, cp_b = _uid(), _uid()
    _seed_feature_observation(db, cp_channel_id=cp_a, feature_name="narration_speaking_rate")
    _seed_feature_observation(db, cp_channel_id=cp_b, feature_name="has_hook")

    assert set(get_exploration_coverage(db, channel_id=cp_a)) == {"narration_speaking_rate"}
    assert set(get_exploration_coverage(db, channel_id=cp_b)) == {"has_hook"}


def test_learning_consumption_check_is_channel_scoped(db):
    from app.intelligence.experiments.outcome_bridge import learning_has_consumed

    cp_a, cp_b = _uid(), _uid()
    _exp, pub_id = _seed_lineage(db, cp_channel_id=cp_a)
    db.execute(
        """INSERT INTO channel_performance_baselines
           (channel_id, workspace_id, metric_name, period_type, publication_count,
            mean, median, min_value, max_value, std_dev, sample_maturity,
            source_publication_ids_json, source_snapshot_ids_json,
            comparison_schema_version, observer_version, input_hash, created_at, updated_at)
           VALUES (?, 'ws', 'views', 'lifetime', 1, 10.0, 10.0, 10.0, 10.0, 0.0,
                   'insufficient', ?, '[]', 'v1', 'v1', ?, ?, ?)""",
        (cp_a, json.dumps([pub_id]), _uid()[:16], _now(), _now()),
    )
    db.commit()

    assert learning_has_consumed(db, cp_channel_id=cp_a, publication_id=pub_id) is True
    assert learning_has_consumed(db, cp_channel_id=cp_b, publication_id=pub_id) is False
