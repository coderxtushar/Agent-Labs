"""Line indentation."""

from __future__ import annotations


def indent(text: str, prefix: str) -> str:
    """Add ``prefix`` to the start of every line of ``text``.

    Line endings (``\\n``, ``\\r\\n``) are preserved exactly, and no newline is
    added or removed.

    Args:
        text: Input text.
        prefix: String to put in front of each line.

    Returns:
        The indented text.
    """
    return "".join(prefix + line for line in text.splitlines(keepends=True))
