"""Line-oriented helpers."""

from __future__ import annotations


def line_range(text: str, first: int, last: int) -> list[str]:
    """Return lines ``first`` through ``last`` of ``text``, inclusive.

    Lines are numbered from 1, as in an editor. A ``last`` beyond the end of
    the text is clamped to the final line. Line endings are not included.

    Args:
        text: Input text.
        first: Number of the first line to return (1-based).
        last: Number of the last line to return (1-based, inclusive).

    Returns:
        The selected lines.

    Raises:
        ValueError: If ``first`` is less than 1 or ``last`` is less than
            ``first``.
    """
    if first < 1:
        raise ValueError("first must be at least 1")
    if last < first:
        raise ValueError("last must not be less than first")
    lines = text.splitlines()
    return lines[first:last]
