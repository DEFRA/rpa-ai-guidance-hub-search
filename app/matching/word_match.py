"""Word's Find matching: any case, anywhere in a word, several words as one
phrase, never across two blocks (lines). 1 or 2 characters match whole words.
"""

import re

_SHORT = 2
_SAME_BLOCK_SPACE = r"[^\S\n]+"


def find(query: str, text: str) -> tuple[tuple[int, int], ...]:
    """Each match of `query` in `text`, as (start, end) offsets."""
    words = query.split()

    if not words:
        return ()

    pattern = _SAME_BLOCK_SPACE.join(re.escape(word) for word in words)

    if len(" ".join(words)) <= _SHORT:
        pattern = rf"\b{pattern}\b"

    return tuple(match.span() for match in re.finditer(pattern, text, re.IGNORECASE))
