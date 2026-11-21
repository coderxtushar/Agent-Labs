"""Hidden acceptance tests for task ``textkit-split-fields-quoted``."""

import pytest

from textkit.fields import split_fields


def test_quoted_field_may_contain_the_separator():
    assert split_fields('"a,b",c') == ["a,b", "c"]


def test_doubled_quote_is_a_literal_quote():
    assert split_fields('"say ""hi""",x') == ['say "hi"', "x"]


def test_empty_quoted_field():
    assert split_fields('"",x') == ["", "x"]


def test_quoted_last_field_and_trailing_separator():
    assert split_fields('a,"b"') == ["a", "b"]
    assert split_fields('"a",') == ["a", ""]


def test_quoted_field_with_custom_separator():
    assert split_fields('"a;b";c', sep=";") == ["a;b", "c"]


def test_unterminated_quote_raises_value_error():
    with pytest.raises(ValueError):
        split_fields('"unterminated,x')


def test_text_after_closing_quote_raises_value_error():
    with pytest.raises(ValueError):
        split_fields('"a"b,c')
