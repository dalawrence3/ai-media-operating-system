"""Typed exceptions for the research / source-acquisition layer."""

from __future__ import annotations


class ResearchError(Exception):
    """Base class for all research-layer errors."""


class SecurityError(ResearchError):
    """A URL or path was rejected by a security policy (SSRF, scheme, etc.)."""


class FetchError(ResearchError):
    """An HTTP or file-read acquisition failed after validation passed.

    Attributes:
        http_status: The HTTP status code, if a response was received.
    """

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


class ExtractionError(ResearchError):
    """Content was retrieved but text extraction produced no usable output."""
