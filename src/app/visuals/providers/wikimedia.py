"""Wikimedia Commons provider — open educational media (Tier 3).

No API key required.  Commons hosts genuinely useful scientific, historical,
and technical diagrams that stock libraries do not carry.

Licensing is the reason this provider is Tier 3 rather than Tier 1: Commons
media is freely licensed but *not* uniformly commercial-safe, and much of it
requires attribution.  Commercial safety is decided per asset from the license
short name, never assumed.
"""

from __future__ import annotations

import re
from typing import Any

from app.visuals.constants import (
    COST_FREE,
    LICENSE_UNKNOWN,
    LICENSE_UNSAFE,
    LICENSE_VERIFIED,
    MEDIA_ILLUSTRATION,
    MEDIA_PHOTO,
)
from app.visuals.models import VisualCandidate
from app.visuals.providers.base import HttpProviderBase, ProviderCapability

_COMMONS_API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia's robot policy rejects bare product-token user agents with 403.
# A descriptive agent carrying a contact route is required.
# https://meta.wikimedia.org/wiki/User-Agent_policy
_COMMONS_USER_AGENT = (
    "ace-content-engine/1.0 "
    "(https://github.com/dalawrence3/ai-content-engine; contact via repository)"
)
_ALLOWED_MIME = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_THUMB_WIDTH = 1400
_TAG_RE = re.compile(r"<[^>]+>")

# License short names that permit commercial use.  Anything not matched here
# is treated as non-commercial-safe rather than optimistically accepted.
_COMMERCIAL_SAFE_PATTERNS = (
    "public domain",
    "cc0",
    "pd-",
    "cc by-sa",
    "cc by",
    "cc-by",
    "attribution",
)
_NON_COMMERCIAL_MARKERS = ("-nc", "noncommercial", "non-commercial", "fair use", "nd")


class WikimediaProvider(HttpProviderBase):
    identity = "wikimedia"
    capability = ProviderCapability(
        media_types=(MEDIA_PHOTO, MEDIA_ILLUSTRATION),
        tier=3,
        cost_units=COST_FREE,
        requires_key=False,
        supports_orientation=False,
        commercial_safe_by_default=False,
        attribution_required_by_default=True,
        max_results=12,
        notes="Per-asset license; commercial safety decided from license metadata.",
    )

    def available(self) -> bool:
        return True

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": _COMMONS_USER_AGENT}

    def search(
        self,
        query: str,
        *,
        media_type: str,
        limit: int = 10,
        orientation: str | None = None,
    ) -> list[VisualCandidate]:
        if media_type not in self.capability.media_types or not query.strip():
            return []

        payload = self._get_json(
            _COMMONS_API,
            {
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": "6",
                "gsrlimit": str(max(3, min(limit, 20))),
                "prop": "imageinfo",
                "iiprop": "url|mime|size|extmetadata",
                "iiurlwidth": str(_THUMB_WIDTH),
                "format": "json",
            },
        )
        if not payload:
            return []

        pages = (payload.get("query") or {}).get("pages") or {}
        candidates: list[VisualCandidate] = []
        for rank, page in enumerate(pages.values()):
            info = (page.get("imageinfo") or [{}])[0]
            mime = info.get("mime", "")
            if mime not in _ALLOWED_MIME:
                continue
            url = info.get("thumburl") or info.get("url")
            if not url:
                continue

            meta = info.get("extmetadata") or {}
            license_name = _plain(meta, "LicenseShortName") or "Unknown"
            artist = _plain(meta, "Artist") or "Unknown"
            description = _plain(meta, "ImageDescription")[:240]
            commercial_safe = _is_commercial_safe(license_name, meta)

            title = str(page.get("title", "")).removeprefix("File:")
            candidates.append(
                VisualCandidate(
                    provider=self.identity,
                    provider_asset_id=str(page.get("pageid") or title),
                    media_type=media_type,
                    query=query,
                    source_url=f"https://commons.wikimedia.org/wiki/{page.get('title', '')}",
                    download_url=url,
                    width=info.get("width"),
                    height=info.get("height"),
                    license_status=(
                        LICENSE_VERIFIED
                        if commercial_safe
                        else (LICENSE_UNSAFE if license_name != "Unknown" else LICENSE_UNKNOWN)
                    ),
                    license_name=license_name,
                    attribution_required=True,
                    attribution_text=f"{artist} / Wikimedia Commons ({license_name})",
                    commercial_safe=commercial_safe,
                    creator=artist,
                    tags=_keywords(title, description),
                    title=title,
                    provider_rank=rank,
                    cost_units=COST_FREE,
                    tier=self.capability.tier,
                )
            )
        return candidates


def _plain(meta: dict[str, Any], key: str) -> str:
    entry = meta.get(key) or {}
    return _TAG_RE.sub("", str(entry.get("value", ""))).strip()


def _is_commercial_safe(license_name: str, meta: dict[str, Any]) -> bool:
    lowered = license_name.lower()
    if any(marker in lowered for marker in _NON_COMMERCIAL_MARKERS):
        return False
    restrictions = _plain(meta, "Restrictions").lower()
    if restrictions:
        return False
    usage = _plain(meta, "UsageTerms").lower()
    if any(marker in usage for marker in _NON_COMMERCIAL_MARKERS):
        return False
    return any(pattern in lowered for pattern in _COMMERCIAL_SAFE_PATTERNS)


def _keywords(title: str, description: str) -> list[str]:
    stem = title.rsplit(".", 1)[0]
    raw = f"{stem} {description}".replace("_", " ").replace("-", " ")
    return [word for word in raw.split() if len(word) > 2][:24]
