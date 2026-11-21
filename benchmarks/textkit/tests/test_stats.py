"""Pre-existing word_count tests (pass on the unmodified repository)."""

from textkit.stats import word_count


def test_two_words():
    assert word_count("hello world") == 2


def test_single_word():
    assert word_count("hello") == 1


def test_sentence():
    assert word_count("the quick brown fox") == 4


def test_punctuation_belongs_to_the_word():
    assert word_count("hello, world!") == 2
