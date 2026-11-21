"""Hidden acceptance tests for task ``textkit-parse-pairs-errors``."""

import pytest

from textkit.pairs import ParseError, parse_pairs


def test_segment_without_equals_raises_parse_error():
    with pytest.raises(ParseError):
        parse_pairs("a=1;oops;b=2")


def test_missing_equals_is_still_a_value_error():
    with pytest.raises(ValueError):
        parse_pairs("novalue")


def test_empty_key_raises_parse_error():
    with pytest.raises(ParseError):
        parse_pairs("=1")


def test_blank_key_raises_parse_error():
    with pytest.raises(ParseError):
        parse_pairs("a=1;   = 2")


def test_error_message_names_the_offending_segment():
    with pytest.raises(ParseError, match="broken-segment"):
        parse_pairs("a=1; broken-segment ;b=2")


def test_value_may_contain_equals_signs():
    assert parse_pairs("url=https://x.test/?q=1&r=2") == {"url": "https://x.test/?q=1&r=2"}
