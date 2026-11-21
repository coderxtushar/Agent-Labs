"""Diacritic removal."""

from __future__ import annotations

import unicodedata


def strip_accents(text: str) -> str:
    """Remove diacritical marks (accents) from ``text``.

    ``"café"`` becomes ``"cafe"``. Characters that carry no diacritic are
    returned unchanged.

    Args:
        text: Input text.

    Returns:
        ``text`` without diacritical marks.
    """
    decomposed = unicodedata.normalize("NFD", text)
    kept = "".join(char for char in decomposed if not unicodedata.combining(char))
    # Recompose so characters that NFD split without a diacritic (e.g. Hangul
    # syllables) come back as their original code points.
    return unicodedata.normalize("NFC", kept)
