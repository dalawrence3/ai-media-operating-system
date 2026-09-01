"""Real stage executors for research, script, narration, captions, visual, and rendering.

Each executor reads credentials and paths from effective_config first, then falls back
to the application Config singleton.  Missing credentials cause a blocked result —
never a crash — so the pipeline fails closed rather than raising unhandled exceptions.

Fake/test provider behaviour is preserved:
  - ScriptGenerationExecutor uses FakeAIProvider when ANTHROPIC_API_KEY is absent.
  - NarrationExecutor uses FakeTTSProvider when ACE_TTS_LIVE_ENABLED is not true.

No eval, exec, shell injection, or arbitrary dynamic imports.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.application.executor import (
    EXECUTOR_CONTRACT_VERSION,
    StageExecutionRequest,
    StageExecutionResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Common stop-words that add noise when searching Wikimedia Commons image titles
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "of",
        "for",
        "in",
        "on",
        "to",
        "with",
        "is",
        "it",
        "its",
        "isn't",
        "isn",
        "t",
        "not",
        "be",
        "been",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "what",
        "if",
        "how",
        "why",
        "when",
        "where",
        "who",
        "which",
        "that",
        "this",
        "these",
        "those",
        "we",
        "our",
        "more",
        "most",
        "some",
        "any",
        "become",
        "became",
        "follow",
        "share",
        "subscribe",
        "comment",
        "like",
        "dream",
        "distant",
        "just",
        "already",
        "dramatically",
        "rapidly",
    }
)


# Scene planner records the script section type inside its visual rationale;
# this recovers it without re-reading the production plan.
_SECTION_RATIONALE_RE = re.compile(r"Section type '(\w+)'")


def _distill_stock_query(raw: str, max_words: int = 4, fallback: str = "energy technology") -> str:
    """Extract a compact keyword phrase suitable for Wikimedia Commons image search.

    Long narration sentences match PDF documents, not photos.  This strips
    directive prefixes ("Illustrate and reinforce the narration: "), removes
    stop-words, and returns 2–4 substantive nouns for better image matching.
    """
    # If the text contains a directive colon ("Directive: Content"), take the content half
    if ": " in raw:
        raw = raw.split(": ", 1)[1]

    # Remove rhetorical question openers
    raw = re.sub(r"^(what if|follow for|how|why|but|and)\s+", "", raw, flags=re.IGNORECASE).strip()

    # Split on sentence boundaries and exclamation/question marks; take first clause
    raw = re.split(r"[!?.]", raw)[0].strip()

    # Tokenise and filter stop-words / very short tokens
    tokens = [
        t
        for t in re.split(r"[\s,;:()\[\]]+", raw.lower())
        if len(t) > 2 and t not in _STOP_WORDS and t.isalpha()
    ]

    # Keep first max_words substantive tokens
    keywords = tokens[:max_words]
    return " ".join(keywords) if keywords else fallback


def _blocked(
    req: StageExecutionRequest, key: str, category: str, message: str
) -> StageExecutionResult:
    return StageExecutionResult(
        stage=req.stage,
        executor_key=key,
        executor_version=EXECUTOR_CONTRACT_VERSION,
        status="blocked",
        error_category=category,
        error_message=message,
    )


def _failed(req: StageExecutionRequest, key: str, message: str) -> StageExecutionResult:
    return StageExecutionResult(
        stage=req.stage,
        executor_key=key,
        executor_version=EXECUTOR_CONTRACT_VERSION,
        status="failed",
        error_category="execution_error",
        error_message=message,
    )


def _cfg_str(effective_config: dict[str, Any], key: str, fallback: str = "") -> str:
    val = effective_config.get(key)
    if val is not None:
        return str(val)
    return fallback


def _get_artifacts_path(effective_config: dict[str, Any]) -> Path:
    from app.core.config import get_config

    raw = effective_config.get("artifacts_path")
    if raw:
        return Path(str(raw))
    return Path(get_config().artifacts_path)


# ---------------------------------------------------------------------------
# ResearchExecutor  (Stage A — topic-seeded synthetic brief, no HTTP fetch)
# ---------------------------------------------------------------------------


class ResearchExecutor:
    """Generate a minimal research record so the pipeline can continue.

    If ``research_urls`` is provided in effective_config, URLs are fetched and
    claims extracted via the Claude provider.  Without URLs (the default for a
    first local test) the executor marks research as completed with no claims,
    allowing ScriptGenerationExecutor to proceed in allow_no_evidence mode.

    Credentials:
      effective_config["anthropic_api_key"] or ACE_ANTHROPIC_API_KEY env var.
      Required only when research_urls are provided.
    """

    executor_key = "research_v1"
    executor_version = EXECUTOR_CONTRACT_VERSION

    def execute(self, conn: Any, req: StageExecutionRequest) -> StageExecutionResult:
        urls: list[str] = req.effective_config.get("research_urls") or []

        if not urls:
            # No URLs → synthetic no-source completion.  Script will run with
            # allow_no_evidence=True using topic title/angle as sole context.
            return StageExecutionResult(
                stage=req.stage,
                executor_key=self.executor_key,
                executor_version=self.executor_version,
                status="completed",
                artifact_type="research_brief",
                artifact_id="no-source",
            )

        # URL mode — requires Claude provider.
        return self._run_with_urls(conn, req, urls)

    def _run_with_urls(
        self, conn: Any, req: StageExecutionRequest, urls: list[str]
    ) -> StageExecutionResult:
        from app.core.config import get_config

        cfg = get_config()
        api_key = _cfg_str(req.effective_config, "anthropic_api_key") or cfg.anthropic_api_key
        if not api_key:
            return _blocked(
                req,
                self.executor_key,
                "missing_credentials",
                "Research with URLs requires ACE_ANTHROPIC_API_KEY "
                "(or effective_config['anthropic_api_key']).",
            )

        if req.topic_id is None:
            return _blocked(req, self.executor_key, "prerequisite_missing", "topic_id is required")

        try:
            import datetime

            from app.ai.claude import ClaudeProvider
            from app.core.repository import get_topic
            from app.research.extractor import extract_claims
            from app.research.fetch import fetch_url
            from app.research.models import ExtractionStatus, FetchStatus
            from app.research.repository import (
                create_source_content,
                get_or_create_source,
            )

            provider = ClaudeProvider(api_key=api_key)
            topic = get_topic(conn, req.topic_id)
            if topic is None:
                return _blocked(
                    req,
                    self.executor_key,
                    "prerequisite_missing",
                    f"Topic {req.topic_id} not found",
                )

            for url in urls[:5]:  # cap at 5 to avoid runaway costs
                try:
                    fetched = fetch_url(url)
                except Exception:
                    continue  # skip unreachable URLs; don't abort

                source = get_or_create_source(conn, url=url, workspace_id=req.workspace_id)
                raw_text = fetched.content.decode("utf-8", errors="replace")
                source_content = create_source_content(
                    conn,
                    source_id=source.id,
                    fetch_status=FetchStatus.fetched,
                    extraction_status=ExtractionStatus.pending,
                    fetched_at=datetime.datetime.utcnow().isoformat(),
                    http_status=fetched.http_status,
                    canonical_url=fetched.canonical_url,
                    mime_type=fetched.mime_type,
                    raw_text=raw_text,
                    word_count=len(raw_text.split()),
                )
                try:
                    extract_claims(conn, source_content, provider=provider)
                except Exception:
                    pass  # extraction failure is non-fatal; continue to next URL

            conn.commit()
            return StageExecutionResult(
                stage=req.stage,
                executor_key=self.executor_key,
                executor_version=self.executor_version,
                status="completed",
                artifact_type="research_brief",
                artifact_id=f"ws:{req.workspace_id}:topic:{req.topic_id}",
            )

        except Exception as exc:
            return _failed(req, self.executor_key, str(exc))


# ---------------------------------------------------------------------------
# ScriptGenerationExecutor  (Stage B — requires Claude)
# ---------------------------------------------------------------------------


class ScriptGenerationExecutor:
    """Generate a video script using the Claude AI provider.

    Returns waiting_for_review — human approval required before production_plan
    can run.

    Credentials:
      effective_config["anthropic_api_key"] or ACE_ANTHROPIC_API_KEY env var.
      Fails closed (blocked) when missing.
    """

    executor_key = "script_generation_v1"
    executor_version = EXECUTOR_CONTRACT_VERSION

    def execute(self, conn: Any, req: StageExecutionRequest) -> StageExecutionResult:
        if req.topic_id is None:
            return _blocked(
                req,
                self.executor_key,
                "prerequisite_missing",
                "topic_id is required for script_generation",
            )

        try:
            from app.core.config import get_config
            from app.core.repository import get_topic
            from app.research.repository import list_active_evidence_for_topic

            topic = get_topic(conn, req.topic_id)
            if topic is None:
                return _blocked(
                    req,
                    self.executor_key,
                    "prerequisite_missing",
                    f"Topic {req.topic_id} not found",
                )

            evidence = list_active_evidence_for_topic(conn, req.topic_id)

            cfg = get_config()
            api_key = _cfg_str(req.effective_config, "anthropic_api_key") or cfg.anthropic_api_key

            if not api_key:
                return _blocked(
                    req,
                    self.executor_key,
                    "missing_credentials",
                    "ScriptGenerationExecutor requires ACE_ANTHROPIC_API_KEY "
                    "(or effective_config['anthropic_api_key']).",
                )

            from app.ai.claude import ClaudeProvider
            from app.content.generator import generate_script

            ai_model = _cfg_str(req.effective_config, "ai_model") or cfg.ai_model
            provider = ClaudeProvider(api_key=api_key, model=ai_model)

            profile_name = _cfg_str(req.effective_config, "production_profile")
            target_duration_s: int | None = None
            min_words: int | None = None
            max_words: int | None = None
            if profile_name:
                from app.profiles.registry import get_profile

                profile = get_profile(profile_name)
                target_duration_s = profile.target_duration_s
                min_words = profile.min_word_count
                max_words = profile.max_word_count

            script_kwargs: dict = {"allow_no_evidence": True}
            if target_duration_s is not None:
                script_kwargs["target_duration_s"] = target_duration_s
            if min_words is not None:
                script_kwargs["min_words"] = min_words
            if max_words is not None:
                script_kwargs["max_words"] = max_words
            # Phase 18B: propagate the strategy brief's content constraints
            # (brand_voice/content_style/audience_description) when the
            # caller supplies them — generate_script already accepts tone/
            # audience; the manual pipeline simply never had a source for
            # them before. Absent (the existing manual-pipeline default),
            # generate_script's own defaults apply unchanged.
            tone = _cfg_str(req.effective_config, "tone")
            if tone:
                script_kwargs["tone"] = tone
            audience = _cfg_str(req.effective_config, "audience")
            if audience:
                script_kwargs["audience"] = audience

            result = generate_script(conn, provider, topic, evidence, **script_kwargs)
            conn.commit()

        except Exception as exc:
            return _failed(req, self.executor_key, str(exc))

        return StageExecutionResult(
            stage=req.stage,
            executor_key=self.executor_key,
            executor_version=self.executor_version,
            status="waiting_for_review",
            review_required=True,
            artifact_type="generated_script",
            artifact_id=str(result.script_id),
        )


# ---------------------------------------------------------------------------
# NarrationExecutor  (Stage B — requires ElevenLabs or fake TTS)
# ---------------------------------------------------------------------------


class NarrationExecutor:
    """Synthesise narration audio for an approved production plan.

    Provider selection:
      - ACE_TTS_LIVE_ENABLED=true  → ElevenLabsTTSProvider (requires ACE_ELEVENLABS_API_KEY)
      - otherwise                  → FakeTTSProvider (silence WAV; safe for CI)

    Credentials:
      effective_config["elevenlabs_api_key"] or ACE_ELEVENLABS_API_KEY env var.
      Required only when tts_live_enabled is true.

    Config:
      effective_config["voice_profile_id"]  — integer; required.
      effective_config["artifacts_path"]    — Path; falls back to ACE_ARTIFACTS_PATH.
    """

    executor_key = "narration_v1"
    executor_version = EXECUTOR_CONTRACT_VERSION

    def execute(self, conn: Any, req: StageExecutionRequest) -> StageExecutionResult:
        if req.topic_id is None:
            return _blocked(
                req,
                self.executor_key,
                "prerequisite_missing",
                "topic_id is required for narration",
            )

        voice_profile_id_raw = req.effective_config.get("voice_profile_id")
        if voice_profile_id_raw is None:
            return _blocked(
                req,
                self.executor_key,
                "missing_config",
                "NarrationExecutor requires effective_config['voice_profile_id'] (integer).",
            )
        try:
            voice_profile_id = int(voice_profile_id_raw)
        except (TypeError, ValueError):
            return _blocked(
                req,
                self.executor_key,
                "missing_config",
                f"voice_profile_id must be an integer, got {voice_profile_id_raw!r}",
            )

        artifacts_path = _get_artifacts_path(req.effective_config)

        try:
            from app.core.config import get_config
            from app.learning.application import (
                consume_proposed_application,
                resolve_speaking_rate_override,
            )
            from app.narration.orchestrator import narrate_plan
            from app.production.repository import get_active_approved_production_plan

            cfg = get_config()
            # Resolve experiment-linked plan first; fall back to non-experiment plan.
            # Persisted experiment_id on the plan is authoritative (Phase 14B.1).
            plan = get_active_approved_production_plan(
                conn, req.topic_id, experiment_id=req.experiment_id
            ) or get_active_approved_production_plan(conn, req.topic_id)
            if plan is None:
                return _blocked(
                    req,
                    self.executor_key,
                    "prerequisite_missing",
                    f"No active approved production plan for topic {req.topic_id}",
                )

            provider = self._build_provider(req.effective_config, cfg)
            if isinstance(provider, str):
                # Error message returned as string sentinel
                return _blocked(req, self.executor_key, "missing_credentials", provider)

            # Phase 14B.1: authoritative lineage wins.
            # plan.experiment_id is the persisted binding and cannot be overridden.
            _plan_exp_id = plan.experiment_id
            if (
                _plan_exp_id is not None
                and req.experiment_id is not None
                and req.experiment_id != _plan_exp_id
            ):
                return _blocked(
                    req,
                    self.executor_key,
                    "lineage_conflict",
                    f"experiment_id mismatch: request supplied {req.experiment_id!r} but "
                    f"production plan {plan.id} is bound to experiment {_plan_exp_id!r}",
                )
            effective_experiment_id = (
                _plan_exp_id if _plan_exp_id is not None else req.experiment_id
            )

            # Derive channel for learning-application scope.
            # Priority 1: experiment lineage (authoritative — persisted at experiment creation).
            # Priority 2: req.channel_id (cp_channel UUID from pipeline execution) bridged to
            #   INTEGER channels.id via cp_channel_id lookup.
            # Fallback: NULL (legacy path for old pipeline runs with no channel identity).
            _narration_channel_id: int | None = None
            if effective_experiment_id is not None:
                _ch_row = conn.execute(
                    "SELECT channel_id FROM experiments WHERE id = ?",
                    (effective_experiment_id,),
                ).fetchone()
                if _ch_row:
                    _narration_channel_id = _ch_row["channel_id"]
            elif req.channel_id is not None:
                _bridge_row = conn.execute(
                    "SELECT id FROM channels WHERE cp_channel_id = ?",
                    (req.channel_id,),
                ).fetchone()
                if _bridge_row:
                    _narration_channel_id = int(_bridge_row["id"])

            active_app, speaking_rate_override = resolve_speaking_rate_override(
                conn, topic_id=req.topic_id, channel_id=_narration_channel_id
            )

            # Phase 14F/14F.1: experiment execution contract governs speaking_rate when the
            # experiment explicitly declares it as a TREATMENT or CONTROL factor.
            # TREATMENT: experiment tests the rate at intended_value → override + suppress learn.
            # CONTROL:   experiment holds the rate at baseline (ENFORCED) → override + suppress.
            # NOT_GOVERNING: experiment has a contract but does not own this parameter →
            #   Learning Application continues to govern (active_app unchanged).
            if effective_experiment_id is not None:
                from app.intelligence.experiments.execution_contract import ParameterAuthority
                from app.intelligence.experiments.execution_service import (
                    resolve_narration_speaking_rate_authority,
                )

                contract_rate, authority = resolve_narration_speaking_rate_authority(
                    conn, effective_experiment_id
                )
                if authority in (
                    ParameterAuthority.EXPERIMENT_TREATMENT,
                    ParameterAuthority.EXPERIMENT_CONTROL,
                ):
                    speaking_rate_override = contract_rate
                    active_app = None  # suppress: experiment governs, do not consume learning app

            run_result = narrate_plan(
                conn,
                plan_id=plan.id,
                plan_input_hash=plan.input_hash,
                voice_profile_id=voice_profile_id,
                artifacts_path=artifacts_path,
                provider=provider,
                speaking_rate_override=speaking_rate_override,
                experiment_id=effective_experiment_id,
            )
            conn.commit()

            if active_app is not None and speaking_rate_override is not None:
                consume_proposed_application(
                    conn,
                    active_app,
                    narration_run_id=run_result.run_id,
                    value_applied=speaking_rate_override,
                )

        except Exception as exc:
            return _failed(req, self.executor_key, str(exc))

        return StageExecutionResult(
            stage=req.stage,
            executor_key=self.executor_key,
            executor_version=self.executor_version,
            status="waiting_for_review",
            review_required=True,
            artifact_type="narration_run",
            artifact_id=str(run_result.run_id),
        )

    @staticmethod
    def _build_provider(effective_config: dict[str, Any], cfg: Any) -> Any:
        if cfg.tts_live_enabled:
            api_key = _cfg_str(effective_config, "elevenlabs_api_key") or cfg.elevenlabs_api_key
            if not api_key:
                return (
                    "NarrationExecutor: ACE_TTS_LIVE_ENABLED=true but "
                    "ACE_ELEVENLABS_API_KEY is not set."
                )
            from app.narration.providers.elevenlabs import ElevenLabsTTSProvider

            provider = ElevenLabsTTSProvider(api_key=api_key)
            provider.initialize()
            return provider
        else:
            from app.narration.fake import FakeTTSProvider

            return FakeTTSProvider()


# ---------------------------------------------------------------------------
# CaptionsExecutor  (Stage B — deterministic, no network)
# ---------------------------------------------------------------------------


class CaptionsExecutor:
    """Generate SRT/VTT/JSON caption artifacts from an approved narration run.

    No network calls.  Requires approved narration run on disk.

    Config:
      effective_config["artifacts_path"] — falls back to ACE_ARTIFACTS_PATH.
    """

    executor_key = "captions_v1"
    executor_version = EXECUTOR_CONTRACT_VERSION

    def execute(self, conn: Any, req: StageExecutionRequest) -> StageExecutionResult:
        if req.topic_id is None:
            return _blocked(
                req,
                self.executor_key,
                "prerequisite_missing",
                "topic_id is required for captions",
            )

        artifacts_path = _get_artifacts_path(req.effective_config)

        plan_id_raw = req.prerequisite_artifact_ids.get("production_plan")
        if plan_id_raw is None:
            return _blocked(
                req,
                self.executor_key,
                "prerequisite_missing",
                "captions requires prerequisite_artifact_ids['production_plan'] (plan_id)",
            )
        try:
            plan_id = int(plan_id_raw)
        except (TypeError, ValueError):
            return _blocked(
                req,
                self.executor_key,
                "prerequisite_missing",
                f"production_plan artifact_id must be an integer plan_id, got {plan_id_raw!r}",
            )

        try:
            from app.captions.orchestrator import generate_captions

            _cap_plan_row = conn.execute(
                "SELECT experiment_id FROM production_plans WHERE id = ?", (plan_id,)
            ).fetchone()
            # Phase 14B.1: authoritative lineage wins.
            _cap_plan_exp_id = _cap_plan_row["experiment_id"] if _cap_plan_row else None
            if (
                _cap_plan_exp_id is not None
                and req.experiment_id is not None
                and req.experiment_id != _cap_plan_exp_id
            ):
                return _blocked(
                    req,
                    self.executor_key,
                    "lineage_conflict",
                    f"experiment_id mismatch: request supplied {req.experiment_id!r} but "
                    f"production plan {plan_id} is bound to experiment {_cap_plan_exp_id!r}",
                )
            _cap_effective_experiment_id = (
                _cap_plan_exp_id if _cap_plan_exp_id is not None else req.experiment_id
            )
            caption_run = generate_captions(
                conn,
                plan_id=plan_id,
                artifacts_path=artifacts_path,
                experiment_id=_cap_effective_experiment_id,
            )

        except Exception as exc:
            return _failed(req, self.executor_key, str(exc))

        return StageExecutionResult(
            stage=req.stage,
            executor_key=self.executor_key,
            executor_version=self.executor_version,
            status="waiting_for_review",
            review_required=True,
            artifact_type="caption_run",
            artifact_id=str(caption_run.id),
        )


# ---------------------------------------------------------------------------
# VisualIntelligenceExecutor  (Stage B — builds scene manifest, placeholder visuals)
# ---------------------------------------------------------------------------


class VisualIntelligenceExecutor:
    """Build a scene manifest from approved narration + captions.

    Visual assets are placeholder (solid colour) — no image-gen provider is
    required for the first test.  The manifest is persisted to the DB and
    returned as waiting_for_review.
    """

    executor_key = "visual_intelligence_v1"
    executor_version = EXECUTOR_CONTRACT_VERSION

    def execute(self, conn: Any, req: StageExecutionRequest) -> StageExecutionResult:
        if req.topic_id is None:
            return _blocked(
                req,
                self.executor_key,
                "prerequisite_missing",
                "topic_id is required for visual_intelligence",
            )

        plan_id_raw = req.prerequisite_artifact_ids.get("production_plan")
        if plan_id_raw is None:
            return _blocked(
                req,
                self.executor_key,
                "prerequisite_missing",
                "visual_intelligence requires prerequisite_artifact_ids['production_plan']",
            )
        try:
            plan_id = int(plan_id_raw)
        except (TypeError, ValueError):
            return _blocked(
                req,
                self.executor_key,
                "prerequisite_missing",
                f"production_plan artifact_id must be integer plan_id, got {plan_id_raw!r}",
            )

        try:
            from app.captions.repository import get_active_approved_caption_run
            from app.narration.repository import get_approved_narration_run_full
            from app.production.repository import get_approved_production_plan_full
            from app.scenes.planner import build_scene_manifest
            from app.scenes.repository import get_or_create_scene_manifest

            # Phase 14B.1: authoritative lineage wins.
            # Must resolve experiment_id BEFORE narration/caption lookups (they filter on it).
            _vi_plan_row = conn.execute(
                "SELECT experiment_id FROM production_plans WHERE id = ?", (plan_id,)
            ).fetchone()
            _vi_plan_exp_id = _vi_plan_row["experiment_id"] if _vi_plan_row else None
            if (
                _vi_plan_exp_id is not None
                and req.experiment_id is not None
                and req.experiment_id != _vi_plan_exp_id
            ):
                return _blocked(
                    req,
                    self.executor_key,
                    "lineage_conflict",
                    f"experiment_id mismatch: request supplied {req.experiment_id!r} but "
                    f"production plan {plan_id} is bound to experiment {_vi_plan_exp_id!r}",
                )
            _vi_effective_experiment_id = (
                _vi_plan_exp_id if _vi_plan_exp_id is not None else req.experiment_id
            )

            # Load narration run
            narration_run = get_approved_narration_run_full(
                conn, plan_id, experiment_id=_vi_effective_experiment_id
            )
            if narration_run is None:
                return _blocked(
                    req,
                    self.executor_key,
                    "prerequisite_missing",
                    f"No approved narration run for plan {plan_id}",
                )

            # Load caption run (keyed off narration_run_id)
            caption_run = get_active_approved_caption_run(
                conn, narration_run.run_id, experiment_id=_vi_effective_experiment_id
            )
            if caption_run is None:
                return _blocked(
                    req,
                    self.executor_key,
                    "prerequisite_missing",
                    f"No approved caption run for narration run {narration_run.run_id}",
                )

            # Load production plan — experiment-linked plans resolved first.
            production_plan = get_approved_production_plan_full(
                conn, req.topic_id, experiment_id=_vi_effective_experiment_id
            ) or get_approved_production_plan_full(conn, req.topic_id)
            if production_plan is None:
                return _blocked(
                    req,
                    self.executor_key,
                    "prerequisite_missing",
                    f"No approved production plan for topic {req.topic_id}",
                )

            draft = build_scene_manifest(
                conn,
                caption_run=caption_run,
                narration_run=narration_run,
                production_plan=production_plan,
            )
            manifest, _ = get_or_create_scene_manifest(conn, draft)
            conn.commit()

        except Exception as exc:
            return _failed(req, self.executor_key, str(exc))

        return StageExecutionResult(
            stage=req.stage,
            executor_key=self.executor_key,
            executor_version=self.executor_version,
            status="waiting_for_review",
            review_required=True,
            artifact_type="scene_manifest",
            artifact_id=str(manifest.id),
        )


# ---------------------------------------------------------------------------
# RenderingExecutor  (Stage B — FFmpeg render to local MP4)
# ---------------------------------------------------------------------------


@dataclass
class VisualAssessmentOutcome:
    """The visual-quality verdict for a render, plus how it was arrived at.

    Carried out of visual planning so `execute` can persist it against the
    render manifest id, which does not exist yet while the beats are being
    resolved.
    """

    assessment: Any
    remediation_attempted: bool = False


class RenderingExecutor:
    """Compose and render the final MP4 using FFmpeg.

    Reads the approved scene manifest, builds the render manifest, runs
    FFmpegRenderBackend, and returns the output path as the artifact_id.

    Config:
      effective_config["artifacts_path"]    — falls back to ACE_ARTIFACTS_PATH.
      effective_config["renders_dir"]       — override output directory (optional).
      effective_config["allow_placeholders"] — bool, default True (placeholder slides
                                              are acceptable for first local test).
    """

    executor_key = "rendering_v1"
    executor_version = EXECUTOR_CONTRACT_VERSION

    # ── Semantic visual planning ────────────────────────────────────────────

    @staticmethod
    def _section_type(approved_scene: Any) -> str:
        """Recover the scene's script section type from its planner rationale."""
        match = _SECTION_RATIONALE_RE.search(getattr(approved_scene, "visual_rationale", "") or "")
        return match.group(1) if match else "body"

    def _plan_visuals(
        self,
        conn: Any,
        req: StageExecutionRequest,
        *,
        approved: Any,
        scene_inputs: list,
        artifacts_path: Path,
        width: int,
        height: int,
    ) -> tuple[Any, Any, Any]:
        """Plan semantic beats, resolve a visual for each, and audit the result.

        Beats are attached to the scene inputs so the render backend can cut
        the visual track without touching narration timing.  Returns the
        resolved plan, its QA report, and its Phase 18E visual-quality
        assessment.

        Remediation happens HERE, before a single frame is encoded: when the
        first resolution pass leaves the video dominated by fallback cards
        because retrieval failed, the deficient beats are re-resolved with
        widened queries and a lower (not absent) relevance bar.  Doing it at
        this point is what makes it cheap — no render is thrown away, and the
        run's provider-search memo means reconsidering a query already issued
        costs nothing.
        """
        import logging as _logging

        from app.captions.repository import get_caption_cues
        from app.media.models import RenderVisualBeat, ResolvedAsset
        from app.visuals.beats import BeatPlannerInput, CueWindow, plan_beats
        from app.visuals.composition import composition_from_plan
        from app.visuals.engine import VisualEngine, VisualEngineConfig
        from app.visuals.policy import policy_from_config
        from app.visuals.qa import audit_visual_plan
        from app.visuals.quality import assess_composition
        from app.visuals.repository import save_visual_plan

        log = _logging.getLogger(__name__)
        policy = policy_from_config(req.effective_config)

        # Caption cues give clause-shaped, already-timed beat boundaries.
        cues_by_segment: dict[int, list[CueWindow]] = {}
        if approved.caption_run_id:
            for cue in get_caption_cues(conn, approved.caption_run_id):
                cues_by_segment.setdefault(cue.segment_id, []).append(
                    CueWindow(text=cue.text, start_ms=cue.start_ms, end_ms=cue.end_ms)
                )

        planner_inputs = [
            BeatPlannerInput(
                scene_index=scene.scene_index,
                scene_id=scene.scene_id,
                segment_id=scene.segment_id,
                start_ms=scene.start_ms,
                duration_ms=scene.duration_ms,
                narration_text=scene.narration_text,
                section_type=self._section_type(scene),
                claim_ids=list(getattr(scene, "claim_ids", []) or []),
                cues=cues_by_segment.get(scene.segment_id),
            )
            for scene in approved.scenes
        ]

        beats = plan_beats(
            planner_inputs,
            target_beat_ms=policy.target_beat_ms,
            min_beat_ms=policy.min_beat_ms,
            max_beat_ms=policy.max_beat_ms,
        )

        engine = VisualEngine(
            VisualEngineConfig(
                width=width,
                height=height,
                policy=policy,
                channel_key=req.channel_id,
                scene_manifest_id=approved.manifest_id,
                topic_id=approved.topic_id,
                experiment_id=approved.experiment_id,
                cache_dir=artifacts_path / "visuals" / "cache",
                graphics_dir=artifacts_path
                / "visuals"
                / "graphics"
                / f"manifest_{approved.manifest_id}",
            ),
            conn=conn,
        )
        plan = engine.resolve(beats)

        # ── Bounded visual remediation (Phase 18E) ─────────────────────────
        # One pass, targeting only beats that fell back because a PROVIDER
        # failed. A beat that chose a diagram on purpose is never touched, and
        # a beat that still finds nothing keeps the card it already has, so
        # this can improve a plan but never damage one.
        composition = composition_from_plan(plan)
        assessment = assess_composition(composition, visual_style=policy.style)
        remediated = False
        if assessment.blocked and bool(
            req.effective_config.get("visual_remediation_enabled", True)
        ):
            deficient = composition.deficient_beat_indexes()
            if deficient:
                log.info(
                    "Visual quality %s before remediation (%.0f%% meaningful runtime); "
                    "attempting to repair %d beat(s)",
                    assessment.status,
                    composition.meaningful_runtime_pct * 100,
                    len(deficient),
                )
                plan = engine.remediate(plan, deficient)
                composition = composition_from_plan(plan)
                assessment = assess_composition(composition, visual_style=policy.style)
                remediated = True
                log.info(
                    "Visual quality %s after remediation (%.0f%% meaningful runtime)",
                    assessment.status,
                    composition.meaningful_runtime_pct * 100,
                )

        save_visual_plan(conn, plan, workspace_id=req.workspace_id)
        conn.commit()

        # Attach beats to their scenes, and expose the scene's opening visual
        # as its primary asset so downstream manifest projections stay populated.
        by_scene: dict[int, list[RenderVisualBeat]] = {}
        for resolution in plan.resolutions:
            beat = resolution.beat
            by_scene.setdefault(beat.scene_index, []).append(
                RenderVisualBeat(
                    beat_index=beat.beat_index,
                    start_ms=beat.start_ms,
                    end_ms=beat.end_ms,
                    duration_ms=beat.duration_ms,
                    local_path=resolution.local_path,
                    media_type=resolution.media_type,
                    motion=resolution.motion,
                    fit_mode=resolution.fit_mode,
                    asset_key=resolution.asset_key,
                    provider=resolution.provider,
                    license_status=resolution.license_status,
                    commercial_safe=resolution.commercial_safe,
                    visual_intent=beat.visual_intent,
                    label=beat.primary_query or beat.narration_text[:60],
                )
            )

        for scene_input in scene_inputs:
            scene_beats = by_scene.get(scene_input.scene_index, [])
            scene_input.visual_beats = scene_beats
            first = next((b for b in scene_beats if b.resolved), None)
            if first is not None:
                scene_input.resolved_assets = [
                    ResolvedAsset(
                        asset_id=-1,
                        scene_id=scene_input.scene_id,
                        segment_id=scene_input.segment_id,
                        asset_index=0,
                        category=first.media_type,
                        priority="required",
                        local_path=first.local_path,
                        local_sha256=None,
                        source_url=None,
                        license_status=first.license_status,
                        commercial_safe=first.commercial_safe,
                    )
                ]

        report = audit_visual_plan(plan, require_commercial_safe=policy.require_commercial_safe)
        log.info(
            "Visual plan: %d beats, QA=%s, providers=%s",
            len(plan.beats),
            report.status,
            plan.provider_calls,
        )
        for finding in report.findings:
            log.warning("Visual QA [%s] %s: %s", finding.severity, finding.code, finding.message)

        for finding in assessment.findings:
            log.warning(
                "Visual quality [%s] %s: %s", finding.severity, finding.code, finding.message
            )

        return (
            plan,
            report,
            VisualAssessmentOutcome(assessment=assessment, remediation_attempted=remediated),
        )

    def execute(self, conn: Any, req: StageExecutionRequest) -> StageExecutionResult:
        if req.topic_id is None:
            return _blocked(
                req,
                self.executor_key,
                "prerequisite_missing",
                "topic_id is required for rendering",
            )

        artifacts_path = _get_artifacts_path(req.effective_config)
        allow_placeholders: bool = bool(req.effective_config.get("allow_placeholders", True))

        try:
            from app.media.backend import (
                FFMPEG_BACKEND_VERSION,
                FFmpegRenderBackend,
                check_ffmpeg_available,
            )
            from app.media.compositor import SceneInputBuilder, build_render_manifest
            from app.media.repository import (
                create_render_job,
                get_or_create_render_manifest,
                list_render_jobs,
                mark_render_job_completed,
                mark_render_job_rendering,
            )
            from app.scenes.repository import get_approved_scene_manifest_full

            if not check_ffmpeg_available():
                return _blocked(
                    req,
                    self.executor_key,
                    "missing_config",
                    "ffmpeg not found on PATH. Install FFmpeg to render videos.",
                )

            approved = get_approved_scene_manifest_full(conn, req.topic_id)
            if approved is None:
                return _blocked(
                    req,
                    self.executor_key,
                    "prerequisite_missing",
                    f"No approved scene manifest for topic {req.topic_id}",
                )

            builder = SceneInputBuilder(conn)
            scene_inputs = builder.build(approved)

            # Resolve relative audio paths to absolute (DB stores paths relative to artifacts dir)
            for scene in scene_inputs:
                if scene.audio_path and not Path(scene.audio_path).is_absolute():
                    scene.audio_path = str((artifacts_path / scene.audio_path).resolve())

            render_profile_name = _cfg_str(req.effective_config, "production_profile")
            render_kwargs: dict = {}
            render_w, render_h = 1080, 1920  # default shorts dimensions
            if render_profile_name:
                from app.profiles.registry import get_profile as _get_profile

                rp = _get_profile(render_profile_name)
                render_kwargs = {"width": rp.width, "height": rp.height, "fps": rp.fps}
                render_w, render_h = rp.width, rp.height

            # ── Semantic visual planning + asset resolution ────────────────
            # Narration is segmented into semantic beats; each beat retrieves
            # and scores its own candidates across the provider stack and
            # falls back to a locally generated explanatory graphic when no
            # candidate is relevant enough to be worth showing.
            visual_plan, visual_qa, visual_outcome = self._plan_visuals(
                conn,
                req,
                approved=approved,
                scene_inputs=scene_inputs,
                artifacts_path=artifacts_path,
                width=render_w,
                height=render_h,
            )

            render_draft = build_render_manifest(
                scene_manifest_id=approved.manifest_id,
                scene_manifest_input_hash=approved.input_hash,
                narration_run_id=approved.narration_run_id,
                narration_input_hash=builder.narration_input_hash,
                caption_run_id=approved.caption_run_id,
                topic_id=approved.topic_id,
                plan_id=approved.plan_id,
                script_id=approved.script_id,
                scenes=scene_inputs,
                caption_burn_in=approved.caption_run_id is not None,
                experiment_id=approved.experiment_id,
                **render_kwargs,
            )

            manifest, _ = get_or_create_render_manifest(conn, render_draft)

            # ── Persist the visual-quality assessment (Phase 18E) ──────────
            # Keyed on the render manifest and upserted, so a re-run of this
            # stage refreshes the verdict instead of accumulating duplicates.
            # Publishing preflight reads this row; it never re-measures.
            try:
                from app.visuals.assessment_repository import (
                    record_remediation_attempt,
                    save_assessment,
                )
                from app.visuals.repository import attach_render_manifest

                save_assessment(
                    conn,
                    visual_outcome.assessment,
                    render_manifest_id=manifest.id,
                    scene_manifest_id=approved.manifest_id,
                    workspace_id=req.workspace_id,
                    channel_id=req.channel_id,
                    experiment_id=approved.experiment_id,
                    remediated=visual_outcome.remediation_attempted,
                )
                if visual_outcome.remediation_attempted:
                    record_remediation_attempt(conn, manifest.id)
                # Backfill asset-usage lineage now that the render manifest
                # this plan belongs to actually exists.
                attach_render_manifest(conn, approved.manifest_id, manifest.id)
                conn.commit()
            except Exception as assess_exc:  # noqa: BLE001
                import logging as _logging

                _logging.getLogger(__name__).warning(
                    "Visual quality assessment could not be persisted (non-fatal "
                    "here; publishing preflight treats a missing assessment as a "
                    "hard block): %s",
                    assess_exc,
                )

            renders_dir_raw = req.effective_config.get("renders_dir")
            if renders_dir_raw:
                renders_dir = Path(str(renders_dir_raw))
            else:
                renders_dir = artifacts_path / "renders" / str(approved.manifest_id)
            renders_dir.mkdir(parents=True, exist_ok=True)
            output_path = renders_dir / f"render_{manifest.id}.mp4"

            # Idempotency: reuse existing completed render if the file is intact.
            existing_jobs = list_render_jobs(conn, manifest.id)
            for existing in existing_jobs:
                if (
                    existing.status == "completed"
                    and existing.output_path
                    and Path(existing.output_path).exists()
                    and Path(existing.output_path).stat().st_size > 0
                ):
                    return StageExecutionResult(
                        stage=req.stage,
                        executor_key=self.executor_key,
                        executor_version=self.executor_version,
                        status="waiting_for_review",
                        review_required=True,
                        artifact_type="render_job",
                        artifact_id=existing.output_path,
                    )

            job = create_render_job(
                conn,
                manifest.id,
                backend="ffmpeg",
                backend_version=FFMPEG_BACKEND_VERSION,
                width=render_draft.width,
                height=render_draft.height,
                fps=render_draft.fps,
                video_codec="libx264",
                audio_codec="aac",
                crf=23,
                audio_bitrate="128k",
                caption_burn_in=render_draft.caption_burn_in,
            )
            conn.commit()

            mark_render_job_rendering(conn, job.id)
            conn.commit()

            backend = FFmpegRenderBackend()
            with tempfile.TemporaryDirectory(prefix="ace_render_") as tmp:
                result = backend.render(
                    render_draft,
                    output_path,
                    Path(tmp),
                    allow_placeholders=allow_placeholders,
                )

            # ── Caption burn-in ────────────────────────────────────────────
            # Load caption JSON, compute absolute timestamps from segment
            # durations, then composite captions over the rendered MP4.
            final_sha256 = result.output_sha256
            final_file_size = result.file_size_bytes
            try:
                from app.captions.repository import get_caption_run
                from app.media.caption_renderer import burn_captions_into

                caption_run_row = get_caption_run(conn, approved.caption_run_id)
                if caption_run_row and caption_run_row.json_path:
                    cues_json_path = artifacts_path / caption_run_row.json_path

                    # Build segment_id → cumulative ms offset map from narration assets
                    seg_rows = conn.execute(
                        "SELECT segment_id, duration_seconds FROM narration_segment_assets "
                        "WHERE run_id=? ORDER BY id",
                        (approved.narration_run_id,),
                    ).fetchall()
                    segment_offsets_ms: dict[int, int] = {}
                    cumulative = 0
                    for sr in seg_rows:
                        segment_offsets_ms[sr["segment_id"]] = cumulative
                        cumulative += int(sr["duration_seconds"] * 1000)

                    captioned_path = output_path.with_stem(output_path.stem + "_captioned")
                    cap_tmp = Path(tmp) / "cap_pngs"
                    burn_captions_into(
                        input_mp4=output_path,
                        cues_json_path=cues_json_path,
                        segment_offsets_ms=segment_offsets_ms,
                        output_mp4=captioned_path,
                        width=render_draft.width,
                        height=render_draft.height,
                        caption_tmp_dir=cap_tmp,
                    )
                    # Replace original with captioned version
                    captioned_path.replace(output_path)
                    # Recompute integrity metadata for the captioned file
                    final_sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
                    final_file_size = output_path.stat().st_size
            except Exception as cap_exc:
                # Caption burn-in failure is non-fatal; the uncaptioned render exists
                import logging as _logging

                _logging.getLogger(__name__).warning(
                    "Caption burn-in failed (non-fatal): %s", cap_exc
                )

            mark_render_job_completed(
                conn,
                job.id,
                output_path=result.output_path,
                output_sha256=final_sha256,
                duration_s=result.duration_s,
                file_size_bytes=final_file_size,
                render_time_s=result.render_time_s,
                ffmpeg_cmd=result.ffmpeg_cmd,
            )
            conn.commit()

        except Exception as exc:
            return _failed(req, self.executor_key, str(exc))

        return StageExecutionResult(
            stage=req.stage,
            executor_key=self.executor_key,
            executor_version=self.executor_version,
            status="waiting_for_review",
            review_required=True,
            artifact_type="render_job",
            artifact_id=result.output_path,
        )
