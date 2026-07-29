"""Unit tests for dedup.py — normalize_topic and jaccard_similarity."""

from __future__ import annotations

import pytest

from app.intelligence.dedup import jaccard_similarity, normalize_topic

# ---------------------------------------------------------------------------
# normalize_topic
# ---------------------------------------------------------------------------


def test_normalize_lowercases() -> None:
    assert normalize_topic("Personal Finance") == "personal finance"


def test_normalize_strips_punctuation() -> None:
    assert normalize_topic("hello, world!") == "hello world"


def test_normalize_removes_stopwords() -> None:
    result = normalize_topic("How to save money")
    assert "how" not in result.split()
    assert "to" not in result.split()
    assert "save" in result.split()
    assert "money" in result.split()


def test_normalize_collapses_whitespace() -> None:
    assert normalize_topic("  lots   of   spaces  ") == "lots spaces"


def test_normalize_empty_string() -> None:
    assert normalize_topic("") == ""


def test_normalize_all_stopwords() -> None:
    assert normalize_topic("how to do it") == ""


def test_normalize_preserves_content_words() -> None:
    result = normalize_topic("personal finance tips for beginners")
    assert "personal" in result.split()
    assert "finance" in result.split()
    assert "tips" in result.split()
    assert "beginners" in result.split()
    assert "for" not in result.split()


def test_normalize_apostrophe_stripped() -> None:
    result = normalize_topic("don't waste money")
    assert "don" in result or "dont" in result
    assert "waste" in result
    assert "money" in result


# ---------------------------------------------------------------------------
# jaccard_similarity
# ---------------------------------------------------------------------------


def test_jaccard_identical_strings() -> None:
    assert jaccard_similarity("personal finance", "personal finance") == pytest.approx(1.0)


def test_jaccard_disjoint_sets() -> None:
    assert jaccard_similarity("personal finance", "cooking recipes") == pytest.approx(0.0)


def test_jaccard_partial_overlap() -> None:
    # {personal, finance, tips} vs {finance, tips, guide}
    # intersection=2, union=4 → 0.5
    s = jaccard_similarity("personal finance tips", "finance tips guide")
    assert s == pytest.approx(0.5)


def test_jaccard_both_empty() -> None:
    assert jaccard_similarity("", "") == pytest.approx(1.0)


def test_jaccard_one_empty() -> None:
    assert jaccard_similarity("hello world", "") == pytest.approx(0.0)
    assert jaccard_similarity("", "hello world") == pytest.approx(0.0)


def test_jaccard_single_shared_token() -> None:
    # {finance} vs {finance, tips} → 1/2 = 0.5
    s = jaccard_similarity("finance", "finance tips")
    assert s == pytest.approx(0.5)


def test_jaccard_superset_relationship() -> None:
    # a is a subset of b: {a, b} ⊆ {a, b, c} → |inter|=2 / |union|=3
    s = jaccard_similarity("a b", "a b c")
    assert s == pytest.approx(2 / 3)


def test_jaccard_commutative() -> None:
    a, b = "save money tips", "tips for saving"
    assert jaccard_similarity(a, b) == pytest.approx(jaccard_similarity(b, a))
