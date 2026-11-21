"""``key=value`` pair parsing."""

from __future__ import annotations


class ParseError(ValueError):
    """Raised when :func:`parse_pairs` cannot parse its input."""


def parse_pairs(text: str, sep: str = ";") -> dict[str, str]:
    """Parse ``"key=value; other=value"`` into a dictionary.

    Segments are separated by ``sep``; empty or whitespace-only segments are
    ignored. Keys and values are stripped of surrounding whitespace. When a key
    repeats, the last value wins. Only the first ``=`` separates the key from
    the value.

    Args:
        text: Input text.
        sep: Segment separator.

    Returns:
        The parsed pairs.

    Raises:
        ParseError: If a segment has no ``=`` or an empty key.
    """
    result: dict[str, str] = {}
    for segment in text.split(sep):
        stripped = segment.strip()
        if not stripped:
            continue
        key, equals, value = stripped.partition("=")
        if not equals:
            raise ParseError(f"segment has no '=': {stripped!r}")
        if not key.strip():
            raise ParseError(f"segment has an empty key: {stripped!r}")
        result[key.strip()] = value.strip()
    return result
