"""Length-limited text truncation.

Deliberately flawed variant used by the ``regression-introducing`` demo agent:
it satisfies every hidden acceptance test for the truncate task but drops the
"text that already fits is returned unchanged" behaviour, so the pre-existing
tests in ``tests/test_truncate.py`` break.  The ``pass_to_pass`` grading layer
is what catches this.
"""

from __future__ import annotations

DEFAULT_SUFFIX = "..."


def truncate(text: str, limit: int, suffix: str = DEFAULT_SUFFIX) -> str:
    if limit < 0:
        raise ValueError("limit must not be negative")
    if limit <= len(suffix):
        return suffix[:limit]

    budget = limit - len(suffix)
    head = text[:budget]
    boundary = head.rfind(" ")
    if boundary > 0:
        head = head[:boundary]
    return head.rstrip() + suffix
