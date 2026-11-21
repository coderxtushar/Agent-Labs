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


def ordinal(n: int) -> str:
    """Return ``n`` with its English ordinal suffix, e.g. ``"21st"``.

    Raises:
        TypeError: If ``n`` is not an ``int`` (``bool`` is rejected too).
        ValueError: If ``n`` is negative.
    """
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError("ordinal() requires an int")
    if n < 0:
        raise ValueError("ordinal() requires a non-negative int")
    if n % 100 in (11, 12, 13):
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
