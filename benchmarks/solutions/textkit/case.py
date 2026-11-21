"""Identifier case conversion."""

from __future__ import annotations


def snake_case(text: str) -> str:
    """Convert an identifier or phrase to ``snake_case``.

    Spaces, hyphens and underscores separate words. An uppercase letter starts
    a new word when it follows a lowercase letter or a digit, or when it follows
    another uppercase letter and is itself followed by a lowercase letter, so
    acronyms stay together (``HTTPServer`` -> ``http_server``).

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
        if char.isupper() and pieces and pieces[-1] != "_":
            previous = text[index - 1]
            following = text[index + 1] if index + 1 < len(text) else ""
            if (
                previous.islower()
                or previous.isdigit()
                or (previous.isupper() and following.islower())
            ):
                pieces.append("_")
        pieces.append(char.lower())
    return "".join(pieces).strip("_")
