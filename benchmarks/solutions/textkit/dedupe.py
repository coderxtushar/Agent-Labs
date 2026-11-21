"""Order-preserving de-duplication."""

from __future__ import annotations

from collections.abc import Iterable


def dedupe_words(words: Iterable[str], *, case_sensitive: bool = True) -> list[str]:
    """Return ``words`` with duplicates removed, keeping first occurrences.

    The input is never modified and may be any iterable, including a one-shot
    iterator or generator.

    Args:
        words: The words to de-duplicate.
        case_sensitive: When False, words that differ only by case (compared
            with ``str.lower``) count as duplicates and the first spelling seen
            is kept.

    Returns:
        A new list of the de-duplicated words, in their original order.
    """
    seen: set[str] = set()
    result: list[str] = []
    for word in words:
        key = word if case_sensitive else word.lower()
        if key not in seen:
            seen.add(key)
            result.append(word)
    return result
