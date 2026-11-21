"""Pre-existing parse_pairs tests (pass on the unmodified repository)."""

from textkit.pairs import ParseError, parse_pairs


def test_basic_pairs():
    assert parse_pairs("a=1;b=2") == {"a": "1", "b": "2"}


def test_whitespace_is_stripped_and_empty_segments_ignored():
    assert parse_pairs(" a = 1 ; ; b= 2 ;") == {"a": "1", "b": "2"}


def test_empty_input():
    assert parse_pairs("") == {}


def test_custom_separator():
    assert parse_pairs("a=1,b=2", sep=",") == {"a": "1", "b": "2"}


def test_empty_value_is_allowed():
    assert parse_pairs("key=") == {"key": ""}


def test_last_value_wins():
    assert parse_pairs("a=1;a=2") == {"a": "2"}


def test_parse_error_is_a_value_error():
    assert issubclass(ParseError, ValueError)
