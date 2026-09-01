"""Phase 18B — Autonomous production cycle.

Covers slot->experiment->topic materialization (idempotent, correct
lineage, correct workspace ownership), the full stage-driving loop against
a real (but Claude/TTS-mocked) production pipeline, restart recovery at
several stage boundaries, duplicate-worker/concurrency protection, the
queue bound across decision+production, deadline status, preflight
validation, bounded retries, channel isolation, publishing-authorization
independence, and — critically — the absolute absence of any
publication/publishing_job/upload side effect.

Claude and TTS are mocked (a fake ClaudeProvider patched onto
app.ai.claude.ClaudeProvider, and ACE_TTS_LIVE_ENABLED left unset so the
existing FakeTTSProvider path is used automatically) so this suite spends
no real external cost — the real integration was independently verified
once, live, with real providers, against an isolated temp database (see
the Phase 18B report) before this suite was written.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from app.ai.provider import AIRequest, AIResponse
from app.control_plane import repository as cp_repo
from app.control_plane.models import ChannelDraft, WorkspaceDraft
from app.core.database import open_db
from app.intelligence.autonomy.models import DeadlineStatus, ProductionOutcome
from app.intelligence.autonomy.production_cycle import run_production_cycle
from app.intelligence.autonomy.repository import (
    get_slot,
    reserve_slot,
    upsert_autonomy_policy,
)
from app.intelligence.channel_bridge import (
    bootstrap_intelligence_channel,
    get_intelligence_channel_id,
)


def _uid() -> str:
    return str(uuid.uuid4())


class _FakeClaudeProvider:
    """Drop-in replacement for app.ai.claude.ClaudeProvider — same
    constructor shape, returns a valid LLMGeneratedScript payload for any
    request so ScriptGenerationExecutor's real code path runs unmodified."""

    name = "claude"

    def __init__(self, api_key: str = "", model: str = ""):
        self.api_key = api_key
        self.model = model
        self.call_count = 0

    def complete(self, request: AIRequest) -> AIResponse:
        self.call_count += 1
        payload = {
            "title": "Test Script Title",
            "sections": [
                {"section_type": "hook", "text": "A short, punchy hook.", "cited_claim_ids": []},
                {
                    "section_type": "body",
                    "text": "The main explanation body goes here.",
                    "cited_claim_ids": [],
                },
            ],
        }
        return AIResponse(
            raw_text=json.dumps(payload),
            provider_name="claude",
            model=request.model or self.model or "claude-fake-test-model",
            input_tokens=10,
            output_tokens=10,
            duration_ms=1,
            retry_count=0,
            parsed=None,
        )


@pytest.fixture(autouse=True)
def _fake_claude(monkeypatch):
    import app.ai.claude as claude_mod

    monkeypatch.setattr(claude_mod, "ClaudeProvider", _FakeClaudeProvider)
    yield


@pytest.fixture(autouse=True)
def _fake_tts(monkeypatch):
    # Ensure the FakeTTSProvider path is used regardless of the host's own
    # .env.local (this suite must never depend on ambient environment).
    monkeypatch.delenv("ACE_TTS_LIVE_ENABLED", raising=False)
    from app.core.config import reset_config

    reset_config()
    yield
    reset_config()


@pytest.fixture()
def db(tmp_path: Path):
    conn = open_db(tmp_path / "production_test.db")
    conn.execute("PRAGMA foreign_keys = OFF")
    yield conn
    conn.close()


@pytest.fixture()
def workspace(db):
    ws = cp_repo.create_workspace(
        db, WorkspaceDraft(id=_uid(), name="Test WS", slug=f"ws-{_uid()[:8]}", actor="cli")
    )
    db.commit()
    return ws


@pytest.fixture()
def channel(db, workspace):
    ch = cp_repo.create_channel(
        db,
        ChannelDraft(
            id=_uid(),
            workspace_id=workspace.id,
            name="Test Channel",
            slug="test-channel",
            actor="cli",
        ),
    )
    db.commit()
    bootstrap_intelligence_channel(db, ch.id, channel_name="Test Channel")
    db.commit()
    return ch


def _seed_voice_profile(conn: sqlite3.Connection, profile_id: int = 1) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO voice_profiles (id, provider, model, voice_id, name, is_default) "
        "VALUES (?, 'fake', 'fake', 'fake-voice', 'Test Voice', 1)",
        (profile_id,),
    )
    conn.commit()


