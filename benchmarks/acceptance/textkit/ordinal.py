"""Hidden acceptance tests for task ``textkit-ordinal``."""

import pytest

import textkit.numbers as numbers


def test_first_ten():
    expected = ["0th", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th"]
    assert [numbers.ordinal(n) for n in range(10)] == expected


def test_teens_always_take_th():
    assert [numbers.ordinal(n) for n in (11, 12, 13)] == ["11th", "12th", "13th"]


def test_twenties():
    assert [numbers.ordinal(n) for n in (21, 22, 23, 24)] == ["21st", "22nd", "23rd", "24th"]


def test_hundreds():
    assert [numbers.ordinal(n) for n in (100, 101, 102, 111, 112, 113, 121)] == [
        "100th",
        "101st",
        "102nd",
        "111th",
        "112th",
        "113th",
        "121st",
    ]


def test_negative_raises_value_error():
    with pytest.raises(ValueError):
        numbers.ordinal(-1)


def test_non_integers_raise_type_error():
    with pytest.raises(TypeError):
        numbers.ordinal(2.0)
    with pytest.raises(TypeError):
        numbers.ordinal("3")


def test_bool_is_rejected():
    with pytest.raises(TypeError):
        numbers.ordinal(True)
