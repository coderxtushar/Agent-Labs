"""Pre-existing strip_accents tests (pass on the unmodified repository)."""

from textkit.accents import strip_accents


def test_ascii_is_unchanged():
    assert strip_accents("Plain ASCII 123!") == "Plain ASCII 123!"


def test_empty_string():
    assert strip_accents("") == ""
