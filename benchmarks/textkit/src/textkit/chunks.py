"""Fixed-size chunking."""

from __future__ import annotations


def chunked(items, size):
    """Split ``items`` into consecutive chunks of ``size`` elements.

    Each chunk is a list. The last chunk may be shorter than ``size``.

    Args:
        items: The elements to split.
        size: Chunk length. Must be at least 1.

    Returns:
        The chunks, in order.

    Raises:
        ValueError: If ``size`` is less than 1.
    """
    if size < 1:
        raise ValueError("size must be at least 1")
    return [list(items[start : start + size]) for start in range(0, len(items), size)]
