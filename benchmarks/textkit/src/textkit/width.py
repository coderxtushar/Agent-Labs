"""Terminal display width and padding."""

from __future__ import annotations


def display_width(text: str) -> int:
    """Return the number of terminal columns ``text`` occupies.

    Args:
        text: Input text.

    Returns:
        The display width in columns.
    """
    return len(text)


def pad(text: str, width: int, align: str = "left") -> str:
    """Pad ``text`` with spaces so it occupies ``width`` display columns.

    Text that already occupies ``width`` columns or more is returned unchanged.

    Args:
        text: Input text.
        width: Target display width in columns.
        align: ``"left"``, ``"right"`` or ``"center"``.

    Returns:
        The padded text.
    """
    gap = width - len(text)
    if gap <= 0:
        return text
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = (gap + 1) // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap
