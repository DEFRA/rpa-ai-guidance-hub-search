"""How close a title search is to a document's title, from 0 to 1.

Typos, half words and words in another order are all forgiven: a title
matches when every query word matches one of its words, whole, as its start
(3 or more characters), or within `word_match.typo_budget` of either. Each
query word scores its best match (whole 1, start 0.75, typo 0.5) and the
title scores their mean, or 0 unless every word matches.
"""

from rapidfuzz.distance import OSA

from app.matching import word_match

_SHORT = 2
_WHOLE, _START, _TYPO = 1.0, 0.75, 0.5


def score(query: str, title: str) -> float:
    words = word_match.near_words(query)
    title_words = word_match.near_words(title)

    if not words:
        return 0

    scores = [
        max((_word_score(word, title_word) for title_word in title_words), default=0)
        for word in words
    ]
    return sum(scores) / len(scores) if all(scores) else 0


def is_close(query: str, title: str) -> bool:
    return score(query, title) > 0


def _word_score(word: str, candidate: str) -> float:
    if word == candidate:
        return _WHOLE

    if len(word) <= _SHORT:
        return 0

    if candidate.startswith(word):
        return _START

    distance = min(
        OSA.distance(word, candidate),
        OSA.distance(word, candidate[: len(word)]),
    )
    return _TYPO if distance <= word_match.typo_budget(word) else 0
