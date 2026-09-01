"""Pixabay visual provider — free stock video and images (Tier 1/2).

Optional: enabled only when ACE_PIXABAY_API_KEY is configured.  When the key
is absent the provider reports itself unavailable and the registry skips it,
so an unconfigured deployment behaves exactly as before.
"""

from __future__ import annotations

import os

from app.visuals.constants import (
    COST_FREE,
    LICENSE_VERIFIED,
    MEDIA_ILLUSTRATION,
    MEDIA_PHOTO,
    MEDIA_VIDEO,
)
from app.visuals.models import VisualCandidate
from app.visuals.providers.base import HttpProviderBase, ProviderCapability

_IMAGE_SEARCH = "https://pixabay.com/api/"
_VIDEO_SEARCH = "https://pixabay.com/api/videos/"

_VIDEO_STREAM_PREFERENCE = ("large", "medium", "small")


class PixabayProvider(HttpProviderBase):
    identity = "pixabay"
    capability = ProviderCapability(
        media_types=(MEDIA_VIDEO, MEDIA_PHOTO, MEDIA_ILLUSTRATION),
        tier=1,
        cost_units=COST_FREE,
        requires_key=True,
        supports_orientation=True,
        commercial_safe_by_default=True,
        attribution_required_by_default=False,
        max_results=20,
        notes="Pixabay Content License — free commercial use, no attribution required.",
    )

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__()
        self._api_key = api_key or os.environ.get("ACE_PIXABAY_API_KEY", "")

    def available(self) -> bool:
        return bool(self._api_key)

    def search(
        self,
        query: str,
        *,
        media_type: str,
        limit: int = 10,
        orientation: str | None = None,
    ) -> list[VisualCandidate]:
        if not self.available() or not query.strip():
            return []
        if media_type == MEDIA_VIDEO:
            return self._search_videos(query, limit)
        if media_type in (MEDIA_PHOTO, MEDIA_ILLUSTRATION):
            return self._search_images(query, limit, media_type, orientation)
        return []

    def _search_videos(self, query: str, limit: int) -> list[VisualCandidate]:
        payload = self._get_json(
            _VIDEO_SEARCH,
            {
                "key": self._api_key,
                "q": query,
                "per_page": max(3, min(limit, 50)),
                "safesearch": "true",
            },
        )
        if not payload:
            return []

        candidates: list[VisualCandidate] = []
        for rank, hit in enumerate(payload.get("hits", []) or []):
            streams = hit.get("videos") or {}
            chosen = next(
                (
                    streams[name]
                    for name in _VIDEO_STREAM_PREFERENCE
                    if streams.get(name, {}).get("url")
                ),
                None,
            )
            if not chosen:
                continue
            candidates.append(
                VisualCandidate(
                    provider=self.identity,
                    provider_asset_id=str(hit.get("id")),
                    media_type=MEDIA_VIDEO,
                    query=query,
                    source_url=hit.get("pageURL"),
                    download_url=chosen.get("url"),
                    width=chosen.get("width"),
                    height=chosen.get("height"),
                    duration_s=float(hit.get("duration") or 0) or None,
                    license_status=LICENSE_VERIFIED,
                    license_name="Pixabay Content License",
                    attribution_required=False,
                    attribution_text=_attribution(hit.get("user")),
                    commercial_safe=True,
                    creator=hit.get("user"),
                    tags=_split_tags(hit.get("tags")),
                    title=(hit.get("tags") or ""),
                    provider_rank=rank,
                    cost_units=COST_FREE,
                    tier=self.capability.tier,
                )
            )
        return candidates

    def _search_images(
        self, query: str, limit: int, media_type: str, orientation: str | None
    ) -> list[VisualCandidate]:
        params: dict[str, object] = {
            "key": self._api_key,
            "q": query,
            "per_page": max(3, min(limit, 50)),
            "safesearch": "true",
            "image_type": "illustration" if media_type == MEDIA_ILLUSTRATION else "photo",
        }
        if orientation == "portrait":
            params["orientation"] = "vertical"
        payload = self._get_json(_IMAGE_SEARCH, params)
        if not payload:
            return []

        candidates: list[VisualCandidate] = []
        for rank, hit in enumerate(payload.get("hits", []) or []):
            url = hit.get("largeImageURL") or hit.get("webformatURL")
            if not url:
                continue
            candidates.append(
                VisualCandidate(
                    provider=self.identity,
                    provider_asset_id=str(hit.get("id")),
                    media_type=media_type,
                    query=query,
                    source_url=hit.get("pageURL"),
                    download_url=url,
                    width=hit.get("imageWidth"),
                    height=hit.get("imageHeight"),
                    license_status=LICENSE_VERIFIED,
                    license_name="Pixabay Content License",
                    attribution_required=False,
                    attribution_text=_attribution(hit.get("user")),
                    commercial_safe=True,
                    creator=hit.get("user"),
                    tags=_split_tags(hit.get("tags")),
                    title=(hit.get("tags") or ""),
                    provider_rank=rank,
                    cost_units=COST_FREE,
                    tier=self.capability.tier,
                )
            )
        return candidates


def _split_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _attribution(creator: str | None) -> str | None:
    return f"Image/video by {creator} on Pixabay" if creator else None
