"""The anchor a heading renders with: the target of a `#anchor` deep link.

A port of the API's `app/guidance/parsing/anchors.py`, which writes the links in
`content.md`. The UI's renderer uses the same rule. Change all three together, or
links stop resolving.

The rule is GitHub's heading slug. Lower-case the heading. Drop everything that
isn't a word character, a space or a hyphen. Turn spaces into hyphens. Headings
that slug alike get `-1`, `-2` suffixes in document order.
"""

import re
from collections.abc import Sequence

_DISCARDED = re.compile(r"[^\w\- ]", re.UNICODE)


def slugify(rendered_heading: str) -> str:
    """`"3.1 Overview"` -> `"31-overview"`: the dot is dropped, not replaced."""
    return _DISCARDED.sub("", rendered_heading.lower()).replace(" ", "-")


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
