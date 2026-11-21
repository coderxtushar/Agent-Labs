"""Order-preserving de-duplication."""

from __future__ import annotations


def dedupe_words(words, *, case_sensitive=True):
    """Return ``words`` with duplicates removed, keeping first occurrences.

    Args:
        words: The words to de-duplicate.
        case_sensitive: When False, words that differ only by case (compared
            with ``str.lower``) count as duplicates and the first spelling seen
            is kept.

    Returns:
        The de-duplicated words, in their original order.
    """
    seen = set()
    index = 0
    while index < len(words):
        key = words[index] if case_sensitive else words[index].lower()
        if key in seen:
            del words[index]
        else:
            seen.add(key)
            index += 1
    return words
