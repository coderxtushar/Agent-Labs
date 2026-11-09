"""Length-limited text truncation."""

from __future__ import annotations

DEFAULT_SUFFIX = "..."


def truncate(text: str, limit: int, suffix: str = DEFAULT_SUFFIX) -> str:
    """Shorten ``text`` so it fits within ``limit`` characters.

    The returned string never exceeds ``limit``. When the text must be cut, the
    cut is made at a word boundary if one exists inside the available budget.

    Args:
        text: Input text.
        limit: Maximum length of the returned string, including ``suffix``.
        suffix: Marker appended when the text was shortened.

    Returns:
        The original text when it already fits, otherwise a shortened version
        no longer than ``limit``.

    Raises:
        ValueError: If ``limit`` is negative.
    """
    if limit < 0:
        raise ValueError("limit must not be negative")
    if len(text) <= limit:
        return text
    if limit <= len(suffix):
        return suffix[:limit]

    budget = limit - len(suffix)
    head = text[:budget]
    boundary = head.rfind(" ")
    if boundary > 0:
        head = head[:boundary]
    return head.rstrip() + suffix
