"""Greedy word wrapping.

``wrap_text`` is declared but not implemented; implementing it is one of the
bundled benchmark tasks.
"""

from __future__ import annotations


def wrap_text(text: str, width: int) -> list[str]:
    """Wrap ``text`` into lines of at most ``width`` characters.

    Args:
        text: Input text. Runs of whitespace separate words.
        width: Maximum line length. Must be at least 1.

    Returns:
        The wrapped lines, without trailing whitespace. Empty input yields an
        empty list.

    Raises:
        ValueError: If ``width`` is less than 1.
    """
    raise NotImplementedError("wrap_text is not implemented yet")
