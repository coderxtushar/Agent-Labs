"""Hidden acceptance tests for task ``textkit-wordwrap``."""

import pytest

from textkit import wrap_text


def test_empty_text_returns_empty_list():
    assert wrap_text("", 10) == []


def test_whitespace_only_returns_empty_list():
    assert wrap_text("   \n\t ", 10) == []


def test_wraps_on_word_boundaries():
    assert wrap_text("the quick brown fox", 10) == ["the quick", "brown fox"]


def test_single_short_word():
    assert wrap_text("hello", 10) == ["hello"]


def test_word_longer_than_width_is_hard_split():
    assert wrap_text("abcdefghij", 4) == ["abcd", "efgh", "ij"]


def test_collapses_whitespace_runs():
    assert wrap_text("a   b", 10) == ["a b"]


def test_width_must_be_positive():
    with pytest.raises(ValueError):
        wrap_text("hello", 0)


def test_no_line_exceeds_width():
    text = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
    lines = wrap_text(text, 12)
    assert lines
    assert all(len(line) <= 12 for line in lines)


def test_round_trips_words_in_order():
    text = "alpha beta gamma delta epsilon"
    assert " ".join(wrap_text(text, 11)).split() == text.split()
