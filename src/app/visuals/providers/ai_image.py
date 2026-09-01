"""Optional AI image provider (Tier 5) — interface and safety gate only.

Deliberately inert by default.  Generative media is the most expensive visual
source available and must never become the silent default when free retrieval
underperforms; a locally generated explanatory graphic is both cheaper and,
for structural intents, usually better.

Enabling requires BOTH:
  ACE_VISUAL_AI_GENERATION_ENABLED=true      (operator authorisation)
  a configured generator implementation registered via ``set_generator``

The engine treats an unavailable provider as simply absent, so leaving this
disabled changes nothing about the fallback chain.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Protocol

from app.visuals.constants import COST_HIGH, LICENSE_NOT_REQUIRED, MEDIA_PHOTO
from app.visuals.models import VisualCandidate
from app.visuals.providers.base import ProviderCapability

logger = logging.getLogger(__name__)

ENABLE_FLAG = "ACE_VISUAL_AI_GENERATION_ENABLED"


class ImageGenerator(Protocol):
    """Minimal contract a paid image backend must satisfy."""

    model_id: str

    def generate(self, prompt: str, dest: Path, *, width: int, height: int) -> Path | None: ...


_generator: ImageGenerator | None = None


def set_generator(generator: ImageGenerator | None) -> None:
    """Register (or clear) the concrete generative backend."""
    global _generator
    _generator = generator


def generation_enabled() -> bool:
    return os.environ.get(ENABLE_FLAG, "").lower() in {"1", "true", "yes"}


class AiImageProvider:
    """Tier-5 fallback point.  Available only under explicit authorisation."""

    identity = "ai_image"
    capability = ProviderCapability(
        media_types=(MEDIA_PHOTO,),
        tier=5,
        cost_units=COST_HIGH,
        requires_key=True,
        supports_orientation=True,
        commercial_safe_by_default=True,
        max_results=1,
        notes="Paid generative imagery; disabled unless explicitly authorised.",
    )

    def __init__(self, *, width: int = 1080, height: int = 1920) -> None:
        self._width = width
        self._height = height

    def available(self) -> bool:
        return generation_enabled() and _generator is not None

    def search(
        self,
        query: str,
        *,
        media_type: str,
        limit: int = 1,
        orientation: str | None = None,
    ) -> list[VisualCandidate]:
        """Describe what *would* be generated. No API call happens here."""
        if not self.available() or not query.strip():
            return []
        model_id = getattr(_generator, "model_id", "unknown")
        return [
            VisualCandidate(
                provider=self.identity,
                provider_asset_id=f"{model_id}:{query}",
                media_type=MEDIA_PHOTO,
                query=query,
                license_status=LICENSE_NOT_REQUIRED,
                license_name="Generated",
                commercial_safe=True,
                title=query,
                tags=query.split(),
                provider_rank=0,
                cost_units=COST_HIGH,
                tier=self.capability.tier,
            )
        ]

    def download(self, candidate: VisualCandidate, cache_dir: Path) -> Path | None:
        if not self.available() or _generator is None:
            return None
        dest = cache_dir / f"ai_{abs(hash(candidate.asset_key)) & 0xFFFFFFFF:08x}.jpg"
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info("AI image generation authorised for %r", candidate.query)
        return _generator.generate(candidate.query, dest, width=self._width, height=self._height)