def _seed_opportunity(conn: sqlite3.Connection, intel_channel_id: int, opp_id: int = 1) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        """INSERT INTO opportunities
           (id, channel_id, discovery_run_id, normalized_topic, raw_topic, title,
            current_lifecycle_state, created_at, updated_at)
           VALUES (?, ?, 0, 'unique test topic explained',
                   'unique test topic explained', 'Test Topic',
                   'new', datetime('now'), datetime('now'))""",
        (opp_id, intel_channel_id),
    )
    conn.commit()


def _seed_filled_slot_via_decision_cycle(
    db, channel, workspace, *, monkeypatch, opp_id: int = 1
) -> int:
    """Reuses the real (Phase 18A) decision cycle to produce a genuinely
    filled slot + strategy brief — the same path Orvella's real slot took."""
    from app.intelligence.autonomy.decision_cycle import run_decision_cycle
    from app.intelligence.experiments import eligibility_service as elig_svc
    from app.intelligence.experiments.eligibility import (
        ExperimentEligibilityAssessment,
        ExperimentEligibilityClassification,
    )

    intel_channel_id = get_intelligence_channel_id(db, channel.id)
    _seed_opportunity(db, intel_channel_id, opp_id=opp_id)

    def _fake_assess(conn, opportunity_id, ch_id, ai_provider=None, policy=None):
        return ExperimentEligibilityAssessment(
            opportunity_id=opportunity_id,
            channel_id=ch_id,
            classification=ExperimentEligibilityClassification.GENERAL_ELIGIBLE,
            findings=[],
            policy_snapshot_json="{}",
            assessed_at="2026-01-01T00:00:00",
            signal_maturity="directional",
            signal_confidence=0.7,
        )

    monkeypatch.setattr(elig_svc, "assess_experiment_eligibility", _fake_assess)

    upsert_autonomy_policy(
        db,
        channel_id=channel.id,
        workspace_id=workspace.id,
        actor="test",
        decision_automation_enabled=True,
        timezone="UTC",
        queue_target=1,
    )
    result = run_decision_cycle(db, cp_channel_id=channel.id, workspace_id=workspace.id)
    assert result.outcome.value == "selected", f"decision cycle setup failed: {result.reason}"
    return result.slot_id


def _enable_production(db, channel, workspace):
    upsert_autonomy_policy(
        db,
        channel_id=channel.id,
        workspace_id=workspace.id,
        actor="test",
        production_automation_enabled=True,
    )
    _seed_voice_profile(db)


# ---------------------------------------------------------------------------
# Disabled / no-slot / basic gating
# ---------------------------------------------------------------------------


def test_disabled_returns_disabled(db, channel, workspace):
    result = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert result.outcome == ProductionOutcome.DISABLED


def test_no_filled_slot_returns_no_slot_to_produce(db, channel, workspace):
    _enable_production(db, channel, workspace)
    result = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert result.outcome == ProductionOutcome.NO_SLOT_TO_PRODUCE


def test_publishing_authorization_independence(db, channel, workspace, monkeypatch):
    """Production automation must function identically regardless of the
    (always-false-in-this-phase) publishing gates — this module never reads
    them at all; the hard stop is structural, not gate-conditional."""
    monkeypatch.delenv("ACE_PUBLISHING_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("ACE_RELEASE_PUBLIC_ENABLED", raising=False)
    slot_id = _seed_filled_slot_via_decision_cycle(db, channel, workspace, monkeypatch=monkeypatch)
    _enable_production(db, channel, workspace)

    result = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert result.outcome == ProductionOutcome.READY
    assert result.slot_id == slot_id


# ---------------------------------------------------------------------------
# Full end-to-end happy path + absolute absence of upload side effects
# ---------------------------------------------------------------------------


def test_happy_path_reaches_ready_with_full_lineage(db, channel, workspace, monkeypatch):
    slot_id = _seed_filled_slot_via_decision_cycle(db, channel, workspace, monkeypatch=monkeypatch)
    _enable_production(db, channel, workspace)

    result = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )

    assert result.outcome == ProductionOutcome.READY
    assert result.stages_completed == [
        "research",
        "script_generation",
        "production_plan",
        "narration",
        "captions",
        "visual_intelligence",
        "rendering",
    ]
    assert result.preflight_passed is True
    assert result.publishing_plan_id is not None
    assert result.experiment_id is not None
    assert result.topic_id is not None
    assert result.pipeline_id is not None

    slot = get_slot(db, slot_id)
    assert slot.production_status == "ready"
    assert slot.experiment_id == result.experiment_id
    assert slot.production_publishing_plan_id == result.publishing_plan_id

    # Full lineage: opportunity -> brief -> experiment -> topic -> script.
    exp_row = db.execute(
        "SELECT opportunity_id, channel_id, status FROM experiments WHERE id = ?",
        (result.experiment_id,),
    ).fetchone()
    assert exp_row["opportunity_id"] == 1
    assert exp_row["status"] == "in_production"
    topic_row = db.execute(
        "SELECT promoted_opportunity_id, workspace_id FROM topics WHERE id = ?", (result.topic_id,)
    ).fetchone()
    assert topic_row["promoted_opportunity_id"] == 1
    assert topic_row["workspace_id"] == workspace.id  # not the known-bad NULL/unset linkage
    script_row = db.execute(
        "SELECT topic_id, status FROM scripts WHERE topic_id = ?", (result.topic_id,)
    ).fetchone()
    assert script_row["status"] == "approved"


