"""Number formatting helpers."""

from __future__ import annotations


def format_count(count: int, noun: str, plural: str | None = None) -> str:
    """Format ``count`` together with a correctly pluralised ``noun``.

    Args:
        count: How many there are.
        noun: Singular form of the noun.
        plural: Plural form; defaults to ``noun + "s"``.

    Returns:
        For example ``"1 file"`` or ``"3 files"``.
    """
    word = noun if count == 1 else (plural if plural is not None else noun + "s")
    return f"{count} {word}"
