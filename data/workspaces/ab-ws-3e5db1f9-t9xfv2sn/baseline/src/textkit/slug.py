"""URL slug generation."""

from __future__ import annotations


def slugify(text: str, sep: str = "-") -> str:
    """Convert ``text`` into a lowercase URL slug.

    Every character that is not a letter or digit becomes the separator.

    Args:
        text: Input text.
        sep: Separator character to use between words.

    Returns:
        The slugified string.

    Raises:
        ValueError: If ``sep`` is not exactly one character.
    """
    if len(sep) != 1:
        raise ValueError("sep must be a single character")

    pieces = []
    for char in text.lower():
        pieces.append(char if char.isalnum() else sep)
    return "".join(pieces)
