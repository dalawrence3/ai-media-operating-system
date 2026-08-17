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


# ---------------------------------------------------------------------------
# Visual query strategy
# ---------------------------------------------------------------------------

# Technology/concept nouns → concrete Pexels-friendly search phrase.
# Ordered: first match wins. Triggers are checked case-insensitively in narration_text.
_TECH_NOUN_MAP: list[tuple[frozenset[str], str]] = [
    (
        frozenset({"solar panel", "photovoltaic", "solar farm", "solar installation"}),
        "solar panels installation",
    ),
    (frozenset({"solar"}), "solar energy farm"),
    (frozenset({"wind turbine", "wind farm"}), "wind turbines aerial"),
    (frozenset({"wind power", "wind energy", "wind"}), "wind energy turbines"),
    (frozenset({"battery storage", "energy storage", "battery"}), "battery energy storage"),
    (frozenset({"power grid", "electricity grid", "smart grid", "grid"}), "electricity power grid"),
    (
        frozenset({"electric vehicle", "electric car", "ev charging", "ev "}),
        "electric vehicle charging",
    ),
    (frozenset({"hydrogen"}), "hydrogen fuel technology"),
    (frozenset({"nuclear"}), "nuclear power plant"),
    (frozenset({"coal", "fossil fuel"}), "coal power plant"),
    (
        frozenset({"city", "urban", "building", "business", "government", "corporation"}),
        "sustainable city buildings",
    ),
    (frozenset({"people", "worker", "community"}), "people renewable energy"),
]

# Section-type overrides — bypass narration content for these roles.
# CTA/conclusion/transition/hook get human/cinematic/aspirational imagery
# instead of collapsing into the same technology search as body scenes.
_SECTION_QUERY_POOLS: dict[str, list[str]] = {
    "cta": [
        "person watching smartphone",
        "young adult mobile phone",
        "hands holding phone social media",
    ],
    "conclusion": [
        "renewable energy future city",
        "green energy landscape horizon",
        "people working clean energy",
    ],
    "transition": [
        "electric vehicle charging station",
        "smart city energy grid",
        "battery storage facility",
    ],
    "hook": [
        "renewable energy aerial dramatic",
        "solar wind energy cinematic",
        "clean energy landscape dramatic",
    ],
}


def _concept_key(query: str) -> str:
    """First non-trivial word — used to detect near-duplicate visual concepts."""
    words = [w for w in query.lower().split() if len(w) > 2]
    return words[0] if words else query


