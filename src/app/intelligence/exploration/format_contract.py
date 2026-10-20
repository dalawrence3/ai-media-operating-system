"""Phase 7A §14 / Phase 7B §5 — format-aware production contract.

This is the single source of truth Phase 7B's format-specific script
generation builds against — script prompt selection (stage_executors.py),
structural validation (§11/§13/§20), and the eventual visual treatment
(§10) all read from here rather than each re-deciding format behavior.
Adding a new format means adding a `FormatSpec` (and, once it has real
production support, a prompt file + validator), never branching new code
through the current explainer pipeline.

Seed specs cover the two Phase 7B test cases plus the current implicit
default, so the registry is never empty. Every other seed format in
`taxonomy.FORMAT_SEEDS` is valid to select as an exploration cell today
even without a spec here — `get_format_spec` returns None for those, and
callers must treat that as "falls back to the current explainer contract",
never as an error. A cell choosing an unspecified format is exploration
working as intended, not a defect.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_FORMAT = "curiosity_explainer"

# Phase 7B, §6/§9 — bounds on an interactive_recall_quiz script's structure.
# Named/small, not implicit magic numbers scattered across validators.
# QUIZ_MAX_ITEMS=3 is not an arbitrary editorial choice: it is the largest
# round count that fits content/schemas.py's shared
# LLMGeneratedScript.sections max_length=12 cap once hook(1) + cta(1) +
# (rounds-1) next_challenge transitions + 2 sections/round are all counted
# — see test_content_schemas.py's quiz-section-count regression test.
QUIZ_MIN_ITEMS = 2
QUIZ_MAX_ITEMS = 3
QUIZ_DEFAULT_THINK_DURATION_MS = 3_000


@dataclass(frozen=True)
class FormatSpec:
    """Declares the structural sections a format's script needs, and any
    production semantics beyond plain narration-over-visuals.

    `sections` names the beats a script for this format is expected to
    produce, in order — analogous to script-generation's existing
    hook/intro/body/transition/conclusion/cta section_type enum
    (content/schemas.py's `_SECTION_TYPES`, which these format-specific
    values were added to). `interaction_semantics` documents non-visual
    production behavior a format needs (a pause, a reveal) that a plain
    narration-over-visuals beat doesn't need.
    """

    name: str
    description: str
    sections: tuple[str, ...]
    prompt_name: str
    prompt_version: str
    interaction_semantics: tuple[str, ...] = field(default_factory=tuple)


_FORMAT_SPECS: dict[str, FormatSpec] = {
    DEFAULT_FORMAT: FormatSpec(
        name=DEFAULT_FORMAT,
        description="The current default: a hook, an explanation, a payoff.",
        sections=("hook", "intro", "body", "transition", "conclusion", "cta"),
        prompt_name="script-generation",
        prompt_version="1",
    ),
    "interactive_recall_quiz": FormatSpec(
        name="interactive_recall_quiz",
        description=(
            "A prompt/question, an intentional pause for the viewer to answer "
            "mentally, then a reveal — repeated for several challenges."
        ),
        # "prompt" carries think_duration_ms (content/schemas.py's
        # LLMScriptSection field) instead of a separate empty "pause"
        # section — an LLM-authored section can never be empty text
        # (min_length=1), so the pause is timing attached to the real
        # question text, not a section of its own. See
        # content/schemas.py's think_duration_ms docstring.
        sections=("hook", "prompt", "reveal", "next_challenge", "cta"),
        prompt_name="interactive-recall-quiz",
        # Phase 7B.1, §2 — v2 adds optional per-section language_code so a
        # language-learning quiz's prompt/reveal can each declare their own
        # source/target locale. v1 remains on disk as the historical
        # pre-multilingual prompt; nothing else has ever referenced it.
        prompt_version="2",
        interaction_semantics=(
            "prompt: carries think_duration_ms — a real, timed silent "
            "interval appended after the question is spoken, before reveal",
            "reveal: must state the correct answer plainly; never let the "
            "answer leak into an earlier section's text or captions",
            "prompt/reveal: may each carry an optional language_code — the "
            "source-language locale on prompt, the target-language locale "
            "on reveal — for language-learning quizzes only; null on both "
            "for every other quiz topic",
        ),
    ),
    "practical_micro_lesson": FormatSpec(
        name="practical_micro_lesson",
        description="A concrete problem, an explanation, a worked example, a takeaway.",
        sections=("hook", "problem", "explanation", "example", "takeaway", "cta"),
        prompt_name="practical-micro-lesson",
        prompt_version="1",
    ),
}


def get_format_spec(format_name: str) -> FormatSpec | None:
    """None for any format without a declared spec — including every other
    seed in `taxonomy.FORMAT_SEEDS`. Callers must fall back to the current
    explainer contract (`DEFAULT_FORMAT`) in that case, never raise."""
    return _FORMAT_SPECS.get(format_name)


def resolve_format_spec(format_name: str | None) -> FormatSpec:
    """Never returns None — the one entry point production code should
    call. An unknown, empty, or None format resolves to the default
    explainer spec, exactly matching pre-Phase-7B implicit behavior."""
    if not format_name:
        return _FORMAT_SPECS[DEFAULT_FORMAT]
    return _FORMAT_SPECS.get(format_name, _FORMAT_SPECS[DEFAULT_FORMAT])


def known_format_specs() -> tuple[str, ...]:
    return tuple(_FORMAT_SPECS.keys())
