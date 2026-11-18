"""Whether a word search names a document's title: the query and the title
cover each other.

Words are letters and digits in any case, so punctuation and hyphens fall
away, and stop words are left out of both. A query word matches a title word
whole or within `word_match.typo_budget`, so plurals and typos are forgiven
("payments" matches "payment") but codes never are ("AB1" never matches
"AB12").

A title is covered when every query word matches one of its words, and either
the query holds enough of the title's words (`MIN_TITLE_COVERAGE`, from at
least `MIN_QUERY_WORDS` words unless it holds them all) or the query is the
start of the title and at least `MIN_PREFIX_WORDS` long. So "Final Payments"
covers "Final Payment Case Check Guide" and "CS MA" covers every
"CS MA Claim - ..." title, but a single keyword such as "weight" never covers
a longer title.
"""

import re

from rapidfuzz.distance import OSA

from app.matching import word_match

MIN_TITLE_COVERAGE = 0.4
MIN_QUERY_WORDS = 2
MIN_PREFIX_WORDS = 2
STOP_WORDS = frozenset({"a", "or", "the", "of", "to", "and"})

_WORD = re.compile(r"[^\W_]+")
_SPACE = re.compile(r"\s+")


def words(text: str) -> list[str]:
    """The text's words, lower-cased, stop words left out."""
    return [
        word
        for word in (match.casefold() for match in _WORD.findall(text))
        if word not in STOP_WORDS
    ]


def coverage(query: str, title: str) -> float:
    """The share of the title's words the query holds, 0 unless it covers it."""
    query_words = words(query)
    title_words = words(title)

    if not query_words or not all(
        any(_same(word, title_word) for title_word in title_words)
        for word in query_words
    ):
        return 0

    held = sum(_held(query_words, title_word) for title_word in title_words)
    share = held / len(title_words)

    return (
        share
        if _is_enough(query_words, share) or _is_start(query_words, title_words)
        else 0
    )


def covers(query: str, title: str) -> tuple[tuple[int, int], ...]:
    """Each run of query words in `title`, as (start, end) offsets, when the
    query covers it; otherwise none.

    Neighbouring query words with only spaces between them are one run.
    """
    if not coverage(query, title):
        return ()

    query_words = words(query)
    spans: list[tuple[int, int]] = []

    for match in _WORD.finditer(title):
        title_word = match[0].casefold()

        if title_word in STOP_WORDS or not _held(query_words, title_word):
            continue

        if spans and _SPACE.fullmatch(title[spans[-1][1] : match.start()]):
            spans[-1] = (spans[-1][0], match.end())
        else:
            spans.append(match.span())

    return tuple(spans)


def _is_enough(query_words: list[str], share: float) -> bool:
    """Most of the title, from more than one keyword unless it is all of it."""
    return share >= MIN_TITLE_COVERAGE and (
        len(query_words) >= MIN_QUERY_WORDS or share == 1
    )


def _is_start(query_words: list[str], title_words: list[str]) -> bool:
    return MIN_PREFIX_WORDS <= len(query_words) <= len(title_words) and all(
        _same(word, title_word)
        for word, title_word in zip(query_words, title_words, strict=False)
    )


def _held(query_words: list[str], title_word: str) -> bool:
    return any(_same(word, title_word) for word in query_words)


def _same(word: str, title_word: str) -> bool:
    """The same word, or within the query word's typo budget of it."""
    if word == title_word:
        return True

    budget = word_match.typo_budget(word)
    return bool(budget) and (
        OSA.distance(word, title_word, score_cutoff=budget) <= budget
    )