def test_no_production_or_upload_side_effects(db, channel, workspace, monkeypatch):
    """Section 21/22's absolute prohibition: even a fully successful READY
    cycle must never create a publications row or a publishing_jobs row."""
    _seed_filled_slot_via_decision_cycle(db, channel, workspace, monkeypatch=monkeypatch)
    _enable_production(db, channel, workspace)

    result = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert result.outcome == ProductionOutcome.READY

    assert db.execute("SELECT COUNT(*) AS n FROM publications").fetchone()["n"] == 0
    assert db.execute("SELECT COUNT(*) AS n FROM publishing_jobs").fetchone()["n"] == 0
    plan = db.execute(
        "SELECT visibility, status FROM publishing_plans WHERE id = ?", (result.publishing_plan_id,)
    ).fetchone()
    assert plan["visibility"] == "private"
    assert plan["status"] == "draft"


def test_no_forbidden_publishing_imports_in_module_source():
    """Static, structural proof (section 21): the module's own source never
    references the live publishing orchestrator/upload-gate/provider
    modules — the hard stop is an architectural property, not a runtime
    'if gate == false' check buried in the same execution chain."""
    src = Path("src/app/intelligence/autonomy/production_cycle.py").read_text()
    # Strip the module docstring (which legitimately *names* the forbidden
    # modules to explain why they're absent) before searching for real imports.
    body = src.split('"""', 2)[-1]
    for forbidden in (
        "app.publishing.orchestrator",
        "app.publishing.upload_gate",
        "app.publishing.providers",
    ):
        assert forbidden not in body, (
            f"{forbidden} must never be imported by the autonomous production orchestrator"
        )


# ---------------------------------------------------------------------------
# Idempotency / restart recovery
# ---------------------------------------------------------------------------


