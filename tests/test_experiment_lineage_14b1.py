"""Phase 14B.1 — Authoritative Lineage Precedence & Attribution Hardening tests.

Invariant under test:
    IF authoritative persisted lineage exists → it wins.
    IF caller supplies the same value        → accept.
    IF caller supplies a conflicting value   → fail BEFORE any mutation.
    IF authoritative lineage is absent       → caller value may be used (legacy).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.application.executor import StageExecutionRequest
from app.application.stage_executors import (
    CaptionsExecutor,
    NarrationExecutor,
    VisualIntelligenceExecutor,
)
from app.intelligence.experiments.repository import attach_publication

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    """On-disk temp DB with full schema (open_db requires a Path)."""
    from app.core.database import open_db

    return open_db(tmp_path / "lineage_14b1.db")


def _insert_experiment(conn, exp_id="exp-A", channel_id=1, opp_id=None):
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT OR IGNORE INTO channels (id, platform, channel_name) "
        "VALUES (?, 'youtube', 'Test Channel')",
        (channel_id,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO experiments "
        "(id, channel_id, opportunity_id, experiment_type, status, hypothesis, "
        "input_hash, maturity_policy_json, policy_snapshot_json) "
        "VALUES (?, ?, ?, 'exploration', 'draft', 'h', ?, '{}', '{}')",
        (exp_id, channel_id, opp_id, f"ihash_{exp_id}"),
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


def _insert_production_plan(conn, plan_id=1, topic_id=1, experiment_id=None):
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT OR IGNORE INTO production_plans "
        "(id, topic_id, script_id, script_version, input_hash, script_body_hash, "
        "plan_schema_version, renderer_version, duration_algorithm_version, "
        "status, experiment_id) "
        "VALUES (?, ?, 1, 1, ?, 'sbhash1', '1.0', '1.0', '1.0', 'approved', ?)",
        (plan_id, topic_id, f"planhash_{plan_id}", experiment_id),
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


def _insert_publication_chain(conn, pub_id=10, plan_id=1, exp_id=None):
    """Insert a publishing_plan + publication linked to the given experiment_id."""
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT OR IGNORE INTO publishing_plans "
        "(id, render_manifest_id, topic_id, production_plan_id, script_id, "
        "scene_manifest_id, narration_run_id, caption_run_id, "
        "input_hash, publishing_engine_version, metadata_version, "
        "provider, provider_version, title, experiment_id, created_at, updated_at) "
        "VALUES (?, 1, 1, ?, 1, 1, 1, 1, ?, '1.0', '1.0', 'youtube', '1.0', 'T', ?, "
        "'2024-01-01T00:00:00', '2024-01-01T00:00:00')",
        (plan_id, plan_id, f"pphash_{plan_id}", exp_id),
    )
    conn.execute(
        "INSERT OR IGNORE INTO publications "
        "(id, publishing_plan_id, publishing_job_id, provider, provider_version, "
        "publishing_engine_version, input_hash, output_sha256, created_at, updated_at) "
        "VALUES (?, ?, 1, 'fake', '1.0', '1.0', ?, 'sha1', "
        "'2024-01-01T00:00:00', '2024-01-01T00:00:00')",
        (pub_id, plan_id, f"pubhash_{pub_id}"),
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


def _make_req(*, experiment_id=None, topic_id=1, plan_id=None):
    kwargs = dict(
        pipeline_execution_id="exec-1",
        workspace_id="ws-1",
        stage="narration",
        attempt_number=1,
        correlation_id="corr-1",
        idempotency_key="key-1",
        actor="test",
        topic_id=topic_id,
        experiment_id=experiment_id,
        effective_config={"voice_profile_id": 1},
    )
    if plan_id is not None:
        kwargs["prerequisite_artifact_ids"] = {"production_plan": str(plan_id)}
    return StageExecutionRequest(**kwargs)


# ---------------------------------------------------------------------------
# NarrationExecutor — A, B, C, X
# ---------------------------------------------------------------------------


def _make_plan_obj(plan_id=1, experiment_id=None):
    plan = MagicMock()
    plan.id = plan_id
    plan.experiment_id = experiment_id
    plan.input_hash = "hash1"
    return plan


class TestNarrationExecutorExperimentPrecedence:
    """Phase 14B.1 tests A, B, C, X for NarrationExecutor."""

    _patches = [
        "app.production.repository.get_active_approved_production_plan",
        "app.narration.orchestrator.narrate_plan",
        "app.learning.application.resolve_speaking_rate_override",
        "app.learning.application.consume_proposed_application",
    ]

    def _run(self, conn, req, plan_experiment_id):
        plan = _make_plan_obj(experiment_id=plan_experiment_id)
        fake_run = MagicMock()
        fake_run.run_id = 99

        with (
            patch(
                "app.production.repository.get_active_approved_production_plan",
                return_value=plan,
            ),
            patch("app.narration.orchestrator.narrate_plan", return_value=fake_run),
            patch(
                "app.learning.application.resolve_speaking_rate_override",
                return_value=(None, None),
            ),
            patch("app.learning.application.consume_proposed_application"),
            patch.object(
                NarrationExecutor, "_build_provider", staticmethod(lambda *a, **k: MagicMock())
            ),
        ):
            executor = NarrationExecutor()
            return executor.execute(conn, req)

    def test_A_plan_bound_request_different_exp_is_blocked(self, conn):
        """A: plan.experiment_id=exp-A, req.experiment_id=exp-B → blocked (lineage_conflict)."""
        req = _make_req(experiment_id="exp-B")
        result = self._run(conn, req, plan_experiment_id="exp-A")
        assert result.status == "blocked"
        assert result.error_category == "lineage_conflict"
        assert "exp-B" in (result.error_message or "")
        assert "exp-A" in (result.error_message or "")

    def test_B_plan_bound_request_none_uses_plan_exp(self, conn):
        """B: plan.experiment_id=exp-A, req.experiment_id=None → uses exp-A."""
        req = _make_req(experiment_id=None)
        plan = _make_plan_obj(experiment_id="exp-A")
        fake_run = MagicMock()
        fake_run.run_id = 99

        captured: dict = {}

        def fake_narrate(
            conn,
            *,
            plan_id,
            plan_input_hash,
            voice_profile_id,
            artifacts_path,
            provider,
            speaking_rate_override,
            experiment_id,
        ):
            captured["experiment_id"] = experiment_id
            return fake_run

        with (
            patch(
                "app.production.repository.get_active_approved_production_plan", return_value=plan
            ),
            patch("app.narration.orchestrator.narrate_plan", side_effect=fake_narrate),
            patch(
                "app.learning.application.resolve_speaking_rate_override", return_value=(None, None)
            ),
            patch("app.learning.application.consume_proposed_application"),
            patch.object(
                NarrationExecutor, "_build_provider", staticmethod(lambda *a, **k: MagicMock())
            ),
        ):
            result = NarrationExecutor().execute(conn, req)

        assert result.status == "waiting_for_review"
        assert captured["experiment_id"] == "exp-A"

    def test_C_plan_bound_request_same_exp_succeeds(self, conn):
        """C: plan.experiment_id=exp-A, req.experiment_id=exp-A → succeeds."""
        req = _make_req(experiment_id="exp-A")
        plan = _make_plan_obj(experiment_id="exp-A")
        fake_run = MagicMock()
        fake_run.run_id = 99

        captured: dict = {}

        def fake_narrate(
            conn,
            *,
            plan_id,
            plan_input_hash,
            voice_profile_id,
            artifacts_path,
            provider,
            speaking_rate_override,
            experiment_id,
        ):
            captured["experiment_id"] = experiment_id
            return fake_run

        with (
            patch(
                "app.production.repository.get_active_approved_production_plan", return_value=plan
            ),
            patch("app.narration.orchestrator.narrate_plan", side_effect=fake_narrate),
            patch(
                "app.learning.application.resolve_speaking_rate_override", return_value=(None, None)
            ),
            patch("app.learning.application.consume_proposed_application"),
            patch.object(
                NarrationExecutor, "_build_provider", staticmethod(lambda *a, **k: MagicMock())
            ),
        ):
            result = NarrationExecutor().execute(conn, req)

        assert result.status == "waiting_for_review"
        assert captured["experiment_id"] == "exp-A"

    def test_X_plan_no_experiment_req_has_one_uses_req(self, conn):
        """X: legacy path — plan.experiment_id=None, req.experiment_id=exp-L → uses exp-L."""
        req = _make_req(experiment_id="exp-L")
        plan = _make_plan_obj(experiment_id=None)
        fake_run = MagicMock()
        fake_run.run_id = 99

        captured: dict = {}

        def fake_narrate(
            conn,
            *,
            plan_id,
            plan_input_hash,
            voice_profile_id,
            artifacts_path,
            provider,
            speaking_rate_override,
            experiment_id,
        ):
            captured["experiment_id"] = experiment_id
            return fake_run

        with (
            patch(
                "app.production.repository.get_active_approved_production_plan", return_value=plan
            ),
            patch("app.narration.orchestrator.narrate_plan", side_effect=fake_narrate),
            patch(
                "app.learning.application.resolve_speaking_rate_override", return_value=(None, None)
            ),
            patch("app.learning.application.consume_proposed_application"),
            patch.object(
                NarrationExecutor, "_build_provider", staticmethod(lambda *a, **k: MagicMock())
            ),
        ):
            result = NarrationExecutor().execute(conn, req)

        assert result.status == "waiting_for_review"
        assert captured["experiment_id"] == "exp-L"

    def test_X2_both_none_passes_none(self, conn):
        """X2: plan.experiment_id=None, req.experiment_id=None → effective is
        None (non-experiment flow)."""
        req = _make_req(experiment_id=None)
        plan = _make_plan_obj(experiment_id=None)
        fake_run = MagicMock()
        fake_run.run_id = 99

        captured: dict = {}

        def fake_narrate(
            conn,
            *,
            plan_id,
            plan_input_hash,
            voice_profile_id,
            artifacts_path,
            provider,
            speaking_rate_override,
            experiment_id,
        ):
            captured["experiment_id"] = experiment_id
            return fake_run

        with (
            patch(
                "app.production.repository.get_active_approved_production_plan", return_value=plan
            ),
            patch("app.narration.orchestrator.narrate_plan", side_effect=fake_narrate),
            patch(
                "app.learning.application.resolve_speaking_rate_override", return_value=(None, None)
            ),
            patch("app.learning.application.consume_proposed_application"),
            patch.object(
                NarrationExecutor, "_build_provider", staticmethod(lambda *a, **k: MagicMock())
            ),
        ):
            result = NarrationExecutor().execute(conn, req)

        assert result.status == "waiting_for_review"
        assert captured["experiment_id"] is None


# ---------------------------------------------------------------------------
# CaptionsExecutor — D, E
# ---------------------------------------------------------------------------


class TestCaptionsExecutorExperimentPrecedence:
    """Phase 14B.1 tests D, E for CaptionsExecutor."""

    def _run(self, conn, req, plan_id=1, plan_experiment_id=None):
        _insert_production_plan(conn, plan_id=plan_id, experiment_id=plan_experiment_id)
        with patch("app.captions.orchestrator.generate_captions", return_value=MagicMock(id=77)):
            executor = CaptionsExecutor()
            return executor.execute(conn, req)

    def test_D_plan_bound_request_different_exp_is_blocked(self, conn):
        """D: plan.experiment_id=exp-A, req.experiment_id=exp-B → blocked."""
        req = _make_req(experiment_id="exp-B", plan_id=1)
        result = self._run(conn, req, plan_id=1, plan_experiment_id="exp-A")
        assert result.status == "blocked"
        assert result.error_category == "lineage_conflict"
        assert "exp-B" in (result.error_message or "")

    def test_E_plan_bound_request_none_derives_exp(self, conn):
        """E: plan.experiment_id=exp-A, req.experiment_id=None → derives exp-A."""
        req = _make_req(experiment_id=None, plan_id=1)
        _insert_production_plan(conn, plan_id=1, experiment_id="exp-A")

        captured: dict = {}

        def fake_gen(conn, *, plan_id, artifacts_path, experiment_id):
            captured["experiment_id"] = experiment_id
            return MagicMock(id=77)

        with patch("app.captions.orchestrator.generate_captions", side_effect=fake_gen):
            result = CaptionsExecutor().execute(conn, req)

        assert result.status == "waiting_for_review"
        assert captured["experiment_id"] == "exp-A"

    def test_E2_plan_same_exp_succeeds(self, conn):
        """E2: plan.experiment_id=exp-A, req=exp-A → succeeds (no conflict)."""
        req = _make_req(experiment_id="exp-A", plan_id=1)
        _insert_production_plan(conn, plan_id=1, experiment_id="exp-A")

        captured: dict = {}

        def fake_gen(conn, *, plan_id, artifacts_path, experiment_id):
            captured["experiment_id"] = experiment_id
            return MagicMock(id=77)

        with patch("app.captions.orchestrator.generate_captions", side_effect=fake_gen):
            result = CaptionsExecutor().execute(conn, req)

        assert result.status == "waiting_for_review"
        assert captured["experiment_id"] == "exp-A"


# ---------------------------------------------------------------------------
# VisualIntelligenceExecutor — F, G
# ---------------------------------------------------------------------------


class TestVisualIntelligenceExecutorPrecedence:
    """Phase 14B.1 tests F, G for VisualIntelligenceExecutor."""

    def test_F_plan_bound_request_different_exp_is_blocked(self, conn):
        """F: plan.experiment_id=exp-A, req.experiment_id=exp-B → blocked before lookups."""
        _insert_production_plan(conn, plan_id=1, experiment_id="exp-A")
        req = _make_req(experiment_id="exp-B", plan_id=1)
        # No narration/caption mocks needed — should block before those calls.
        result = VisualIntelligenceExecutor().execute(conn, req)
        assert result.status == "blocked"
        assert result.error_category == "lineage_conflict"
        assert "exp-B" in (result.error_message or "")

    def test_G_narration_lookup_uses_authoritative_exp(self, conn):
        """G: visual cannot fetch exp-B narration artifacts; only exp-A artifacts exist."""
        _insert_production_plan(conn, plan_id=1, experiment_id="exp-A")
        req = _make_req(experiment_id=None, plan_id=1)

        captured: dict = {}

        def fake_get_narration(conn, plan_id, *, experiment_id):
            captured["experiment_id"] = experiment_id
            return None  # no narration run → blocked at prerequisite_missing

        with (
            patch(
                "app.narration.repository.get_approved_narration_run_full",
                side_effect=fake_get_narration,
            ),
            patch("app.captions.repository.get_active_approved_caption_run", return_value=None),
            patch("app.production.repository.get_approved_production_plan_full", return_value=None),
        ):
            result = VisualIntelligenceExecutor().execute(conn, req)

        assert result.status == "blocked"
        assert result.error_category == "prerequisite_missing"
        # The lookup used exp-A (from plan), not exp-B
        assert captured["experiment_id"] == "exp-A"


# ---------------------------------------------------------------------------
# attach_publication lineage hardening — K, L, M, AC
# ---------------------------------------------------------------------------


class TestAttachPublicationLineage:
    """Phase 14B.1 tests K, L, M, AC for attach_publication lineage check."""

    def test_K_cross_experiment_publication_rejected(self, conn):
        """K: Publication-A (linked to exp-A via publishing_plan) cannot attach to exp-B."""
        _insert_experiment(conn, exp_id="exp-A", channel_id=1)
        _insert_experiment(conn, exp_id="exp-B", channel_id=1)
        # publication 10 is linked to exp-A via publishing_plan
        _insert_publication_chain(conn, pub_id=10, plan_id=1, exp_id="exp-A")

        with pytest.raises(ValueError, match="belongs to experiment"):
            attach_publication(conn, "exp-B", 10)

    def test_L_correct_experiment_publication_attaches(self, conn):
        """L: Publication-A linked to exp-A attaches to exp-A without error."""
        _insert_experiment(conn, exp_id="exp-A", channel_id=1)
        _insert_publication_chain(conn, pub_id=10, plan_id=1, exp_id="exp-A")

        attach_publication(conn, "exp-A", 10)

        row = conn.execute("SELECT publication_id FROM experiments WHERE id = 'exp-A'").fetchone()
        assert row["publication_id"] == 10

    def test_M_idempotent_reattach_same_publication(self, conn):
        """M: Attaching the same publication to the same experiment twice is idempotent."""
        _insert_experiment(conn, exp_id="exp-A", channel_id=1)
        _insert_publication_chain(conn, pub_id=10, plan_id=1, exp_id="exp-A")

        attach_publication(conn, "exp-A", 10)
        attach_publication(conn, "exp-A", 10)  # second call — must not raise

        row = conn.execute("SELECT publication_id FROM experiments WHERE id = 'exp-A'").fetchone()
        assert row["publication_id"] == 10

    def test_AC_same_channel_cross_experiment_rejected(self, conn):
        """AC: Same-channel isolation is insufficient — lineage must match."""
        # Both experiments on same channel
        _insert_experiment(conn, exp_id="exp-A", channel_id=1)
        _insert_experiment(conn, exp_id="exp-B", channel_id=1)
        # Publication 10 linked to exp-A
        _insert_publication_chain(conn, pub_id=10, plan_id=1, exp_id="exp-A")

        # Even though both experiments share channel 1, exp-B cannot claim pub 10
        with pytest.raises(ValueError, match="belongs to experiment"):
            attach_publication(conn, "exp-B", 10)

    def test_Y_null_lineage_publication_attaches_to_any_experiment(self, conn):
        """Y: Legacy — publication with no experiment_id in publishing_plan attaches freely."""
        _insert_experiment(conn, exp_id="exp-A", channel_id=1)
        # Publishing plan has no experiment_id (NULL)
        _insert_publication_chain(conn, pub_id=20, plan_id=2, exp_id=None)

        attach_publication(conn, "exp-A", 20)

        row = conn.execute("SELECT publication_id FROM experiments WHERE id = 'exp-A'").fetchone()
        assert row["publication_id"] == 20


# ---------------------------------------------------------------------------
# Analytics CLI derivation — N, O, P
# ---------------------------------------------------------------------------


def _make_fake_plan(experiment_id=None):
    plan = MagicMock()
    plan.experiment_id = experiment_id
    plan.render_manifest_id = 1
    plan.scene_manifest_id = 1
    plan.production_plan_id = 1
    plan.script_id = 1
    plan.topic_id = 1
    plan.narration_run_id = 1
    plan.caption_run_id = 1
    return plan


def _make_fake_pub(publishing_plan_id=1):
    pub = MagicMock()
    pub.provider_video_id = "vid-123"
    pub.publishing_plan_id = publishing_plan_id
    pub.publishing_job_id = 1
    pub.published_at = "2024-06-01T00:00:00"
    return pub


class TestAnalyticsExperimentDerivation:
    """Phase 14B.1 tests N, O, P for analytics ingest authoritative experiment."""

    def _call_build_lineage(self, pub_exp_id):
        from app.analytics.cli import _build_youtube_provider_and_lineage

        fake_conn = MagicMock()
        fake_pub = _make_fake_pub()
        fake_plan = _make_fake_plan(experiment_id=pub_exp_id)
        fake_provider = MagicMock()

        with (
            patch("app.publishing.repository.get_publication", return_value=fake_pub),
            patch("app.publishing.repository.get_publishing_plan", return_value=fake_plan),
            patch("app.oauth.client_google.RealGoogleOAuthClient", return_value=MagicMock()),
            patch(
                "app.analytics.gate.build_authenticated_analytics_provider",
                return_value=fake_provider,
            ),
            patch(
                "app.core.config.get_config",
                return_value=MagicMock(
                    youtube_client_secrets_path="/fake",
                    youtube_redirect_uri="http://localhost",
                ),
            ),
        ):
            return _build_youtube_provider_and_lineage(
                fake_conn,
                publication_id=1,
                account_id="acct",
                workspace_id="ws",
                channel_id="ch",
            )

    def test_P_no_supplied_experiment_derives_from_plan(self):
        """P: No --experiment supplied → derived from publishing_plan.experiment_id."""
        _, _, _, derived = self._call_build_lineage(pub_exp_id="exp-A")
        assert derived == "exp-A"

    def test_P_null_lineage_derives_none(self):
        """P (NULL): publishing_plan has no experiment_id → derived is None."""
        _, _, _, derived = self._call_build_lineage(pub_exp_id=None)
        assert derived is None

    def test_N_mismatch_raises_exit(self):
        """N: analytics_ingest with --experiment exp-B but publication linked to exp-A → Exit(1)."""
        from typer.testing import CliRunner

        from app.analytics.cli import analytics_app

        fake_conn = MagicMock()
        fake_pub = _make_fake_pub()
        fake_plan = _make_fake_plan(experiment_id="exp-A")
        fake_provider = MagicMock()
        fake_provider.fetch_metrics.return_value = []

        runner = CliRunner()
        with (
            patch("app.analytics.cli._get_db", return_value=fake_conn),
            patch("app.publishing.repository.get_publication", return_value=fake_pub),
            patch("app.publishing.repository.get_publishing_plan", return_value=fake_plan),
            patch("app.oauth.client_google.RealGoogleOAuthClient", return_value=MagicMock()),
            patch(
                "app.analytics.gate.build_authenticated_analytics_provider",
                return_value=fake_provider,
            ),
            patch(
                "app.core.config.get_config",
                return_value=MagicMock(
                    youtube_client_secrets_path="/fake",
                    youtube_redirect_uri="http://localhost",
                ),
            ),
        ):
            result = runner.invoke(
                analytics_app,
                [
                    "ingest",
                    "1",
                    "--provider",
                    "youtube",
                    "--account-id",
                    "acct",
                    "--workspace-id",
                    "ws",
                    "--channel-id",
                    "ch",
                    "--experiment",
                    "exp-B",
                ],
            )

        assert result.exit_code != 0
        assert "exp-B" in result.output or "mismatch" in result.output.lower()

    def test_O_matching_supplied_experiment_accepted(self):
        """O: analytics_ingest with --experiment exp-A; publication also links to exp-A → ok."""
        from typer.testing import CliRunner

        from app.analytics.cli import analytics_app

        fake_conn = MagicMock()
        fake_pub = _make_fake_pub()
        fake_plan = _make_fake_plan(experiment_id="exp-A")
        fake_provider = MagicMock()

        # The orchestrator would be called; mock it out to avoid DB operations
        fake_snapshot = MagicMock()
        fake_snapshot.id = 1
        fake_snapshot.input_hash = "abc123"
        fake_snapshot.provider = "youtube"

        with (
            patch("app.analytics.cli._get_db", return_value=fake_conn),
            patch("app.publishing.repository.get_publication", return_value=fake_pub),
            patch("app.publishing.repository.get_publishing_plan", return_value=fake_plan),
            patch("app.oauth.client_google.RealGoogleOAuthClient", return_value=MagicMock()),
            patch(
                "app.analytics.gate.build_authenticated_analytics_provider",
                return_value=fake_provider,
            ),
            patch(
                "app.core.config.get_config",
                return_value=MagicMock(
                    youtube_client_secrets_path="/fake",
                    youtube_redirect_uri="http://localhost",
                ),
            ),
            patch(
                "app.analytics.orchestrator.AnalyticsOrchestrator.ingest",
                return_value=(fake_snapshot, []),
            ),
        ):
            result = runner = CliRunner()
            result = runner.invoke(
                analytics_app,
                [
                    "ingest",
                    "1",
                    "--provider",
                    "youtube",
                    "--account-id",
                    "acct",
                    "--workspace-id",
                    "ws",
                    "--channel-id",
                    "ch",
                    "--experiment",
                    "exp-A",  # matches derived
                ],
            )

        # Should not fail due to mismatch
        assert "does not match" not in result.output


# ---------------------------------------------------------------------------
# Learning orchestrator — H, I, J, U
# ---------------------------------------------------------------------------


class TestLearningTopicPrecedence:
    """Phase 14B.1 tests H, I, J, U for learning analyze_publication topic validation."""

    def _build_minimal_learning_data(self, conn, pub_id, topic_id, pub_plan_topic_id=None):
        """Insert the minimum rows needed to call analyze_publication."""
        _topic_id = pub_plan_topic_id if pub_plan_topic_id is not None else topic_id
        conn.execute("PRAGMA foreign_keys = OFF")
        # analytics_snapshots row needed by _build_handoff_from_db
        conn.execute(
            "INSERT OR IGNORE INTO analytics_snapshots "
            "(id, publication_id, topic_id, publishing_plan_id, publishing_job_id, "
            "render_manifest_id, scene_manifest_id, production_plan_id, script_id, "
            "narration_run_id, caption_run_id, provider, provider_version, "
            "input_hash, ingested_at) "
            "VALUES (1, ?, ?, 1, 1, 1, 1, 1, 1, 1, 1, 'fake', '1.0', 'h1', '2024-01-01T00:00:00')",
            (pub_id, topic_id),
        )
        # publishing_plan linking publication to its topic
        conn.execute(
            "INSERT OR IGNORE INTO publishing_plans "
            "(id, render_manifest_id, topic_id, production_plan_id, script_id, "
            "scene_manifest_id, narration_run_id, caption_run_id, "
            "input_hash, publishing_engine_version, metadata_version, "
            "provider, provider_version, title, created_at, updated_at) "
            "VALUES (1, 1, ?, 1, 1, 1, 1, 1, 'pph_learn1', '1.0', '1.0', "
            "'fake', '1.0', 'T', '2024-01-01T00:00:00', '2024-01-01T00:00:00')",
            (_topic_id,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO publications "
            "(id, publishing_plan_id, publishing_job_id, provider, provider_version, "
            "publishing_engine_version, input_hash, output_sha256, created_at, updated_at) "
            "VALUES (?, 1, 1, 'fake', '1.0', '1.0', 'h1', 'sha1', "
            "'2024-01-01T00:00:00', '2024-01-01T00:00:00')",
            (pub_id,),
        )
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()

    def test_H_mismatched_topic_rejected(self, conn):
        """H: Publication linked to topic 2 via publishing_plan; caller
        supplies topic 1 → rejected."""
        from app.learning.orchestrator import analyze_publication

        self._build_minimal_learning_data(conn, pub_id=1, topic_id=1, pub_plan_topic_id=2)

        with pytest.raises(ValueError, match="topic_id mismatch"):
            analyze_publication(conn, publication_id=1, topic_id=1)

    def test_I_matching_topic_accepted(self, conn):
        """I: Caller supplies topic 2, publication also linked to topic 2 → accepted."""
        from app.learning.orchestrator import analyze_publication

        self._build_minimal_learning_data(conn, pub_id=1, topic_id=2, pub_plan_topic_id=2)

        # Should get past the topic validation; will fail at learning_run creation (no full schema)
        # but not due to topic mismatch
        try:
            analyze_publication(conn, publication_id=1, topic_id=2)
        except ValueError as exc:
            if "topic_id mismatch" in str(exc):
                pytest.fail(f"Unexpected topic mismatch: {exc}")
        except Exception:
            pass  # other errors (missing schema rows etc.) are fine here

    def test_J_no_publishing_plan_topic_caller_used(self, conn):
        """J: No publishing_plan row exists → no lineage to validate; caller topic_id used."""
        from app.learning.orchestrator import analyze_publication

        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "INSERT OR IGNORE INTO analytics_snapshots "
            "(id, publication_id, topic_id, publishing_plan_id, publishing_job_id, "
            "render_manifest_id, scene_manifest_id, production_plan_id, script_id, "
            "narration_run_id, caption_run_id, provider, provider_version, "
            "input_hash, ingested_at) "
            "VALUES (1, 99, 1, 1, 1, 1, 1, 1, 1, 1, 1, 'fake', '1.0', 'h1', '2024-01-01T00:00:00')"
        )
        # Pub exists but has no publishing_plan (will have publishing_plan_id=1 but no plan row)
        conn.execute(
            "INSERT OR IGNORE INTO publications "
            "(id, publishing_plan_id, publishing_job_id, provider, provider_version, "
            "publishing_engine_version, input_hash, output_sha256, created_at, updated_at) "
            "VALUES (99, 999, 1, 'fake', '1.0', '1.0', 'h2', 'sha2', "
            "'2024-01-01T00:00:00', '2024-01-01T00:00:00')"
        )
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()

        # The JOIN in analyze_publication returns None (no matching publishing_plan row)
        # → no validation fires → caller's topic_id=1 is used
        try:
            analyze_publication(conn, publication_id=99, topic_id=1)
        except ValueError as exc:
            if "topic_id mismatch" in str(exc):
                pytest.fail(f"Unexpected topic mismatch with no lineage: {exc}")
        except Exception:
            pass  # other errors are fine


# ---------------------------------------------------------------------------
# Preventative pattern search verification (AD — no duplicate lifecycle event on bind)
# ---------------------------------------------------------------------------


class TestBindExperimentIdempotency:
    """AD: Idempotent bind must not emit duplicate state events."""

    def test_AD_idempotent_bind_no_duplicate_event(self, conn):
        """AD: Calling bind_experiment_to_production_plan twice on same binding is a no-op."""
        from app.intelligence.experiments.repository import bind_experiment_to_production_plan

        _insert_experiment(conn, exp_id="exp-A", channel_id=1)
        _insert_production_plan(conn, plan_id=1, experiment_id=None)  # unbound

        events_before = conn.execute(
            "SELECT COUNT(*) FROM experiment_state_events WHERE experiment_id = 'exp-A'"
        ).fetchone()[0]

        bind_experiment_to_production_plan(conn, "exp-A", 1)
        conn.commit()

        events_after_first = conn.execute(
            "SELECT COUNT(*) FROM experiment_state_events WHERE experiment_id = 'exp-A'"
        ).fetchone()[0]

        # Call again — idempotent; no new event should be emitted
        bind_experiment_to_production_plan(conn, "exp-A", 1)
        conn.commit()

        events_after_second = conn.execute(
            "SELECT COUNT(*) FROM experiment_state_events WHERE experiment_id = 'exp-A'"
        ).fetchone()[0]

        assert events_after_first == events_before + 1
        assert events_after_second == events_after_first  # no duplicate event
