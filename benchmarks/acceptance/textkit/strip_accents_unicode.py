"""Hidden acceptance tests for task ``textkit-strip-accents-unicode``."""

from textkit.accents import strip_accents


def test_precomposed_accents_are_removed():
    assert strip_accents("café") == "cafe"


def test_several_accents_in_a_phrase():
    assert strip_accents("naïve Ångström façade") == "naive Angstrom facade"


def test_decomposed_accents_are_removed():
    assert strip_accents("cafe\u0301 \u03b1\u0301") == "cafe \u03b1"


def test_greek_tonos_is_removed():
    assert strip_accents("Ελληνικά") == "Ελληνικα"


def test_japanese_is_unchanged():
    assert strip_accents("日本語のテキスト") == "日本語のテキスト"


def test_korean_is_returned_with_identical_code_points():
    text = "한국어"
    result = strip_accents(text)
    assert result == text
    assert len(result) == 3


def test_letters_without_diacritics_are_kept():
    assert strip_accents("Straße") == "Straße"
