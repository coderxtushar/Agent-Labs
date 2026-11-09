"""Hidden acceptance tests for task ``textkit-slugify-collapse``.

Stored outside the benchmark repository so the agent never sees it: this
directory is not copied into the sandbox during the agent phase. The grading
engine injects the file into the workspace immediately before running tests.
"""

from textkit import slugify


def test_collapses_repeated_separators():
    assert slugify("Hello,  World!") == "hello-world"


def test_strips_leading_and_trailing_separators():
    assert slugify("  Hello  ") == "hello"


def test_punctuation_runs_collapse():
    assert slugify("a---b") == "a-b"


def test_only_punctuation_yields_empty_slug():
    assert slugify("!!!") == ""


def test_custom_separator_also_collapses():
    assert slugify("Hello,  World!", sep="_") == "hello_world"
