"""Delimited-field splitting."""

from __future__ import annotations


def split_fields(line: str, sep: str = ",") -> list[str]:
    """Split one delimited line into its fields.

    Fields are returned exactly as written: surrounding whitespace is kept, an
    empty input yields a single empty field, and a trailing separator yields a
    trailing empty field.

    Args:
        line: A single line of delimited text.
        sep: Field separator. Must be exactly one character.

    Returns:
        The fields, in order.

    Raises:
        ValueError: If ``sep`` is not exactly one character.
    """
    if len(sep) != 1:
        raise ValueError("sep must be a single character")
    return line.split(sep)
