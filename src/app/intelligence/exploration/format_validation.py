"""Phase 7B, §11/§13/§20 — format-specific script structure validation.

Additive, opt-in, and separate from `content.validator.validate_script`:
the base validator's citation/word-count/duration checks apply identically
to every format and are never touched here. This module only adds checks
`content.schemas`' shared `_SECTION_TYPES`/`LLMGeneratedScript` structural
rules cannot express (ordering between specific section types, per-format
item-count bounds) — called by the caller only for a format that actually
has one (`known_format_specs()`), never for the default explainer, which
keeps relying on the base validator alone exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.content.schemas import GeneratedScript
from app.intelligence.exploration.format_contract import QUIZ_MAX_ITEMS, QUIZ_MIN_ITEMS


@dataclass(frozen=True)
class FormatValidationResult:
    valid: bool
    violations: tuple[str, ...]


def _ok() -> FormatValidationResult:
    return FormatValidationResult(valid=True, violations=())


def _fail(*violations: str) -> FormatValidationResult:
    return FormatValidationResult(valid=False, violations=violations)


def validate_quiz_script(script: GeneratedScript) -> FormatValidationResult:
    """interactive_recall_quiz structural rules (§11):
    - each round is exactly one "prompt" immediately followed by one
      "reveal" — never two prompts in a row (which would let an answer be
      asked before the prior one is revealed) and never a reveal that
      isn't immediately preceded by its own prompt (which would let the
      answer appear before any question was asked at all — the concrete
      "answer leaks before reveal" failure mode named in §11).
    - every "prompt" section has a real think_duration_ms > 0; every other
      section has none.
    - the number of rounds (prompt sections) is within QUIZ_MIN_ITEMS..
      QUIZ_MAX_ITEMS.
    - Phase 7B.1, §2: a round's language_code is either set on BOTH its
      prompt (source language) and reveal (target language), or unset on
      both — a language-learning quiz always declares a full pair, and a
      non-language quiz (the common case) never sets it at all.
    """
    violations: list[str] = []
    sections = script.sections

    prompts = [s for s in sections if s.section_type == "prompt"]
    reveals = [s for s in sections if s.section_type == "reveal"]

    if len(prompts) != len(reveals):
        violations.append(
            f"prompt/reveal count mismatch: {len(prompts)} prompt(s), {len(reveals)} reveal(s)"
        )

    if not (QUIZ_MIN_ITEMS <= len(prompts) <= QUIZ_MAX_ITEMS):
        violations.append(
            f"quiz has {len(prompts)} round(s); must be between "
            f"{QUIZ_MIN_ITEMS} and {QUIZ_MAX_ITEMS}"
        )

    # Ordering: scan for a prompt with no immediately-following reveal
    # (allowing "next_challenge" to sit between a reveal and the next
    # prompt, but never between a prompt and its own reveal).
    i = 0
    n = len(sections)
    while i < n:
        section = sections[i]
        if section.section_type == "prompt":
            if i + 1 >= n or sections[i + 1].section_type != "reveal":
                violations.append(
                    f"section {section.section_index} ('prompt') is not immediately "
                    "followed by its own 'reveal' — the answer must never be exposed "
                    "before the reveal, nor a new prompt started before this one "
                    "is resolved"
                )
        i += 1

    for section in sections:
        if section.section_type == "prompt" and section.think_duration_ms <= 0:
            violations.append(
                f"section {section.section_index} ('prompt') has no think_duration_ms "
                "— a recall quiz must give the viewer real time to answer"
            )
        if section.section_type != "prompt" and section.think_duration_ms > 0:
            violations.append(
                f"section {section.section_index} ('{section.section_type}') has "
                "think_duration_ms set — only 'prompt' sections may carry a pause"
            )

    if len(prompts) == len(reveals):
        for round_index, (prompt_section, reveal_section) in enumerate(
            zip(prompts, reveals, strict=True)
        ):
            prompt_has_lang = bool(prompt_section.language_code)
            reveal_has_lang = bool(reveal_section.language_code)
            if prompt_has_lang != reveal_has_lang:
                violations.append(
                    f"round {round_index}: prompt and reveal must both set language_code "
                    "(a source/target language pair) or both leave it unset — got "
                    f"prompt.language_code={prompt_section.language_code!r}, "
                    f"reveal.language_code={reveal_section.language_code!r}"
                )

    return _fail(*violations) if violations else _ok()


_MICRO_LESSON_REQUIRED_SECTIONS = ("hook", "problem", "explanation", "example", "takeaway")


def validate_micro_lesson_script(script: GeneratedScript) -> FormatValidationResult:
    """practical_micro_lesson structural rules (§13/§20): every required
    section is present exactly once, and "example" is a genuinely distinct
    section from "explanation" (never merely absent because the model
    folded the worked example into the explanation text)."""
    violations: list[str] = []
    present = [s.section_type for s in script.sections]

    for required in _MICRO_LESSON_REQUIRED_SECTIONS:
        count = present.count(required)
        if count == 0:
            violations.append(f"missing required section: '{required}'")
        elif count > 1:
            violations.append(f"section '{required}' appears {count} times; expected exactly 1")

    return _fail(*violations) if violations else _ok()


_VALIDATORS = {
    "interactive_recall_quiz": validate_quiz_script,
    "practical_micro_lesson": validate_micro_lesson_script,
}


def validate_format_script(
    script: GeneratedScript, format_name: str | None
) -> FormatValidationResult:
    """The single entry point production code should call. Any format
    without a dedicated validator (including the default explainer, and
    every other exploration-cell format seed with no production support
    yet) always passes — this module only ever ADDS constraints for the
    two formats it actually knows, never blocks anything else."""
    validator = _VALIDATORS.get(format_name or "")
    if validator is None:
        return _ok()
    return validator(script)
