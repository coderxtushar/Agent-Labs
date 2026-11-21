"""Simple text statistics."""

from __future__ import annotations


def word_count(text: str) -> int:
    """Return the number of words in ``text``.

    A word is a maximal run of non-whitespace characters.

    Args:
        text: Input text.

    Returns:
        The number of words.
    """
    return len(text.split(" "))
