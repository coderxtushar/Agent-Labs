"""Hidden acceptance tests for task ``textkit-dedupe-iterables``."""

from textkit.dedupe import dedupe_words


def test_input_list_is_not_mutated():
    words = ["a", "b", "a", "c", "b"]
    dedupe_words(words)
    assert words == ["a", "b", "a", "c", "b"]


def test_returns_a_new_list():
    words = ["a", "b"]
    result = dedupe_words(words)
    assert result == ["a", "b"]
    assert result is not words


def test_accepts_a_tuple():
    assert dedupe_words(("x", "y", "x")) == ["x", "y"]


def test_accepts_a_generator():
    assert dedupe_words(word for word in ["x", "y", "x", "z"]) == ["x", "y", "z"]


def test_accepts_a_one_shot_iterator_case_insensitively():
    result = dedupe_words(iter(["Go", "GO", "go", "Rust"]), case_sensitive=False)
    assert result == ["Go", "Rust"]
    assert type(result) is list
