"""Tests for Phase 5 constants."""

from __future__ import annotations

import math

from app.content.constants import (
    DEFAULT_AUDIENCE,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    DEFAULT_TONE,
    SCRIPT_DURATION_TOLERANCE_S,
    SCRIPT_WORDS_PER_MINUTE,
    SHORT_FORM_DEFAULT_DURATION_S,
    SHORT_FORM_MAX_DURATION_S,
    SHORT_FORM_MAX_WORDS,
    SHORT_FORM_MIN_DURATION_S,
    SHORT_FORM_MIN_WORDS,
)


def test_wpm_constant():
    assert SCRIPT_WORDS_PER_MINUTE == 150


def test_duration_range():
    assert SHORT_FORM_MIN_DURATION_S < SHORT_FORM_DEFAULT_DURATION_S < SHORT_FORM_MAX_DURATION_S


def test_min_words_yields_min_duration():
    computed = math.ceil(SHORT_FORM_MIN_WORDS / SCRIPT_WORDS_PER_MINUTE * 60)
    assert computed >= SHORT_FORM_MIN_DURATION_S


def test_max_words_yields_max_duration():
    computed = math.ceil(SHORT_FORM_MAX_WORDS / SCRIPT_WORDS_PER_MINUTE * 60)
    assert computed <= SHORT_FORM_MAX_DURATION_S


def test_tolerance_positive():
    assert SCRIPT_DURATION_TOLERANCE_S > 0


def test_defaults_sensible():
    assert DEFAULT_TONE == "conversational"
    assert DEFAULT_AUDIENCE == ""
    assert 0.0 < DEFAULT_TEMPERATURE < 1.0
    assert DEFAULT_MAX_TOKENS > 0
