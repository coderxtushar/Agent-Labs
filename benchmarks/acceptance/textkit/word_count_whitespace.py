"""Hidden acceptance tests for task ``textkit-word-count-whitespace``."""

from textkit.stats import word_count


def test_empty_text_has_no_words():
    assert word_count("") == 0


def test_whitespace_only_has_no_words():
    assert word_count("   \t\n ") == 0


def test_repeated_spaces_count_once():
    assert word_count("hello    world") == 2


def test_leading_and_trailing_whitespace_is_ignored():
    assert word_count("  padded text  ") == 2


def test_tabs_and_newlines_separate_words():
    assert word_count("one\ttwo\nthree\r\nfour") == 4


def test_unicode_spaces_separate_words():
    assert word_count("no\u00a0break\u2003em\u3000ideographic") == 4
