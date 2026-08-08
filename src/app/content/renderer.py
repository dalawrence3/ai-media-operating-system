"""Deterministic script body rendering and local duration computation.

render_body() is the single source of truth for the human-readable script body.
It is pure: given the same GeneratedScript it always produces the same string.
Warnings are excluded from the rendered body.
Citation markers ([claim:N]) are preserved in the rendered output.
"""

from __future__ import annotations

import math
import re

from app.content.constants import (
    SCRIPT_WORDS_PER_MINUTE,
    SHORT_FORM_MAX_DURATION_S,
    SHORT_FORM_MIN_DURATION_S,
)
from app.content.schemas import GeneratedScript

_MARKER_RE = re.compile(r"\[claim:[1-9]\d*\]")

# Section type display labels
_SECTION_LABELS: dict[str, str] = {
    "hook": "[HOOK]",
    "intro": "[INTRO]",
    "body": "[BODY]",
    "transition": "[TRANSITION]",
    "conclusion": "[CONCLUSION]",
    "cta": "[CTA]",
}


def render_body(script: GeneratedScript) -> str:
    """Produce the canonical human-readable body string.

    Format:
        # {title}
        <blank line>
        [SECTION_TYPE]
        {section text}
        <blank line between sections>

    Markers are preserved. Warnings are excluded.
    """
    parts: list[str] = [f"# {script.title}"]
    for section in script.sections:
        label = _SECTION_LABELS.get(section.section_type, f"[{section.section_type.upper()}]")
        parts.append("")
        parts.append(label)
        parts.append(section.text)
    return "\n".join(parts)


def strip_markers(text: str) -> str:
    """Remove all [claim:N] markers from text (for spoken/word-count purposes)."""
    return _MARKER_RE.sub("", text)


def count_words(script: GeneratedScript) -> int:
    """Count spoken words: concatenate all section text with markers stripped."""
    all_text = " ".join(strip_markers(s.text) for s in script.sections)
    return len(all_text.split())


def compute_duration_s(word_count: int) -> int:
    """Convert word count to duration in seconds, clamped to hard bounds."""
    raw = math.ceil(word_count / SCRIPT_WORDS_PER_MINUTE * 60)
    return max(SHORT_FORM_MIN_DURATION_S, min(SHORT_FORM_MAX_DURATION_S, raw))
