"""Word's Find matching: any case, anywhere in a word, several words as one
phrase, never across two blocks (lines). 1 or 2 characters match whole words.

Near matching forgives typos: each query word may match a whole word within a
typo budget, a swap of two letters counting as one edit, and the words must
still be one phrase in one block.
"""

import re

from rapidfuzz.distance import OSA

_SHORT = 2
_ONE_TYPO_FROM = 4
_TWO_TYPOS_FROM = 8
_SAME_BLOCK_SPACE = r"[^\S\n]+"

# Words as the index's unstemmed tokenizer sees them: letters and digits.
_WORD = re.compile(r"[^\W_]+")
_GAP = re.compile(_SAME_BLOCK_SPACE)


def find(query: str, text: str) -> tuple[tuple[int, int], ...]:
    """Each match of `query` in `text`, as (start, end) offsets."""
    words = query.split()

    if not words:
        return ()

    pattern = _SAME_BLOCK_SPACE.join(re.escape(word) for word in words)

    if len(" ".join(words)) <= _SHORT:
        pattern = rf"\b{pattern}\b"

    return tuple(match.span() for match in re.finditer(pattern, text, re.IGNORECASE))


def typo_budget(word: str) -> int:
    """Edits a word of this length may be off by: 0 up to 3, 1 up to 7, else 2."""
    if len(word) >= _TWO_TYPOS_FROM:
        return 2

    return 1 if len(word) >= _ONE_TYPO_FROM else 0


def near_words(query: str) -> list[str]:
    """The query's words for near matching, lower-cased."""
    return [word.casefold() for word in _WORD.findall(query)]


def find_near(query: str, text: str) -> tuple[tuple[int, int], ...]:
    """Each near match of `query` in `text`, as (start, end) offsets.

    Matches whole words only, each within its `typo_budget`.
    """
    words = near_words(query)

    if not words:
        return ()

    tokens = list(_WORD.finditer(text))
    spans: list[tuple[int, int]] = []
    start = 0

    while start + len(words) <= len(tokens):
        run = tokens[start : start + len(words)]

        if _is_phrase(text, run) and all(
            _within_budget(word, token[0])
            for word, token in zip(words, run, strict=True)
        ):
            spans.append((run[0].start(), run[-1].end()))
            start += len(words)
        else:
            start += 1

    return tuple(spans)


def _is_phrase(text: str, run: list[re.Match[str]]) -> bool:
    """Consecutive words with only spaces, not a line break, between them."""
    return all(
        _GAP.fullmatch(text[left.end() : right.start()])
        for left, right in zip(run, run[1:], strict=False)
    )


def _within_budget(word: str, token: str) -> bool:
    budget = typo_budget(word)
    return OSA.distance(word, token.casefold(), score_cutoff=budget) <= budget
