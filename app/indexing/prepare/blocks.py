"""A Markdown section body split into `models.Block`s.

A block is a paragraph, a list, a blockquote or a table. Blocks are never split.
Each one keeps its raw Markdown and its plain-text lines. A line is one
paragraph, one list item, one table row or one line of a blockquote, so a phrase
never matches across two of them.

Chonkie's `MarkdownChef` finds the tables. The dialect has no code blocks, so
blank lines separate everything else.
"""

import re
from collections.abc import Sequence

import chonkie

from app.indexing.prepare import plain_text
from app.records import models

# Groups: indent, bullet, ordered delimiter. Items of one list share all three.
_LIST_MARKER = re.compile(r"^(\s*)(?:([-*+])|\d+([.)]))\s+")
_QUOTE_MARKER = re.compile(r"^>\s?")
_TABLE_DELIMITER = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+\s*$")
_ESCAPED_PIPE = "\\|"
_PIPE_PLACEHOLDER = "\x00"


def parse(body: str, chef: chonkie.MarkdownChef) -> list[models.Block]:
    text = body if body.endswith("\n") else f"{body}\n"
    blocks: list[models.Block] = []
    cursor = 0

    for table in chef.parse(text).tables:
        # A match starting mid-line is a `|` in prose, not a table.
        if table.start_index < cursor or (
            table.start_index > 0 and text[table.start_index - 1] != "\n"
        ):
            continue

        blocks.extend(_text_blocks(text[cursor : table.start_index]))
        blocks.append(_table(table.content))
        cursor = table.end_index

    blocks.extend(_text_blocks(text[cursor:]))

    return blocks


def _text_blocks(text: str) -> list[models.Block]:
    blocks: list[models.Block] = []
    current: list[str] = []
    kind = models.BlockKind.PARAGRAPH
    lines = text.split("\n")

    for index, line in enumerate(lines):
        if not line.strip():
            if (
                kind is models.BlockKind.LIST
                and current
                and _list_continues(lines, index, current[0])
            ):
                current.append(line)
            elif current:
                blocks.append(_block(kind, current))
                current = []
            continue

        line_kind = _kind_of(line)

        if current and _starts_new_block(kind, line_kind, line):
            blocks.append(_block(kind, current))
            current = []

        if not current:
            kind = line_kind

        current.append(line)

    if current:
        blocks.append(_block(kind, current))

    return blocks


def _kind_of(line: str) -> models.BlockKind:
    if _QUOTE_MARKER.match(line):
        return models.BlockKind.BLOCKQUOTE

    if _LIST_MARKER.match(line):
        return models.BlockKind.LIST

    return models.BlockKind.PARAGRAPH


def _starts_new_block(
    kind: models.BlockKind, line_kind: models.BlockKind, line: str
) -> bool:
    match kind:
        case models.BlockKind.PARAGRAPH:
            return line_kind is not models.BlockKind.PARAGRAPH
        case models.BlockKind.BLOCKQUOTE:
            return line_kind is not models.BlockKind.BLOCKQUOTE
        case _:
            # A list. Tables are found before this, so never reach here.
            return line_kind is models.BlockKind.BLOCKQUOTE and not line[:1].isspace()


def _list_continues(lines: Sequence[str], blank: int, first: str) -> bool:
    """Whether the list starting `first` goes on past the blank line at `blank`.

    It does when the next line is indented (a continuation of the item above) or
    is another item of the same list: same indent and the same kind of marker.
    """
    following = next((line for line in lines[blank + 1 :] if line.strip()), None)

    if following is None:
        return False

    if following[:1].isspace():
        return True

    item = _LIST_MARKER.match(following)
    opening = _LIST_MARKER.match(first)

    return bool(item and opening and item.groups() == opening.groups())


def _block(kind: models.BlockKind, lines: Sequence[str]) -> models.Block:
    markdown = "\n".join(lines).strip("\n")

    match kind:
        case models.BlockKind.BLOCKQUOTE:
            plain = _quote_lines(lines)
        case _:
            plain = _logical_lines(lines)

    return models.Block(kind=kind, markdown=markdown, lines=plain)


def _logical_lines(lines: Sequence[str]) -> tuple[str, ...]:
    """A new line at each list item, after a blank line and after a hard break."""
    groups: list[list[str]] = []
    breaks = True

    for line in lines:
        if not line.strip():
            breaks = True
            continue

        marker = _LIST_MARKER.match(line)

        if marker or breaks:
            groups.append([line[marker.end() :] if marker else line])
        else:
            groups[-1].append(line)

        breaks = _ends_with_hard_break(line.rstrip())

    stripped = (plain_text.strip_inline(" ".join(group)) for group in groups)
    return tuple(line for line in stripped if line)


def _ends_with_hard_break(line: str) -> bool:
    """An odd run of trailing backslashes: the last one is not itself escaped."""
    return (len(line) - len(line.rstrip("\\"))) % 2 == 1


def _quote_lines(lines: Sequence[str]) -> tuple[str, ...]:
    inner = "\n".join(_QUOTE_MARKER.sub("", line, count=1) for line in lines)
    return tuple(line for block in _text_blocks(inner) for line in block.lines)


def _table(content: str) -> models.Block:
    rows = content.strip("\n").split("\n")
    plain = (_row_text(row) for row in rows if not _TABLE_DELIMITER.match(row.strip()))

    return models.Block(
        kind=models.BlockKind.TABLE,
        markdown="\n".join(rows),
        lines=tuple(row for row in plain if row),
    )


def _row_text(row: str) -> str:
    """Cells joined by `" | "`, so a phrase never runs from one cell into the next."""
    protected = row.strip().replace(_ESCAPED_PIPE, _PIPE_PLACEHOLDER).strip("|")
    cells = (
        plain_text.strip_inline(cell.replace(_PIPE_PLACEHOLDER, _ESCAPED_PIPE))
        for cell in protected.split("|")
    )

    return " | ".join(cell for cell in cells if cell)
