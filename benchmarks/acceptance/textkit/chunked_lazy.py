"""Hidden acceptance tests for task ``textkit-chunked-lazy``."""

import itertools

from textkit.chunks import chunked


def test_accepts_a_generator():
    assert list(chunked((n for n in range(5)), 2)) == [[0, 1], [2, 3], [4]]


def test_works_on_an_infinite_iterator():
    assert next(iter(chunked(itertools.count(), 3))) == [0, 1, 2]


def test_returns_an_iterator_not_a_list():
    result = chunked([1, 2, 3], 2)
    assert not isinstance(result, list)
    assert iter(result) is result


def test_consumes_the_source_only_as_needed():
    source = iter(range(10))
    chunks = chunked(source, 3)
    assert next(chunks) == [0, 1, 2]
    assert next(source) == 3


def test_chunks_from_an_iterator_are_lists():
    assert all(type(chunk) is list for chunk in chunked(iter("abcde"), 2))
