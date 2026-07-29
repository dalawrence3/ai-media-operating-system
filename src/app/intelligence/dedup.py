"""Deterministic Jaccard-based deduplication for opportunity candidates."""

from __future__ import annotations

import string

_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "how",
        "what",
        "why",
        "when",
        "where",
        "which",
        "who",
        "will",
        "do",
        "does",
        "did",
        "can",
        "could",
        "should",
        "would",
        "your",
        "my",
        "our",
        "their",
        "its",
        "it",
        "this",
        "that",
        "these",
        "those",
    }
)

_PUNCT_TABLE = str.maketrans("", "", string.punctuation)


def normalize_topic(raw: str) -> str:
    """Lowercase, strip punctuation, remove stopwords, collapse whitespace."""
    text = raw.lower().translate(_PUNCT_TABLE)
    tokens = [t for t in text.split() if t and t not in _STOPWORDS]
    return " ".join(tokens)


def jaccard_similarity(a: str, b: str) -> float:
    """Jaccard similarity between two whitespace-tokenized strings."""
    set_a = set(a.split())
    set_b = set(b.split())
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)