def test_rerun_after_ready_is_a_pure_no_op(db, channel, workspace, monkeypatch):
    _seed_filled_slot_via_decision_cycle(db, channel, workspace, monkeypatch=monkeypatch)
    _enable_production(db, channel, workspace)

    first = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert first.outcome == ProductionOutcome.READY

    def _count_all():
        tables = [
            "experiments",
            "topics",
            "scripts",
            "production_plans",
            "narration_runs",
            "caption_runs",
            "scene_manifests",
            "render_jobs",
            "publishing_plans",
        ]
        return {t: db.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"] for t in tables}

    before = _count_all()
    second = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert second.outcome == ProductionOutcome.NO_SLOT_TO_PRODUCE
    assert _count_all() == before


def test_restart_after_experiment_materialization_resumes_not_restarts(
    db, channel, workspace, monkeypatch
):
    """Simulates a crash right after experiment+topic materialization (no
    pipeline started yet): re-running must reuse the SAME experiment/topic,
    never create a second pair."""
    _seed_filled_slot_via_decision_cycle(db, channel, workspace, monkeypatch=monkeypatch)
    _enable_production(db, channel, workspace)

    from app.intelligence.autonomy import production_cycle as pc_mod

    real_drive = pc_mod._drive_pipeline
    call_state = {"n": 0}

    def _boom_once(*args, **kwargs):
        call_state["n"] += 1
        if call_state["n"] == 1:
            raise RuntimeError("simulated crash after experiment/topic materialization")
        return real_drive(*args, **kwargs)

    monkeypatch.setattr(pc_mod, "_drive_pipeline", _boom_once)
    first = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert first.outcome == ProductionOutcome.FAILED

    exp_count_after_crash = db.execute("SELECT COUNT(*) AS n FROM experiments").fetchone()["n"]
    topic_count_after_crash = db.execute("SELECT COUNT(*) AS n FROM topics").fetchone()["n"]
    assert exp_count_after_crash == 1
    assert topic_count_after_crash == 1

    second = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert second.outcome == ProductionOutcome.READY
    assert db.execute("SELECT COUNT(*) AS n FROM experiments").fetchone()["n"] == 1
    assert db.execute("SELECT COUNT(*) AS n FROM topics").fetchone()["n"] == 1
    assert second.experiment_id == first.experiment_id or first.experiment_id is None


def test_restart_after_script_resumes_without_regenerating_it(db, channel, workspace, monkeypatch):
    """Simulates a crash right after script_generation completes: a fresh
    cycle must resume the SAME pipeline at production_plan, not regenerate
    the script (which would spend a second real LLM call in production)."""
    _seed_filled_slot_via_decision_cycle(db, channel, workspace, monkeypatch=monkeypatch)
    _enable_production(db, channel, workspace)

    from app.intelligence.autonomy import production_cycle as pc_mod

    original_approve = pc_mod._approve_artifact
    call_state = {"n": 0}

    def _crash_after_script_approval(conn, *, stage, artifact_type, artifact_id, actor):
        original_approve(
            conn, stage=stage, artifact_type=artifact_type, artifact_id=artifact_id, actor=actor
        )
        call_state["n"] += 1
        if stage == "script_generation" and call_state["n"] == 1:
            raise RuntimeError("simulated crash right after script approval")

    monkeypatch.setattr(pc_mod, "_approve_artifact", _crash_after_script_approval)
    first = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert first.outcome == ProductionOutcome.FAILED

    script_count = db.execute("SELECT COUNT(*) AS n FROM scripts").fetchone()["n"]
    assert script_count == 1

    monkeypatch.setattr(pc_mod, "_approve_artifact", original_approve)
    second = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert second.outcome == ProductionOutcome.READY
    assert db.execute("SELECT COUNT(*) AS n FROM scripts").fetchone()["n"] == 1


def test_restart_after_narration_resumes_without_regenerating_it(
    db, channel, workspace, monkeypatch
):
    _seed_filled_slot_via_decision_cycle(db, channel, workspace, monkeypatch=monkeypatch)
    _enable_production(db, channel, workspace)

    from app.intelligence.autonomy import production_cycle as pc_mod

    original_approve = pc_mod._approve_artifact
    call_state = {"n": 0}

    def _crash_after_narration_approval(conn, *, stage, artifact_type, artifact_id, actor):
        original_approve(
            conn, stage=stage, artifact_type=artifact_type, artifact_id=artifact_id, actor=actor
        )
        if stage == "narration":
            call_state["n"] += 1
            if call_state["n"] == 1:
                raise RuntimeError("simulated crash right after narration approval")

    monkeypatch.setattr(pc_mod, "_approve_artifact", _crash_after_narration_approval)
    first = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert first.outcome == ProductionOutcome.FAILED
    assert db.execute("SELECT COUNT(*) AS n FROM narration_runs").fetchone()["n"] == 1

    monkeypatch.setattr(pc_mod, "_approve_artifact", original_approve)
    second = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert second.outcome == ProductionOutcome.READY
    assert db.execute("SELECT COUNT(*) AS n FROM narration_runs").fetchone()["n"] == 1


def test_restart_after_visuals_resumes_without_regenerating_render(
    db, channel, workspace, monkeypatch
):
    _seed_filled_slot_via_decision_cycle(db, channel, workspace, monkeypatch=monkeypatch)
    _enable_production(db, channel, workspace)

    from app.intelligence.autonomy import production_cycle as pc_mod

    original_approve = pc_mod._approve_artifact
    call_state = {"n": 0}

    def _crash_after_visuals_approval(conn, *, stage, artifact_type, artifact_id, actor):
        original_approve(
            conn, stage=stage, artifact_type=artifact_type, artifact_id=artifact_id, actor=actor
        )
        if stage == "visual_intelligence":
            call_state["n"] += 1
            if call_state["n"] == 1:
                raise RuntimeError("simulated crash right after visual_intelligence approval")

    monkeypatch.setattr(pc_mod, "_approve_artifact", _crash_after_visuals_approval)
    first = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert first.outcome == ProductionOutcome.FAILED
    assert db.execute("SELECT COUNT(*) AS n FROM scene_manifests").fetchone()["n"] == 1

    monkeypatch.setattr(pc_mod, "_approve_artifact", original_approve)
    second = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert second.outcome == ProductionOutcome.READY
    assert db.execute("SELECT COUNT(*) AS n FROM scene_manifests").fetchone()["n"] == 1
    assert (
        db.execute("SELECT COUNT(*) AS n FROM render_jobs WHERE status = 'completed'").fetchone()[
            "n"
        ]
        == 1
    )


def test_restart_after_render_resumes_to_preflight_without_rerendering(
    db, channel, workspace, monkeypatch
):
    _seed_filled_slot_via_decision_cycle(db, channel, workspace, monkeypatch=monkeypatch)
    _enable_production(db, channel, workspace)

    from app.intelligence.autonomy import production_cycle as pc_mod

    original_preflight = pc_mod._run_preflight_and_create_plan
    call_state = {"n": 0}

    def _crash_before_preflight(*args, **kwargs):
        call_state["n"] += 1
        if call_state["n"] == 1:
            raise RuntimeError("simulated crash right after rendering, before preflight")
        return original_preflight(*args, **kwargs)

    monkeypatch.setattr(pc_mod, "_run_preflight_and_create_plan", _crash_before_preflight)
    first = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert first.outcome == ProductionOutcome.FAILED
    render_count = db.execute(
        "SELECT COUNT(*) AS n FROM render_jobs WHERE status = 'completed'"
    ).fetchone()["n"]
    assert render_count == 1

    monkeypatch.setattr(pc_mod, "_run_preflight_and_create_plan", original_preflight)
    second = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert second.outcome == ProductionOutcome.READY
    assert (
        db.execute("SELECT COUNT(*) AS n FROM render_jobs WHERE status = 'completed'").fetchone()[
            "n"
        ]
        == 1
    )


# ---------------------------------------------------------------------------
# Concurrency / duplicate-worker protection
# ---------------------------------------------------------------------------


def test_concurrent_production_attempt_is_blocked(db, channel, workspace, monkeypatch):
    """A second worker attempting the same slot while the first is
    'running' (per cp_operation_executions) must be turned away immediately,
    never re-entering the pipeline drive loop."""
    _seed_filled_slot_via_decision_cycle(db, channel, workspace, monkeypatch=monkeypatch)
    _enable_production(db, channel, workspace)

    from app.control_plane.jobs import start_operation
    from app.intelligence.autonomy.repository import find_slot_needing_production

    slot = find_slot_needing_production(db, channel.id)
    start_operation(
        db,
        operation_type="autonomous_production_cycle",
        workspace_id=workspace.id,
        actor="other-worker",
        channel_id=channel.id,
        idempotency_key=f"autonomy_production:{channel.id}:{slot.id}",
    )

    result = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert result.outcome == ProductionOutcome.ALREADY_RUNNING
    assert result.already_running is True
    assert db.execute("SELECT COUNT(*) AS n FROM experiments").fetchone()["n"] == 0


# ---------------------------------------------------------------------------
# Queue bound across decision + production
# ---------------------------------------------------------------------------


def test_queue_bound_respected_across_decision_and_production(db, channel, workspace, monkeypatch):
    """With queue_target=1, once a slot reaches 'ready', the decision cycle
    must not create a second slot — production consumes the one slot the
    decision cycle already reserved, it doesn't create new demand for more."""
    _seed_filled_slot_via_decision_cycle(db, channel, workspace, monkeypatch=monkeypatch)
    _enable_production(db, channel, workspace)
    result = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert result.outcome == ProductionOutcome.READY

    from app.intelligence.autonomy.repository import list_active_slots

    slots = list_active_slots(db, channel.id)
    assert len(slots) == 1
    assert slots[0].state == "filled"
    assert slots[0].production_status == "ready"


# ---------------------------------------------------------------------------
# Deadline status
# ---------------------------------------------------------------------------


def test_deadline_status_for_a_future_slot_matches_the_time_actually_reserved(
    db,
    channel,
    workspace,
    monkeypatch,
):
    """A slot reserved for the future is never reported as missed.

    This previously asserted `comfortably_ahead` on the premise that the
    decision cycle "reserves at least a day out". It does not: compute_next_slot
    reserves the next occurrence of preferred_local_hour after a one-hour lead,
    which can be anywhere from ~1h to ~25h away depending on the time of day the
    suite runs. The assertion therefore failed for every run started after
    21:00 UTC — a wall-clock flake, not a defect in the cycle.

    What is actually invariant is that the reported status agrees with the time
    the slot really holds, so that is what is asserted. The threshold arithmetic
    itself is pinned separately, with explicit times, below.
    """
    from datetime import UTC, datetime

    _seed_filled_slot_via_decision_cycle(db, channel, workspace, monkeypatch=monkeypatch)
    _enable_production(db, channel, workspace)
    result = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )

    slot = get_slot(db, result.slot_id)
    scheduled = datetime.fromisoformat(slot.scheduled_for_utc.replace("Z", "+00:00"))
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=UTC)
    remaining_h = (scheduled - datetime.now(UTC)).total_seconds() / 3600

    assert remaining_h > 0, "the decision cycle must never reserve a slot in the past"
    assert result.deadline_status != DeadlineStatus.missed
    if remaining_h >= 12:
        assert result.deadline_status == DeadlineStatus.comfortably_ahead
    elif remaining_h >= 2:
        assert result.deadline_status == DeadlineStatus.approaching
    else:
        assert result.deadline_status == DeadlineStatus.late


