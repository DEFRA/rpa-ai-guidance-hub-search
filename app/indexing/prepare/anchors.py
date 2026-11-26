"""The anchor a heading renders with: the target of a `#anchor` deep link.

A port of the API's `app/guidance/parsing/anchors.py`, which writes the links in
`content.md`. The UI's renderer uses the same rule. Change all three together, or
links stop resolving.

The rule is GitHub's heading slug, except that a dot between two digits becomes a
hyphen, so "1.2" is not read as "12". Lower-case the heading. Drop everything else
that isn't a word character, a space or a hyphen. Turn spaces into hyphens.
Headings that slug alike get `-1`, `-2` suffixes in document order.
"""

import re
from collections.abc import Sequence

_DISCARDED = re.compile(r"[^\w\- ]", re.UNICODE)

_DIGITS = frozenset("0123456789")


def slugify(rendered_heading: str) -> str:
    """`"3.1 Overview"` -> `"3-1-overview"`."""
    hyphenated = _numbers_hyphenated(rendered_heading)
    return _DISCARDED.sub("", hyphenated.lower()).replace(" ", "-")


def _numbers_hyphenated(text: str) -> str:
    """`text` with each dot between two digits made a hyphen: "3.1" is "3-1"."""
    return "".join(
        "-" if char == "." and _between_digits(text, at) else char
        for at, char in enumerate(text)
    )


def _between_digits(text: str, at: int) -> bool:
    return 0 < at < len(text) - 1 and {text[at - 1], text[at + 1]} <= _DIGITS


def of_headings(rendered_headings: Sequence[str]) -> list[str]:
    """Each heading's anchor in document order, suffixing duplicates."""
    seen: dict[str, int] = {}
    result: list[str] = []

    for heading in rendered_headings:
        base = slugify(heading)
        count = seen.get(base, 0)
        seen[base] = count + 1
        result.append(base if count == 0 else f"{base}-{count}")

    return result
