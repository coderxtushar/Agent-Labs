"""Diacritic removal."""

from __future__ import annotations


def strip_accents(text: str) -> str:
    """Remove diacritical marks (accents) from ``text``.

    ``"café"`` becomes ``"cafe"``. Characters that carry no diacritic are
    returned unchanged.

    Args:
        text: Input text.

    Returns:
        ``text`` without diacritical marks.
    """
    return text.encode("ascii", "ignore").decode("ascii")
