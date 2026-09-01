"""Semantic visual engine — domain-agnostic narration analysis.

Everything here works from the narration text alone.  There is deliberately
no topic map, no niche vocabulary, and no channel-specific vocabulary: the
previous engine's hardcoded technology-noun table is precisely why a CRISPR
script retrieved renewable-energy footage.

Public surface
--------------
extract_terms(text)            -> ranked content terms
extract_entities(text)         -> proper-noun / acronym / measurement spans
classify_intent(text, ...)     -> a beat intent constant
build_queries(...)             -> short, retrieval-shaped search strings
build_avoid_terms(...)         -> concepts a candidate must not drift toward
salience(text)                 -> 0..1 importance estimate for a beat
"""

from __future__ import annotations

import re
from collections import Counter

from app.visuals.constants import (
    INTENT_ACTION,
    INTENT_COMPARISON,
    INTENT_CONCEPT,
    INTENT_CTA,
    INTENT_DIAGRAM,
    INTENT_EMPHASIS,
    INTENT_ENTITY,
    INTENT_LOCATION,
    INTENT_MEDIA_PREFERENCES,
    INTENT_NUMBER,
    INTENT_PROCESS,
    INTENT_QUOTE,
    INTENT_TIMELINE,
    INTENT_TRANSITION,
)

# ---------------------------------------------------------------------------
# Vocabulary — function words only.  No subject-matter vocabulary lives here.
# ---------------------------------------------------------------------------

_STOPWORDS: frozenset[str] = frozenset(
    """
a about above after again against all also am an and another any are aren as at
be because been before being below between both but by
can cannot could couldn
did didn do does doesn doing don down during
each either else even ever every
few for from further
had hadn has hasn have haven having he her here hers herself him himself his how
however
i if in into is isn it its itself
just
let like ll
me more most much must my myself
need no nor not now
of off on once only or other others ought our ours ourselves out over own
per
quite
re really
s same shan she should shouldn so some something such
t than that the their theirs them themselves then there these they thing things
this those though through to too
under until up upon us
ve very
was wasn we well were weren what when where whether which while who whom why will
with within won would wouldn
yet you your yours yourself yourselves
actually basically essentially literally simply
get gets got going goes went come comes came make makes made take takes took
say says said see sees saw know knows knew think thinks thought want wants
happen happens happened turn turns turned put puts look looks looked
one two three four five six seven eight nine ten
""".split()
)

# Generic discourse cues.  These describe *rhetorical function*, which is
# language-level, not topic-level.
_COMPARISON_CUES = (
    " versus ",
    " vs ",
    " vs. ",
    " compared ",
    " whereas ",
    " unlike ",
    " instead of ",
    " rather than ",
    " on the other hand",
    " the difference ",
    " differs ",
    " but ",
    " while ",
    " either ",
    " or ",
)
_PROCESS_CUES = (
    "first,",
    "second,",
    "third,",
    "next,",
    "then ",
    "after that",
    "finally",
    "step ",
    "begins",
    "starts",
    "leads to",
    "results in",
    "causes",
    "so that",
    "here's what happens",
    "what happens",
    "how it works",
    "process",
)
_TIMELINE_CUES = (
    "in 19",
    "in 20",
    "years ago",
    "decade",
    "century",
    "since ",
    "until ",
    "by 20",
    "today",
    "eventually",
    "historically",
)
_CTA_CUES = (
    "follow",
    "subscribe",
    "like and",
    "comment",
    "share this",
    "check out",
    "link in",
    "watch the",
    "hit the",
    "join ",
    "more on",
)
_EMPHASIS_CUES = (
    "most people",
    "nobody",
    "everyone",
    "the truth",
    "the catch",
    "here's the",
    "surprisingly",
    "the problem",
    "the real",
    "actually",
    "never",
    "always",
    "critical",
    "matters",
    "key",
)
_DIAGRAM_CUES = (
    "structure",
    "consists of",
    "made up of",
    "composed of",
    "anatomy",
    "layers",
    "components",
    "parts of",
    "inside",
    "architecture",
    "shaped like",
    "looks like",
)
_ABSTRACT_CUES = (
    "means",
    "meaning",
    "concept",
    "idea",
    "theory",
    "principle",
    "in other words",
    "think of it",
    "essentially",
    "depends on",
    "because",
    "why",
)
_TRANSITION_CUES = ("but here", "now,", "so,", "and that's", "which brings", "meanwhile")
_LOCATION_CUES = (" in the ", " across ", " throughout ", " around the world", " globally")

_MOTION_VERB_SUFFIXES = ("ing", "es", "ed")

_NUMBER_RE = re.compile(r"(?<![\w.])(?:[<>~±]\s*)?\d+(?:[.,]\d+)?\s*(?:%|percent|x|×)?")
_ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}\b")
_PROPER_RE = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b")
_QUOTE_RE = re.compile(r"[\"“”].{6,}?[\"“”]")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'’-]*")