def test_deadline_status_thresholds_are_pinned_to_explicit_times():
    """The threshold arithmetic itself, independent of when the suite runs."""
    from datetime import UTC, datetime, timedelta

    from app.intelligence.autonomy.production_cycle import _deadline_status

    now = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)

    def status_at(hours: float) -> DeadlineStatus:
        when = now + timedelta(hours=hours)
        return _deadline_status(when.strftime("%Y-%m-%dT%H:%M:%S"), now)

    assert status_at(-0.5) == DeadlineStatus.missed
    assert status_at(1.0) == DeadlineStatus.late
    assert status_at(1.99) == DeadlineStatus.late
    assert status_at(2.01) == DeadlineStatus.approaching
    assert status_at(11.99) == DeadlineStatus.approaching
    assert status_at(12.01) == DeadlineStatus.comfortably_ahead
    assert status_at(48.0) == DeadlineStatus.comfortably_ahead


def test_deadline_status_missed_for_a_past_slot(db, channel, workspace):
    from datetime import UTC, datetime, timedelta

    utc_dt = datetime.now(UTC) - timedelta(hours=2)
    reserve_slot(
        db,
        channel_id=channel.id,
        workspace_id=workspace.id,
        slot_key="past-slot",
        scheduled_for_local=utc_dt.isoformat(),
        timezone="UTC",
        scheduled_for_utc=utc_dt,
    )
    from app.intelligence.autonomy.repository import fill_slot

    slot = db.execute("SELECT id FROM publishing_slots WHERE slot_key='past-slot'").fetchone()
    fill_slot(db, slot["id"], brief_id=None, selection_decision_id=None, opportunity_id=None)
    _enable_production(db, channel, workspace)

    result = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert result.deadline_status == DeadlineStatus.missed
    # A missed deadline never compromises validation — production still ran
    # to a genuine terminal outcome rather than being silently skipped or faked.
    assert result.outcome in (
        ProductionOutcome.FAILED,
    )  # no brief -> can't materialize an experiment


