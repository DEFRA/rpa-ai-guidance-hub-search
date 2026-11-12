"""Document, version, section and chunk ids.

Document and version ids are UUIDs, whatever the source; each source decides
where they come from. A source that has its own UUIDs keeps them: the CMS mints
UUIDv7s for guides and their versions (`is_uuid7`). A source without them mints
its own with `minted_id`, deterministically (UUIDv5) under a namespace of its
own, from its native identity: a GOV.UK content item's document from its
`content_id` and locale, a version from the document and its content hash. The
UUID version then says where an id came from, and two sources never collide.

Section ids hash: source type, document id, the heading path without section
numbers (normalised), and an occurrence index for duplicate headings. Chunk ids
hash the chunking strategy, the section id and the part index. Version, number,
anchor and position are left out, so renumbering never changes an id; renaming
a heading does.
"""

import hashlib
import json
import re
import unicodedata
import uuid
from collections.abc import Sequence
from typing import NewType

DocumentId = NewType("DocumentId", uuid.UUID)
VersionId = NewType("VersionId", uuid.UUID)
SectionId = NewType("SectionId", str)
ChunkId = NewType("ChunkId", str)

_SECTION_TAG = "section/v1"
_CHUNK_TAG = "chunk/v1"

# "4.2.1 Text" or "A.1 Text"; "Annex A" is author text, not a number. Any
# whitespace may separate them (tab, non-breaking space), so renumbering never
# leaks the number into the heading that gets hashed. The text starts at a
# non-space so the whitespace run and the text can't overlap, which keeps
# matching linear however the input is split.
_NUMBERED_HEADING = re.compile(
    r"(?P<number>\d+(?:\.\d+)*|[A-Z]+(?:\.\d+)+)\s+(?P<text>\S.*)"
)


_TIME_ORDERED = 7


def is_older(version_id: VersionId, than: VersionId) -> bool:
    """Only UUIDv7s sort by when they were minted; other ids are never older."""
    return version_id.version == than.version == _TIME_ORDERED and version_id < than


def split_section_number(rendered_heading: str) -> tuple[str | None, str]:
    """`"4.2.1 Evidence"` -> `("4.2.1", "Evidence")`; `"Annex A"` -> `(None, "Annex A")`.

    Can't tell an appendix titled `"2026 changes"` from section 2026; callers that
    know a heading is an appendix shouldn't split it.
    """
    match = _NUMBERED_HEADING.fullmatch(rendered_heading.strip())

    if match is None:
        return None, rendered_heading.strip()

    return match["number"], match["text"]


def normalise_heading(text: str) -> str:
    """NFC, single-spaced, trimmed, casefolded."""
    return " ".join(unicodedata.normalize("NFC", text).split()).casefold()


def is_uuid7(value: uuid.UUID) -> bool:
    """A time-ordered UUID, as the CMS mints for guides and their versions."""
    return value.version == 7  # noqa: PLR2004


def minted_id(namespace: uuid.UUID, *parts: str) -> uuid.UUID:
    """The same UUIDv5 for the same parts, under a source's own namespace.

    For sources with no UUIDs of their own. Each defines one namespace (a
    UUID picked once and never changed) beside its reader.
    """
    if not parts:
        msg = "a minted id needs at least one part"
        raise ValueError(msg)

    return uuid.uuid5(namespace, json.dumps(list(parts), ensure_ascii=False))


def section_id(
    source_type: str,
    document_id: DocumentId,
    heading_path: Sequence[str],
    occurrence: int = 0,
) -> SectionId:
    if occurrence < 0:
        msg = f"occurrence must not be negative, got {occurrence}"
        raise ValueError(msg)

    return SectionId(
        _digest(
            [
                _SECTION_TAG,
                source_type,
                str(document_id),
                [normalise_heading(heading) for heading in heading_path],
                occurrence,
            ],
        ),
    )


def section_ids(
    source_type: str,
    document_id: DocumentId,
    heading_paths: Sequence[Sequence[str]],
) -> list[SectionId]:
    """Ids for a document's sections in order, counting duplicate paths."""
    seen: dict[tuple[str, ...], int] = {}
    result: list[SectionId] = []

    for heading_path in heading_paths:
        normalised = tuple(normalise_heading(heading) for heading in heading_path)
        occurrence = seen.get(normalised, 0)
        seen[normalised] = occurrence + 1
        result.append(section_id(source_type, document_id, heading_path, occurrence))

    return result


def chunk_id(strategy: str, section: SectionId, part: int) -> ChunkId:
    """Strategies chunk the same section differently, so the strategy is in the id."""
    if part < 0:
        msg = f"part must not be negative, got {part}"
        raise ValueError(msg)

    if not strategy:
        msg = "strategy must be named"
        raise ValueError(msg)

    return ChunkId(_digest([_CHUNK_TAG, strategy, section, part]))


def _digest(parts: list[object]) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
