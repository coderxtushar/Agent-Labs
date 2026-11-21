"""Pre-existing indent tests (pass on the unmodified repository)."""

from textkit.indent import indent


def test_prefixes_every_line():
    assert indent("a\nb", "  ") == "  a\n  b"


def test_trailing_newline_is_preserved():
    assert indent("a\nb\n", "> ") == "> a\n> b\n"


def test_blank_lines_are_prefixed_by_default():
    assert indent("a\n\nb", "> ") == "> a\n> \n> b"


def test_crlf_is_preserved():
    assert indent("a\r\nb", "# ") == "# a\r\n# b"


def test_empty_text():
    assert indent("", "> ") == ""