# ---------------------------------------------------------------------------
# Preflight validation failure
# ---------------------------------------------------------------------------


def test_preflight_failure_marks_slot_failed_not_ready(db, channel, workspace, monkeypatch):
    _seed_filled_slot_via_decision_cycle(db, channel, workspace, monkeypatch=monkeypatch)
    _enable_production(db, channel, workspace)

    import app.publishing.validation as validation_mod
    from app.publishing.errors import PublishingValidationError

    def _always_fail(approved_render):
        raise PublishingValidationError("simulated: render duration below minimum")

    monkeypatch.setattr(validation_mod, "validate_approved_render_for_publishing", _always_fail)
    # production_cycle imports the function by name at call time inside
    # _run_preflight_and_create_plan, so patch it at that import site too.
    import app.intelligence.autonomy.production_cycle as pc_mod

    monkeypatch.setattr(
        pc_mod,
        "_run_preflight_and_create_plan",
        lambda *a, **kw: _patched_preflight(validation_mod, *a, **kw),
    )

    def _patched_preflight(
        validation_module,
        conn,
        *,
        slot,
        experiment,
        topic,
        pipeline_id,
        workspace_id,
        actor,
        result,
    ):
        from app.media.repository import get_approved_render

        scene_manifest_row = conn.execute(
            "SELECT artifact_id FROM app_pipeline_stage_log "
            "WHERE pipeline_id = ? AND stage = 'visual_intelligence'",
            (pipeline_id,),
        ).fetchone()
        approved_render = get_approved_render(
            conn, int(scene_manifest_row["artifact_id"]), experiment_id=experiment.id
        )
        from app.intelligence.autonomy.repository import mark_slot_production_failed
        from app.publishing.errors import PublishingValidationError as _PVE

        try:
            validation_module.validate_approved_render_for_publishing(approved_render)
            result.preflight_passed = True
        except _PVE as exc:
            result.preflight_passed = False
            result.preflight_errors.append(str(exc))
            result.outcome = ProductionOutcome.FAILED
            result.reason = f"Preflight failed: {exc}"
            mark_slot_production_failed(conn, slot.id, stage="preflight", error=str(exc))

    result = run_production_cycle(
        db,
        cp_channel_id=channel.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert result.outcome == ProductionOutcome.FAILED
    assert result.preflight_passed is False
    assert result.preflight_errors
    assert db.execute("SELECT COUNT(*) AS n FROM publishing_plans").fetchone()["n"] == 0


# ---------------------------------------------------------------------------
# Bounded retries
# ---------------------------------------------------------------------------


def test_failed_slot_is_retried_up_to_the_bound_then_stops(db, channel, workspace, monkeypatch):
    _seed_filled_slot_via_decision_cycle(db, channel, workspace, monkeypatch=monkeypatch)
    _enable_production(db, channel, workspace)

    import app.intelligence.autonomy.production_cycle as pc_mod

    def _always_fail(*args, **kwargs):
        raise RuntimeError("simulated persistent failure")

    monkeypatch.setattr(pc_mod, "_drive_pipeline", _always_fail)

    outcomes = []
    for _ in range(4):
        r = run_production_cycle(
            db,
            cp_channel_id=channel.id,
            workspace_id=workspace.id,
            voice_profile_id=1,
            anthropic_api_key="fake-test-key-not-real",
        )
        outcomes.append(r.outcome)

    # Bounded: after _MAX_PRODUCTION_RETRIES exhausted, the slot stops being
    # picked up at all (NO_SLOT_TO_PRODUCE), never retried indefinitely.
    assert ProductionOutcome.NO_SLOT_TO_PRODUCE in outcomes
    assert outcomes[-1] == ProductionOutcome.NO_SLOT_TO_PRODUCE


# ---------------------------------------------------------------------------
# Channel isolation
# ---------------------------------------------------------------------------


def test_channel_isolation(db, workspace, channel, monkeypatch):
    ch2 = cp_repo.create_channel(
        db,
        ChannelDraft(id=_uid(), workspace_id=workspace.id, name="CH2", slug="ch2", actor="cli"),
    )
    db.commit()
    bootstrap_intelligence_channel(db, ch2.id, channel_name="CH2")
    db.commit()

    _seed_filled_slot_via_decision_cycle(db, channel, workspace, monkeypatch=monkeypatch, opp_id=1)
    _enable_production(db, channel, workspace)
    # ch2 has no policy at all -> must never be touched.

    result = run_production_cycle(
        db,
        cp_channel_id=ch2.id,
        workspace_id=workspace.id,
        voice_profile_id=1,
        anthropic_api_key="fake-test-key-not-real",
    )
    assert result.outcome == ProductionOutcome.DISABLED

    from app.intelligence.autonomy.repository import list_active_slots

    assert len(list_active_slots(db, ch2.id)) == 0
    assert len(list_active_slots(db, channel.id)) == 1
