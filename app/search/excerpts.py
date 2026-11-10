"""The excerpt a hit is shown with: the first sentence holding a match.

The rule is the milestone 1 spike's:

- It looks in the text before the heading. The heading is shown with its
  number, as `"1.1 Scope"`.
- A sentence of up to `LONGEST_SENTENCE` characters is shown whole.
- A longer one is cut at words to about `SENTENCE_REACH` characters either
  side of its first match, with ellipses.

A near hit shows its near matches; an exact hit, its exact ones.
"""

from collections.abc import Callable

from app.index import chunk_index
from app.matching import word_match
from app.search import service

LONGEST_SENTENCE = 300
SENTENCE_REACH = 120
ELLIPSIS = "…"

_SENTENCE_ENDS = frozenset(".?!")

type _Finder = Callable[[str, str], tuple[tuple[int, int], ...]]


def of_hit(query: str, hit: chunk_index.SectionHit) -> service.Excerpt:
    find: _Finder = word_match.find if hit.exact else word_match.find_near
    lines = [*hit.plain_text.split("\n"), heading_line(hit)]

    for line in lines:
        for sentence in sentences(line):
            matches = find(query, sentence)

            if matches:
                shown = around(sentence, matches[0][0])
                return service.Excerpt(text=shown, matches=find(query, shown))

    # A hit on its section number alone has no match in its text.
    return service.Excerpt(text=heading_line(hit), matches=())


def heading_line(hit: chunk_index.SectionHit) -> str:
    """The heading as the document prints it; its title before any section."""
    heading = hit.heading_path[-1] if hit.heading_path else hit.title
    return f"{hit.section_number} {heading}" if hit.section_number else heading


def sentences(text: str) -> list[str]:
    """A line's sentences, each ending at `.`, `?` or `!` before a space or the end."""
    found: list[str] = []
    start = 0

    for at, character in enumerate(text):
        if character in _SENTENCE_ENDS and (at + 1 == len(text) or text[at + 1] == " "):
            found.append(text[start : at + 1].strip())
            start = at + 1

    rest = text[start:].strip()
    return [sentence for sentence in [*found, rest] if sentence]


def around(sentence: str, first: int) -> str:
    """A long sentence cut, at words, to the text around its first match."""
    if len(sentence) <= LONGEST_SENTENCE:
        return sentence

    start = max(first - SENTENCE_REACH, 0)
    end = min(first + SENTENCE_REACH, len(sentence))
    cut_from = 0 if start == 0 else sentence.find(" ", start) + 1
    cut_to = end if end == len(sentence) else sentence.rfind(" ", 0, end)
    lead = ELLIPSIS if cut_from > 0 else ""
    tail = ELLIPSIS if cut_to < len(sentence) else ""

    return f"{lead}{sentence[cut_from:cut_to]}{tail}"
