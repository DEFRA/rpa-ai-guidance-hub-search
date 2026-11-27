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
_MARK = "- "
_ESCAPED_PIPE = "\\|"
_PIPE_PLACEHOLDER = "\x00"


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
    "- ", when every line after it does too, each is an item of a list, after the
    lines before it as one line; otherwise the cell is one line (the UI's
    `tiptap/tables.js`, `promote`)."""
    parts = _BREAK.split(cell.strip())
    first = next((at for at, part in enumerate(parts) if part.startswith(_MARK)), None)
    if first is None or not all(part.startswith(_MARK) for part in parts[first:]):
        groups = [cell]
    else:
        lead = ["<br>".join(parts[:first])] if first else []
        groups = lead + [part.removeprefix(_MARK) for part in parts[first:]]
    found = (plain_text.strip_inline(group) for group in groups)
    return tuple(line for line in found if line)
