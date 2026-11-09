"""Hidden acceptance tests for task ``textkit-truncate-boundary``."""

from textkit import truncate


def test_result_never_exceeds_limit():
    assert len(truncate("abcdefghijklmnop", 10)) <= 10


def test_cuts_at_word_boundary_when_possible():
    assert truncate("hello wonderful world", 12) == "hello..."


def test_suffix_only_when_no_room_for_a_word():
    assert truncate("abcdefghij", 5) == "ab..."


def test_limit_smaller_than_suffix_truncates_the_suffix():
    assert truncate("abcdefghij", 2) == ".."


def test_zero_limit_returns_empty_string():
    assert truncate("abcdefghij", 0) == ""


def test_custom_suffix_is_respected():
    assert truncate("hello wonderful world", 10, suffix="~") == "hello~"


def test_no_trailing_space_before_suffix():
    assert not truncate("hello wonderful world", 13).removesuffix("...").endswith(" ")
