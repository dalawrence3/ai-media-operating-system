"""Named constants for Phase 4.1 acquisition limits and Phase 4.2 claim extraction."""

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

# ---------------------------------------------------------------------------
# Phase 4.2 — claim extraction limits and versioned algorithm identifiers
# ---------------------------------------------------------------------------

CHUNK_MAX_CHARS: int = 6_000
CHUNK_MAX_COUNT: int = 8
CHUNK_CLAIMS_MAX: int = 10
CLAIMS_RUN_MAX: int = 60

CHUNK_ALGO_VERSION: str = "para-v1"
EXTRACTION_ALGO_VERSION: str = "4.2-v1"
QUOTE_VALIDATION_VERSION: str = "qv-1"
CLAIM_SCHEMA_VERSION: str = "ExtractedClaim-v1"

CLAIM_EXTRACTION_PROMPT_NAME: str = "claim-extraction"
CLAIM_EXTRACTION_PROMPT_VERSION: str = "1"

STAT_REVIEW_THRESHOLD_DAYS: int = 730
TIME_SENSITIVE_REVIEW_DAYS: int = 365
CLAIM_RUN_STALE_HOURS: int = 2
