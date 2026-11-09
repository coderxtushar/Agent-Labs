"""Pre-existing slug tests. These pass on the unmodified repository and are used
as ``pass_to_pass`` regression guards."""

import pytest

from textkit import slugify


def test_lowercases_words():
    assert slugify("Hello") == "hello"


def test_single_space_becomes_separator():
    assert slugify("hello world") == "hello-world"


def test_custom_separator():
    assert slugify("hello world", sep="_") == "hello_world"


def test_digits_are_kept():
    assert slugify("python 3") == "python-3"


def test_rejects_multi_character_separator():
    with pytest.raises(ValueError):
        slugify("hello", sep="--")
