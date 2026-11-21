"""Hidden acceptance tests for task ``textkit-snake-case-acronyms``."""

from textkit.case import snake_case


def test_leading_acronym():
    assert snake_case("HTTPServer") == "http_server"


def test_acronym_in_the_middle():
    assert snake_case("getHTTPResponseCode") == "get_http_response_code"


def test_trailing_acronym():
    assert snake_case("userID") == "user_id"


def test_all_caps_word():
    assert snake_case("ID") == "id"


def test_acronym_followed_by_capitalised_word():
    assert snake_case("XMLHttpRequest") == "xml_http_request"


def test_acronym_before_explicit_separator():
    assert snake_case("HTTP_Server") == "http_server"
