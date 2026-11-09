"""URL slug generation."""

from __future__ import annotations


def slugify(text: str, sep: str = "-") -> str:
    """Convert ``text`` into a lowercase URL slug.

    Runs of non-alphanumeric characters collapse into a single separator, and
    leading/trailing separators are stripped.

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

    pieces: list[str] = []
    previous_was_separator = False
    for char in text.lower():
        if char.isalnum():
            pieces.append(char)
            previous_was_separator = False
        elif not previous_was_separator:
            pieces.append(sep)
            previous_was_separator = True
    return "".join(pieces).strip(sep)
