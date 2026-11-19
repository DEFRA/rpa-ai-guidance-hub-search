"""A block's Markdown table, read as its rows of plain-text cells.

Indexing joins a row's cells into one line, and search reads them a cell at a
time, as a page does; both read the cells here, so an escaped pipe (`\\|`) stays
within its cell on either side.
"""

import re

from app.records import plain_text

_DELIMITER = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+\s*$")
_ESCAPED_PIPE = "\\|"
_PIPE_PLACEHOLDER = "\x00"


def rows(markdown: str) -> tuple[tuple[str, ...], ...]:
    """Each row but the delimiter, as its cells with words; rows with none are
    left out."""
    found = (cells(row) for row in markdown.strip("\n").split("\n"))
    return tuple(row for row in found if row)


def cells(row: str) -> tuple[str, ...]:
    """A row's cells with words, each as plain text; none for the delimiter."""
    if _DELIMITER.match(row.strip()):
        return ()

    protected = row.strip().replace(_ESCAPED_PIPE, _PIPE_PLACEHOLDER).strip("|")
    stripped = (
        plain_text.strip_inline(cell.replace(_PIPE_PLACEHOLDER, _ESCAPED_PIPE))
        for cell in protected.split("|")
    )

    return tuple(cell for cell in stripped if cell)
