"""Quote support classification and offset provenance for Phase 4.2.

Classifies each LLM-provided quote as exact, normalized, unsupported, or no_quote.
Builds a character-level index map so normalized matches can return raw-text offsets
deterministically.
"""

from __future__ import annotations

import re
import unicodedata

from app.research.models import ClaimSupportStatus

# PDF page separator pattern produced by extract.py
_PAGE_SEP_RE = re.compile(r"--- Page (\d+) ---\n")


# ---------------------------------------------------------------------------
# Normalization with index map
# ---------------------------------------------------------------------------


def _normalize_with_map(raw: str) -> tuple[str, list[int]]:
    """Return (normalized_text, norm_to_raw) index map.

    norm_to_raw[i] is the position in *raw* of the character that produced
    the i-th character in the normalized string.

    Normalization steps (applied to NFC-normalized input):
      1. Apply NFC normalization.  If NFC changes the character count the map
         is built over NFC positions, not original raw positions.  In that case
         the caller should treat offsets as approximate and return NULL.
      2. Scan character-by-character:
         - CRLF (\\r\\n) → single '\\n'; map records position of '\\r'
         - bare '\\r'     → single '\\n'; map records position of '\\r'
         - run of \\n{2,} → single '\\n'; map records position of first '\\n'
         - run of horizontal whitespace (non-newline) → single ' ';
           map records position of first character in run
         - all other characters: 1-to-1 mapping
    """
    nfc = unicodedata.normalize("NFC", raw)
    nfc_differs = len(nfc) != len(raw)

    norm_chars: list[str] = []
    norm_to_src: list[int] = []  # index into nfc (or raw when nfc == raw)

    i = 0
    while i < len(nfc):
        c = nfc[i]
        if c == "\r":
            norm_chars.append("\n")
            norm_to_src.append(i)
            if i + 1 < len(nfc) and nfc[i + 1] == "\n":
                i += 2
            else:
                i += 1
        elif c == "\n":
            # Collapse consecutive newlines.
            norm_chars.append("\n")
            norm_to_src.append(i)
            j = i + 1
            while j < len(nfc) and nfc[j] == "\n":
                j += 1
            i = j
        elif c != "\n" and (c == " " or (c.isspace() and c != "\r")):
            # Collapse horizontal whitespace run.
            norm_chars.append(" ")
            norm_to_src.append(i)
            j = i + 1
            while j < len(nfc) and nfc[j] != "\n" and nfc[j] != "\r" and nfc[j].isspace():
                j += 1
            i = j
        else:
            norm_chars.append(c)
            norm_to_src.append(i)
            i += 1

    return "".join(norm_chars), norm_to_src, nfc_differs  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_quote_support(
    quote: str | None,
    raw_text: str,
) -> tuple[ClaimSupportStatus, int | None, int | None]:
    """Classify quote support and return (status, quote_start, quote_end).

    Offsets are character positions in *raw_text*.
    Returns (no_quote, None, None) when quote is None or empty.
    Returns (exact, start, end) for verbatim substring matches.
    Returns (normalized, start, end) for whitespace/NFC-normalised matches,
    or (normalized, None, None) when offset mapping is ambiguous (NFC changed
    character count).
    Returns (unsupported, None, None) when no match is found.
    """
    if not quote or not quote.strip():
        return ClaimSupportStatus.no_quote, None, None

    # Step 1: exact match.
    idx = raw_text.find(quote)
    if idx != -1:
        return ClaimSupportStatus.exact, idx, idx + len(quote)

    # Step 2: normalized match via index map.
    norm_raw, norm_to_src, nfc_differs = _normalize_with_map(raw_text)
    norm_quote, norm_quote_map, quote_nfc_differs = _normalize_with_map(quote)

    norm_idx = norm_raw.find(norm_quote)
    if norm_idx == -1:
        return ClaimSupportStatus.unsupported, None, None

    # Normalized match found.
    if nfc_differs:
        # Cannot reliably map normalized positions back to original raw positions.
        return ClaimSupportStatus.normalized, None, None

    raw_start = norm_to_src[norm_idx]
    last_norm_pos = norm_idx + len(norm_quote) - 1
    raw_end = norm_to_src[last_norm_pos] + 1
    return ClaimSupportStatus.normalized, raw_start, raw_end


def derive_page_number(
    raw_text: str,
    quote_start: int | None,
    extraction_method: str | None,
) -> int | None:
    """Return the 1-based PDF page number containing *quote_start*.

    Returns None for non-PDF sources, when quote_start is None, or when no
    page separator precedes the quote position.
    """
    if quote_start is None or extraction_method != "pdf":
        return None
    page: int | None = None
    for m in _PAGE_SEP_RE.finditer(raw_text):
        if m.start() <= quote_start:
            page = int(m.group(1))
        else:
            break
    return page
