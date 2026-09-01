"""Provider-neutral visual retrieval interface.

The engine never branches on a provider name.  It asks the registry for the
providers that can serve a media type, in cost order, and scores whatever
comes back.  Adding Pixabay, an open-media archive, or a paid generative
provider is a registration, not an engine change.

Two-phase contract
------------------
``search`` returns metadata only — it must not download bytes.  ``download``
is called only for the candidate that actually won scoring, so a rejected
candidate costs no bandwidth and no disk.

Cache identity is ``provider:provider_asset_id``.  Query-keyed caching is what
previously let one video's footage resurface in an unrelated video.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from app.visuals.constants import COST_FREE
from app.visuals.models import VisualCandidate

logger = logging.getLogger(__name__)

USER_AGENT = "ace-content-engine/1.0"
DEFAULT_TIMEOUT = 20.0
DOWNLOAD_TIMEOUT = 90.0


@dataclass(frozen=True)
class ProviderCapability:
    """What a provider can do and what it costs to ask.

    ``cost_units`` is relative, not currency: it exists so fallback order can
    reason about cost without a pricing engine.
    """

    media_types: tuple[str, ...]
    tier: int
    cost_units: int = COST_FREE
    requires_key: bool = False
    supports_orientation: bool = False
    commercial_safe_by_default: bool = False
    attribution_required_by_default: bool = False
    max_results: int = 15
    notes: str = ""


@runtime_checkable
class VisualProvider(Protocol):
    """Retrieval interface every visual source implements."""

    identity: str
    capability: ProviderCapability

    def available(self) -> bool:
        """True when this provider can be called right now (keys, config)."""
        ...

    def search(
        self,
        query: str,
        *,
        media_type: str,
        limit: int = 10,
        orientation: str | None = None,
    ) -> list[VisualCandidate]:
        """Return candidate metadata.  Must not download media bytes."""
        ...

    def download(self, candidate: VisualCandidate, cache_dir: Path) -> Path | None:
        """Fetch the candidate's bytes into *cache_dir*; return the local path."""
        ...


@dataclass
class ProviderCallLog:
    """Bounded record of what each provider was actually asked to do."""

    searches: dict[str, int] = field(default_factory=dict)
    downloads: dict[str, int] = field(default_factory=dict)

    def record_search(self, identity: str) -> None:
        self.searches[identity] = self.searches.get(identity, 0) + 1

    def record_download(self, identity: str) -> None:
        self.downloads[identity] = self.downloads.get(identity, 0) + 1

    def as_dict(self) -> dict[str, int]:
        merged: dict[str, int] = {}
        for identity, count in self.searches.items():
            merged[f"{identity}.search"] = count
        for identity, count in self.downloads.items():
            merged[f"{identity}.download"] = count
        return merged


class HttpProviderBase:
    """Shared HTTP plumbing: bounded requests, id-keyed cache, sidecars.

    Concrete providers supply headers and parse their own payloads.
    """

    identity: str = "http"
    capability: ProviderCapability

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    # ── HTTP ────────────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": USER_AGENT}

    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any] | None:
        try:
            with httpx.Client(headers=self._headers(), follow_redirects=True) as client:
                response = client.get(url, params=params, timeout=self._timeout)
                response.raise_for_status()
                return response.json()
        except Exception as exc:  # network/quota/parse — never fatal to a render
            logger.warning("%s search failed: %s", self.identity, type(exc).__name__)
            return None

    # ── Cache ───────────────────────────────────────────────────────────────

    def cache_stem(self, candidate: VisualCandidate) -> str:
        """Stable, provider-scoped cache stem.

        Deliberately derived from the asset identity rather than the search
        query so two different queries can never collide onto one file.
        """
        digest = hashlib.sha256(candidate.asset_key.encode()).hexdigest()[:16]
        return f"{candidate.provider}_{digest}"

    def _sidecar(self, path: Path, candidate: VisualCandidate) -> None:
        payload = {
            "provider": candidate.provider,
            "provider_asset_id": candidate.provider_asset_id,
            "asset_key": candidate.asset_key,
            "media_type": candidate.media_type,
            "query": candidate.query,
            "source_url": candidate.source_url,
            "license_name": candidate.license_name,
            "license_status": candidate.license_status,
            "attribution_required": candidate.attribution_required,
            "attribution_text": candidate.attribution_text,
            "commercial_safe": candidate.commercial_safe,
            "creator": candidate.creator,
            "title": candidate.title,
        }
        path.with_suffix(path.suffix + ".attr.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _download_to(self, url: str, dest: Path) -> bool:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with httpx.Client(headers=self._headers(), follow_redirects=True) as client:
                with client.stream("GET", url, timeout=DOWNLOAD_TIMEOUT) as response:
                    response.raise_for_status()
                    with dest.open("wb") as handle:
                        for chunk in response.iter_bytes(65536):
                            handle.write(chunk)
            return dest.stat().st_size > 0
        except Exception as exc:
            logger.warning("%s download failed: %s", self.identity, type(exc).__name__)
            dest.unlink(missing_ok=True)
            return False

    def download(self, candidate: VisualCandidate, cache_dir: Path) -> Path | None:
        if not candidate.download_url:
            return None
        suffix = ".mp4" if candidate.media_type == "video" else ".jpg"
        dest = cache_dir / f"{self.cache_stem(candidate)}{suffix}"
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        if not self._download_to(candidate.download_url, dest):
            return None
        self._sidecar(dest, candidate)
        return dest
