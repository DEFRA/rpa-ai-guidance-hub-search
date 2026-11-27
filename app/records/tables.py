"""A block's Markdown table, read as its rows of plain-text cells.

Indexing joins a row's cells into one line, and search reads each cell's lines, as
a page does; both read the cells here, so an escaped pipe (`\\|`) stays within its
cell on either side. A cell's last lines that each start with "- " are a list, as
the guide page shows them: each item a line of its own, without its mark.
"""

import re

from app.records import plain_text

_DELIMITER = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+\s*$")
_BREAK = re.compile(r"<br\s*/?>")
# A list item's mark, its dash written plain, escaped or as an entity.
_MARKS = ("- ", "\\- ", "&#45; ", "&#x2d; ", "&#x2D; ")
_TAGS = ("u", "sup", "sub")
# Each mark that can close at the start of a part, and the mark it closes.
_CLOSERS = (
    ("**", "**"),
    ("~~", "~~"),
    ("*", "*"),
    ("</u>", "<u>"),
    ("</sup>", "<sup>"),
    ("</sub>", "<sub>"),
)
_SPAN_ENDS = ("]{.red}", "]{.blue}")
# ASCII punctuation: all CommonMark lets a backslash escape.
_ESCAPABLE = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
_ESCAPED_PIPE = "\\|"
_PIPE_PLACEHOLDER = "\x00"
# What stands for an escaped character while marks are counted.
_ESCAPE_STAND_IN = "\x01"


def rows(markdown: str) -> tuple[tuple[str, ...], ...]:
    """Each row but the delimiter, as its cells with words; rows with none are
    left out."""
    found = (cells(row) for row in markdown.strip("\n").split("\n"))
    return tuple(row for row in found if row)


def lines(markdown: str) -> tuple[str, ...]:
    """Each cell's lines, in order, as the guide page shows them."""
    return tuple(
        line
        for row in markdown.strip("\n").split("\n")
        for cell in _cells_of(row)
        for line in _lines_of(cell)
    )


def cells(row: str) -> tuple[str, ...]:
    """A row's cells with words, each as plain text; none for the delimiter."""
    joined = (" ".join(_lines_of(cell)) for cell in _cells_of(row))
    return tuple(cell for cell in joined if cell)


def _cells_of(row: str) -> list[str]:
    """A row's cells as written; none for the delimiter."""
    if _DELIMITER.match(row.strip()):
        return []
    protected = row.strip().replace(_ESCAPED_PIPE, _PIPE_PLACEHOLDER).strip("|")
    return [
        cell.replace(_PIPE_PLACEHOLDER, _ESCAPED_PIPE) for cell in protected.split("|")
    ]


def _lines_of(cell: str) -> tuple[str, ...]:
    """A cell's lines with words, as plain text. From the first line that starts with
    an unstyled "- ", when every line after it does too, each is an item of a list,
    after the lines before it as one line; otherwise the cell is one line (the UI's
    `tiptap/tables.js`, `promote`)."""
    parts = _closed_back(_BREAK.split(cell.strip()))
    items = [
        _mark_of(part) is not None and not _open_marks("<br>".join(parts[:at]))
        for at, part in enumerate(parts)
    ]
    first = next((at for at, item in enumerate(items) if item), None)
    if first is None or not all(items[first:]):
        groups = [cell]
    else:
        lead = ["<br>".join(parts[:first])] if first else []
        groups = lead + [
            part.removeprefix(_mark_of(part) or "") for part in parts[first:]
        ]
    found = (plain_text.strip_inline(group) for group in groups)
    return tuple(line for line in found if line)


def _mark_of(part: str) -> str | None:
    """The list item's mark that `part` starts with, however its dash is written."""
    return next((mark for mark in _MARKS if part.startswith(mark)), None)


def _closed_back(parts: list[str]) -> list[str]:
    """The parts, each mark that closes at the start of one moved back to the end of
    the part before it, where it was opened: `**Intro<br>**- Apply` is the parts
    `**Intro**` and `- Apply`, whose mark is unstyled."""
    fixed = parts[:1]
    for part in parts[1:]:
        rest = part
        while (mark := _closing_mark("<br>".join(fixed), rest)) is not None:
            fixed[-1] += mark
            rest = rest.removeprefix(mark)
        fixed.append(rest)
    return fixed


def _closing_mark(before: str, rest: str) -> str | None:
    """The mark at the start of `rest` that closes one still open in `before`."""
    opened = _open_marks(before)
    for mark, opener in _CLOSERS:
        if opener in opened and rest.startswith(mark):
            return mark
    if "[" not in opened:
        return None
    colour = next((end for end in _SPAN_ENDS if rest.startswith(end)), None)
    return colour if colour is not None else _link_end(rest)


def _link_end(rest: str) -> str | None:
    """`rest`'s start when it ends a link's words and gives where it goes, `](...)`."""
    if not rest.startswith("]("):
        return None
    if rest.startswith("](<"):
        end = rest.find(">)")
        return None if end < 0 else rest[: end + 2]
    depth = 1
    for at, character in enumerate(rest[2:], start=2):
        depth += (character == "(") - (character == ")")
        if depth == 0:
            return rest[: at + 1]
    return None


def _open_marks(text: str) -> set[str]:
    """The marks opened in `text` and still open at its end, so that what follows is
    styled: bold, italics, strikethrough, a link's or colour span's words, or
    underline, superscript or subscript. The converter writes each mark in pairs."""
    plain = _without_addresses(_unescaped(text))
    counts = {
        "**": plain.count("**") % 2,
        "*": plain.replace("**", "").count("*") % 2,
        "~~": plain.count("~~") % 2,
        "[": plain.count("[") - plain.count("]"),
    }
    counts |= {
        f"<{tag}>": plain.count(f"<{tag}>") - plain.count(f"</{tag}>") for tag in _TAGS
    }
    return {mark for mark, count in counts.items() if count}


def _without_addresses(text: str) -> str:
    """`text` without where its links and pictures go, whose characters mark
    nothing: `[words](address)` is read as `[words]`."""
    kept: list[str] = []
    at = 0
    while (start := text.find("](", at)) >= 0:
        address = _link_end(text[start:])
        if address is None:
            break
        kept.append(text[at : start + 1])
        at = start + len(address)
    kept.append(text[at:])
    return "".join(kept)


def _unescaped(text: str) -> str:
    """`text` with each escaped character, which marks nothing, as a stand-in."""
    kept: list[str] = []
    at = 0
    while at < len(text):
        if text[at] == "\\" and at + 1 < len(text) and text[at + 1] in _ESCAPABLE:
            # A stand-in, so that marks either side of it do not run together.
            kept.append(_ESCAPE_STAND_IN)
            at += 2
            continue
        kept.append(text[at])
        at += 1
    return "".join(kept)
