"""Pexels stock video and photo fetcher.

Tries video clips first (better motion for YouTube Shorts), falls back to photos.
No API key is baked in — reads from the `api_key` argument, sourced by callers
from ACE_PEXELS_API_KEY.

Public interface
----------------
fetch_pexels_video(query, cache_dir, api_key, *, used_ids=None) -> tuple[Path, int] | None
fetch_pexels_photo(query, cache_dir, api_key, *, used_ids=None) -> tuple[Path, int] | None
    Both return (local_path, pexels_id) or None.  Add returned id to used_ids to
    prevent duplicate assets across scenes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

_PEXELS_HEADERS_BASE = {
    "User-Agent": "ace-content-engine/1.0",
}

_VIDEO_SEARCH = "https://api.pexels.com/videos/search"
_PHOTO_SEARCH = "https://api.pexels.com/v1/search"

# Minimum pixel dimension for photos
_MIN_PHOTO_DIM = 900
# Preferred video quality labels, in priority order
_VIDEO_QUALITY_PREF = ("hd", "sd")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cache_key(prefix: str, query: str) -> str:
    return hashlib.sha256(f"{prefix}|{query.lower().strip()}".encode()).hexdigest()[:16]


def _headers(api_key: str) -> dict[str, str]:
    return {**_PEXELS_HEADERS_BASE, "Authorization": api_key}


def _download(url: str, dest: Path, client: httpx.Client) -> bool:
    try:
        with client.stream("GET", url, timeout=60, follow_redirects=True) as resp:
            resp.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as f:
                for chunk in resp.iter_bytes(65536):
                    f.write(chunk)
        return True
    except Exception:
        if dest.exists():
            dest.unlink(missing_ok=True)
        return False


def _write_attr(dest: Path, data: dict[str, Any]) -> None:
    dest.with_suffix(".attr.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Video
# ---------------------------------------------------------------------------


def _pick_video_file(video_files: list[dict]) -> dict | None:
    """Return the best video file dict from a Pexels video's `video_files` list."""
    by_quality: dict[str, list[dict]] = {}
    for vf in video_files:
        q = (vf.get("quality") or "").lower()
        by_quality.setdefault(q, []).append(vf)

    for q in _VIDEO_QUALITY_PREF:
        candidates = by_quality.get(q, [])
        if not candidates:
            continue
        # Prefer widest (usually 1920-wide for HD) for best crop quality
        return max(candidates, key=lambda v: v.get("width", 0))
    return None


def fetch_pexels_video(
    query: str,
    cache_dir: Path,
    api_key: str,
    *,
    used_ids: set[int] | None = None,
) -> tuple[Path, int] | None:
    """Download a Pexels video clip for *query*.

    Returns (local_mp4_path, pexels_video_id) or None.
    Skips any video whose id is already in *used_ids*.
    """
    key = _cache_key("pexels_video", query)
    meta_path = cache_dir / f"{key}.meta.json"

    # Check cache
    if meta_path.exists():
        meta = json.loads(meta_path.read_bytes())
        vid = cache_dir / meta["filename"]
        if vid.exists() and (used_ids is None or meta["pexels_id"] not in used_ids):
            if used_ids is not None:
                used_ids.add(meta["pexels_id"])
            return vid, meta["pexels_id"]

    with httpx.Client(headers=_headers(api_key), follow_redirects=True) as client:
        try:
            resp = client.get(
                _VIDEO_SEARCH,
                params={"query": query, "per_page": 15, "orientation": "portrait"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None

        videos = data.get("videos", [])
        if not videos:
            return None

        for v in videos:
            vid_id: int = v["id"]
            if used_ids is not None and vid_id in used_ids:
                continue

            vf = _pick_video_file(v.get("video_files", []))
            if not vf:
                continue

            url = vf.get("link")
            if not url:
                continue

            filename = f"{key}.mp4"
            dest = cache_dir / filename
            if not dest.exists() and not _download(url, dest, client):
                continue

            meta = {
                "pexels_id": vid_id,
                "filename": filename,
                "query": query,
                "url": v.get("url", ""),
                "photographer": v.get("user", {}).get("name", "Unknown"),
                "license": "Pexels License",
                "source": "Pexels",
                "type": "video",
                "width": vf.get("width"),
                "height": vf.get("height"),
            }
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            _write_attr(dest, meta)

            if used_ids is not None:
                used_ids.add(vid_id)
            return dest, vid_id

    return None


# ---------------------------------------------------------------------------
# Photo
# ---------------------------------------------------------------------------


def fetch_pexels_photo(
    query: str,
    cache_dir: Path,
    api_key: str,
    *,
    used_ids: set[int] | None = None,
) -> tuple[Path, int] | None:
    """Download a Pexels photo for *query*.

    Returns (local_jpg_path, pexels_photo_id) or None.
    Skips any photo whose id is already in *used_ids*.
    """
    key = _cache_key("pexels_photo", query)
    meta_path = cache_dir / f"{key}.photo.meta.json"

    if meta_path.exists():
        meta = json.loads(meta_path.read_bytes())
        img = cache_dir / meta["filename"]
        if img.exists() and (used_ids is None or meta["pexels_id"] not in used_ids):
            if used_ids is not None:
                used_ids.add(meta["pexels_id"])
            return img, meta["pexels_id"]

    with httpx.Client(headers=_headers(api_key), follow_redirects=True) as client:
        try:
            resp = client.get(
                _PHOTO_SEARCH,
                params={"query": query, "per_page": 15, "orientation": "portrait"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None

        photos = data.get("photos", [])
        if not photos:
            return None

        for p in photos:
            photo_id: int = p["id"]
            if used_ids is not None and photo_id in used_ids:
                continue

            w = p.get("width", 0)
            h = p.get("height", 0)
            if min(w, h) < _MIN_PHOTO_DIM:
                continue

            # Use "large2x" for best quality; fall back to "original"
            src = p.get("src", {})
            url = src.get("large2x") or src.get("original")
            if not url:
                continue

            filename = f"{key}.jpg"
            dest = cache_dir / filename
            if not dest.exists() and not _download(url, dest, client):
                continue

            meta = {
                "pexels_id": photo_id,
                "filename": filename,
                "query": query,
                "url": p.get("url", ""),
                "photographer": p.get("photographer", "Unknown"),
                "license": "Pexels License",
                "source": "Pexels",
                "type": "photo",
                "width": w,
                "height": h,
            }
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            _write_attr(dest, meta)

            if used_ids is not None:
                used_ids.add(photo_id)
            return dest, photo_id

    return None
