"""Greedy word wrapping."""

from __future__ import annotations


def wrap_text(text: str, width: int) -> list[str]:
    """Wrap ``text`` into lines of at most ``width`` characters.

    Args:
        text: Input text. Runs of whitespace separate words.
        width: Maximum line length. Must be at least 1.

    Returns:
        The wrapped lines, without trailing whitespace. Empty input yields an
        empty list.

    Raises:
        ValueError: If ``width`` is less than 1.
    """
    if width < 1:
        raise ValueError("width must be at least 1")

    lines: list[str] = []
    current = ""
    for raw_word in text.split():
        word = raw_word
        # A word that cannot fit on any line is hard-split across lines.
        while len(word) > width:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:width])
            word = word[width:]
        if not word:
            continue
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word

    if current:
        lines.append(current)
    return lines
