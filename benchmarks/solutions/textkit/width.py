"""Terminal display width and padding."""

from __future__ import annotations

import unicodedata

_ALIGNMENTS = ("left", "right", "center")


def _char_width(char: str) -> int:
    if unicodedata.combining(char) or unicodedata.category(char) == "Cf":
        return 0
    if unicodedata.east_asian_width(char) in ("W", "F"):
        return 2
    return 1


def display_width(text: str) -> int:
    """Return the number of terminal columns ``text`` occupies.

    Combining marks and zero-width format characters (category ``Cf``) take no
    columns, East Asian wide and fullwidth characters take two, and everything
    else takes one.

    Args:
        text: Input text.

    Returns:
        The display width in columns.
    """
    return sum(_char_width(char) for char in text)


def pad(text: str, width: int, align: str = "left") -> str:
    """Pad ``text`` with spaces so it occupies ``width`` display columns.

    Text that already occupies ``width`` columns or more is returned unchanged.
    When centring needs an odd number of spaces, the extra one goes on the right.

    Args:
        text: Input text.
        width: Target display width in columns.
        align: ``"left"``, ``"right"`` or ``"center"``.

    Returns:
        The padded text.

    Raises:
        ValueError: If ``align`` is not one of the supported values.
    """
    if align not in _ALIGNMENTS:
        raise ValueError(f"align must be one of {_ALIGNMENTS}")
    gap = width - display_width(text)
    if gap <= 0:
        return text
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap
