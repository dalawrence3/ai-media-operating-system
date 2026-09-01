"""Phase 17G — Autonomy readiness projection.

get_autonomy_readiness() is read-only and must never spend an LLM or
YouTube call itself — it only reads config and persisted state (including
the Phase 17G semantic-fit cache via ai_provider=None). It must distinguish
READY FOR DECISION AUTOMATION (the pipeline is operational) from
AUTHORIZED FOR PUBLIC PUBLISHING (every one of the four authorization
layers passes) — these are never conflated.

Tests:
  A  all-green channel -> ready_for_decision_automation is True
  B  missing YouTube key -> market_intelligence_configured is False, overall not ready
  C  no active strategy profile -> strategy_profile_active is False, overall not ready
  D  no recurring market_refresh schedule -> that check is False
  E  global publishing gates alone do NOT authorize publishing — the
     per-channel grant and the runtime checks are separate layers, and
     decision-automation readiness is unaffected either way
  F  eligible_opportunities_available reflects a real cached eligibility result
  G  readiness computation spends zero LLM calls (uses cache only)
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.application.autonomy_readiness import get_autonomy_readiness
from app.application.scheduler import create_schedule
from app.control_plane import repository as cp_repo
from app.control_plane import services as cp_services
from app.control_plane.models import ChannelDraft, WorkspaceDraft
from app.core.config import reset_config
from app.core.database import open_db
from app.intelligence.channel_bridge import bootstrap_intelligence_channel


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _reset_config():
    reset_config()
    yield
    reset_config()


@pytest.fixture()
def db_conn(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ACE_DB_PATH", str(tmp_path / "readiness_test.db"))
    conn = open_db(tmp_path / "readiness_test.db")
    yield conn
    conn.close()


@pytest.fixture()
def workspace(db_conn):
    ws = cp_repo.create_workspace(
        db_conn,
        WorkspaceDraft(id=_uid(), name="Test WS", slug=f"test-ws-{_uid()[:8]}", actor="cli"),
    )
    db_conn.commit()
    return ws


@pytest.fixture()
def channel(db_conn, workspace):
    ch = cp_repo.create_channel(
        db_conn,
        ChannelDraft(
            id=_uid(),
            workspace_id=workspace.id,
            name="Test Channel",
            slug="test-channel",
            actor="cli",
        ),
    )
    db_conn.commit()
    bootstrap_intelligence_channel(db_conn, ch.id, channel_name="Test Channel")
    db_conn.commit()
    return ch


def _default_strategy_config() -> dict:
    return {
        "bootstrap": {
            "target_publication_count": 18,
            "market_intelligence_weight": 0.8,
            "channel_evidence_weight": 0.2,
            "exploration_share": 0.67,
        },
        "steady_state": {
            "market_intelligence_weight": 0.4,
            "channel_evidence_weight": 0.6,
            "exploration_share": 0.2,
        },
        "transition": {
            "trigger_metric": "average_view_percentage",
            "maturity_threshold": "directional",
        },
        "diversity": {"max_cluster_share": 0.4, "max_consecutive_same_cluster": 2},
        "creative_dimensions": [],
        "total_portfolio_slots": 3,
    }


def _create_market_refresh_schedule(conn, workspace_id: str, channel_id: str):
    return create_schedule(
        conn,
        workspace_id=workspace_id,
        name="market refresh",
        operation_type="market_refresh",
        schedule_type="interval",
        schedule_config={"interval_seconds": 21600},
        actor="test",
        channel_id=channel_id,
    )


def _create_analytics_observation_schedule(conn, workspace_id: str, channel_id: str):
    return create_schedule(
        conn,
        workspace_id=workspace_id,
        name="analytics observation",
        operation_type="analytics_observation",
        schedule_type="interval",
        schedule_config={"interval_seconds": 3600},
        actor="test",
        channel_id=channel_id,
    )


def _make_ready_channel(db_conn, workspace, channel, monkeypatch):
    monkeypatch.setenv("ACE_YOUTUBE_API_KEY", "fake-test-key-not-real")
    reset_config()
    cp_services.create_channel_strategy_version(
        db_conn, channel.id, _default_strategy_config(), actor="test"
    )
    _create_market_refresh_schedule(db_conn, workspace.id, channel.id)
    _create_analytics_observation_schedule(db_conn, workspace.id, channel.id)
    db_conn.commit()


def _seed_eligible_opportunity_stub(db_conn, channel, monkeypatch) -> None:
    """Seed one bare opportunity and stub assess_experiment_eligibility to
    GENERAL_ELIGIBLE — see _seed_bare_opportunity's docstring for why the
    full eligibility chain isn't re-exercised in this aggregation-focused
    test file."""
    from app.intelligence.channel_bridge import get_intelligence_channel_id
    from app.intelligence.experiments import eligibility_service as elig_svc
    from app.intelligence.experiments.eligibility import (
        ExperimentEligibilityAssessment,
        ExperimentEligibilityClassification,
    )

    intel_channel_id = get_intelligence_channel_id(db_conn, channel.id)
    _seed_bare_opportunity(db_conn, intel_channel_id)

    def _fake_eligible(conn, opp_id, ch_id, ai_provider=None, policy=None):
        return ExperimentEligibilityAssessment(
            opportunity_id=opp_id,
            channel_id=ch_id,
            classification=ExperimentEligibilityClassification.GENERAL_ELIGIBLE,
            findings=[],
            policy_snapshot_json="{}",
            assessed_at="2026-01-01T00:00:00",
        )

    monkeypatch.setattr(elig_svc, "assess_experiment_eligibility", _fake_eligible)


def test_A_all_green_channel_is_ready_for_decision_automation(
    db_conn, workspace, channel, monkeypatch
):
    _make_ready_channel(db_conn, workspace, channel, monkeypatch)
    _seed_eligible_opportunity_stub(db_conn, channel, monkeypatch)
    view = get_autonomy_readiness(db_conn, workspace.id, channel.id)
    by_key = {c.key: c.ready for c in view.checks}
    assert by_key["market_intelligence_configured"] is True
    assert by_key["recurring_market_refresh"] is True
    assert by_key["strategy_profile_active"] is True
    assert by_key["analytics_observer_active"] is True
    assert view.ready_for_decision_automation is True
    # No global gates, no channel grant, no connected account — publishing
    # must read as unauthorized, and the check reports it that way round
    # (Phase 18D removed the inverted 'publishing_not_enabled' check, which
    # went red exactly when a channel became correctly authorized).
    assert by_key["public_publishing_authorized"] is False
    assert view.authorized_for_public_publishing is False

    # Every check belongs to a category, and categories roll up to their
    # worst member. This channel has every decision INPUT ready but no
    # autonomy policy configured, so the category is degraded while
    # ready_for_decision_automation (which measures inputs only) stays True.
    by_cat = {c.key: c.status for c in view.categories}
    assert by_cat["decision"] == "degraded"
    assert set(by_cat) >= {
        "decision",
        "production",
        "analytics_learning",
        "provider_oauth",
        "publishing_authorization",
        "scheduler",
    }


def test_B_missing_youtube_key_blocks_readiness(db_conn, workspace, channel, monkeypatch):
    monkeypatch.delenv("ACE_YOUTUBE_API_KEY", raising=False)
    reset_config()
    cp_services.create_channel_strategy_version(
        db_conn, channel.id, _default_strategy_config(), actor="test"
    )
    _create_market_refresh_schedule(db_conn, workspace.id, channel.id)
    _create_analytics_observation_schedule(db_conn, workspace.id, channel.id)
    db_conn.commit()

    view = get_autonomy_readiness(db_conn, workspace.id, channel.id)
    by_key = {c.key: c.ready for c in view.checks}
    assert by_key["market_intelligence_configured"] is False
    assert view.ready_for_decision_automation is False


def test_C_no_strategy_profile_blocks_readiness(db_conn, workspace, channel, monkeypatch):
    monkeypatch.setenv("ACE_YOUTUBE_API_KEY", "fake-test-key-not-real")
    reset_config()
    _create_market_refresh_schedule(db_conn, workspace.id, channel.id)
    _create_analytics_observation_schedule(db_conn, workspace.id, channel.id)
    db_conn.commit()

    view = get_autonomy_readiness(db_conn, workspace.id, channel.id)
    by_key = {c.key: c.ready for c in view.checks}
    assert by_key["strategy_profile_active"] is False
    assert view.ready_for_decision_automation is False


def test_D_no_recurring_refresh_schedule_blocks_readiness(db_conn, workspace, channel, monkeypatch):
    monkeypatch.setenv("ACE_YOUTUBE_API_KEY", "fake-test-key-not-real")
    reset_config()
    cp_services.create_channel_strategy_version(
        db_conn, channel.id, _default_strategy_config(), actor="test"
    )
    _create_analytics_observation_schedule(db_conn, workspace.id, channel.id)
    db_conn.commit()

    view = get_autonomy_readiness(db_conn, workspace.id, channel.id)
    by_key = {c.key: c.ready for c in view.checks}
    assert by_key["recurring_market_refresh"] is False
    assert view.ready_for_decision_automation is False


def test_E_global_gates_alone_do_not_authorize_publishing(
    db_conn,
    workspace,
    channel,
    monkeypatch,
):
    """Turning both env gates on must not, by itself, report authorization.

    The gates are two of four layers; the per-channel grant and the runtime
    account/rate checks are the other two. Phase 17G's view conflated
    "gates are on" with "authorized", which would have told an operator a
    channel could publish when no account was even connected.
    """
    _make_ready_channel(db_conn, workspace, channel, monkeypatch)
    _seed_eligible_opportunity_stub(db_conn, channel, monkeypatch)
    monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
    monkeypatch.setenv("ACE_RELEASE_PUBLIC_ENABLED", "true")
    reset_config()

    view = get_autonomy_readiness(db_conn, workspace.id, channel.id)
    by_key = {c.key: c.ready for c in view.checks}
    assert by_key["public_publishing_authorized"] is False
    assert view.authorized_for_public_publishing is False

    detail = next(c.detail for c in view.checks if c.key == "public_publishing_authorized")
    assert "channel_not_authorized" in detail or "not authorized" in detail.lower()

    # Decision-automation readiness is unaffected by the publishing gates —
    # it is deliberately a separate concern.
    assert view.ready_for_decision_automation is True


def test_E2_global_gates_state_is_reported_without_being_an_error(
    db_conn,
    workspace,
    channel,
    monkeypatch,
):
    """Both gates off is a valid operating state, not a failed check.

    Standing the system down must not make the readiness page look broken,
    and neither must activating it. The gates check reports position.
    """
    _make_ready_channel(db_conn, workspace, channel, monkeypatch)
    _seed_eligible_opportunity_stub(db_conn, channel, monkeypatch)
    monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "false")
    monkeypatch.setenv("ACE_RELEASE_PUBLIC_ENABLED", "false")
    reset_config()
    view_off = get_autonomy_readiness(db_conn, workspace.id, channel.id)
    gates_off = next(c for c in view_off.checks if c.key == "global_publishing_gates")
    assert gates_off.status == "ready"
    assert "OFF" in gates_off.detail

    monkeypatch.setenv("ACE_PUBLISHING_LIVE_ENABLED", "true")
    monkeypatch.setenv("ACE_RELEASE_PUBLIC_ENABLED", "true")
    reset_config()
    view_on = get_autonomy_readiness(db_conn, workspace.id, channel.id)
    gates_on = next(c for c in view_on.checks if c.key == "global_publishing_gates")
    assert gates_on.status == "ready"
    assert "ON" in gates_on.detail


def test_F2_rate_limited_channel_still_reports_as_authorized(
    db_conn,
    workspace,
    channel,
    monkeypatch,
):
    """Being at the 24h ceiling is not the same as being unauthorized.

    A channel that has published its allowance is fully authorized and simply
    rate-limited. Reporting that as "Not authorized" would tell an operator
    their correctly-activated channel had been switched off — the same
    conflation this module removed from the inverted Phase 17G gate check.

    The check's own status stays `degraded`, because "can it publish right
    now" genuinely is no; the view-level flag answers the different question,
    "is it authorized to publish at all".
    """
    from app.application.autonomy_readiness import get_autonomy_readiness as _readiness
    from app.publishing.authorization import BlockReason

    _make_ready_channel(db_conn, workspace, channel, monkeypatch)
    _seed_eligible_opportunity_stub(db_conn, channel, monkeypatch)

    class _Decision:
        allowed = False
        blocked_by = [BlockReason.rate_limit_reached]
        detail = "Rate limit reached: 1/1 publications in the last 24h."
        publications_last_24h = 1
        max_publications_per_24h = 1

    import app.application.autonomy_readiness as mod

    monkeypatch.setattr(
        mod,
        "_publishing_authorization_check",
        lambda conn, ch: mod._check(
            key="public_publishing_authorized",
            label="Autonomous public publishing authorized",
            status=mod.STATUS_DEGRADED,
            detail=_Decision.detail,
            category=mod.CAT_PUBLISHING,
        ),
    )

    view = _readiness(db_conn, workspace.id, channel.id)
    check = next(c for c in view.checks if c.key == "public_publishing_authorized")

    assert check.status == "degraded", "cannot publish this instant"
    assert view.authorized_for_public_publishing is True, "but it IS authorized"


def test_F3_a_genuinely_blocked_channel_is_not_reported_as_authorized(
    db_conn,
    workspace,
    channel,
    monkeypatch,
):
    """The distinction must not become a blanket pass: a missing grant, a
    closed gate or an unhealthy account still reads as unauthorized."""
    from app.application.autonomy_readiness import get_autonomy_readiness as _readiness

    _make_ready_channel(db_conn, workspace, channel, monkeypatch)
    _seed_eligible_opportunity_stub(db_conn, channel, monkeypatch)

    view = _readiness(db_conn, workspace.id, channel.id)
    check = next(c for c in view.checks if c.key == "public_publishing_authorized")

    assert check.status == "blocked"
    assert view.authorized_for_public_publishing is False


def _seed_bare_opportunity(conn, intel_channel_id: int, opp_id: int = 1) -> None:
    """Minimal opportunity row — foreign_keys off, discovery_run_id=0, matching
    the established pattern in test_three_channel_isolation_16b1.py. Deep
    eligibility-chain scenarios (market signal, semantic fit, classification
    roll-up) are exhaustively covered by test_semantic_fit_resolution_17g.py
    and test_experiment_eligibility_14c.py — this file only tests that
    get_autonomy_readiness correctly aggregates whatever
    assess_experiment_eligibility reports, so the opportunity content itself
    doesn't need to fully resolve GENERAL_ELIGIBLE on its own."""
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        """INSERT INTO opportunities
           (id, channel_id, discovery_run_id, normalized_topic, raw_topic, title,
            current_lifecycle_state, created_at, updated_at)
           VALUES (?, ?, 0, 'unique test topic', 'unique test topic', 'Test',
                   'new', datetime('now'), datetime('now'))""",
        (opp_id, intel_channel_id),
    )
    conn.commit()


