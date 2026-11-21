"""Fixed-size chunking."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from itertools import islice
from typing import TypeVar

T = TypeVar("T")


def chunked(items: Iterable[T], size: int) -> Iterator[list[T]]:
    """Split ``items`` into consecutive chunks of ``size`` elements.

    Each chunk is a list. The last chunk may be shorter than ``size``. ``items``
    may be any iterable, including an infinite one: chunks are produced lazily
    and the source is consumed only as far as the chunks requested so far.

    Args:
        items: The elements to split.
        size: Chunk length. Must be at least 1.

    Returns:
        An iterator over the chunks, in order.

    Raises:
        ValueError: If ``size`` is less than 1 (raised at call time).
    """
    if size < 1:
        raise ValueError("size must be at least 1")
    source = iter(items)

    def generate() -> Iterator[list[T]]:
        while chunk := list(islice(source, size)):
            yield chunk

    return generate()
