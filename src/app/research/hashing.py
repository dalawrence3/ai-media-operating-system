"""Versioned content hashing for retrieval-identity and change-detection."""

from __future__ import annotations

import hashlib
import re
import unicodedata

from app.research.constants import HASH_ALGORITHM

__all__ = ["HASH_ALGORITHM", "normalize_for_hash", "sha256_hex"]


def normalize_for_hash(text: str) -> str:
    """Return a canonical form of *text* for semantic change detection.

    Algorithm version: sha256-nfc-v1
    Steps (in order):
      1. NFC Unicode normalization
      2. Normalize line endings to \\n
      3. Collapse runs of horizontal whitespace (non-newline) to a single space
      4. Collapse runs of 3+ consecutive newlines to 2 newlines
      5. Strip leading and trailing whitespace
    """
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def sha256_hex(data: bytes | str) -> str:
    """Return the lowercase hex SHA-256 digest of *data*.

    Accepts bytes directly (for retrieval_hash) or a str encoded as UTF-8
    (for normalized_text_hash).
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