# Sentence-ish boundary used for beat segmentation and clause detection.
_CLAUSE_BOUNDARY_RE = re.compile(r"[.!?;:—]|,\s+(?:and|but|so|which|while|then)\b")


_POSSESSIVE_RE = re.compile(r"['’]s$")


def _normalise(word: str) -> str:
    """Lowercase, strip possessives and edge punctuation.

    Without this, "Cas9's" and "Cas9" rank as two different subjects and both
    can end up in the same query.
    """
    lowered = _POSSESSIVE_RE.sub("", (word or "").lower())
    return lowered.strip("'’-")


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(text or "")


def _is_content_word(word: str) -> bool:
    lowered = _normalise(word)
    if len(lowered) < 3:
        return False
    return lowered not in _STOPWORDS


# ---------------------------------------------------------------------------
# Term extraction
# ---------------------------------------------------------------------------


def extract_terms(text: str, *, limit: int = 8) -> list[str]:
    """Return content terms ranked by estimated visual salience.

    Ranking is deterministic and uses only surface features that generalise
    across subject matter: capitalisation, token length, morphology, digit
    content, and first-mention position.
    """
    raw_tokens = _tokens(text)
    if not raw_tokens:
        return []

    counts = Counter(_normalise(t) for t in raw_tokens)
    seen: dict[str, float] = {}
    first_position: dict[str, int] = {}

    for position, token in enumerate(raw_tokens):
        if not _is_content_word(token):
            continue
        key = _normalise(token)
        if not key:
            continue
        first_position.setdefault(key, position)

        score = 1.0
        # Longer words carry more specific meaning than short ones.
        score += min(len(key), 14) / 14.0
        # Capitalised mid-sentence, or an acronym → likely a named entity.
        if token[:1].isupper() and position > 0:
            score += 0.8
        if token.isupper() and len(token) > 1:
            score += 1.0
        if any(ch.isdigit() for ch in token):
            score += 0.4
        if "-" in token:
            score += 0.3
        # Repeated mention within the beat signals the beat's subject.
        score += min(counts[key] - 1, 3) * 0.35
        # Earlier mentions bias slightly higher.
        score += max(0.0, 0.4 - position * 0.02)

        seen[key] = max(seen.get(key, 0.0), score)

    ranked = sorted(seen.items(), key=lambda kv: (-kv[1], first_position[kv[0]]))
    return [term for term, _ in ranked[:limit]]


def extract_entities(text: str, *, limit: int = 6) -> list[str]:
    """Return acronyms, proper-noun spans, and measurements, in text order."""
    found: list[str] = []

    def _add(value: str) -> None:
        cleaned = value.strip()
        if cleaned and cleaned not in found:
            found.append(cleaned)

    for match in _ACRONYM_RE.finditer(text or ""):
        _add(match.group(0))
    body = text or ""
    for match in _PROPER_RE.finditer(body):
        span = match.group(0)
        if " " not in span:
            # A sentence-initial capital is not evidence of a proper noun.
            preceding = body[: match.start()].rstrip()
            if not preceding or preceding[-1] in ".!?:;":
                continue
            # "Non" in "Non-Homologous" is half of a hyphenated compound the
            # token pass already captured whole.
            if body[match.end() : match.end() + 1] == "-":
                continue
        _add(span)
    for match in _NUMBER_RE.finditer(text or ""):
        _add(match.group(0))

    return found[:limit]


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------


def _contains_any(haystack: str, needles: tuple[str, ...]) -> bool:
    return any(n in haystack for n in needles)


def classify_intent(
    text: str,
    *,
    section_type: str | None = None,
    is_first_beat: bool = False,
    is_last_beat: bool = False,
) -> str:
    """Classify what the narration is doing at this moment.

    Section type is a hint, never an override — a CTA section that is actually
    explaining a process should still get process visuals.
    """
    body = f" {(text or '').lower().strip()} "

    if _QUOTE_RE.search(text or ""):
        return INTENT_QUOTE

    # An explicit numeric claim is the strongest signal available: numbers are
    # exactly what stock footage cannot show and a graphic can.
    numbers = _NUMBER_RE.findall(text or "")
    if (
        numbers
        and _contains_any(body, ("%", "percent"))
        or (numbers and any(ch in (text or "") for ch in "<>~"))
    ):
        return INTENT_NUMBER

    if _contains_any(body, _CTA_CUES) and (
        is_last_beat or (section_type or "").lower() in {"cta", "outro", "conclusion"}
    ):
        return INTENT_CTA

    if _contains_any(body, _COMPARISON_CUES) and len(extract_terms(text, limit=4)) >= 2:
        return INTENT_COMPARISON

    if _contains_any(body, _TIMELINE_CUES):
        return INTENT_TIMELINE

    if _contains_any(body, _PROCESS_CUES):
        return INTENT_PROCESS

    if _contains_any(body, _DIAGRAM_CUES):
        return INTENT_DIAGRAM

    if numbers:
        return INTENT_NUMBER

    if _contains_any(body, _EMPHASIS_CUES):
        return INTENT_EMPHASIS

    if _contains_any(body, _ABSTRACT_CUES):
        return INTENT_CONCEPT

    if _contains_any(body, _LOCATION_CUES) and extract_entities(text):
        return INTENT_LOCATION

    if _contains_any(body, _TRANSITION_CUES) and not is_first_beat:
        return INTENT_TRANSITION

    # An -ing/-s verb alongside a concrete subject reads as depictable action.
    for token in _tokens(text):
        lowered = _normalise(token)
        if (
            _is_content_word(lowered)
            and lowered.endswith(_MOTION_VERB_SUFFIXES)
            and len(lowered) > 5
        ):
            return INTENT_ACTION

    return INTENT_ENTITY


