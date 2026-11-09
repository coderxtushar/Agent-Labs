"""Length-limited text truncation."""

from __future__ import annotations

DEFAULT_SUFFIX = "..."


def truncate(text: str, limit: int, suffix: str = DEFAULT_SUFFIX) -> str:
    """Shorten ``text`` so it fits within ``limit`` characters.

    When the text is longer than ``limit`` it is cut and ``suffix`` is appended
    to show that content was removed.

    Args:
        text: Input text.
        limit: Maximum length of the returned string, including ``suffix``.
        suffix: Marker appended when the text was shortened.

    Returns:
        The original text when it already fits, otherwise a shortened version.

    Raises:
        ValueError: If ``limit`` is negative.
    """
    if limit < 0:
        raise ValueError("limit must not be negative")
    if len(text) <= limit:
        return text
    return text[:limit] + suffix
