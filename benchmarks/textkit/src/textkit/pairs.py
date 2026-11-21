"""``key=value`` pair parsing."""

from __future__ import annotations


class ParseError(ValueError):
    """Raised when :func:`parse_pairs` cannot parse its input."""


def parse_pairs(text: str, sep: str = ";") -> dict[str, str]:
    """Parse ``"key=value; other=value"`` into a dictionary.

    Segments are separated by ``sep``; empty or whitespace-only segments are
    ignored. Keys and values are stripped of surrounding whitespace. When a key
    repeats, the last value wins.

    Args:
        text: Input text.
        sep: Segment separator.

    Returns:
        The parsed pairs.

    Raises:
        ParseError: If a segment is malformed.
    """
    result: dict[str, str] = {}
    for segment in text.split(sep):
        if not segment.strip():
            continue
        parts = segment.split("=")
        key = parts[0].strip()
        value = parts[1].strip()
        result[key] = value
    return result
