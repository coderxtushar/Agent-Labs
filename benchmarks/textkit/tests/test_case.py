"""Pre-existing snake_case tests (pass on the unmodified repository)."""

from textkit.case import snake_case


def test_camel_case():
    assert snake_case("helloWorld") == "hello_world"


def test_pascal_case():
    assert snake_case("HelloWorld") == "hello_world"


def test_spaces_and_hyphens():
    assert snake_case("hello big-world") == "hello_big_world"


def test_already_snake_case_is_unchanged():
    assert snake_case("already_snake_case") == "already_snake_case"


def test_digits_stay_attached():
    assert snake_case("version2") == "version2"


def test_uppercase_after_digit_starts_a_word():
    assert snake_case("version2Update") == "version2_update"


def test_no_leading_trailing_or_double_underscores():
    assert snake_case("  __hello  world__ ") == "hello_world"
