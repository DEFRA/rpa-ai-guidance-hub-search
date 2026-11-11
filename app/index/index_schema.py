"""Chunk and title index fields, declared without reference to any engine.

`weight` marks a field as free-text searched; weights are relative, so engines
promise the same field ordering, not the same scores.

`SCHEMA_VERSION` names the layout of a built index. Bump it whenever a field's
name, kind, storage or analysis changes, or the chunking strategy that feeds the
index does: a build is only ever read by code with the same version, and a new
version is rebuilt from the canonical section files.
"""

import dataclasses
import enum
from collections.abc import Mapping, Sequence

from app.records import models

SCHEMA_VERSION = 1

type IndexValue = str | int | tuple[str, ...] | Mapping[str, models.JsonValue] | None


class FieldKind(enum.StrEnum):
    KEYWORD = "keyword"
    TEXT = "text"
    TEXT_EXACT = "text_exact"
    TEXT_PARTIAL = "text_partial"  # 3-character runs, lower-cased; shortlists only
    INTEGER = "integer"
    TAGS = "tags"
    OBJECT = "object"


class EntryKind(enum.StrEnum):
    """A section's chunk, or the one title entry per document."""

    SECTION = "section"
    TITLE = "title"


_SEARCHABLE = frozenset({FieldKind.TEXT, FieldKind.TEXT_EXACT})


@dataclasses.dataclass(frozen=True)
class IndexField:
    """`stored` fields come back with hits; unstored ones are only searched."""

    name: str
    kind: FieldKind
    stored: bool = True
    weight: float | None = None
    multi_valued: bool = False

    def __post_init__(self) -> None:
        if self.weight is None:
            return

        if self.kind not in _SEARCHABLE:
            msg = f"{self.name}: only text fields can be weighted, not {self.kind}"
            raise ValueError(msg)

        if self.weight <= 0:
            msg = f"{self.name}: weight must be positive, got {self.weight}"
            raise ValueError(msg)


KIND = "kind"
CHUNK_ID = "chunk_id"
SECTION_ID = "section_id"
DOCUMENT_ID = "document_id"
VERSION_ID = "version_id"
STATUS = "status"
SOURCE_TYPE = "source_type"
URL = "url"
TITLE = "title"
TITLE_NGRAM = "title_ngram"
HEADING_PATH = "heading_path"
SECTION_NUMBER = "section_number"
BODY = "body"
BODY_EXACT = "body_exact"
BODY_NGRAM = "body_ngram"
TOPICS = "topics"
ANCHOR = "anchor"
LEVEL = "level"
ORDINAL = "ordinal"
META = "meta"

CHUNK_INDEX: tuple[IndexField, ...] = (
    IndexField(KIND, FieldKind.KEYWORD),
    IndexField(CHUNK_ID, FieldKind.KEYWORD),
    IndexField(SECTION_ID, FieldKind.KEYWORD),
    IndexField(DOCUMENT_ID, FieldKind.KEYWORD),
    IndexField(VERSION_ID, FieldKind.KEYWORD),
    IndexField(STATUS, FieldKind.KEYWORD),
    IndexField(SOURCE_TYPE, FieldKind.KEYWORD),
    IndexField(URL, FieldKind.KEYWORD),
    IndexField(TITLE, FieldKind.TEXT, weight=2),
    IndexField(TITLE_NGRAM, FieldKind.TEXT_PARTIAL, stored=False),
    IndexField(HEADING_PATH, FieldKind.TEXT, weight=3, multi_valued=True),
    IndexField(SECTION_NUMBER, FieldKind.KEYWORD),
    IndexField(BODY, FieldKind.TEXT, weight=1),
    IndexField(BODY_EXACT, FieldKind.TEXT_EXACT, stored=False, weight=1),
    IndexField(BODY_NGRAM, FieldKind.TEXT_PARTIAL, stored=False),
    IndexField(TOPICS, FieldKind.TAGS, multi_valued=True),
    IndexField(ANCHOR, FieldKind.KEYWORD),
    IndexField(LEVEL, FieldKind.INTEGER),
    IndexField(ORDINAL, FieldKind.INTEGER),
    IndexField(META, FieldKind.OBJECT),
)


def weights(fields: Sequence[IndexField] = CHUNK_INDEX) -> dict[str, float]:
    return {field.name: field.weight for field in fields if field.weight is not None}


def to_index_document(
    header: models.DocumentHeader,
    chunk: models.ChunkRecord,
) -> dict[str, IndexValue]:
    """The one mapping from a chunk to index fields. `None` means no value."""
    if _owner(chunk) != _owner(header):
        msg = f"chunk {chunk.chunk_id} does not belong to the header's version"
        raise ValueError(msg)

    return _document_fields(header, EntryKind.SECTION) | {
        CHUNK_ID: chunk.chunk_id,
        SECTION_ID: chunk.section_id,
        HEADING_PATH: chunk.heading_path,
        SECTION_NUMBER: chunk.section_number,
        BODY: chunk.plain_text,
        BODY_EXACT: chunk.plain_text,
        BODY_NGRAM: chunk.plain_text,
        TOPICS: chunk.topics,
        ANCHOR: chunk.anchor,
        LEVEL: chunk.level,
        ORDINAL: chunk.ordinal,
        META: chunk.meta,
    }


def to_title_document(header: models.DocumentHeader) -> dict[str, IndexValue]:
    """The document's one title entry, for title search."""
    return _document_fields(header, EntryKind.TITLE) | {TITLE_NGRAM: header.title}


def _document_fields(
    header: models.DocumentHeader,
    kind: EntryKind,
) -> dict[str, IndexValue]:
    empty: dict[str, IndexValue] = dict.fromkeys(field.name for field in CHUNK_INDEX)

    return empty | {
        KIND: kind.value,
        DOCUMENT_ID: str(header.document_id),
        VERSION_ID: str(header.version_id),
        STATUS: header.status.value,
        SOURCE_TYPE: header.source_type.value,
        URL: header.url,
        TITLE: header.title,
    }


def _owner(
    record: models.DocumentHeader | models.ChunkRecord,
) -> tuple[object, ...]:
    """What a chunk shares with its header: version, status and source."""
    return (record.document_id, record.version_id, record.status, record.source_type)
