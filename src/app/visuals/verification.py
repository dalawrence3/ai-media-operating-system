"""Actual-content verification for selected visual assets (Phase 2, §7-9).

Provider metadata (a candidate's own title/tags) is not the final semantic
authority — `scoring.py` already scores every candidate against that
metadata, but the metadata itself can be wrong, generic, or (as the
gym-bicycle case showed — a clip literally titled "elliptical bicycles")
coincidentally polysemous with the narration. This module is the second,
optional stage: sample one representative frame from each beat's already-
SELECTED asset, composite a bounded set of them into one contact sheet, and
ask a vision-capable judge whether the frames actually depict what the
beat's narration describes.

Design constraints this module exists to satisfy:

  * Off by default. `verify_plan()` is only ever called with a real verifier
    when a channel/policy explicitly opts in — see
    `VisualPolicy.enable_content_verification`. Until that happens, every
    beat's verification fields stay None: "not measured", never "measured as
    fine". `NullVerifier` is the safe default implementation.
  * Cost-bounded. One contact sheet of up to `MAX_BEATS_PER_CONTACT_SHEET`
    beats per verifier call, not one call per beat or per candidate — a
    20-beat video costs at most two calls, not twenty.
  * Fail-safe. A verifier that raises, times out, or is simply not wired up
    never fabricates a "verified" status — `verify_plan` treats any failure
    to obtain a verdict as "leave the beat's verification fields alone"
    (i.e. still None/not-measured), which `composition.py`'s
    `severe_mismatch` property (only true for an explicit "rejected") and
    `quality.py`'s severe-mismatch floor already treat as "no evidence of a
    problem" rather than as either a pass or a block. See §20: production
    does not hold merely because the (optional) verifier is unavailable —
    the deterministic scorer (scoring.py) is the floor that always applies;
    verification is additional assurance on top of it, not a load-bearing
    gate by itself.
  * No new expensive provider is hardwired. `ContentVerifier` is a narrow
    protocol; which model/provider implements it is a separate, explicit
    decision left to the caller (see the module docstring note on
    `ClaudeVisionVerifier` below for why this pass does not ship one).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol

from PIL import Image, ImageDraw

from app.ai.errors import (
    AIError,
    MissingCredentialsError,
    ProviderUnavailableError,
    RateLimitError,
    RequestTimeoutError,
    RetryExhaustedError,
    TransientProviderError,
)
from app.ai.retry import with_retry
from app.visuals.constants import MEDIA_VIDEO
from app.visuals.models import BeatResolution

logger = logging.getLogger(__name__)

VERIFICATION_VERSION = "1.0"

STATUS_VERIFIED = "verified"
STATUS_QUESTIONABLE = "questionable"
STATUS_REJECTED = "rejected"
STATUS_UNAVAILABLE = "unavailable"

ALL_STATUSES = (STATUS_VERIFIED, STATUS_QUESTIONABLE, STATUS_REJECTED, STATUS_UNAVAILABLE)

# One video's worth of retrieved assets is rarely more than this; chunking
# bounds a pathological video to a handful of calls rather than one giant,
# unreadable contact sheet.
MAX_BEATS_PER_CONTACT_SHEET = 12
_THUMB_SIZE = (320, 320)
_LABEL_HEIGHT = 28


@dataclass(frozen=True)
class BeatIntent:
    """The structured intent a verifier judges a frame against.

    Deliberately narrow — everything here already exists on `VisualBeat`;
    this is a read-only projection, not a new schema (§2's "reuse existing
    fields" instruction applies here too).
    """

    beat_index: int
    narration_text: str
    keywords: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    visual_intent: str = "entity"
    avoid_terms: list[str] = field(default_factory=list)
    # Phase 5 (§12) — populated only when narration→literal-visual-meaning
    # translation ran for this beat (`intent_translation.py`); "" otherwise,
    # in which case the verifier judges against narration alone exactly as
    # before this field existed. When present, the judge is told what the
    # narration MEANS (topic-grounded), not just what it says — this is
    # what makes a metaphor's own literal surface domain (firefighters, for
    # "a fire drill for your immune system") judgeable as wrong even though
    # it visually matches the narration's literal words.
    topic_context: str = ""
    literal_visual_subject: str = ""


@dataclass(frozen=True)
class BeatVerification:
    beat_index: int
    status: str
    reason: str | None = None
    confidence: float | None = None


class ContentVerifier(Protocol):
    """Judges a contact sheet of frames against their beats' intent.

    Implementations must return exactly one `BeatVerification` per beat
    passed in `beats` (same `beat_index`s), and must never raise for an
    ordinary "the model was unsure" outcome — that is `STATUS_QUESTIONABLE`,
    not an exception. An exception signals infrastructure failure and is
    caught by `verify_plan`, which treats it as "verification did not run"
    for every beat in that chunk.
    """

    def verify(self, contact_sheet: bytes, beats: list[BeatIntent]) -> list[BeatVerification]: ...


class NullVerifier:
    """The default: verification never ran. Never fabricates a verdict."""

    def verify(self, contact_sheet: bytes, beats: list[BeatIntent]) -> list[BeatVerification]:
        return [
            BeatVerification(b.beat_index, STATUS_UNAVAILABLE, "verification disabled")
            for b in beats
        ]


DEFAULT_VERIFICATION_MODEL = "claude-haiku-4-5-20251001"
ENV_VERIFICATION_ENABLED = "ACE_VISUAL_VERIFICATION_ENABLED"
ENV_VERIFICATION_MODEL = "ACE_VISUAL_VERIFICATION_MODEL"


def get_content_verifier() -> ContentVerifier:
    """The single place a real verifier gets selected.

    Two independent switches must both be true for a real (costed) verifier
    to be returned, matching §15/§20 — a missing flag must never
    unexpectedly start provider spending:

      1. The deployment-level kill switch, ``ACE_VISUAL_VERIFICATION_ENABLED``
         (unset/false by default).
      2. Credentials actually being present (`Config.anthropic_api_key`,
         i.e. ``ACE_ANTHROPIC_API_KEY`` — the same credential every other AI
         call in this codebase already uses; no second secret is created).

    The per-channel `VisualPolicy.enable_content_verification` flag (Phase
    2) is the finer-grained switch on top of this one — `stage_executors`
    only calls anything in this module at all when that policy flag is also
    set. Missing either layer, or any failure constructing the client,
    fails back to `NullVerifier` rather than raising — a configuration
    mistake must degrade to "no verification", never crash production.
    """
    import os

    from app.core.config import get_config

    if os.environ.get(ENV_VERIFICATION_ENABLED, "").strip().lower() not in ("1", "true", "yes"):
        return NullVerifier()

    api_key = get_config().anthropic_api_key
    if not api_key:
        return NullVerifier()

    model = os.environ.get(ENV_VERIFICATION_MODEL, "").strip() or DEFAULT_VERIFICATION_MODEL
    try:
        return AnthropicVisionVerifier(api_key=api_key, model=model)
    except MissingCredentialsError:
        return NullVerifier()


# ── Vision-backed adapter ────────────────────────────────────────────────────
#
# Deliberately bypasses `app.ai.claude.ClaudeProvider`/`AIRequest`: that pair
# only builds a single plain-string `user` message
# (`messages=[{"role": "user", "content": request.user}]` — app/ai/claude.py)
# not the Anthropic Messages API's multi-part content blocks (image + text) a
# vision call needs, and it is the shared abstraction behind every other AI
# call in this codebase (script generation, topic evaluation, semantic fit).
# Extending it for this one narrow visual-QA use case would give every
# unrelated text workflow a wider blast radius for a feature only this module
# needs — exactly the "unnecessary blast radius" this adapter exists to
# avoid. This class instead talks to the `anthropic` SDK directly, while
# reusing the same credential source (`Config.anthropic_api_key`), the same
# retry helper (`app.ai.retry.with_retry`), and the same typed error
# hierarchy (`app.ai.errors`) as `ClaudeProvider` — so it fails the same way
# the rest of the AI layer does, it just does not share that layer's code.

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)
_RETRYABLE_VISION_ERRORS = (RateLimitError, TransientProviderError)

_VERIFICATION_SYSTEM_PROMPT = """\
You are a strict visual-content auditor for short-form educational video \
production. You will see one contact sheet image containing several \
labelled thumbnails ("beat N"), and a text description of what each \
numbered beat's narration is about.

Judge whether each thumbnail actually depicts or strongly supports the \
EDUCATIONAL CONCEPT each beat is illustrating — not merely whether it \
visually matches the narration's literal words. This is not a creative or \
aesthetic judgement — you are checking for a real-world content mismatch. \
Three distinct mismatch patterns to watch for:

1. Ambiguous-word mismatch: a word in the narration has an unrelated \
everyday meaning that coincidentally matches unrelated stock footage (for \
example: a narration about an "elliptical galaxy" paired with footage of \
an "elliptical" exercise machine; a narration about a battery "cell" \
paired with a prison cell; a narration about an electrical "current" \
paired with an ocean current).

2. Literal-metaphor mismatch: when a beat carries a "topic" and a "literal \
visual subject" (shown below, when available), the narration was \
figurative and those two fields tell you what it actually means. A \
thumbnail that matches the metaphor's own surface wording but NOT the \
topic-grounded literal subject is WRONG even though it is a literally \
accurate match for the narration's words — for example, real firefighters \
for a beat whose narration says "a fire drill for your immune system" but \
whose topic is immunology and literal visual subject is immune cells \
rehearsing a response: reject or mark questionable, never verify, unless \
the image ALSO happens to genuinely support the topic-grounded concept.

3. Generic-shared-object mismatch: a thumbnail can share a generic object \
or scene TYPE with the narration (equations on a surface, a person at a \
whiteboard, a chart, a laboratory, "a scientist") while actually belonging \
to a completely different field or subject than the beat's topic/domain. \
Sharing an object class is not evidence of relevance on its own — check \
whether the SPECIFIC content shown is actually consistent with the beat's \
topic, not just its category. For example: a chemistry teacher deriving an \
organic-chemistry reaction on a whiteboard is NOT a valid "equation" visual \
for a beat about unifying physics and gravity, even though both frames show \
"a person explaining equations on a board" — the equations, and the field \
they belong to, are wrong. Ask yourself "would an expert in THIS beat's own \
topic recognize this as their subject?", not "does this generically look \
like the right kind of picture?". When in doubt whether the specific \
content actually matches the topic (versus only its surface category), \
prefer "questionable" over "verified".

When a beat has no topic/literal-visual-subject given, judge patterns 1 and \
3 against its narration text alone, exactly as usual; pattern 2 does not \
apply without those fields.

For each beat, decide:
- "verified": the image clearly depicts or strongly supports the narrated \
subject, process, or context.
- "questionable": the image is broadly related but generic, weak, or not \
specific enough to confirm — this is the correct verdict for a reasonable \
but non-specific match, not "rejected".
- "rejected": the image is visually unrelated, misleading, depicts the \
wrong sense of an ambiguous word, contradicts the narration, or clearly \
belongs to a different subject/domain entirely.

Respond with ONLY a JSON array, one object per beat, in this exact shape, \
and nothing else:
[{"beat_index": <int>, "status": "verified"|"questionable"|"rejected", \
"confidence": <0.0-1.0>, "reason": "<one concise sentence>"}]

You must include every beat listed below exactly once, in any order. Do \
not invent beats, omit any, or include a beat twice."""


def _strip_json_fences(text: str) -> str:
    match = _JSON_FENCE_RE.search(text)
    return match.group(1).strip() if match else text


def _translate_vision_error(exc: Exception) -> AIError:
    """Map an Anthropic SDK exception to the shared `app.ai.errors` hierarchy.

    A small, local copy of `app.ai.claude._translate`'s mapping rather than
    an import — this adapter is deliberately self-contained (see the module
    note above), and the mapping itself is ~10 lines of SDK-exception
    triage, not meaningful shared logic.
    """
    import anthropic

    if isinstance(exc, anthropic.AuthenticationError):
        return MissingCredentialsError(f"Anthropic authentication failed: {type(exc).__name__}")
    if isinstance(exc, anthropic.RateLimitError):
        return RateLimitError(f"Anthropic rate limit: {type(exc).__name__}")
    if isinstance(exc, anthropic.APITimeoutError):
        return RequestTimeoutError(f"Anthropic request timed out: {type(exc).__name__}")
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderUnavailableError(f"Anthropic connection error: {type(exc).__name__}")
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500:
        return TransientProviderError(
            f"Anthropic server error {exc.status_code}: {type(exc).__name__}"
        )
    return ProviderUnavailableError(f"Anthropic API error: {type(exc).__name__}")


def _format_beat_intent(beat: BeatIntent) -> str:
    lines = [
        f"Beat {beat.beat_index}",
        f"  narration: {beat.narration_text!r}",
    ]
    if beat.topic_context or beat.literal_visual_subject:
        lines.append(f"  topic: {beat.topic_context or '(unknown)'}")
        lines.append(
            f"  literal visual subject (what this beat MEANS, topic-grounded): "
            f"{beat.literal_visual_subject or '(unknown)'}"
        )
    lines.append(f"  subject/entities: {', '.join(beat.entities) or '(none extracted)'}")
    lines.append(f"  keywords: {', '.join(beat.keywords) or '(none)'}")
    lines.append(f"  visual intent: {beat.visual_intent}")
    lines.append(f"  avoid terms: {', '.join(beat.avoid_terms) or '(none)'}")
    return "\n".join(lines)


def _build_user_prompt(beats: list[BeatIntent]) -> str:
    body = "\n\n".join(_format_beat_intent(b) for b in beats)
    indexes = ", ".join(str(b.beat_index) for b in beats)
    return (
        f"{body}\n\n"
        f"Return one JSON object for exactly these beat indexes, each exactly "
        f"once: {indexes}."
    )


class _MalformedVerifierResponse(ValueError):
    """The provider's response could not be trusted as-is — never partially
    applied. See `_parse_verification_response`."""


def _extract_text(content: list[Any]) -> str:
    """Find the actual text block in an Anthropic response's content list.

    Phase 7C.1 — content[0] is NOT always the text block: some models
    (confirmed: claude-sonnet-5, via a real cold-start call in
    app.ai.claude) auto-enable extended thinking for complex requests,
    putting a ThinkingBlock (no `.text` attribute) at content[0] and the
    real text at a later index. Same fix as app.ai.claude._extract_text,
    applied here too since this module builds its own raw Anthropic
    client rather than going through ClaudeProvider.
    """
    for block in content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise _MalformedVerifierResponse(
        "response contains no text block "
        f"(block types: {[getattr(b, 'type', type(b).__name__) for b in content]})"
    )


def _parse_verification_response(
    raw_text: str, *, expected_indexes: list[int]
) -> list[BeatVerification]:
    """Strictly parse and validate the model's JSON array.

    Every expected beat index must appear exactly once, with a recognised
    status and a numeric confidence in [0, 1]. Any violation — missing beat,
    duplicate beat, extra beat, unrecognised status, non-numeric confidence,
    invalid JSON — raises `_MalformedVerifierResponse` rather than guessing:
    the caller treats that as "verification unavailable" for the whole
    chunk, never a partial or fabricated result.
    """
    try:
        data = json.loads(_strip_json_fences(raw_text))
    except json.JSONDecodeError as exc:
        raise _MalformedVerifierResponse(f"response is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise _MalformedVerifierResponse("response is not a JSON array")

    seen: dict[int, BeatVerification] = {}
    for entry in data:
        if not isinstance(entry, dict):
            raise _MalformedVerifierResponse("response entry is not an object")
        try:
            beat_index = int(entry["beat_index"])
            status = entry["status"]
            confidence = float(entry["confidence"])
            reason = str(entry.get("reason", "")).strip() or None
        except (KeyError, TypeError, ValueError) as exc:
            raise _MalformedVerifierResponse(f"malformed entry: {exc}") from exc
        if status not in (STATUS_VERIFIED, STATUS_QUESTIONABLE, STATUS_REJECTED):
            raise _MalformedVerifierResponse(f"unrecognised status {status!r}")
        if not (0.0 <= confidence <= 1.0):
            raise _MalformedVerifierResponse(f"confidence {confidence!r} out of [0,1]")
        if beat_index in seen:
            raise _MalformedVerifierResponse(f"duplicate beat_index {beat_index}")
        seen[beat_index] = BeatVerification(beat_index, status, reason, confidence)

    expected = set(expected_indexes)
    got = set(seen)
    if got != expected:
        raise _MalformedVerifierResponse(
            f"beat index mismatch: expected {sorted(expected)}, got {sorted(got)}"
        )
    return [seen[i] for i in expected_indexes]


class AnthropicVisionVerifier:
    """Vision-backed `ContentVerifier` using the Anthropic Messages API.

    Never fabricates "verified": any provider failure (auth, rate limit,
    timeout, connection, 5xx) or malformed/incomplete response is classified
    `STATUS_UNAVAILABLE` for every beat in that request — see
    `_parse_verification_response` and the module docstring's fail-safe
    contract.
    """

    name = "anthropic_vision"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_VERIFICATION_MODEL,
        timeout: float = 30.0,
        max_retries: int = 2,
        max_tokens_per_beat: int = 100,
        sleep_fn: Callable[[float], None] | None = None,
        client: Any = None,
    ) -> None:
        if not api_key:
            raise MissingCredentialsError(
                "ANTHROPIC_API_KEY is required for AnthropicVisionVerifier. "
                "Set the ACE_ANTHROPIC_API_KEY environment variable."
            )
        self._model = model
        self._max_retries = max_retries
        self._max_tokens_per_beat = max_tokens_per_beat
        self._sleep_fn = sleep_fn or time.sleep
        if client is not None:
            self._client = client
        else:
            import anthropic

            self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        # Non-secret usage telemetry (§14): model, batch/image counts, token
        # counts per call. Read by the caller after a run; never persisted
        # here (this module owns no database connection — see
        # verification_repository.py for the caching/cost-recording layer).
        self.last_run_stats: list[dict[str, Any]] = []

    def verify(self, contact_sheet: bytes, beats: list[BeatIntent]) -> list[BeatVerification]:
        if not beats:
            return []
        image_b64 = base64.standard_b64encode(contact_sheet).decode("ascii")
        prompt = _build_user_prompt(beats)
        max_tokens = max(300, self._max_tokens_per_beat * len(beats))

        try:
            raw_response, retry_count = with_retry(
                lambda: self._call_api(prompt, image_b64, max_tokens),
                max_attempts=self._max_retries,
                retryable=_RETRYABLE_VISION_ERRORS,
                backoff_base=1.0,
                sleep_fn=self._sleep_fn,
            )
        except (RetryExhaustedError, AIError) as exc:
            logger.warning(
                "visual content verification unavailable: model=%s beats=%d error=%s",
                self._model,
                len(beats),
                type(exc).__name__,
            )
            return [
                BeatVerification(b.beat_index, STATUS_UNAVAILABLE, f"provider error: {exc}")
                for b in beats
            ]

        expected = [b.beat_index for b in beats]
        try:
            verdicts = _parse_verification_response(
                _extract_text(raw_response.content), expected_indexes=expected
            )
        except _MalformedVerifierResponse as exc:
            logger.warning(
                "visual content verification malformed response: model=%s beats=%d error=%s",
                self._model,
                len(beats),
                exc,
            )
            return [
                BeatVerification(b.beat_index, STATUS_UNAVAILABLE, f"malformed response: {exc}")
                for b in beats
            ]

        usage = getattr(raw_response, "usage", None)
        stats = {
            "model": getattr(raw_response, "model", self._model),
            "beats": len(beats),
            "retry_count": retry_count,
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        }
        self.last_run_stats.append(stats)
        logger.info("visual content verification call: %s", stats)
        return verdicts

    def _call_api(self, prompt: str, image_b64: str, max_tokens: int) -> Any:
        import anthropic

        try:
            return self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=_VERIFICATION_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": image_b64,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
        except anthropic.APIError as exc:
            raise _translate_vision_error(exc) from exc


def _ffmpeg_frame(local_path: str, *, ffmpeg_bin: str = "ffmpeg") -> bytes | None:
    """Grab one representative frame from a video file as PNG bytes.

    Sampled at 40% of... actually duration is unknown without a second probe
    call, so this seeks a fixed, generous offset (1.2s) instead — beats are
    short (1.5-6.5s per constants.MIN_BEAT_MS/MAX_BEAT_MS) and stock clips
    routinely run long, so a fixed early-but-not-first-frame offset avoids
    both a black opening frame and running past a short clip's end. Returns
    None on any failure — this is a best-effort optional layer, never a
    reason to fail a render.
    """
    if not shutil.which(ffmpeg_bin) or not Path(local_path).exists():
        return None
    try:
        result = subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-ss",
                "1.2",
                "-i",
                local_path,
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "pipe:1",
            ],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def extract_representative_frame(
    resolution: BeatResolution, *, ffmpeg_bin: str = "ffmpeg"
) -> bytes | None:
    """One representative frame for a resolved beat's asset, or None.

    Only meaningful for a retrieved (non-`programmatic`) asset — a generated
    graphic is an exact deterministic render of the beat's own structured
    text, not a claim about the world a vision model needs to check.
    """
    if resolution.provider == "programmatic" or not resolution.local_path:
        return None
    if not Path(resolution.local_path).exists():
        return None
    if resolution.media_type == MEDIA_VIDEO:
        return _ffmpeg_frame(resolution.local_path, ffmpeg_bin=ffmpeg_bin)
    try:
        with Image.open(resolution.local_path) as img:
            buf = BytesIO()
            img.convert("RGB").save(buf, format="PNG")
            return buf.getvalue()
    except OSError:
        return None


def build_contact_sheet(frames: list[tuple[int, bytes]]) -> bytes | None:
    """Composite labelled thumbnails into one grid image, as PNG bytes.

    Each cell is labelled with its beat index so a verifier's per-beat
    verdicts can be matched back to the frame it actually judged — an
    unlabelled grid would let a verifier's response silently misalign with
    the beats it claims to describe.
    """
    if not frames:
        return None
    columns = min(4, len(frames))
    rows = (len(frames) + columns - 1) // columns
    cell_w, cell_h = _THUMB_SIZE
    sheet = Image.new("RGB", (columns * cell_w, rows * (cell_h + _LABEL_HEIGHT)), (24, 24, 28))
    draw = ImageDraw.Draw(sheet)
    for i, (beat_index, data) in enumerate(frames):
        col, row = i % columns, i // columns
        x, y = col * cell_w, row * (cell_h + _LABEL_HEIGHT)
        try:
            with Image.open(BytesIO(data)) as thumb:
                thumb = thumb.convert("RGB")
                thumb.thumbnail(_THUMB_SIZE)
                offset = (x + (cell_w - thumb.width) // 2, y + (cell_h - thumb.height) // 2)
                sheet.paste(thumb, offset)
        except OSError:
            continue
        draw.rectangle([x, y + cell_h, x + cell_w, y + cell_h + _LABEL_HEIGHT], fill=(40, 40, 46))
        draw.text((x + 6, y + cell_h + 6), f"beat {beat_index}", fill=(230, 230, 235))
    buf = BytesIO()
    sheet.save(buf, format="PNG")
    return buf.getvalue()


def verification_cache_key(
    resolution: BeatResolution, *, model: str, verifier_version: str = VERIFICATION_VERSION
) -> str:
    """A stable identity for "this asset, judged against this intent, by
    this model version" (§7).

    Built from the SELECTED asset's identity and every field of the beat's
    intent a verifier actually sees (`_format_beat_intent`) plus the
    model/version — so a cache hit is reused only when both the asset AND
    the narration/intent it is being judged against are unchanged, and a
    result from one verifier version is never silently reused under another.
    Deliberately excludes the local file *path* (which can change between
    renders even for the identical provider asset) in favour of
    `asset_key`, the same provider-scoped identity `memory.py`'s reuse
    tracking already treats as the asset's true identity.
    """
    beat = resolution.beat
    payload = json.dumps(
        {
            "asset_key": resolution.asset_key,
            "narration_text": beat.narration_text,
            "keywords": beat.keywords,
            "entities": beat.entities,
            "visual_intent": beat.visual_intent,
            "avoid_terms": beat.avoid_terms,
            "topic_context": beat.topic_context,
            "literal_visual_subject": beat.literal_visual_subject,
            "model": model,
            "verifier_version": verifier_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:40]


def _chunks(items: list[BeatResolution], size: int) -> list[list[BeatResolution]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def verify_plan(
    resolutions: list[BeatResolution],
    verifier: ContentVerifier,
    *,
    ffmpeg_bin: str = "ffmpeg",
    max_beats_per_sheet: int = MAX_BEATS_PER_CONTACT_SHEET,
) -> int:
    """Run actual-content verification over a resolved plan's beats, in place.

    Mutates each covered `BeatResolution`'s `verification_status` /
    `verification_reason` / `visual_relevance_confidence`. Returns the number
    of beats a verdict was actually written for.

    Only retrieved (non-graphic) resolutions with a real local file are
    candidates. Beats whose frame could not be extracted, or whose chunk's
    verifier call raised, are left untouched (still None) rather than marked
    with any status — a failure to check is not evidence either way.

    A beat that already carries a `verification_status` (Phase 4: vision
    rescue — engine.VisualEngine.vision_rescue — already verified its own
    replacement asset before this ever runs) is skipped, so the same asset
    is never paid for twice.
    """
    candidates = [
        r
        for r in resolutions
        if r.provider != "programmatic" and r.local_path and r.verification_status is None
    ]
    written = 0
    for chunk in _chunks(candidates, max_beats_per_sheet):
        frames: list[tuple[int, bytes]] = []
        intents: list[BeatIntent] = []
        by_index = {}
        for resolution in chunk:
            frame = extract_representative_frame(resolution, ffmpeg_bin=ffmpeg_bin)
            if frame is None:
                continue
            beat = resolution.beat
            frames.append((beat.beat_index, frame))
            intents.append(
                BeatIntent(
                    beat_index=beat.beat_index,
                    narration_text=beat.narration_text,
                    keywords=list(beat.keywords),
                    entities=list(beat.entities),
                    visual_intent=beat.visual_intent,
                    avoid_terms=list(beat.avoid_terms),
                    topic_context=beat.topic_context,
                    literal_visual_subject=beat.literal_visual_subject,
                )
            )
            by_index[beat.beat_index] = resolution

        if not frames:
            continue
        sheet = build_contact_sheet(frames)
        if sheet is None:
            continue

        try:
            verdicts = verifier.verify(sheet, intents)
        except Exception:  # a verifier outage must never fail the render
            continue

        for verdict in verdicts:
            resolution = by_index.get(verdict.beat_index)
            if resolution is None:
                continue
            resolution.verification_status = verdict.status
            resolution.verification_reason = verdict.reason
            resolution.visual_relevance_confidence = verdict.confidence
            written += 1

    return written
