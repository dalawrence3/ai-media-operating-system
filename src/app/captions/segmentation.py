"""Phase 6 M6.3A deterministic English caption segmentation.

All behaviour is governed by CAPTION_SEGMENTATION_VERSION.  Any change to
segmentation rules, line-wrapping logic, abbreviation lists, or punctuation
handling requires bumping that version constant and the caption input hash.

Contract:
  - preserves narration wording exactly (no word mutations)
  - never fabricates, deletes, reorders, or duplicates words
  - never splits a word across lines
  - handles common English abbreviations (no false sentence ends)
  - avoids splitting decimal numbers
  - treats ellipses as continuation
  - supports quotation-ending punctuation
  - handles short hooks and CTAs as single cues
  - produces no empty cues
  - text integrity: normalize_for_integrity(joined_cue_texts)
    == normalize_for_integrity(narration_text)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.captions.constants import (
    CAPTION_MAX_CHARS_PER_LINE,
    CAPTION_MAX_LINES_PER_CUE,
)

# ── Abbreviation handling ────────────────────────────────────────────────────
# These token patterns must NOT be treated as sentence boundaries even when
# followed by a space and a capital letter.
_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        # titles
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "rev",
        "gen",
        "sgt",
        "cpl",
        "pvt",
        "lt",
        "col",
        "maj",
        "brig",
        "adm",
        "cdr",
        "capt",
        "gov",
        "atty",
        "supt",
        # latin
        "vs",
        "etc",
        "e.g",
        "i.e",
        "et al",
        "ibid",
        "cf",
        "approx",
        # geographic
        "st",
        "ave",
        "blvd",
        "rd",
        "apt",
        "dept",
        # units and time
        "sec",
        "min",
        "hrs",
        "ft",
        "sq",
        "vol",
        "no",
        "fig",
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
        "sep",
        "oct",
        "nov",
        "dec",
    }
)

# Sentence-ending punctuation followed by space and (uppercase or quote).
# Using lookahead to avoid consuming the following character.
_SENTENCE_BOUNDARY = re.compile(
    r"""
    (?<!\.\.)       # not inside an ellipsis (preceded by ..)
    (?<!\d)         # not after a digit (avoid splitting decimal numbers)
    [.!?]           # sentence-terminal punctuation
    ['""‘’“”]?  # optional closing quote
    (?=\s+[A-Z-￿"'‘“])  # followed by whitespace then capital / quote
    """,
    re.VERBOSE,
)


def normalize_for_integrity(text: str) -> str:
    """Normalize whitespace for text-integrity comparisons.

    This is the single named normalization function used to verify that
    joined cue text equals the original narration text.

    Rule: collapse every run of whitespace to a single space and strip.
    """
    return " ".join(text.split())


@dataclass
class SegmentedCueText:
    """A single caption cue before timing assignment."""

    lines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def char_count(self) -> int:
        return sum(len(line) for line in self.lines)

    @property
    def display_char_count(self) -> int:
        """Character count of the text when lines are joined with spaces for speed calculation."""
        return len(" ".join(self.lines))


def _is_abbreviation_boundary(pre: str) -> bool:
    """Return True if the word before the dot is a known abbreviation."""
    token = pre.rstrip().rsplit(None, 1)[-1].lower().rstrip(".")
    return token in _ABBREVIATIONS


def _split_into_sentences(text: str) -> list[str]:
    """Split narration text into sentences using deterministic rules.

    Preserves all characters; splitting is done by inserting a sentinel
    and then splitting on it.
    """
    _SENTINEL = "\x00"
    result: list[str] = []
    pos = 0
    for m in _SENTENCE_BOUNDARY.finditer(text):
        end = m.end()
        pre = text[pos:end]
        # Check if it is actually an abbreviation
        word_before = text[pos : m.start()].rstrip().rsplit(None, 1)
        last_word = word_before[-1].lower().rstrip(".") if word_before else ""
        if last_word in _ABBREVIATIONS:
            continue
        result.append(pre)
        pos = end
    if pos < len(text):
        result.append(text[pos:])
    # Filter out empty sentences from over-splitting
    return [s for s in result if s.strip()]


def _wrap_to_lines(
    words: list[str],
    max_chars: int = CAPTION_MAX_CHARS_PER_LINE,
    max_lines: int = CAPTION_MAX_LINES_PER_CUE,
) -> list[str] | None:
    """Wrap a word list into at most max_lines lines.

    Returns the wrapped lines or None if it cannot fit.
    """
    lines: list[str] = []
    current: list[str] = []
    current_len = 0

    for word in words:
        if not current:
            current = [word]
            current_len = len(word)
        elif current_len + 1 + len(word) <= max_chars:
            current.append(word)
            current_len += 1 + len(word)
        else:
            # Word doesn't fit on the current line.
            if len(lines) >= max_lines - 1:
                # Cannot open a new line; caller must split.
                return None
            lines.append(" ".join(current))
            current = [word]
            current_len = len(word)

    if current:
        lines.append(" ".join(current))

    return lines if lines else None


def _split_long_sentence(sentence: str) -> list[SegmentedCueText]:
    """Split a sentence that doesn't fit in one cue into multiple cues.

    Splits at clause boundaries (comma, semicolon, colon) first, then at
    word boundaries.  Never splits a word across cues.
    """
    max_cue_chars = CAPTION_MAX_CHARS_PER_LINE * CAPTION_MAX_LINES_PER_CUE
    cues: list[SegmentedCueText] = []

    # Try to split at clause boundaries first.
    clause_split = re.split(r"(?<=[,;:])\s+", sentence)
    if len(clause_split) > 1:
        # Group clauses into cue-sized chunks.
        buffer: list[str] = []
        for clause in clause_split:
            candidate = " ".join(buffer + [clause]) if buffer else clause
            if len(candidate) <= max_cue_chars:
                buffer.append(clause)
            else:
                if buffer:
                    cues.extend(_wrap_chunk(" ".join(buffer)))
                buffer = [clause]
        if buffer:
            cues.extend(_wrap_chunk(" ".join(buffer)))
        return cues

    # No clause boundaries: split at word boundaries.
    return _wrap_chunk(sentence)


def _wrap_chunk(text: str) -> list[SegmentedCueText]:
    """Split a contiguous chunk of text into cues, never breaking a word."""
    words = text.split()
    cues: list[SegmentedCueText] = []
    while words:
        # Try to fit as many words as possible into one cue.
        fitted = None
        for n in range(len(words), 0, -1):
            lines = _wrap_to_lines(words[:n])
            if lines is not None:
                fitted = (n, lines)
                break
        if fitted is None:
            # Single word too long for one line — force it.
            word = words.pop(0)
            cues.append(SegmentedCueText(lines=[word]))
        else:
            n, lines = fitted
            cues.append(SegmentedCueText(lines=lines))
            words = words[n:]
    return cues


def segment_narration_text(
    text: str,
    *,
    max_chars_per_line: int = CAPTION_MAX_CHARS_PER_LINE,
    max_lines_per_cue: int = CAPTION_MAX_LINES_PER_CUE,
) -> list[SegmentedCueText]:
    """Segment narration text into caption cues.

    Parameters are accepted for testability but must match the frozen
    constants to preserve the CAPTION_SEGMENTATION_VERSION contract.

    Invariant: normalize_for_integrity(" ".join(c.text for c in result))
               == normalize_for_integrity(text)
    """
    if not text.strip():
        return []

    sentences = _split_into_sentences(text)
    cues: list[SegmentedCueText] = []

    for sentence in sentences:
        stripped = sentence.strip()
        if not stripped:
            continue
        words = stripped.split()
        lines = _wrap_to_lines(words, max_chars_per_line, max_lines_per_cue)
        if lines is not None:
            cues.append(SegmentedCueText(lines=lines))
        else:
            cues.extend(_split_long_sentence(stripped))

    # Post-pass: add warnings.
    _add_per_cue_warnings(cues, max_chars_per_line)

    # Verify text integrity.
    original_norm = normalize_for_integrity(text)
    joined = " ".join(c.text for c in cues)
    rejoined_norm = normalize_for_integrity(joined)
    if original_norm != rejoined_norm:
        raise ValueError(
            f"Text integrity violation after segmentation.\n"
            f"  Original: {original_norm!r}\n"
            f"  Rejoined: {rejoined_norm!r}"
        )

    return cues


def _add_per_cue_warnings(
    cues: list[SegmentedCueText],
    max_chars_per_line: int,
) -> None:
    """Attach deterministic warnings to cues (mutates in place)."""
    for cue in cues:
        # Orphan: last line has exactly one word.
        last_line = cue.lines[-1]
        if len(last_line.split()) == 1 and len(cue.lines) > 1:
            cue.warnings.append("orphan_word")
        # Line too long.
        for line in cue.lines:
            if len(line) > max_chars_per_line:
                cue.warnings.append("line_too_long")
                break
