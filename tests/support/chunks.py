"""Invented fixture records; a naive stand-in for a source reader and a chunker.

`sections()` makes canonical section records, one paragraph block per part.
`document()` chunks them one chunk per block, as the `FIXTURE_STRATEGY`.
"""

import dataclasses
import datetime as dt
import re
import uuid
from collections.abc import Sequence

from app.records import ids, models, source_metadata

DOCUMENT_ID = ids.DocumentId(uuid.UUID("0199b8a2-0000-7000-8000-000000000001"))
VERSION_ID = ids.VersionId(uuid.UUID("0199b8a2-0000-7000-8000-000000000002"))
OCCURRED_AT = dt.datetime(2026, 11, 3, 9, 30, tzinfo=dt.UTC)
FIXTURE_STRATEGY = "fixture"


@dataclasses.dataclass(frozen=True)
class Section:
    rendered_heading: str
    level: int
    parts: tuple[str, ...] = ("Body text.",)


def header(
    *,
    document_id: ids.DocumentId = DOCUMENT_ID,
    version_id: ids.VersionId = VERSION_ID,
    status: models.DocumentStatus = models.DocumentStatus.PUBLISHED,
    title: str = "Claims Guidance",
) -> models.DocumentHeader:
    return models.DocumentHeader(
        document_id=document_id,
        version_id=version_id,
        status=status,
        source_type=models.SourceType.GUIDE,
        title=title,
        url=f"/guides/{document_id}",
        content_url=f"s3://managed-docs/{document_id}/{version_id}/content.md",
        occurred_at=OCCURRED_AT,
        source=source_metadata.GuideMetadata.empty(),
    )


def paragraph(text: str) -> models.Block:
    return models.Block(models.BlockKind.PARAGRAPH, text, (text,))


def sections(
    specs: Sequence[Section],
    document_header: models.DocumentHeader | None = None,
) -> tuple[models.DocumentHeader, list[models.SectionRecord]]:
    document_header = document_header or header()
    paths, numbers = _heading_paths(specs)
    section_ids = ids.section_ids(
        document_header.source_type,
        document_header.document_id,
        paths,
    )
    parents = _parents(specs, section_ids)

    return document_header, [
        models.SectionRecord(
            section_id=section_ids[index],
            document_id=document_header.document_id,
            version_id=document_header.version_id,
            status=document_header.status,
            source_type=document_header.source_type,
            parent_section_id=parents[index],
            heading_path=paths[index],
            section_number=numbers[index],
            anchor=_slug(spec.rendered_heading),
            level=spec.level,
            ordinal=index,
            blocks=tuple(paragraph(text) for text in spec.parts),
        )
        for index, spec in enumerate(specs)
    ]


def chunks_of(
    section_records: Sequence[models.SectionRecord],
) -> list[models.ChunkRecord]:
    chunks: list[models.ChunkRecord] = []

    for section in section_records:
        for part, block in enumerate(section.blocks):
            chunks.append(
                models.ChunkRecord.of_section(
                    section,
                    strategy=FIXTURE_STRATEGY,
                    part=part,
                    ordinal=len(chunks),
                    blocks=(block,),
                ),
            )

    return chunks


def document(
    specs: Sequence[Section],
    document_header: models.DocumentHeader | None = None,
) -> tuple[models.DocumentHeader, list[models.ChunkRecord]]:
    document_header, section_records = sections(specs, document_header)
    return document_header, chunks_of(section_records)


def by_heading(chunks: Sequence[models.ChunkRecord]) -> dict[tuple[str, ...], str]:
    return {chunk.heading_path: chunk.chunk_id for chunk in chunks if chunk.part == 0}


def _heading_paths(
    specs: Sequence[Section],
) -> tuple[list[tuple[str, ...]], list[str | None]]:
    stack: list[str] = []
    paths: list[tuple[str, ...]] = []
    numbers: list[str | None] = []

    for spec in specs:
        number, text = ids.split_section_number(spec.rendered_heading)
        del stack[spec.level - 1 :]
        stack.append(text)
        paths.append(tuple(stack))
        numbers.append(number)

    return paths, numbers


def _parents(
    specs: Sequence[Section],
    section_ids: Sequence[ids.SectionId],
) -> list[ids.SectionId | None]:
    stack: list[ids.SectionId] = []
    parents: list[ids.SectionId | None] = []

    for spec, section_id in zip(specs, section_ids, strict=True):
        del stack[spec.level - 1 :]
        parents.append(stack[-1] if stack else None)
        stack.append(section_id)

    return parents


def _slug(rendered_heading: str) -> str:
    return re.sub(r"[^\w\- ]", "", rendered_heading.lower()).replace(" ", "-")
