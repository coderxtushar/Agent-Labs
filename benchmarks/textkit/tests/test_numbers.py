"""Pre-existing number formatting tests (pass on the unmodified repository)."""

from textkit.numbers import format_count


def test_singular():
    assert format_count(1, "file") == "1 file"


def test_plural():
    assert format_count(3, "file") == "3 files"


def test_zero_is_plural():
    assert format_count(0, "file") == "0 files"


def test_irregular_plural():
    assert format_count(2, "child", "children") == "2 children"
