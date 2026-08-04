"""HTTP acquisition with SSRF protection, size enforcement, and redirect validation."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.research.constants import (
    ALLOWED_MIME_PREFIXES,
    HTTP_CONNECT_TIMEOUT,
    HTTP_MAX_BYTES,
    HTTP_MAX_REDIRECTS,
    HTTP_READ_TIMEOUT,
)
from app.research.errors import FetchError, SecurityError
from app.research.validate import validate_url


@dataclass
class FetchResult:
    """Outcome of a successful HTTP fetch."""

    content: bytes
    http_status: int
    canonical_url: str
    mime_type: str | None


def _check_https_downgrade(response: httpx.Response, original_scheme: str) -> None:
    """Raise SecurityError if any redirect in the chain downgraded from HTTPS to HTTP."""
    if original_scheme != "https":
        return
    for r in response.history:
        if r.url.scheme == "https":
            next_location = r.headers.get("location", "")
            if next_location.lower().startswith("http://"):
                raise SecurityError(
                    f"HTTPS-to-HTTP redirect detected (blocked): {r.url} → {next_location}"
                )
    # Also check final URL
    if response.url.scheme == "http":
        raise SecurityError(
            f"Request started as HTTPS but final URL is HTTP: {response.url}"
        )


def fetch_url(
    url: str,
    *,
    http_client: httpx.Client | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> FetchResult:
    """Fetch *url* with SSRF validation, size enforcement, and redirect checks.

    Raises:
        SecurityError: URL failed security validation (scheme, SSRF, redirect downgrade).
        FetchError: HTTP or network error after validation passed.
    """
    validate_url(url)

    original_scheme = urlparse(url).scheme.lower()

    timeout = httpx.Timeout(
        HTTP_READ_TIMEOUT, connect=HTTP_CONNECT_TIMEOUT, pool=5.0
    )

    own_client = http_client is None
    client = http_client or httpx.Client(
        follow_redirects=True,
        max_redirects=HTTP_MAX_REDIRECTS,
        timeout=timeout,
    )

    try:
        try:
            with client.stream("GET", url) as response:
                # Check for HTTPS→HTTP downgrade in redirect history before reading body
                _check_https_downgrade(response, original_scheme)

                http_status = response.status_code
                if http_status >= 400:
                    raise FetchError(
                        f"HTTP {http_status} for {url}",
                        http_status=http_status,
                    )

                # Validate MIME type
                content_type = response.headers.get("content-type", "")
                mime_type = content_type.split(";")[0].strip().lower() or None
                if mime_type and not any(
                    mime_type.startswith(p) for p in ALLOWED_MIME_PREFIXES
                ):
                    raise FetchError(
                        f"Unsupported content type {content_type!r}. "
                        f"Expected one of: {', '.join(ALLOWED_MIME_PREFIXES)}",
                        http_status=http_status,
                    )

                # Stream body with decompressed-size enforcement
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > HTTP_MAX_BYTES:
                        raise FetchError(
                            f"Response exceeded {HTTP_MAX_BYTES // (1024 * 1024)} MB "
                            f"decompressed size limit.",
                            http_status=http_status,
                        )
                    chunks.append(chunk)

                content = b"".join(chunks)
                canonical_url = str(response.url)

        except httpx.TooManyRedirects as exc:
            raise FetchError(
                f"Too many redirects (limit: {HTTP_MAX_REDIRECTS}): {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise FetchError(f"Request timed out: {exc}") from exc
        except httpx.NetworkError as exc:
            raise FetchError(f"Network error: {exc}") from exc
        except (SecurityError, FetchError):
            raise
        except httpx.HTTPError as exc:
            raise FetchError(f"HTTP error: {exc}") from exc

    finally:
        if own_client:
            client.close()

    return FetchResult(
        content=content,
        http_status=http_status,  # type: ignore[possibly-undefined]
        canonical_url=canonical_url,  # type: ignore[possibly-undefined]
        mime_type=mime_type,  # type: ignore[possibly-undefined]
    )