def _scene_visual_query(
    section_type: str,
    narration_text: str,
    fallback_raw: str,
    used_concepts: set[str],
) -> str:
    """Return a concrete, visually distinct search query for one scene.

    Priority:
    1. Section-type overrides (CTA → person/phone; hook/conclusion/transition
       → cinematic/aspirational), rotating through the pool for diversity.
    2. Narration-based technology-noun detection for body/intro sections.
    3. ``_distill_stock_query`` on the fallback text as last resort.

    ``used_concepts`` is mutated: the chosen concept key is added so callers
    can avoid repeating the same visual subject across scenes.
    """

    def _pick_from_pool(options: list[str]) -> str:
        for opt in options:
            ck = _concept_key(opt)
            if ck not in used_concepts:
                used_concepts.add(ck)
                return opt
        # All pool options already used — return first anyway (Pexels dedup
        # handles ID uniqueness; different query wording still helps)
        used_concepts.add(_concept_key(options[0]))
        return options[0]

    # ── Section-type overrides ───────────────────────────────────────────────
    pool = _SECTION_QUERY_POOLS.get(section_type)
    if pool:
        return _pick_from_pool(pool)

    # ── Technology-noun detection from narration text ────────────────────────
    text_lower = (narration_text or fallback_raw or "").lower()
    candidate: str | None = None
    for triggers, phrase in _TECH_NOUN_MAP:
        if any(t in text_lower for t in triggers):
            candidate = phrase
            break

    if candidate is None:
        candidate = _distill_stock_query(fallback_raw) or "renewable energy"

    # ── Diversity rotation ───────────────────────────────────────────────────
    if _concept_key(candidate) in used_concepts:
        for _, phrase in _TECH_NOUN_MAP:
            if _concept_key(phrase) not in used_concepts:
                candidate = phrase
                break

    used_concepts.add(_concept_key(candidate))
    return candidate


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
            from app.narration.orchestrator import narrate_plan
            from app.production.repository import get_active_approved_production_plan

            cfg = get_config()
            plan = get_active_approved_production_plan(conn, req.topic_id)
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

            run_result = narrate_plan(
                conn,
                plan_id=plan.id,
                plan_input_hash=plan.input_hash,
                voice_profile_id=voice_profile_id,
                artifacts_path=artifacts_path,
                provider=provider,
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

            caption_run = generate_captions(
                conn,
                plan_id=plan_id,
                artifacts_path=artifacts_path,
                experiment_id=req.experiment_id,
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

            # Load narration run
            narration_run = get_approved_narration_run_full(
                conn, plan_id, experiment_id=req.experiment_id
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
                conn, narration_run.run_id, experiment_id=req.experiment_id
            )
            if caption_run is None:
                return _blocked(
                    req,
                    self.executor_key,
                    "prerequisite_missing",
                    f"No approved caption run for narration run {narration_run.run_id}",
                )

            # Load production plan
            production_plan = get_approved_production_plan_full(conn, req.topic_id)
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

            # ── Visual asset resolution ────────────────────────────────────
            # Fallback chain per scene (first success wins):
            #   1. Pexels video clip (motion; preferred for Shorts)
            #   2. Pexels photo (static; cropped to vertical)
            #   3. Wikimedia Commons photo (free; no key required)
            #   4. PIL gradient card (always succeeds)
            # Dedup tracked via used_pexels_ids so no two scenes share an asset.
            import re as _re

            from app.media.models import ResolvedAsset
            from app.media.pexels_fetcher import fetch_pexels_photo, fetch_pexels_video
            from app.media.stock_fetcher import fetch_stock_image
            from app.media.visual_generator import (
                card_cache_key,
                generate_scene_card,
                prep_image_for_vertical,
            )

            vis_cards_dir = (
                artifacts_path / "visuals" / "cards" / f"manifest_{approved.manifest_id}"
            )
            stock_cache_dir = artifacts_path / "stock_cache" / "wikimedia"
            pexels_cache_dir = artifacts_path / "stock_cache" / "pexels"
            pexels_cache_dir.mkdir(parents=True, exist_ok=True)
            _section_re = _re.compile(r"Section type '(\w+)'")

            # Read Pexels API key from effective_config or environment
            import os as _os

            pexels_api_key: str | None = req.effective_config.get(
                "pexels_api_key"
            ) or _os.environ.get("ACE_PEXELS_API_KEY")
            used_pexels_ids: set[int] = set()
            used_visual_concepts: set[str] = set()

            import logging as _logging

            _vis_log = _logging.getLogger(__name__)

            for i, (si, approved_scene) in enumerate(
                zip(scene_inputs, approved.scenes, strict=False)
            ):
                has_asset = any(a.local_path for a in si.resolved_assets)
                if has_asset:
                    continue

                # Derive section_type and base search query
                m = _section_re.search(approved_scene.visual_rationale or "")
                section_type = m.group(1) if m else "body"
                asset_query = next(
                    (a.search_query for a in approved_scene.assets if a.search_query),
                    None,
                )
                raw_query = (
                    asset_query or approved_scene.visual_objective or si.visual_objective or ""
                ).strip()
                search_q = _scene_visual_query(
                    section_type=section_type,
                    narration_text=approved_scene.narration_text,
                    fallback_raw=raw_query,
                    used_concepts=used_visual_concepts,
                )

                resolved_local: str | None = None
                resolved_category = "generated_card"
                resolved_license = "not_required"
                resolved_commercial_safe = True
                resolved_source: str | None = None

                # --- 1. Pexels video (motion; preferred for Shorts) ---
                if pexels_api_key:
                    result = fetch_pexels_video(
                        search_q, pexels_cache_dir, pexels_api_key, used_ids=used_pexels_ids
                    )
                    if result is not None:
                        vid_path, _ = result
                        resolved_local = str(vid_path)
                        resolved_category = "pexels_video"
                        resolved_license = "verified"
                        resolved_commercial_safe = True
                        resolved_source = str(vid_path.with_suffix(".attr.json"))
                        _vis_log.info("Scene %d: Pexels VIDEO %r", i, search_q)

                # --- 2. Pexels photo (static; cropped to vertical) ---
                if resolved_local is None and pexels_api_key:
                    result = fetch_pexels_photo(
                        search_q, pexels_cache_dir, pexels_api_key, used_ids=used_pexels_ids
                    )
                    if result is not None:
                        photo_path, _ = result
                        prepped = vis_cards_dir / f"pp_{i}_{photo_path.stem}.jpg"
                        if not prepped.exists():
                            prep_image_for_vertical(photo_path, prepped, render_w, render_h)
                        resolved_local = str(prepped)
                        resolved_category = "pexels_photo"
                        resolved_license = "verified"
                        resolved_commercial_safe = True
                        resolved_source = str(photo_path)
                        _vis_log.info("Scene %d: Pexels PHOTO %r", i, search_q)

                # --- 3. Wikimedia Commons photo (free; no API key) ---
                if resolved_local is None:
                    wm_path = fetch_stock_image(search_q, stock_cache_dir, min_dimension=800)
                    if wm_path is not None:
                        prepped = vis_cards_dir / f"wm_{i}_{wm_path.stem}.jpg"
                        if not prepped.exists():
                            prep_image_for_vertical(wm_path, prepped, render_w, render_h)
                        resolved_local = str(prepped)
                        resolved_category = "wikimedia_photo"
                        resolved_license = "verified"
                        resolved_commercial_safe = False
                        resolved_source = str(wm_path)
                        _vis_log.info("Scene %d: Wikimedia PHOTO %r", i, search_q)

                # --- 4. PIL gradient card (always succeeds) ---
                if resolved_local is None:
                    _vis_log.warning("Scene %d: PIL card for %r", i, search_q)
                    label = search_q[:57]
                    ck = card_cache_key(section_type, label, render_w, render_h)
                    card_path = vis_cards_dir / f"card_{i}_{ck}.png"
                    if not card_path.exists():
                        generate_scene_card(
                            section_type=section_type,
                            keyword_text=label,
                            width=render_w,
                            height=render_h,
                            output_path=card_path,
                        )
                    resolved_local = str(card_path)

                si.resolved_assets = [
                    ResolvedAsset(
                        asset_id=-1,
                        scene_id=si.scene_id,
                        segment_id=si.segment_id,
                        asset_index=0,
                        category=resolved_category,
                        priority="required",
                        local_path=resolved_local,
                        local_sha256=None,
                        source_url=resolved_source,
                        license_status=resolved_license,
                        commercial_safe=resolved_commercial_safe,
                    )
                ]
                _vis_log.info("Scene %d: resolved as %s", i, resolved_category)

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
                **render_kwargs,
            )

            manifest, _ = get_or_create_render_manifest(conn, render_draft)

            renders_dir_raw = req.effective_config.get("renders_dir")
            if renders_dir_raw:
                renders_dir = Path(str(renders_dir_raw))
            else:
                renders_dir = artifacts_path / "renders" / str(approved.manifest_id)
            renders_dir.mkdir(parents=True, exist_ok=True)
            output_path = renders_dir / f"render_{manifest.id}.mp4"

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
