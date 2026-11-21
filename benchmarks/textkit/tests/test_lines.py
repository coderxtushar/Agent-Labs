"""Pre-existing line_range tests (pass on the unmodified repository)."""

import pytest

from textkit.lines import line_range


def test_first_must_be_positive():
    with pytest.raises(ValueError):
        line_range("a\nb", 0, 1)


def test_last_must_not_precede_first():
    with pytest.raises(ValueError):
        line_range("a\nb", 2, 1)


def test_empty_text_has_no_lines():
    assert line_range("", 1, 3) == []
