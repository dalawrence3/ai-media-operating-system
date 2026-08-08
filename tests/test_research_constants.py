"""Tests for Phase 4.1 named constants."""

from __future__ import annotations

from app.research.constants import (
    ALLOWED_FILE_EXTENSIONS,
    ALLOWED_MIME_PREFIXES,
    ALLOWED_URL_SCHEMES,
    FILE_MAX_BYTES,
    HASH_ALGORITHM,
    HTTP_CONNECT_TIMEOUT,
    HTTP_MAX_BYTES,
    HTTP_MAX_REDIRECTS,
    HTTP_READ_TIMEOUT,
    PDF_MAX_PAGES,
    QUALITY_SCORER_VERSION,
)


def test_http_max_bytes_is_5mb():
    assert HTTP_MAX_BYTES == 5 * 1024 * 1024


def test_file_max_bytes_is_10mb():
    assert FILE_MAX_BYTES == 10 * 1024 * 1024


def test_pdf_max_pages():
    assert PDF_MAX_PAGES == 200


def test_http_max_redirects():
    assert HTTP_MAX_REDIRECTS == 5


def test_timeouts_are_positive():
    assert HTTP_CONNECT_TIMEOUT > 0
    assert HTTP_READ_TIMEOUT > 0


def test_quality_scorer_version_format():
    assert QUALITY_SCORER_VERSION.startswith("quality-v")


def test_hash_algorithm_format():
    assert HASH_ALGORITHM == "sha256-nfc-v1"


def test_allowed_url_schemes():
    assert "http" in ALLOWED_URL_SCHEMES
    assert "https" in ALLOWED_URL_SCHEMES
    assert "ftp" not in ALLOWED_URL_SCHEMES
    assert "file" not in ALLOWED_URL_SCHEMES


def test_allowed_file_extensions():
    assert ".txt" in ALLOWED_FILE_EXTENSIONS
    assert ".md" in ALLOWED_FILE_EXTENSIONS
    assert ".pdf" in ALLOWED_FILE_EXTENSIONS
    assert ".docx" not in ALLOWED_FILE_EXTENSIONS


def test_allowed_mime_prefixes_contains_expected():
    assert "text/html" in ALLOWED_MIME_PREFIXES
    assert "text/plain" in ALLOWED_MIME_PREFIXES
    assert "application/pdf" in ALLOWED_MIME_PREFIXES
