"""A Markdown section body split into `models.Block`s.

A block is a paragraph, a list, a blockquote or a table. Blocks are never split.
Each one keeps its raw Markdown and its plain-text lines. A line is one
paragraph, one list item, one table row or one line of a blockquote, so a phrase
never matches across two of them. A hard break is a space within its line, as a
page renders it (`<br>`).

Chonkie's `MarkdownChef` finds the tables. The dialect has no code blocks, so
blank lines separate everything else.
"""

import re
from collections.abc import Sequence

import chonkie

from app.records import models, plain_text, tables

# Groups: indent, bullet, ordered delimiter. Items of one list share all three.
_LIST_MARKER = re.compile(r"^(\s*)(?:([-*+])|\d+([.)]))\s+")
_QUOTE_MARKER = re.compile(r"^>\s?")
# How a table row's cells join into its one line.
_TABLE_CELL_SEPARATOR = " | "


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
    """A new line at each list item, after a blank line, and where a quote in a list
    item starts; a quote's lines are read without their "> " marks, and an empty one
    ends a paragraph, as a blank line does."""
    groups: list[list[str]] = []
    blank = True
    quoting = False

    for line in lines:
        quote = _quoted_in_item(line)
        if not line.strip() or quote == "":
            blank = True
            continue

        marker = _LIST_MARKER.match(line)
        if quote is not None:
            text = _without_hard_break(quote)
        else:
            text = _without_hard_break(line[marker.end() :] if marker else line)

        if marker or blank or (quote is not None) != quoting:
            groups.append([text])
        else:
            groups[-1].append(text)

        blank = False
        quoting = quote is not None

    stripped = (plain_text.strip_inline(" ".join(group)) for group in groups)
    return tuple(line for line in stripped if line)


def _quoted_in_item(line: str) -> str | None:
    """An indented line's words after its quote mark, as in a quote in a list item;
    None for any other line."""
    words = line.lstrip()
    if not line[:1].isspace() or not words.startswith(">"):
        return None
    return words[1:].removeprefix(" ").strip()


def _without_hard_break(line: str) -> str:
    """The line without its hard break: an odd run of trailing backslashes, the
    last one not itself escaped. The break reads as the space the lines join on.
    """
    trimmed = line.rstrip()
    breaks = (len(trimmed) - len(trimmed.rstrip("\\"))) % 2 == 1

    return trimmed[:-1] if breaks else line


def _quote_lines(lines: Sequence[str]) -> tuple[str, ...]:
    inner = "\n".join(_QUOTE_MARKER.sub("", line, count=1) for line in lines)
    return tuple(line for block in _text_blocks(inner) for line in block.lines)


def _table(content: str) -> models.Block:
    """A line per row, its cells joined so a phrase never runs from one into
    the next."""
    markdown = content.strip("\n")

    return models.Block(
        kind=models.BlockKind.TABLE,
        markdown=markdown,
        lines=tuple(_TABLE_CELL_SEPARATOR.join(row) for row in tables.rows(markdown)),
    )
