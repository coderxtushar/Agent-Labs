"""Hidden acceptance tests for task ``textkit-display-width-unicode``."""

import pytest

from textkit.width import display_width, pad


def test_wide_characters_take_two_columns():
    assert display_width("日本") == 4


def test_fullwidth_and_halfwidth_forms():
    assert display_width("\uff21\uff71") == 3


def test_emoji_is_wide():
    assert display_width("ok😀") == 4


def test_combining_marks_take_no_columns():
    assert display_width("cafe\u0301") == 4


def test_zero_width_format_characters_take_no_columns():
    assert display_width("a\u200bb\u200dc\ufeff") == 3


def test_pad_wide_text_left_and_right():
    assert pad("日本", 6) == "日本  "
    assert pad("日本", 6, "right") == "  日本"


def test_pad_counts_combining_marks_as_zero():
    assert pad("cafe\u0301", 6) == "cafe\u0301  "


def test_wide_text_already_at_width_is_unchanged():
    assert pad("日本語", 4) == "日本語"


def test_center_puts_the_extra_space_on_the_right():
    assert pad("ab", 5, "center") == " ab  "


def test_unknown_alignment_raises_value_error():
    with pytest.raises(ValueError):
        pad("ab", 4, "middle")
