"""Line indentation."""

from __future__ import annotations


def indent(text: str, prefix: str, *, skip_blank: bool = False) -> str:
    """Add ``prefix`` to the start of every line of ``text``.

    Line endings (``\\n``, ``\\r\\n``) are preserved exactly, and no newline is
    added or removed.

    Args:
        text: Input text.
        prefix: String to put in front of each line.
        skip_blank: When True, lines that are empty or contain only whitespace
            are left unchanged instead of being prefixed.

    Returns:
        The indented text.
    """
    return "".join(
        line if skip_blank and not line.strip() else prefix + line
        for line in text.splitlines(keepends=True)
    )
