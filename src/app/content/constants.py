"""Named constants for Phase 5 script generation."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Short-form duration and word count
# ---------------------------------------------------------------------------

SCRIPT_WORDS_PER_MINUTE: int = 150

SHORT_FORM_DEFAULT_DURATION_S: int = 60
SHORT_FORM_MIN_DURATION_S: int = 15
SHORT_FORM_MAX_DURATION_S: int = 90
SCRIPT_DURATION_TOLERANCE_S: int = 10

# Prompt-guidance only — not hard validation boundaries.
SHORT_FORM_MIN_WORDS: int = 40
SHORT_FORM_MAX_WORDS: int = 135

# ---------------------------------------------------------------------------
# Algorithm version identifiers (changing any breaks the input hash)
# ---------------------------------------------------------------------------

SCRIPT_SCHEMA_VERSION: str = "GeneratedScript-v1"
SCRIPT_RENDERER_VERSION: str = "renderer-v1"
SCRIPT_CITATION_VERSION: str = "citation-v1"
SCRIPT_GENERATION_VERSION: str = "5-v1"

# ---------------------------------------------------------------------------
# Prompt identity
# ---------------------------------------------------------------------------

SCRIPT_GENERATION_PROMPT_NAME: str = "script-generation"
SCRIPT_GENERATION_PROMPT_VERSION: str = "1"

# Default generation settings
DEFAULT_TONE: str = "conversational"
DEFAULT_AUDIENCE: str = ""
DEFAULT_TEMPERATURE: float = 0.3
DEFAULT_MAX_TOKENS: int = 2048
