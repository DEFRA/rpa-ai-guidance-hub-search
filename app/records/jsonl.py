"""One JSON Lines file per document version: a header line, then one line per section.

Sections are the canonical records; chunk sets (`chunk_sets`) are staged
copies of what one strategy derived from them. The header carries `schema_version`. Files outlive the code that wrote
them, so a breaking record change bumps `SCHEMA_VERSION` and adds a reader that
upgrades the older version's lines; files of every supported version stay
readable.
"""

import json
from collections.abc import Sequence
from typing import Any

from app.records import ids, models

SCHEMA_VERSION = 1

_RECORD = "record"
_DOCUMENT = "document"
_SECTION = "section"
_SCHEMA_VERSION = "schema_version"


class MalformedSectionFileError(ValueError):
    """Records that don't form one document version."""


def key(document_id: ids.DocumentId, version_id: ids.VersionId) -> str:
    return f"sections/{document_id}/{version_id}.jsonl"


def dumps(
    header: models.DocumentHeader,
    sections: Sequence[models.SectionRecord],
) -> str:
    _check(header, sections)

    lines = [
        {_RECORD: _DOCUMENT, _SCHEMA_VERSION: SCHEMA_VERSION, **header.to_record()},
        *({_RECORD: _SECTION, **section.to_record()} for section in sections),
    ]

    return dump_lines(lines)


def loads(
    text: str,
) -> tuple[models.DocumentHeader, tuple[models.SectionRecord, ...]]:
    lines = parse_lines(text, MalformedSectionFileError)

    if not lines or lines[0].get(_RECORD) != _DOCUMENT:
        msg = "the first line must be the document header"
        raise MalformedSectionFileError(msg)

    first, *rest = lines

    if first.get(_SCHEMA_VERSION) != SCHEMA_VERSION:
        msg = f"unsupported schema version {first.get(_SCHEMA_VERSION)!r}"
        raise MalformedSectionFileError(msg)

    if any(line.get(_RECORD) != _SECTION for line in rest):
        msg = "every line after the header must be a section"
        raise MalformedSectionFileError(msg)

    try:
        header = models.DocumentHeader.from_record(first)
        sections = tuple(models.SectionRecord.from_record(line) for line in rest)
    except (KeyError, TypeError, ValueError) as e:
        msg = f"invalid record: {e!r}"
        raise MalformedSectionFileError(msg) from e

    _check(header, sections)
    return header, sections


def dump_lines(lines: Sequence[dict[str, Any]]) -> str:
    """One JSON object per line, non-ASCII kept readable, ending with a newline."""
    return "".join(f"{json.dumps(line, ensure_ascii=False)}\n" for line in lines)


def parse_lines(text: str, error: type[ValueError]) -> list[dict[str, Any]]:
    """Every non-blank line as a JSON object; `error` names the bad line.

    Splits on `\\n` only: `str.splitlines()` would also break at U+2028 and
    friends, which JSON allows unescaped inside strings.
    """
    return [
        _parse(number, line, error)
        for number, line in enumerate(text.split("\n"), start=1)
        if line.strip()
    ]


def _parse(number: int, line: str, error: type[ValueError]) -> dict[str, Any]:
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError as e:
        msg = f"line {number} is not JSON"
        raise error(msg) from e

    if not isinstance(parsed, dict):
        msg = f"line {number} is not a JSON object"
        raise error(msg)

    return parsed


def _check(
    header: models.DocumentHeader,
    sections: Sequence[models.SectionRecord],
) -> None:
    for position, section in enumerate(sections):
        if not _belongs_to(section, header):
            msg = (
                f"section {section.section_id} does not belong to the header's version"
            )
            raise MalformedSectionFileError(msg)

        if section.ordinal != position:
            msg = f"expected ordinal {position}, got {section.ordinal}"
            raise MalformedSectionFileError(msg)


def _belongs_to(section: models.SectionRecord, header: models.DocumentHeader) -> bool:
    """Whether the section carries the same version identity as the header."""
    return (
        section.document_id == header.document_id
        and section.version_id == header.version_id
        and section.status == header.status
        and section.source_type == header.source_type
    )
