"""Pre-existing chunked tests (pass on the unmodified repository)."""

import pytest

from textkit.chunks import chunked


def test_even_split():
    assert list(chunked([1, 2, 3, 4], 2)) == [[1, 2], [3, 4]]


def test_last_chunk_may_be_short():
    assert list(chunked([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]


def test_empty_input():
    assert list(chunked([], 3)) == []


def test_chunks_are_lists():
    assert list(chunked("abc", 2)) == [["a", "b"], ["c"]]


def test_size_must_be_positive_at_call_time():
    with pytest.raises(ValueError):
        chunked([1, 2, 3], 0)
