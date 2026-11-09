"""Pre-existing truncate tests.

``test_short_text_is_returned_unchanged`` is the regression trap for the
truncate task: the obvious "just always cut and append the suffix" fix breaks
it.
"""

import pytest

from textkit import truncate


def test_short_text_is_returned_unchanged():
    assert truncate("hello", 10) == "hello"


def test_exact_length_is_returned_unchanged():
    assert truncate("hello", 5) == "hello"


def test_empty_text():
    assert truncate("", 5) == ""


def test_negative_limit_rejected():
    with pytest.raises(ValueError):
        truncate("hello", -1)
