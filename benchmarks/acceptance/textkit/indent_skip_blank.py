"""Hidden acceptance tests for task ``textkit-indent-skip-blank``."""

from textkit.indent import indent


def test_skip_blank_leaves_empty_lines_alone():
    assert indent("a\n\nb\n", "> ", skip_blank=True) == "> a\n\n> b\n"


def test_skip_blank_leaves_whitespace_only_lines_alone():
    assert indent("a\n   \n\tb", "> ", skip_blank=True) == "> a\n   \n> \tb"


def test_skip_blank_preserves_crlf():
    assert indent("a\r\n\r\nb", "# ", skip_blank=True) == "# a\r\n\r\n# b"


def test_skip_blank_false_matches_default_behaviour():
    assert indent("a\n\nb", "> ", skip_blank=False) == "> a\n> \n> b"


def test_skip_blank_on_text_without_blank_lines():
    assert indent("x\ny", "  ", skip_blank=True) == "  x\n  y"
