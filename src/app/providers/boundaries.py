"""Provider boundary gates: enforce Class A/B/C stage classification.

Stage Classification (from Phase 13):
  Class A — Local computation, no external provider required.
             Always allowed. Examples: research planning, script drafting,
             scene extraction, caption generation (fake provider).
  Class B — Requires a live AI/TTS provider credential.
             Allowed only when: provider key is configured AND live flag is True.
             Examples: AI content generation (Anthropic), narration (ElevenLabs).
  Class C — Requires live publishing to a platform (social media, video host).
             Allowed only when: Class B conditions met AND publishing_live_enabled=True
             AND a platform account credential is attached to the channel.
             Examples: YouTube upload, social media publish.

Fail-closed: unknown or unlisted stages are treated as Class C (most restrictive).
Production must never degrade to a permissive default.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class StageClass(str, Enum):
    A = "A"  # Local — always allowed
    B = "B"  # Provider API required
    C = "C"  # Live publishing required


class ProviderBoundaryError(Exception):
    """Raised when a live provider call is attempted without meeting prerequisites."""


# Stage → class mapping (deny-by-default for unknown stages).
_STAGE_CLASS: dict[str, StageClass] = {
    # Class A — local computation
    "research": StageClass.A,
    "planning": StageClass.A,
    "scripting": StageClass.A,
    "scenes": StageClass.A,
    "captions": StageClass.A,
    "thumbnails": StageClass.A,
    "metadata": StageClass.A,
    "packaging": StageClass.A,
    # Class B — requires live AI/TTS provider
    "content_generation": StageClass.B,
    "narration": StageClass.B,
    "ai_research": StageClass.B,
    "ai_scripting": StageClass.B,
    "voice_synthesis": StageClass.B,
    # Class C — requires live publishing
    "upload": StageClass.C,
    "publish": StageClass.C,
    "social_publish": StageClass.C,
    "distribution": StageClass.C,
}


def classify_stage(stage: str) -> StageClass:
    """Return the class for a pipeline stage. Unknown stages → Class C (most restrictive)."""
    return _STAGE_CLASS.get(stage, StageClass.C)


class ProviderBoundary:
    """Enforces provider and publishing live-call prerequisites.

    Reads config at check time (not at construction) so that test overrides
    applied via env vars or patching take effect immediately.
    """

    def __init__(self, config: Any = None) -> None:
        self._config = config

    def _get_config(self) -> Any:
        if self._config is not None:
            return self._config
        from app.core.config import get_config

        return get_config()

    def check_stage(self, stage: str) -> None:
        """Raise ProviderBoundaryError if `stage` is not currently authorized.

        Class A — always passes.
        Class B — requires at least one live provider flag to be True.
        Class C — additionally requires publishing_live_enabled=True.

        This is a fail-closed check; when in doubt, raise.
        """
        stage_class = classify_stage(stage)
        cfg = self._get_config()

        if stage_class == StageClass.A:
            return  # always allowed

        if stage_class in (StageClass.B, StageClass.C):
            ai_live = getattr(cfg, "ai_provider", "fake") != "fake" and bool(
                getattr(cfg, "anthropic_api_key", "")
                or getattr(cfg, "elevenlabs_api_key", "")
            )
            tts_live = getattr(cfg, "tts_live_enabled", False)
            provider_live = ai_live or tts_live

            if not provider_live:
                raise ProviderBoundaryError(
                    f"Stage '{stage}' (Class {stage_class.value}) requires a live provider. "
                    "Set ACE_AI_PROVIDER (not 'fake') with a valid API key, "
                    "or ACE_TTS_LIVE_ENABLED=true with ACE_ELEVENLABS_API_KEY."
                )

        if stage_class == StageClass.C:
            if not getattr(cfg, "publishing_live_enabled", False):
                raise ProviderBoundaryError(
                    f"Stage '{stage}' (Class C) requires ACE_PUBLISHING_LIVE_ENABLED=true. "
                    "Live publishing must be explicitly enabled by an operator."
                )

    def stage_class(self, stage: str) -> StageClass:
        """Return the class for a stage without raising."""
        return classify_stage(stage)

    def is_stage_allowed(self, stage: str) -> bool:
        """Return True if the stage would pass check_stage() without raising."""
        try:
            self.check_stage(stage)
            return True
        except ProviderBoundaryError:
            return False