def test_F_eligible_opportunities_check_reflects_real_eligibility_result(
    db_conn,
    workspace,
    channel,
    monkeypatch,
):
    """get_autonomy_readiness's job is aggregation, not eligibility
    computation — so this asserts it correctly reads whatever
    assess_experiment_eligibility (tested exhaustively elsewhere) reports,
    for both an ineligible-only state and an eligible state."""
    from app.intelligence.channel_bridge import get_intelligence_channel_id
    from app.intelligence.experiments import eligibility_service as elig_svc
    from app.intelligence.experiments.eligibility import (
        ExperimentEligibilityAssessment,
        ExperimentEligibilityClassification,
    )

    _make_ready_channel(db_conn, workspace, channel, monkeypatch)
    intel_channel_id = get_intelligence_channel_id(db_conn, channel.id)
    _seed_bare_opportunity(db_conn, intel_channel_id)

    def _fake_ineligible(conn, opp_id, ch_id, ai_provider=None, policy=None):
        return ExperimentEligibilityAssessment(
            opportunity_id=opp_id,
            channel_id=ch_id,
            classification=ExperimentEligibilityClassification.UNRESOLVED,
            findings=[],
            policy_snapshot_json="{}",
            assessed_at="2026-01-01T00:00:00",
        )

    monkeypatch.setattr(elig_svc, "assess_experiment_eligibility", _fake_ineligible)
    view_before = get_autonomy_readiness(db_conn, workspace.id, channel.id)
    by_key_before = {c.key: c.ready for c in view_before.checks}
    assert by_key_before["eligible_opportunities_available"] is False
    assert view_before.ready_for_decision_automation is False

    def _fake_eligible(conn, opp_id, ch_id, ai_provider=None, policy=None):
        return ExperimentEligibilityAssessment(
            opportunity_id=opp_id,
            channel_id=ch_id,
            classification=ExperimentEligibilityClassification.GENERAL_ELIGIBLE,
            findings=[],
            policy_snapshot_json="{}",
            assessed_at="2026-01-01T00:00:00",
        )

    monkeypatch.setattr(elig_svc, "assess_experiment_eligibility", _fake_eligible)
    view_after = get_autonomy_readiness(db_conn, workspace.id, channel.id)
    by_key_after = {c.key: c.ready for c in view_after.checks}
    assert by_key_after["eligible_opportunities_available"] is True
    assert view_after.ready_for_decision_automation is True


def test_G_readiness_computation_spends_zero_llm_calls(db_conn, workspace, channel, monkeypatch):
    """Regression guard: get_autonomy_readiness must never construct or call
    an AI provider — it only reads the persisted semantic-fit cache."""
    _make_ready_channel(db_conn, workspace, channel, monkeypatch)

    import app.ai.claude as claude_module

    def _boom(*args, **kwargs):
        raise AssertionError("get_autonomy_readiness must never construct a live AI provider")

    monkeypatch.setattr(claude_module.ClaudeProvider, "__init__", _boom)
    # Must not raise — proves the readiness path never reaches ClaudeProvider().
    get_autonomy_readiness(db_conn, workspace.id, channel.id)
