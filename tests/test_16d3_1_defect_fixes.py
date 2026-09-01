"""Targeted regression tests for Phase 16D.3.1 defect fixes.

DEFECT 1 — experiment-aware production plan lookup:
  get_active_approved_production_plan now accepts experiment_id kwarg.
  NarrationExecutor / VisualIntelligenceExecutor resolve experiment-linked plans.

DEFECT 2 — biology/CRISPR visual query domain:
  _TECH_NOUN_MAP now includes CRISPR/DNA/cell biology triggers.
  _scene_visual_query resolves topic-specific imagery before section-type pool.

DEFECT 3 — render atomicity:
  FFmpegRenderBackend writes to .part staging path and atomically promotes.
  RenderingExecutor is idempotent for completed jobs with intact output files.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.database import open_db
from app.core.models import Script, ScriptStatus, Topic
from app.core.repository import create_script, create_topic
from app.media.backend import FFmpegRenderBackend
from app.media.models import RenderManifestDraft, RenderSceneDraft
from app.production.constants import (
    PRODUCTION_DURATION_VERSION,
    PRODUCTION_PLAN_RENDERER_VERSION,
    PRODUCTION_PLAN_SCHEMA_VERSION,
)
from app.production.models import ProductionPlanDraft, ProductionSegmentDraft
from app.production.repository import (
    approve_production_plan,
    create_production_plan,
    get_active_approved_production_plan,
    get_approved_production_plan_full,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "test.db")


def _topic(db: sqlite3.Connection) -> Topic:
    return create_topic(db, Topic(title="Test Topic"))


def _script(db: sqlite3.Connection, topic_id: int) -> Script:
    s = create_script(
        db, Script(topic_id=topic_id, version=1, body="body", status=ScriptStatus.draft)
    )
    db.commit()
    return s


def _make_plan_draft(
    topic_id: int, script_id: int, *, experiment_id: str | None = None, hash_suffix: str = "a"
) -> ProductionPlanDraft:
    return ProductionPlanDraft(
        topic_id=topic_id,
        script_id=script_id,
        script_version=1,
        input_hash=hash_suffix * 64,
        script_body_hash="b" * 64,
        plan_schema_version=PRODUCTION_PLAN_SCHEMA_VERSION,
        renderer_version=PRODUCTION_PLAN_RENDERER_VERSION,
        duration_algorithm_version=PRODUCTION_DURATION_VERSION,
        title="Test",
        format="short",
        total_estimated_duration_s=6,
        total_word_count=4,
        warnings=[],
        requires_evidence_review=False,
        evidence_hash="e" * 64,
        generation_run_id=None,
        experiment_id=experiment_id,
        segments=[
            ProductionSegmentDraft(
                segment_index=0,
                section_index=0,
                section_type="hook",
                narration_text="Hook.",
                estimated_duration_s=4,
                estimated_word_count=2,
            ),
        ],
    )


def _staging_path(output_path: Path) -> Path:
    """Mirror the backend's staging name.

    The suffix must keep the real container extension — ``render.part`` alone
    left FFmpeg unable to infer the output format.
    """
    return output_path.with_name(f"{output_path.stem}.part{output_path.suffix}")


def _make_render_draft(audio_path: str | None = "/tmp/ace-test-segment.wav") -> RenderManifestDraft:
    """A narrated draft.

    ``audio_path`` is populated because a draft carrying narration lineage may
    not silently fall back to a silent track — see TestNarrationNeverSilent.
    These tests cover atomic promotion, so the audio only has to resolve.
    """
    scene = RenderSceneDraft(
        scene_index=0,
        scene_id=1,
        segment_id=10,
        narration_asset_id=28,
        audio_path=audio_path,
        audio_sha256=None,
        start_ms=0,
        end_ms=3000,
        duration_ms=3000,
        shot_type="medium",
        camera_movement="static",
        visual_objective="Test",
        caption_cue_ids=[],
        primary_asset=None,
    )
    return RenderManifestDraft(
        scene_manifest_id=1,
        narration_run_id=1,
        caption_run_id=None,
        topic_id=1,
        plan_id=1,
        script_id=1,
        experiment_id=None,
        input_hash="s" * 64,
        render_schema_version="Render-v1",
        compositor_version="compositor-1.0.0",
        total_scene_count=1,
        total_duration_ms=3000,
        scenes=[scene],
        caption_burn_in=False,
        width=1080,
        height=1920,
        fps=30,
    )


# ---------------------------------------------------------------------------
# DEFECT 1 — experiment-aware plan lookup
# ---------------------------------------------------------------------------


class TestExperimentAwarePlanLookup:
    def test_non_experiment_lookup_returns_none_for_experiment_linked_plan(self, db):
        """Backward-compat: experiment=NULL filter returns None for experiment-linked plans."""
        topic = _topic(db)
        script = _script(db, topic.id)
        draft = _make_plan_draft(topic.id, script.id, experiment_id="exp-abc")
        plan = create_production_plan(db, draft)
        approve_production_plan(db, plan.id, actor="test")
        db.commit()

        result = get_active_approved_production_plan(db, topic.id)
        assert result is None

    def test_experiment_id_lookup_finds_experiment_linked_plan(self, db):
        """experiment_id kwarg resolves experiment-bound approved plans."""
        topic = _topic(db)
        script = _script(db, topic.id)
        draft = _make_plan_draft(topic.id, script.id, experiment_id="exp-abc")
        plan = create_production_plan(db, draft)
        approve_production_plan(db, plan.id, actor="test")
        db.commit()

        result = get_active_approved_production_plan(db, topic.id, experiment_id="exp-abc")
        assert result is not None
        assert result.id == plan.id
        assert result.experiment_id == "exp-abc"

    def test_wrong_experiment_id_returns_none(self, db):
        """Querying with a different experiment_id returns None."""
        topic = _topic(db)
        script = _script(db, topic.id)
        draft = _make_plan_draft(topic.id, script.id, experiment_id="exp-abc")
        plan = create_production_plan(db, draft)
        approve_production_plan(db, plan.id, actor="test")
        db.commit()

        result = get_active_approved_production_plan(db, topic.id, experiment_id="exp-xyz")
        assert result is None

    def test_non_experiment_plan_found_without_experiment_id(self, db):
        """Non-experiment plans are unaffected by the new signature."""
        topic = _topic(db)
        script = _script(db, topic.id)
        draft = _make_plan_draft(topic.id, script.id, experiment_id=None)
        plan = create_production_plan(db, draft)
        approve_production_plan(db, plan.id, actor="test")
        db.commit()

        result = get_active_approved_production_plan(db, topic.id)
        assert result is not None
        assert result.id == plan.id

    def test_full_plan_resolves_with_experiment_id(self, db):
        """get_approved_production_plan_full honours experiment_id kwarg."""
        topic = _topic(db)
        script = _script(db, topic.id)
        draft = _make_plan_draft(topic.id, script.id, experiment_id="exp-full")
        plan = create_production_plan(db, draft)
        approve_production_plan(db, plan.id, actor="test")
        db.commit()

        full = get_approved_production_plan_full(db, topic.id, experiment_id="exp-full")
        assert full is not None
        assert full.plan_id == plan.id
        assert full.experiment_id == "exp-full"

    def test_two_plans_same_topic_different_experiment_ids_isolated(self, db):
        """Two experiment-linked plans for the same topic are isolated by experiment_id."""
        topic = _topic(db)
        script = _script(db, topic.id)
        draft_a = _make_plan_draft(topic.id, script.id, experiment_id="exp-a", hash_suffix="a")
        draft_b = _make_plan_draft(topic.id, script.id, experiment_id="exp-b", hash_suffix="b")
        plan_a = create_production_plan(db, draft_a)
        plan_b = create_production_plan(db, draft_b)
        approve_production_plan(db, plan_a.id, actor="test")
        approve_production_plan(db, plan_b.id, actor="test")
        db.commit()

        result_a = get_active_approved_production_plan(db, topic.id, experiment_id="exp-a")
        result_b = get_active_approved_production_plan(db, topic.id, experiment_id="exp-b")
        assert result_a is not None and result_a.id == plan_a.id
        assert result_b is not None and result_b.id == plan_b.id
        assert result_a.id != result_b.id


# ---------------------------------------------------------------------------
# DEFECT 1B — RenderingExecutor preserves experiment lineage
# ---------------------------------------------------------------------------


class TestRenderingExecutorExperimentLineage:
    @pytest.mark.parametrize(
        "experiment_id",
        ["exp-render-lineage", None],
    )
    def test_render_draft_inherits_scene_manifest_experiment_id(
        self,
        tmp_path: Path,
        experiment_id: str | None,
    ):
        """RenderingExecutor must preserve approved-scene experiment lineage."""

        from app.application.stage_executors import RenderingExecutor

        approved = MagicMock()
        approved.manifest_id = 5
        approved.input_hash = "scene-hash"
        approved.narration_run_id = 6
        approved.caption_run_id = 5
        approved.topic_id = 4
        approved.plan_id = 6
        approved.script_id = 6
        approved.experiment_id = experiment_id

        builder = MagicMock()
        builder.narration_input_hash = "narration-hash"
        builder.build.return_value = []

        captured: dict[str, object] = {}

        manifest = MagicMock()
        manifest.id = 7

        def fake_get_or_create_render_manifest(conn, draft):
            captured["draft"] = draft
            return manifest, True

        existing_output = tmp_path / "existing.mp4"
        existing_output.write_bytes(b"valid render")

        existing_job = MagicMock()
        existing_job.status = "completed"
        existing_job.output_path = str(existing_output)

        req = MagicMock()
        req.topic_id = 4
        req.stage = "render"
        req.effective_config = {
            "artifacts_path": str(tmp_path),
            "allow_placeholders": True,
        }

        executor = RenderingExecutor()

        with (
            patch(
                "app.media.backend.check_ffmpeg_available",
                return_value=True,
            ),
            patch(
                "app.scenes.repository.get_approved_scene_manifest_full",
                return_value=approved,
            ),
            patch(
                "app.media.compositor.SceneInputBuilder",
                return_value=builder,
            ),
            patch.object(
                executor,
                "_plan_visuals",
                # (plan, qa_report, VisualAssessmentOutcome) since Phase 18E —
                # the third element carries the visual-quality verdict that
                # execute() persists against the render manifest.
                return_value=(MagicMock(), MagicMock(), MagicMock()),
            ),
            patch(
                "app.media.repository.get_or_create_render_manifest",
                side_effect=fake_get_or_create_render_manifest,
            ),
            patch(
                "app.media.repository.list_render_jobs",
                return_value=[existing_job],
            ),
        ):
            result = executor.execute(MagicMock(), req)

        assert "draft" in captured
        draft = captured["draft"]
        assert draft.experiment_id == experiment_id
        assert result.status == "waiting_for_review"


# ---------------------------------------------------------------------------
# DEFECT 2 — visual queries stay inside the narration's own subject
# ---------------------------------------------------------------------------
#
# 16D.3.1 fixed this with a hardcoded technology-noun table. 16D.3.2 replaced
# that table with domain-agnostic extraction in app.visuals.semantics, because
# the table only ever knew the domains someone had already thought of — and its
# renewable-energy entries were what put wind turbines in a CRISPR video.
#
# The invariant these tests protect is unchanged: a query must come from the
# narration in front of it. They now also assert it holds for domains no one
# enumerated, which is the property the table could not have.


class TestVisualQueriesFollowNarrationDomain:
    @staticmethod
    def _queries(text: str, topic_terms: list[str] | None = None) -> str:
        from app.visuals import semantics

        intent = semantics.classify_intent(text)
        queries = semantics.build_queries(
            text, intent=intent, topic_terms=topic_terms or semantics.extract_terms(text, limit=4)
        )
        assert queries, f"no query built for {text!r}"
        return " ".join(queries).lower()

    def test_crispr_narration_yields_crispr_vocabulary(self):
        joined = self._queries("CRISPR can cut your DNA with incredible precision")
        assert "crispr" in joined or "dna" in joined

    def test_crispr_narration_never_yields_energy_vocabulary(self):
        """The exact Video 2 defect: biology narration retrieving energy imagery."""
        joined = self._queries("CRISPR can cut your DNA with incredible precision")
        assert not ({"solar", "wind", "turbine", "renewable", "grid"} & set(joined.split()))

    def test_cta_narration_never_yields_stock_phone_vocabulary(self):
        joined = self._queries("Share this before your next biology class")
        assert not ({"smartphone", "phone", "social", "mobile"} & set(joined.split()))

    def test_queries_are_retrieval_shaped_not_sentences(self):
        """Full narration sentences match documents, not footage."""
        from app.visuals import semantics

        text = "Cas9's two nuclease domains slice both strands of your DNA, creating a break."
        for query in semantics.build_queries(text, intent="action", topic_terms=["dna"]):
            assert len(query.split()) <= 4, query

    def test_generalises_to_domains_no_table_enumerates(self):
        """The property the hardcoded map could not have: unknown domains work."""
        cases = [
            (
                "The Federal Reserve raised interest rates again this quarter.",
                {"reserve", "rates", "federal", "interest", "quarter"},
            ),
            (
                "Hannibal marched war elephants across the Alps in 218 BC.",
                {"hannibal", "elephants", "alps", "marched"},
            ),
            (
                "The goalkeeper saved three penalties in the shootout.",
                {"goalkeeper", "penalties", "shootout", "saved"},
            ),
        ]
        for text, expected_any in cases:
            joined = self._queries(text)
            assert expected_any & set(joined.split()), f"{text!r} produced {joined!r}"

    def test_beat_queries_are_anchored_to_the_video_subject(self):
        """Ordinary words must not retrieve their everyday sense.

        "break", "repair" and "template" are the words that put ocean waves, a
        car garage and a tailor into a CRISPR video.
        """
        from app.visuals import semantics

        topic = ["dna", "crispr", "cell"]
        for text in (
            "creating a double-strand break .",
            "which copies from a matching template .",
            "So the cell's repair choice determines your outcome.",
        ):
            queries = semantics.build_queries(text, intent="action", topic_terms=topic)
            assert any(anchor in queries[0].lower().split() for anchor in topic), (
                f"first query {queries[0]!r} carries no subject anchor"
            )


# ---------------------------------------------------------------------------
# DEFECT 3 — render atomicity
# ---------------------------------------------------------------------------


class TestRenderAtomicity:
    @patch("app.media.backend.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("app.media.backend.subprocess.run")
    def test_interrupted_render_leaves_prior_artifact_intact(self, mock_run, mock_which, tmp_path):
        """If FFmpeg fails, the prior valid output file is untouched."""
        output_path = tmp_path / "render.mp4"
        prior_content = b"prior valid render content"
        output_path.write_bytes(prior_content)

        mock_run.return_value = MagicMock(returncode=1, stderr="interrupted")

        backend = FFmpegRenderBackend()
        from app.media.errors import RenderBackendError

        with pytest.raises(RenderBackendError):
            backend.render(
                _make_render_draft(), output_path, tmp_path / "tmp", allow_placeholders=True
            )

        assert output_path.exists()
        assert output_path.read_bytes() == prior_content

    @patch("app.media.backend.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("app.media.backend.subprocess.run")
    def test_staging_file_promoted_on_success(self, mock_run, mock_which, tmp_path):
        """Successful render promotes .part to final path; .part is cleaned up."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        output_path = tmp_path / "render.mp4"
        staging = _staging_path(output_path)
        staging.write_bytes(b"new render bytes")

        backend = FFmpegRenderBackend()
        with patch.object(backend, "_sha256", return_value="abc123"):
            result = backend.render(
                _make_render_draft(), output_path, tmp_path / "tmp", allow_placeholders=True
            )

        assert output_path.exists()
        assert not staging.exists()
        assert result.output_path == str(output_path)

    @patch("app.media.backend.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("app.media.backend.subprocess.run")
    def test_final_path_reflects_promoted_content(self, mock_run, mock_which, tmp_path):
        """After promotion, output_path contains what was written to .part."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        output_path = tmp_path / "render.mp4"
        staging = _staging_path(output_path)
        staging.write_bytes(b"X" * 2048)

        backend = FFmpegRenderBackend()
        with patch.object(backend, "_sha256", return_value="hash"):
            result = backend.render(
                _make_render_draft(), output_path, tmp_path / "tmp", allow_placeholders=True
            )

        assert result.file_size_bytes == 2048
        assert output_path.stat().st_size == 2048
