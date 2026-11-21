"""Pre-existing split_fields tests (pass on the unmodified repository)."""

import pytest

from textkit.fields import split_fields


def test_plain_fields():
    assert split_fields("a,b,c") == ["a", "b", "c"]


def test_empty_fields_are_kept():
    assert split_fields("a,,c") == ["a", "", "c"]


def test_trailing_separator_gives_trailing_empty_field():
    assert split_fields("a,b,") == ["a", "b", ""]


def test_empty_line_is_one_empty_field():
    assert split_fields("") == [""]


def test_whitespace_is_preserved():
    assert split_fields(" a , b ") == [" a ", " b "]


def test_custom_separator():
    assert split_fields("a;b", sep=";") == ["a", "b"]


def test_quote_inside_an_unquoted_field_is_literal():
    assert split_fields('ab"c,d') == ['ab"c', "d"]
    assert split_fields(' "a",b') == [' "a"', "b"]


def test_rejects_multi_character_separator():
    with pytest.raises(ValueError):
        split_fields("a,b", sep=",,")
