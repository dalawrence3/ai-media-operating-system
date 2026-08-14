"""Wikimedia Commons stock image fetcher.

Downloads freely-licensed images from Wikimedia Commons (no API key needed).
Results are cached on disk so repeated renders don't re-download.

Public interface
----------------
fetch_stock_image(query, cache_dir, *, min_dimension=800) -> Path | None
    Returns the local path to a cached JPEG/PNG, or None if nothing suitable
    could be found or the network is unavailable.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

_COMMONS_API = "https://commons.wikimedia.org/w/api.php"
_USER_AGENT = "ace-content-engine/1.0 (https://github.com; internal use)"

# Skew toward real photos; reject SVG, animated GIF, PDF
_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}

# How many search results to examine
_SEARCH_LIMIT = 10

# Target thumbnail width requested from Wikimedia (they rescale on their CDN)
_THUMB_WIDTH = 1200


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _cache_key(query: str) -> str:
    return hashlib.sha256(f"wikimedia|{query.lower().strip()}".encode()).hexdigest()[:16]


def _sidecar_path(img_path: Path) -> Path:
    return img_path.with_suffix(".attr.json")


# ---------------------------------------------------------------------------
# Wikimedia API helpers
# ---------------------------------------------------------------------------


def _search_pages(query: str, client: httpx.Client) -> list[dict[str, Any]]:
    """Return up to _SEARCH_LIMIT image page results from Commons."""
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",
        "gsrlimit": str(_SEARCH_LIMIT),
        "prop": "imageinfo",
        "iiprop": "url|mime|size|extmetadata",
        "iiurlwidth": str(_THUMB_WIDTH),
        "format": "json",
    }
    try:
        resp = client.get(_COMMONS_API, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []
    pages = data.get("query", {}).get("pages", {})
    return list(pages.values())


def _pick_best(pages: list[dict[str, Any]], min_dimension: int) -> dict[str, Any] | None:
    """Return the best page dict, preferring large JPEGs over PNGs."""
    candidates = []
    for page in pages:
        ii = page.get("imageinfo", [{}])[0]
        mime = ii.get("mime", "")
        if mime not in _ALLOWED_MIME:
            continue
        w = ii.get("width", 0)
        h = ii.get("height", 0)
        if min(w, h) < min_dimension:
            continue
        # Prefer JPEG; penalise very small files
        score = (1 if mime == "image/jpeg" else 0) * 1000 + min(w, h)
        candidates.append((score, page))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _extract_attribution(extmetadata: dict[str, Any]) -> dict[str, str]:
    def _val(key: str) -> str:
        entry = extmetadata.get(key, {})
        raw = entry.get("value", "")
        # Strip HTML tags
        return re.sub(r"<[^>]+>", "", raw).strip()

    return {
        "artist": _val("Artist") or "Unknown",
        "license": _val("LicenseShortName") or "Unknown",
        "description": _val("ImageDescription")[:200] or "",
        "source": "Wikimedia Commons",
    }


def _download(url: str, dest: Path, client: httpx.Client) -> bool:
    try:
        with client.stream("GET", url, timeout=30, follow_redirects=True) as resp:
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_stock_image(
    query: str,
    cache_dir: Path,
    *,
    min_dimension: int = 800,
    max_retries: int = 2,
) -> Path | None:
    """Return a local cached image path for *query*, or None on failure.

    Downloads from Wikimedia Commons; caches permanently under *cache_dir*.
    A JSON sidecar file `{image}.attr.json` stores attribution metadata.
    """
    key = _cache_key(query)

    # Check cache first (any extension)
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        cached = cache_dir / f"{key}{ext}"
        if cached.exists() and cached.stat().st_size > 0:
            return cached

    headers = {"User-Agent": _USER_AGENT}
    with httpx.Client(headers=headers, follow_redirects=True) as client:
        for _attempt in range(max_retries):
            pages = _search_pages(query, client)
            if pages:
                break
            time.sleep(1)
        else:
            return None

        best = _pick_best(pages, min_dimension)
        if best is None:
            return None

        ii = best.get("imageinfo", [{}])[0]
        thumb_url = ii.get("thumburl") or ii.get("url")
        if not thumb_url:
            return None

        mime = ii.get("mime", "image/jpeg")
        ext = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }.get(mime, ".jpg")

        dest = cache_dir / f"{key}{ext}"
        if not _download(thumb_url, dest, client):
            return None

        # Write attribution sidecar
        extmeta = ii.get("extmetadata", {})
        attr = _extract_attribution(extmeta)
        _sidecar_path(dest).write_text(
            json.dumps(attr, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return dest
