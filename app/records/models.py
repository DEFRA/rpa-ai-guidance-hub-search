"""Canonical document headers and section records, and the chunks derived from them.

Every source (a guide today, GOV.UK pages later) becomes a header and its
sections. Sections are canonical: they are stored, and every index can be
rebuilt from them. Chunks are derived from sections by a named chunking
strategy when a version is prepared, and staged as a chunk set for the apply
step to write; a new strategy only re-chunks the same sections.

Stored keys are snake_case. Readers ignore unknown keys, so changes must be additive.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import hashlib
import uuid
from collections.abc import Mapping
from typing import Any

from app.records import fields, ids, source_metadata

type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)


class DocumentStatus(enum.StrEnum):
    READY_FOR_PUBLISH = "ready_for_publish"
    PUBLISHED = "published"


class SourceType(enum.StrEnum):
    GUIDE = "guide"


class ReferenceKind(enum.StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class BlockKind(enum.StrEnum):
    PARAGRAPH = "paragraph"
    LIST = "list"
    BLOCKQUOTE = "blockquote"
    TABLE = "table"


def content_hash(text: str) -> str:
    """sha256 hex of raw Markdown."""
    return hashlib.sha256(text.encode()).hexdigest()


def token_count(text: str) -> int:
    """Whitespace-separated words. Good enough to size chunks; not a model's count."""
    return len(text.split())


