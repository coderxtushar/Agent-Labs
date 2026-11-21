"""Hidden acceptance tests for task ``textkit-line-range-inclusive``."""

from textkit.lines import line_range

TEXT = "alpha\nbeta\ngamma\ndelta"


def test_middle_range_is_inclusive():
    assert line_range(TEXT, 2, 3) == ["beta", "gamma"]


def test_single_first_line():
    assert line_range(TEXT, 1, 1) == ["alpha"]


def test_whole_text():
    assert line_range(TEXT, 1, 4) == ["alpha", "beta", "gamma", "delta"]


def test_last_is_clamped_to_the_end():
    assert line_range(TEXT, 3, 99) == ["gamma", "delta"]


def test_single_line_text():
    assert line_range("only", 1, 1) == ["only"]


def test_crlf_line_endings_are_not_included():
    assert line_range("a\r\nb\r\nc\r\n", 2, 3) == ["b", "c"]
