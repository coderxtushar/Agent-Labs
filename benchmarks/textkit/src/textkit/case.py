"""Identifier case conversion."""

from __future__ import annotations


def snake_case(text: str) -> str:
    """Convert an identifier or phrase to ``snake_case``.

    Spaces, hyphens and underscores separate words, and so does a change from a
    lowercase letter to an uppercase one (``helloWorld`` -> ``hello_world``).
    The result is lowercase, never contains two underscores in a row, and never
    starts or ends with one.

    Args:
        text: Input identifier, e.g. ``camelCase``, ``PascalCase`` or
            ``kebab-case``.

    Returns:
        The ``snake_case`` form of ``text``.
    """
    pieces: list[str] = []
    for index, char in enumerate(text):
        if char in " -_":
            if pieces and pieces[-1] != "_":
                pieces.append("_")
            continue
        if char.isupper() and index > 0 and pieces and pieces[-1] != "_":
            pieces.append("_")
        pieces.append(char.lower())
    return "".join(pieces).strip("_")
