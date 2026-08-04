"""Named constants for Phase 4.1 acquisition limits and versioned identifiers."""

from __future__ import annotations

HTTP_MAX_BYTES: int = 5 * 1024 * 1024
FILE_MAX_BYTES: int = 10 * 1024 * 1024
PDF_MAX_PAGES: int = 200
HTTP_MAX_REDIRECTS: int = 5
HTTP_CONNECT_TIMEOUT: float = 10.0
HTTP_READ_TIMEOUT: float = 60.0

QUALITY_SCORER_VERSION: str = "quality-v1"
HASH_ALGORITHM: str = "sha256-nfc-v1"

ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})
ALLOWED_FILE_EXTENSIONS: frozenset[str] = frozenset({".txt", ".md", ".pdf"})
ALLOWED_MIME_PREFIXES: tuple[str, ...] = ("text/html", "text/plain", "application/pdf")
