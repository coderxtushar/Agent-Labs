"""Pre-existing dedupe_words tests (pass on the unmodified repository)."""

from textkit.dedupe import dedupe_words


def test_removes_duplicates():
    assert dedupe_words(["a", "b", "a"]) == ["a", "b"]


def test_keeps_first_occurrence_order():
    assert dedupe_words(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]


def test_case_insensitive_keeps_first_spelling():
    assert dedupe_words(["Apple", "apple", "APPLE", "pear"], case_sensitive=False) == [
        "Apple",
        "pear",
    ]


def test_case_sensitive_by_default():
    assert dedupe_words(["Apple", "apple"]) == ["Apple", "apple"]


def test_empty_input():
    assert dedupe_words([]) == []
