"""Pexels visual provider — free stock video and photos (Tier 1/2).

Requires ACE_PEXELS_API_KEY.  Returns candidate metadata including the tags
and description Pexels exposes, which the scorer uses for real semantic
overlap rather than trusting result order.
"""

from __future__ import annotations

import os

from app.visuals.constants import (
    COST_FREE,
    LICENSE_VERIFIED,
    MEDIA_PHOTO,
    MEDIA_VIDEO,
)
from app.visuals.models import VisualCandidate
from app.visuals.providers.base import HttpProviderBase, ProviderCapability

_VIDEO_SEARCH = "https://api.pexels.com/videos/search"
_PHOTO_SEARCH = "https://api.pexels.com/v1/search"

_MIN_PHOTO_DIMENSION = 900
# Short-side resolution we want a video file to cover before it is cropped to
# the target frame.  Files are chosen by actual dimensions, not by Pexels'
# quality label: that label is now frequently null, and matching on it made
# every video lookup silently return nothing.
_TARGET_SHORT_SIDE = 1080
# Fallback ordering for the legacy label, used only when dimensions are absent.
_QUALITY_PREFERENCE = ("uhd", "hd", "sd")


class PexelsProvider(HttpProviderBase):
    identity = "pexels"
    capability = ProviderCapability(
        media_types=(MEDIA_VIDEO, MEDIA_PHOTO),
        tier=1,
        cost_units=COST_FREE,
        requires_key=True,
        supports_orientation=True,
        commercial_safe_by_default=True,
        attribution_required_by_default=False,
        max_results=15,
        notes="Pexels License — free commercial use, attribution appreciated.",
    )

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__()
        self._api_key = api_key or os.environ.get("ACE_PEXELS_API_KEY", "")

    def available(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        return {**super()._headers(), "Authorization": self._api_key}

    # ── Search ──────────────────────────────────────────────────────────────

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
            return self._search_videos(query, limit, orientation)
        if media_type == MEDIA_PHOTO:
            return self._search_photos(query, limit, orientation)
        return []

    def _search_videos(
        self, query: str, limit: int, orientation: str | None
    ) -> list[VisualCandidate]:
        params: dict[str, object] = {"query": query, "per_page": min(limit, 30)}
        if orientation:
            params["orientation"] = orientation
        payload = self._get_json(_VIDEO_SEARCH, params)
        if not payload:
            return []

        candidates: list[VisualCandidate] = []
        for rank, video in enumerate(payload.get("videos", []) or []):
            best = _pick_video_file(video.get("video_files", []) or [])
            if not best or not best.get("link"):
                continue
            candidates.append(
                VisualCandidate(
                    provider=self.identity,
                    provider_asset_id=str(video.get("id")),
                    media_type=MEDIA_VIDEO,
                    query=query,
                    source_url=video.get("url"),
                    download_url=best.get("link"),
                    width=best.get("width"),
                    height=best.get("height"),
                    duration_s=float(video.get("duration") or 0) or None,
                    license_status=LICENSE_VERIFIED,
                    license_name="Pexels License",
                    attribution_required=False,
                    attribution_text=_attribution(video.get("user", {}).get("name")),
                    commercial_safe=True,
                    creator=(video.get("user") or {}).get("name"),
                    tags=_tags_from_url(video.get("url")),
                    title=_title_from_url(video.get("url")),
                    provider_rank=rank,
                    cost_units=COST_FREE,
                    tier=self.capability.tier,
                )
            )
        return candidates

    def _search_photos(
        self, query: str, limit: int, orientation: str | None
    ) -> list[VisualCandidate]:
        params: dict[str, object] = {"query": query, "per_page": min(limit, 30)}
        if orientation:
            params["orientation"] = orientation
        payload = self._get_json(_PHOTO_SEARCH, params)
        if not payload:
            return []

        candidates: list[VisualCandidate] = []
        for rank, photo in enumerate(payload.get("photos", []) or []):
            width = int(photo.get("width") or 0)
            height = int(photo.get("height") or 0)
            if min(width, height) < _MIN_PHOTO_DIMENSION:
                continue
            src = photo.get("src") or {}
            url = src.get("large2x") or src.get("original")
            if not url:
                continue
            alt = (photo.get("alt") or "").strip()
            candidates.append(
                VisualCandidate(
                    provider=self.identity,
                    provider_asset_id=str(photo.get("id")),
                    media_type=MEDIA_PHOTO,
                    query=query,
                    source_url=photo.get("url"),
                    download_url=url,
                    width=width,
                    height=height,
                    license_status=LICENSE_VERIFIED,
                    license_name="Pexels License",
                    attribution_required=False,
                    attribution_text=_attribution(photo.get("photographer")),
                    commercial_safe=True,
                    creator=photo.get("photographer"),
                    # Pexels' alt text is a real description of the photo and is
                    # the only per-asset semantic signal the API exposes.
                    tags=[t for t in alt.replace(",", " ").split() if t]
                    or _tags_from_url(photo.get("url")),
                    title=alt or _title_from_url(photo.get("url")),
                    provider_rank=rank,
                    cost_units=COST_FREE,
                    tier=self.capability.tier,
                )
            )
        return candidates


def _pick_video_file(
    video_files: list[dict], *, target_short_side: int = _TARGET_SHORT_SIDE
) -> dict | None:
    """Choose the smallest rendition that still covers the target frame.

    Smallest-sufficient rather than largest keeps download size (and render
    time) down without costing visible quality after the 9:16 crop.
    """
    playable = [entry for entry in video_files if entry.get("link")]
    if not playable:
        return None

    sized = [
        entry
        for entry in playable
        if int(entry.get("width") or 0) > 0 and int(entry.get("height") or 0) > 0
    ]
    if sized:

        def short_side(entry: dict) -> int:
            return min(int(entry["width"]), int(entry["height"]))

        sufficient = [entry for entry in sized if short_side(entry) >= target_short_side]
        if sufficient:
            return min(sufficient, key=short_side)
        return max(sized, key=short_side)

    by_quality: dict[str, list[dict]] = {}
    for entry in playable:
        by_quality.setdefault((entry.get("quality") or "").lower(), []).append(entry)
    for quality in _QUALITY_PREFERENCE:
        options = by_quality.get(quality) or []
        if options:
            return options[0]
    return playable[0]


def _attribution(creator: str | None) -> str | None:
    return f"Photo/video by {creator} on Pexels" if creator else None


def _tags_from_url(url: str | None) -> list[str]:
    """Pexels encodes the asset's own slug in its page URL.

    That slug is the only per-asset keyword signal on video results, and it is
    far more trustworthy than result position.
    """
    if not url:
        return []
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    parts = [p for p in slug.split("-") if p and not p.isdigit()]
    return parts


def _title_from_url(url: str | None) -> str:
    return " ".join(_tags_from_url(url))
