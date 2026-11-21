"""Pre-existing width/padding tests (pass on the unmodified repository)."""

from textkit.width import display_width, pad


def test_ascii_width():
    assert display_width("abc") == 3


def test_empty_width():
    assert display_width("") == 0


def test_left_pad_is_default():
    assert pad("ab", 4) == "ab  "


def test_right_align():
    assert pad("ab", 4, "right") == "  ab"


def test_center_even_gap():
    assert pad("ab", 6, "center") == "  ab  "


def test_text_wider_than_width_is_unchanged():
    assert pad("abcdef", 3) == "abcdef"