def media_preferences(intent: str) -> list[str]:
    return list(INTENT_MEDIA_PREFERENCES.get(intent, INTENT_MEDIA_PREFERENCES[INTENT_ENTITY]))


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------


def build_queries(
    text: str,
    *,
    intent: str,
    topic_terms: list[str] | None = None,
    limit: int = 3,
    max_words: int = 4,
) -> list[str]:
    """Build short retrieval-shaped queries for one beat.

    Stock search engines match 2–4 word noun phrases; they match full narration
    sentences against nothing useful at all.  ``topic_terms`` are video-level
    terms used to keep a thin beat anchored to the video's subject.
    """
    beat_terms = extract_terms(text, limit=6)
    entities = [e for e in extract_entities(text) if not _NUMBER_RE.fullmatch(e)]
    anchors = [t for t in (topic_terms or []) if t not in beat_terms]
    anchor = anchors[0] if anchors else None

    queries: list[str] = []

    def _push(words: list[str]) -> None:
        parts: list[str] = []
        seen_parts: set[str] = set()
        for word in words:
            if not word:
                continue
            # Entities and terms overlap ("DNA" and "dna"); keep one.
            for piece in word.split():
                marker = _normalise(piece)
                if not marker or marker in seen_parts:
                    continue
                seen_parts.add(marker)
                parts.append(piece)
        parts = parts[:max_words]
        if not parts:
            return
        phrase = " ".join(parts).strip()
        if len(phrase) < 3:
            return
        signature = frozenset(_normalise(p) for p in phrase.split())
        if signature not in {frozenset(_normalise(p) for p in q.split()) for q in queries}:
            queries.append(phrase)

    # Beat terms alone retrieve the everyday sense of ordinary English words:
    # "break" finds surf, "joining" finds clasped hands, "template" finds a
    # tailor.  Anchoring to the video's own subject vocabulary disambiguates
    # them without any domain knowledge being hardcoded.
    #
    # 1. Named entity + subject anchor — the most specific query available.
    if entities:
        _push([entities[0], anchor, beat_terms[0] if beat_terms else ""])

    # 2. Subject anchor + the beat's own top terms.
    if anchor and beat_terms:
        _push([anchor, *beat_terms[:2]])

    # 3. Unanchored beat terms, so a beat that is already specific can still
    #    find its own subject rather than the video's general one.
    if beat_terms:
        _push(beat_terms[:3])

    if not queries and topic_terms:
        _push(list(topic_terms[:3]))

    return queries[:limit]


def build_avoid_terms(
    text: str,
    *,
    sibling_texts: list[str] | None = None,
    topic_terms: list[str] | None = None,
    limit: int = 6,
) -> list[str]:
    """Terms a candidate for this beat should *not* be about.

    Derived from the other beats' distinctive terms: if a term belongs to a
    different beat and not this one, footage dominated by it is off-beat.

    The video's own subject vocabulary is excluded.  A term that recurs across
    the whole narration is what the video *is about*; treating it as a
    disqualifier rejects exactly the footage the beat most wants.
    """
    own = set(extract_terms(text, limit=8))
    protected = own | {t.lower() for t in (topic_terms or [])}
    others: Counter[str] = Counter()
    for sibling in sibling_texts or []:
        for term in extract_terms(sibling, limit=5):
            if term not in protected:
                others[term] += 1
    return [term for term, _ in others.most_common(limit)]


def salience(text: str) -> float:
    """0..1 estimate of how much this beat matters visually."""
    terms = extract_terms(text, limit=8)
    entities = extract_entities(text)
    score = 0.3
    score += min(len(terms), 6) * 0.06
    score += min(len(entities), 4) * 0.06
    if _NUMBER_RE.search(text or ""):
        score += 0.1
    if _contains_any(f" {(text or '').lower()} ", _EMPHASIS_CUES):
        score += 0.08
    return round(min(1.0, score), 4)


def clause_boundaries(text: str) -> list[int]:
    """Character offsets just past each clause/sentence boundary."""
    return [m.end() for m in _CLAUSE_BOUNDARY_RE.finditer(text or "")]


def ends_clause(text: str) -> bool:
    stripped = (text or "").rstrip()
    return bool(stripped) and stripped[-1] in ".!?;:,—"
