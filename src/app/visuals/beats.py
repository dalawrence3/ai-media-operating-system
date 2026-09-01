"""Semantic visual engine — beat segmentation.

A *scene* owns one narration segment and its audio asset.  A *beat* owns a
stretch of the visual track inside a scene.  Beats tile their parent scene
exactly, so re-planning visuals can never disturb narration lineage, audio
concatenation, or caption timing.

Segmentation is deterministic and prefers boundaries the narration already
has: caption cues (which are themselves clause-shaped).  When no cues exist,
clause punctuation in the narration text is used instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.visuals import semantics
from app.visuals.constants import (
    BEAT_PLANNER_VERSION,
    DEFAULT_TARGET_BEAT_MS,
    MAX_BEAT_MS,
    MIN_BEAT_MS,
    MOTION_NONE,
    STILL_MOTIONS,
)
from app.visuals.models import VisualBeat

__all__ = ["BeatPlannerInput", "CueWindow", "plan_beats", "BEAT_PLANNER_VERSION"]


@dataclass(frozen=True)
class CueWindow:
    """A timed text window inside one scene, relative to the scene start."""

    text: str
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass
class BeatPlannerInput:
    """One narration scene to be segmented into visual beats."""

    scene_index: int
    scene_id: int | None
    segment_id: int
    start_ms: int  # absolute position of the scene on the video timeline
    duration_ms: int
    narration_text: str
    section_type: str = "body"
    claim_ids: list[int] | None = None
    cues: list[CueWindow] | None = None


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def _group_windows(
    windows: list[CueWindow],
    *,
    target_ms: int,
    min_ms: int,
    max_ms: int,
) -> list[list[CueWindow]]:
    """Greedily group cue windows into beat-sized runs.

    A run closes at whichever boundary lands closest to *target_ms*, subject to
    the min/max envelope.  This keeps beats aligned to real clause boundaries
    instead of slicing on a fixed clock.
    """
    if not windows:
        return []

    groups: list[list[CueWindow]] = []
    current: list[CueWindow] = []
    accumulated = 0

    for index, window in enumerate(windows):
        current.append(window)
        accumulated += window.duration_ms
        next_window = windows[index + 1] if index + 1 < len(windows) else None

        if next_window is None:
            break

        if accumulated < min_ms:
            continue

        projected = accumulated + next_window.duration_ms
        if projected > max_ms:
            close = True
        elif accumulated >= target_ms:
            close = True
        else:
            # Close here if stopping now lands nearer the target than going on.
            close = abs(accumulated - target_ms) <= abs(projected - target_ms)

        if close:
            groups.append(current)
            current = []
            accumulated = 0

    if current:
        # A trailing run below the minimum is absorbed by its predecessor
        # rather than shown as a subliminal flash.
        if groups and accumulated < min_ms:
            groups[-1].extend(current)
        else:
            groups.append(current)

    return groups


def _split_oversized(
    spans: list[tuple[int, int, str]],
    *,
    max_ms: int,
    target_ms: int,
) -> list[tuple[int, int, str]]:
    """Divide any span longer than *max_ms* into equal sub-spans."""
    result: list[tuple[int, int, str]] = []
    for start, end, text in spans:
        duration = end - start
        if duration <= max_ms:
            result.append((start, end, text))
            continue
        pieces = max(2, round(duration / max(target_ms, 1)))
        step = duration / pieces
        for i in range(pieces):
            piece_start = int(start + step * i)
            piece_end = int(start + step * (i + 1)) if i < pieces - 1 else end
            result.append((piece_start, piece_end, text))
    return result


def _spans_from_text(
    narration_text: str, duration_ms: int, target_ms: int
) -> list[tuple[int, int, str]]:
    """Fallback segmentation when a scene has no caption cues.

    Clause offsets are mapped onto the scene duration proportionally to
    character count — narration pace is close enough to uniform for this to be
    a reasonable estimate, and cues are used whenever they exist.
    """
    text = (narration_text or "").strip()
    if not text or duration_ms <= 0:
        return [(0, max(duration_ms, 0), text)]

    boundaries = [b for b in semantics.clause_boundaries(text) if 0 < b < len(text)]
    boundaries.append(len(text))

    wanted = max(1, round(duration_ms / max(target_ms, 1)))
    if len(boundaries) > wanted:
        # Keep the boundaries closest to evenly spaced positions.
        keep: list[int] = []
        for i in range(1, wanted):
            ideal = len(text) * i / wanted
            keep.append(min(boundaries[:-1], key=lambda b: abs(b - ideal)))
        boundaries = sorted(set(keep)) + [len(text)]

    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for boundary in boundaries:
        if boundary <= cursor:
            continue
        start_ms = int(duration_ms * cursor / len(text))
        end_ms = int(duration_ms * boundary / len(text))
        spans.append((start_ms, end_ms, text[cursor:boundary].strip()))
        cursor = boundary

    if spans:
        spans[-1] = (spans[-1][0], duration_ms, spans[-1][2])
    return spans or [(0, duration_ms, text)]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def plan_beats(
    scenes: list[BeatPlannerInput],
    *,
    target_beat_ms: int = DEFAULT_TARGET_BEAT_MS,
    min_beat_ms: int = MIN_BEAT_MS,
    max_beat_ms: int = MAX_BEAT_MS,
) -> list[VisualBeat]:
    """Segment every scene into semantic visual beats.

    Returns beats ordered by absolute start time, globally indexed.  Beats
    tile each scene exactly: the first beat starts at the scene start and the
    last beat ends at the scene end.
    """
    target_beat_ms = max(1, target_beat_ms)
    min_beat_ms = max(1, min(min_beat_ms, target_beat_ms))
    max_beat_ms = max(target_beat_ms, max_beat_ms)

    # Video-level terms anchor thin beats to the subject of the whole video.
    topic_terms = semantics.extract_terms(" ".join(s.narration_text for s in scenes), limit=4)

    # First pass: timing + text only, so avoid-terms can see every sibling.
    raw: list[tuple[BeatPlannerInput, int, int, str]] = []
    for scene in scenes:
        spans: list[tuple[int, int, str]]
        if scene.cues:
            groups = _group_windows(
                sorted(scene.cues, key=lambda c: c.start_ms),
                target_ms=target_beat_ms,
                min_ms=min_beat_ms,
                max_ms=max_beat_ms,
            )
            spans = [
                (
                    group[0].start_ms,
                    group[-1].end_ms,
                    " ".join(w.text.replace("\n", " ").strip() for w in group).strip(),
                )
                for group in groups
            ]
        else:
            spans = _spans_from_text(scene.narration_text, scene.duration_ms, target_beat_ms)

        spans = _split_oversized(spans, max_ms=max_beat_ms, target_ms=target_beat_ms)

        if not spans:
            spans = [(0, scene.duration_ms, scene.narration_text)]

        # Clamp to the scene envelope and close any gaps so beats tile exactly.
        normalised: list[tuple[int, int, str]] = []
        cursor = 0
        for i, (_start, end, text) in enumerate(spans):
            end = scene.duration_ms if i == len(spans) - 1 else min(end, scene.duration_ms)
            if end <= cursor and i < len(spans) - 1:
                continue
            normalised.append((cursor, max(end, cursor), text))
            cursor = max(end, cursor)

        for start, end, text in normalised:
            raw.append((scene, scene.start_ms + start, scene.start_ms + end, text))

    all_texts = [text for _, _, _, text in raw]
    beats: list[VisualBeat] = []

    for beat_index, (scene, start_ms, end_ms, text) in enumerate(raw):
        is_first = beat_index == 0
        is_last = beat_index == len(raw) - 1
        intent = semantics.classify_intent(
            text,
            section_type=scene.section_type,
            is_first_beat=is_first,
            is_last_beat=is_last,
        )
        siblings = all_texts[:beat_index] + all_texts[beat_index + 1 :]
        beats.append(
            VisualBeat(
                beat_index=beat_index,
                scene_index=scene.scene_index,
                scene_id=scene.scene_id,
                segment_id=scene.segment_id,
                start_ms=start_ms,
                end_ms=end_ms,
                duration_ms=end_ms - start_ms,
                narration_text=text,
                keywords=semantics.extract_terms(text, limit=6),
                entities=semantics.extract_entities(text),
                visual_intent=intent,
                media_type_preferences=semantics.media_preferences(intent),
                search_queries=semantics.build_queries(
                    text, intent=intent, topic_terms=topic_terms
                ),
                avoid_terms=semantics.build_avoid_terms(
                    text, sibling_texts=siblings, topic_terms=topic_terms
                ),
                claim_ids=list(scene.claim_ids or []),
                evidence_ids=[],
                preferred_motion=(
                    MOTION_NONE
                    if end_ms - start_ms < min_beat_ms
                    else STILL_MOTIONS[beat_index % len(STILL_MOTIONS)]
                ),
                importance=semantics.salience(text),
                confidence=round(
                    min(1.0, 0.55 + 0.05 * len(semantics.extract_terms(text, limit=6))), 4
                ),
            )
        )

    return beats
