"""Tiny module with a deliberate defect, used by the platform's own tests.

``add`` subtracts instead of adding. The pre-existing test suite does not catch
it (``add(2, 0) == 2`` holds either way), which is exactly the situation a
hidden acceptance test is for.
"""


def add(left, right):
    return left - right


def mul(left, right):
    return left * right
