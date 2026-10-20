"""Narration → literal visual meaning translation (Phase 5, §2-9).

`semantics.py`'s deterministic query builder can only ever be as literal as
the narration text itself. That is exactly the gap this module exists to
close: narration constantly uses figurative language ("a fire drill for
your immune system"), and a purely lexical pipeline searches the metaphor's
OWN surface domain (firefighters) rather than what it actually means within
the video's real topic (immune cells rehearsing a response). This module
makes ONE structured, text-only Anthropic call per video — reusing the
existing `AIRequest`/`ClaudeProvider` machinery exactly as
`eligibility_service.py`'s semantic-fit call does, since this is plain
JSON-in/JSON-out, not vision — and merges its output onto the deterministic
beats `beats.plan_beats()` already produced.

Off by default (`ACE_VISUAL_INTENT_TRANSLATION_ENABLED`), and fails open at
every step: a disabled flag, a missing provider, a malformed response, or a
beat the model's response simply omitted all leave that beat's fields
exactly as `plan_beats()` produced them — this module only ever ADDS
grounding, never removes the Phase 1-2 deterministic baseline underneath it.
Phases 1-4's hardening (tri-state scoring, polysemy protection, vision
rescue, the severe-mismatch block) all run downstream of this and are
completely unaware it exists — a translated beat is scored by the exact
same `score_candidate` as an untranslated one.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.visuals import semantics
from app.visuals.constants import ALL_MEDIA_TYPES
from app.visuals.models import VisualBeat

logger = logging.getLogger(__name__)

DEFAULT_TRANSLATION_MODEL = "claude-haiku-4-5-20251001"
ENV_TRANSLATION_ENABLED = "ACE_VISUAL_INTENT_TRANSLATION_ENABLED"
ENV_TRANSLATION_MODEL = "ACE_VISUAL_INTENT_MODEL"
PROMPT_NAME = "visual-intent-translation"
PROMPT_VERSION = "1"

# Bounded: a script with more beats than this is translated in the first
# chunk only (rare — MAX_BEAT_MS/MIN_BEAT_MS bound a typical Short to well
# under this) rather than growing the call unboundedly. Untranslated beats
# fail open onto their deterministic baseline exactly like any other
# skipped beat.
MAX_BEATS_PER_TRANSLATION_CALL = 40

# Each beat's translation carries seven fields, several of them lists — a
# real multi-beat response does not fit in AIRequest's 1024-token default
# (a 16-beat video truncates mid-JSON and fails the strict parse below).
# Mirrors verification.AnthropicVisionVerifier's identical per-item sizing.
_MAX_TOKENS_PER_BEAT = 220
_MIN_TRANSLATION_MAX_TOKENS = 1024


class _BeatTranslation(BaseModel):
    beat_index: int
    literal_visual_subject: str
    process_or_action: str = ""
    setting: str = ""
    concrete_entities: list[str] = Field(default_factory=list)
    preferred_visual_types: list[str] = Field(default_factory=list)
    search_concepts: list[str] = Field(default_factory=list)
    avoid_literal_interpretations: list[str] = Field(default_factory=list)
    continues_previous: bool = False


class _TranslationOutput(BaseModel):
    topic_context: str
    beats: list[_BeatTranslation]


@dataclass(frozen=True)
class BeatVisualMeaning:
    """One beat's literal visual meaning — the merge-ready projection of
    `_BeatTranslation`, decoupled from the pydantic/wire schema."""

    beat_index: int
    topic_context: str
    literal_visual_subject: str
    process_or_action: str = ""
    setting: str = ""
    concrete_entities: list[str] = field(default_factory=list)
    preferred_visual_types: list[str] = field(default_factory=list)
    search_concepts: list[str] = field(default_factory=list)
    avoid_literal_interpretations: list[str] = field(default_factory=list)
    continues_previous: bool = False


def translation_enabled() -> bool:
    """The deployment-level switch (§5) — mirrors
    `verification.get_content_verifier`'s exact contract: unset/false by
    default, so a missing flag can never unexpectedly start spending."""
    return os.environ.get(ENV_TRANSLATION_ENABLED, "").strip().lower() in ("1", "true", "yes")


def translation_model() -> str:
    return os.environ.get(ENV_TRANSLATION_MODEL, "").strip() or DEFAULT_TRANSLATION_MODEL


def _format_beats_block(beats: list[VisualBeat]) -> str:
    lines = []
    for beat in beats[:MAX_BEATS_PER_TRANSLATION_CALL]:
        lines.append(f"Beat {beat.beat_index}: {beat.narration_text.strip()}")
    return "\n".join(lines)


def translate_visual_intents(
    beats: list[VisualBeat],
    *,
    topic_title: str,
    topic_angle: str,
    ai_provider: Any,
    model: str | None = None,
) -> dict[int, BeatVisualMeaning]:
    """One structured call translating every beat's narration into literal
    visual meaning. Returns an empty dict — never raises — on any failure:
    no provider, a malformed/incomplete response, or a beat count the
    response doesn't exactly account for. §6 duplicate/missing validation is
    "reject the whole response", the same fail-closed contract the vision
    verifier (`verification._parse_verification_response`) already uses for
    the identical reason: a partially-trusted structured response is worse
    than a plainly-absent one.
    """
    if not beats or ai_provider is None:
        return {}

    from app.ai.provider import AIRequest
    from app.ai.registry import PromptRegistry

    try:
        prompt = PromptRegistry().get(PROMPT_NAME, PROMPT_VERSION)
    except Exception as exc:
        logger.warning("visual intent translation: prompt unavailable: %s", exc)
        return {}

    target_beats = beats[:MAX_BEATS_PER_TRANSLATION_CALL]
    expected = {b.beat_index for b in target_beats}
    user_text = prompt.format_user(
        topic_title=topic_title or "(untitled)",
        topic_angle=topic_angle or "(none)",
        beats_block=_format_beats_block(target_beats),
    )

    max_tokens = max(_MIN_TRANSLATION_MAX_TOKENS, _MAX_TOKENS_PER_BEAT * len(target_beats))
    try:
        request = AIRequest(
            system=prompt.system,
            user=user_text,
            model=model or translation_model(),
            max_tokens=max_tokens,
            response_schema=_TranslationOutput,
        )
        response = ai_provider.complete(request)
        if response.parsed is not None:
            output: _TranslationOutput = response.parsed  # type: ignore[assignment]
        else:
            output = _TranslationOutput(**json.loads(response.raw_text))
    except Exception as exc:  # a translation failure must never fail the render
        logger.warning("visual intent translation call failed: %s", exc)
        return {}

    got = {b.beat_index for b in output.beats}
    if got != expected:
        logger.warning(
            "visual intent translation: beat index mismatch (expected %s, got %s) — "
            "discarding the whole response rather than trusting a partial one",
            sorted(expected),
            sorted(got),
        )
        return {}

    allowed_types = set(ALL_MEDIA_TYPES)
    result: dict[int, BeatVisualMeaning] = {}
    for bt in output.beats:
        preferred = [t for t in bt.preferred_visual_types if t in allowed_types]
        result[bt.beat_index] = BeatVisualMeaning(
            beat_index=bt.beat_index,
            topic_context=output.topic_context.strip(),
            literal_visual_subject=bt.literal_visual_subject.strip(),
            process_or_action=bt.process_or_action.strip(),
            setting=bt.setting.strip(),
            concrete_entities=[e.strip() for e in bt.concrete_entities if e.strip()],
            preferred_visual_types=preferred,
            search_concepts=[s.strip() for s in bt.search_concepts if s.strip()],
            avoid_literal_interpretations=[
                a.strip() for a in bt.avoid_literal_interpretations if a.strip()
            ],
            continues_previous=bt.continues_previous,
        )
    return result


def apply_visual_intent_translation(
    beats: list[VisualBeat], translations: dict[int, BeatVisualMeaning]
) -> list[VisualBeat]:
    """Merge literal visual meaning onto the deterministic beats
    `plan_beats()` already produced.

    A beat with no matching entry (translation disabled, failed, or simply
    absent from the response) is returned byte-for-byte unchanged. For a
    translated beat: `entities` and `avoid_terms` are EXTENDED (never
    replaced — the deterministic sibling-derived avoid-terms are still
    real signal), `search_queries` are REPLACED with meaning-grounded ones
    from `build_queries_from_meaning`, and `media_type_preferences` is
    reordered to lead with the translation's preference when it named one.
    """
    if not translations:
        return beats

    updated: list[VisualBeat] = []
    for beat in beats:
        meaning = translations.get(beat.beat_index)
        if meaning is None or not meaning.literal_visual_subject:
            updated.append(beat)
            continue

        new_queries = semantics.build_queries_from_meaning(
            meaning.literal_visual_subject,
            topic_context=meaning.topic_context,
            concrete_entities=meaning.concrete_entities,
            process_or_action=meaning.process_or_action,
        )
        merged_entities = list(beat.entities)
        for e in meaning.concrete_entities:
            if e not in merged_entities:
                merged_entities.append(e)

        merged_avoid = list(beat.avoid_terms)
        for a in meaning.avoid_literal_interpretations:
            if a not in merged_avoid:
                merged_avoid.append(a)

        media_prefs = list(beat.media_type_preferences)
        if meaning.preferred_visual_types:
            # The translation may broaden the offering (e.g. adding
            # "illustration" for a process the deterministic intent
            # classifier alone called "action") as well as reorder it —
            # every entry was already validated against ALL_MEDIA_TYPES in
            # `translate_visual_intents`, so this can never introduce an
            # unknown media type, only a wider or differently-ordered set
            # of already-legitimate ones.
            reordered = list(meaning.preferred_visual_types)
            reordered += [t for t in media_prefs if t not in reordered]
            media_prefs = reordered

        updated.append(
            _replace_beat(
                beat,
                entities=merged_entities,
                avoid_terms=merged_avoid,
                media_type_preferences=media_prefs,
                search_queries=new_queries or beat.search_queries,
                literal_visual_subject=meaning.literal_visual_subject,
                visual_setting=meaning.setting,
                topic_context=meaning.topic_context,
                continues_sequence=meaning.continues_previous,
            )
        )
    return updated


def _replace_beat(beat: VisualBeat, **overrides: Any) -> VisualBeat:
    from dataclasses import replace

    return replace(beat, **overrides)