@dataclasses.dataclass(frozen=True)
class DocumentHeader:
    """One version of one document, from any source.

    `url` is where a reader views the document; a section's deep link is
    `{url}#{anchor}`. `content_url` is where its content was read from.
    `source` is what the source says about the document, typed for
    `source_type` (`source_metadata`).
    """

    document_id: ids.DocumentId
    version_id: ids.VersionId
    status: DocumentStatus
    source_type: SourceType
    title: str
    url: str
    content_url: str
    occurred_at: dt.datetime
    source: source_metadata.SourceMetadata

    def __post_init__(self) -> None:
        if self.occurred_at.utcoffset() is None:
            msg = "occurred_at must be timezone-aware"
            raise ValueError(msg)

        if self.source_type != self.source.SOURCE_TYPE:
            msg = (
                f"{type(self.source).__name__} is not metadata for "
                f"{self.source_type.value!r} documents"
            )
            raise ValueError(msg)

    def link(self, anchor: str) -> str:
        """The deep link to a section, or the document itself for no anchor."""
        return f"{self.url}#{anchor}" if anchor else self.url

    def to_record(self) -> dict[str, Any]:
        return {
            "document_id": str(self.document_id),
            "version_id": str(self.version_id),
            "status": self.status.value,
            "source_type": self.source_type.value,
            "title": self.title,
            "url": self.url,
            "content_url": self.content_url,
            "occurred_at": self.occurred_at.isoformat(),
            "source": self.source.to_record(),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> DocumentHeader:
        """A header stored before `source` existed reads with empty metadata."""
        source_type = SourceType(fields.text(record["source_type"], "source_type"))

        return cls(
            document_id=ids.DocumentId(
                uuid.UUID(fields.text(record["document_id"], "document_id"))
            ),
            version_id=ids.VersionId(
                uuid.UUID(fields.text(record["version_id"], "version_id"))
            ),
            status=DocumentStatus(fields.text(record["status"], "status")),
            source_type=source_type,
            title=fields.text(record["title"], "title"),
            url=fields.text(record["url"], "url"),
            content_url=fields.text(record["content_url"], "content_url"),
            occurred_at=dt.datetime.fromisoformat(
                fields.text(record["occurred_at"], "occurred_at")
            ),
            source=source_metadata.of(source_type, record.get("source")),
        )


@dataclasses.dataclass(frozen=True)
class Block:
    """A paragraph, list, blockquote or table; never split.

    `markdown` is its raw Markdown. `lines` is its plain text, one paragraph,
    list item, table row or quote line each, so a phrase never spans two.
    """

    kind: BlockKind
    markdown: str
    lines: tuple[str, ...]

    @property
    def token_count(self) -> int:
        return token_count("\n".join(self.lines))

    def to_record(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "markdown": self.markdown,
            "lines": list(self.lines),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> Block:
        return cls(
            kind=BlockKind(fields.text(record["kind"], "kind")),
            markdown=fields.text(record["markdown"], "markdown"),
            lines=fields.texts(record["lines"], "lines"),
        )


@dataclasses.dataclass(frozen=True)
class Reference:
    """Internal links carry an anchor (and its section id); external, a URL."""

    kind: ReferenceKind
    text: str
    anchor: str | None = None
    section_id: ids.SectionId | None = None
    url: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "text": self.text,
            "anchor": self.anchor,
            "section_id": self.section_id,
            "url": self.url,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> Reference:
        section_id = record.get("section_id")

        return cls(
            kind=ReferenceKind(fields.text(record["kind"], "kind")),
            text=fields.text(record["text"], "text"),
            anchor=fields.optional_text(record.get("anchor"), "anchor"),
            section_id=(
                ids.SectionId(fields.text(section_id, "section_id"))
                if section_id is not None
                else None
            ),
            url=fields.optional_text(record.get("url"), "url"),
        )


@dataclasses.dataclass(frozen=True)
class SectionRecord:
    """One heading and the blocks under it, whole; the canonical unit.

    `anchor` changes on renumbering; `section_id` doesn't. `ordinal` is the
    section's position in the document, from 0. A section before the first
    heading has an empty `heading_path` and anchor. `content_hash` changes only
    when the section's Markdown does, so derived work (such as generated
    questions) can be reused across versions and status changes.
    """

    section_id: ids.SectionId
    document_id: ids.DocumentId
    version_id: ids.VersionId
    status: DocumentStatus
    source_type: SourceType
    parent_section_id: ids.SectionId | None
    heading_path: tuple[str, ...]
    section_number: str | None
    anchor: str
    level: int
    ordinal: int
    blocks: tuple[Block, ...]
    references: tuple[Reference, ...] = ()
    topics: tuple[str, ...] = ()
    meta: Mapping[str, JsonValue] = dataclasses.field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n\n".join(block.markdown for block in self.blocks)

    @property
    def plain_text(self) -> str:
        return "\n".join(line for block in self.blocks for line in block.lines)

    @property
    def token_count(self) -> int:
        return token_count(self.plain_text)

    @property
    def content_hash(self) -> str:
        return content_hash(self.text)

    def to_record(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "document_id": str(self.document_id),
            "version_id": str(self.version_id),
            "status": self.status.value,
            "source_type": self.source_type.value,
            "parent_section_id": self.parent_section_id,
            "heading_path": list(self.heading_path),
            "section_number": self.section_number,
            "anchor": self.anchor,
            "level": self.level,
            "ordinal": self.ordinal,
            "blocks": [block.to_record() for block in self.blocks],
            "content_hash": self.content_hash,
            "references": [reference.to_record() for reference in self.references],
            "topics": list(self.topics),
            "meta": dict(self.meta),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> SectionRecord:
        """`content_hash` is stored for readers of the raw file, and recomputed."""
        parent_section_id = record.get("parent_section_id")

        return cls(
            section_id=ids.SectionId(fields.text(record["section_id"], "section_id")),
            document_id=ids.DocumentId(
                uuid.UUID(fields.text(record["document_id"], "document_id"))
            ),
            version_id=ids.VersionId(
                uuid.UUID(fields.text(record["version_id"], "version_id"))
            ),
            status=DocumentStatus(fields.text(record["status"], "status")),
            source_type=SourceType(fields.text(record["source_type"], "source_type")),
            parent_section_id=(
                ids.SectionId(fields.text(parent_section_id, "parent_section_id"))
                if parent_section_id is not None
                else None
            ),
            heading_path=fields.texts(record["heading_path"], "heading_path"),
            section_number=fields.optional_text(
                record.get("section_number"), "section_number"
            ),
            anchor=fields.text(record["anchor"], "anchor"),
            level=fields.integer(record["level"], "level"),
            ordinal=fields.integer(record["ordinal"], "ordinal"),
            blocks=tuple(
                Block.from_record(block)
                for block in fields.mappings(record["blocks"], "blocks")
            ),
            references=tuple(
                Reference.from_record(reference)
                for reference in fields.mappings(
                    record.get("references", []), "references"
                )
            ),
            topics=fields.texts(record.get("topics", []), "topics"),
            meta=dict(fields.mapping(record.get("meta", {}), "meta")),
        )


@dataclasses.dataclass(frozen=True)
class ChunkRecord:
    """One chunk of one section, made by `strategy`; never crosses a heading.

    Derived from `SectionRecord`s; staged in a chunk set. `ordinal` is position in
    the document, `part` position in the section, both from 0. `plain_text` is
    what gets indexed.
    """

    chunk_id: ids.ChunkId
    strategy: str
    document_id: ids.DocumentId
    version_id: ids.VersionId
    status: DocumentStatus
    source_type: SourceType
    section_id: ids.SectionId
    parent_section_id: ids.SectionId | None
    heading_path: tuple[str, ...]
    section_number: str | None
    anchor: str
    level: int
    ordinal: int
    part: int
    text: str
    plain_text: str
    content_hash: str
    token_count: int
    topics: tuple[str, ...] = ()
    meta: Mapping[str, JsonValue] = dataclasses.field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "strategy": self.strategy,
            "document_id": str(self.document_id),
            "version_id": str(self.version_id),
            "status": self.status.value,
            "source_type": self.source_type.value,
            "section_id": self.section_id,
            "parent_section_id": self.parent_section_id,
            "heading_path": list(self.heading_path),
            "section_number": self.section_number,
            "anchor": self.anchor,
            "level": self.level,
            "ordinal": self.ordinal,
            "part": self.part,
            "text": self.text,
            "plain_text": self.plain_text,
            "content_hash": self.content_hash,
            "token_count": self.token_count,
            "topics": list(self.topics),
            "meta": dict(self.meta),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> ChunkRecord:
        parent_section_id = record.get("parent_section_id")

        return cls(
            chunk_id=ids.ChunkId(fields.text(record["chunk_id"], "chunk_id")),
            strategy=fields.text(record["strategy"], "strategy"),
            document_id=ids.DocumentId(
                uuid.UUID(fields.text(record["document_id"], "document_id"))
            ),
            version_id=ids.VersionId(
                uuid.UUID(fields.text(record["version_id"], "version_id"))
            ),
            status=DocumentStatus(fields.text(record["status"], "status")),
            source_type=SourceType(fields.text(record["source_type"], "source_type")),
            section_id=ids.SectionId(fields.text(record["section_id"], "section_id")),
            parent_section_id=(
                ids.SectionId(fields.text(parent_section_id, "parent_section_id"))
                if parent_section_id is not None
                else None
            ),
            heading_path=fields.texts(record["heading_path"], "heading_path"),
            section_number=fields.optional_text(
                record.get("section_number"), "section_number"
            ),
            anchor=fields.text(record["anchor"], "anchor"),
            level=fields.integer(record["level"], "level"),
            ordinal=fields.integer(record["ordinal"], "ordinal"),
            part=fields.integer(record["part"], "part"),
            text=fields.text(record["text"], "text"),
            plain_text=fields.text(record["plain_text"], "plain_text"),
            content_hash=fields.text(record["content_hash"], "content_hash"),
            token_count=fields.integer(record["token_count"], "token_count"),
            topics=fields.texts(record.get("topics", []), "topics"),
            meta=dict(fields.mapping(record.get("meta", {}), "meta")),
        )

    @classmethod
    def of_section(
        cls,
        section: SectionRecord,
        *,
        strategy: str,
        part: int,
        ordinal: int,
        blocks: tuple[Block, ...],
    ) -> ChunkRecord:
        """A chunk holding `blocks` of `section`; identity copied from the section."""
        text = "\n\n".join(block.markdown for block in blocks)
        plain = "\n".join(line for block in blocks for line in block.lines)

        return cls(
            chunk_id=ids.chunk_id(strategy, section.section_id, part),
            strategy=strategy,
            document_id=section.document_id,
            version_id=section.version_id,
            status=section.status,
            source_type=section.source_type,
            section_id=section.section_id,
            parent_section_id=section.parent_section_id,
            heading_path=section.heading_path,
            section_number=section.section_number,
            anchor=section.anchor,
            level=section.level,
            ordinal=ordinal,
            part=part,
            text=text,
            plain_text=plain,
            content_hash=content_hash(text),
            token_count=token_count(plain),
            topics=section.topics,
            meta=section.meta,
        )
