"""The chunks one strategy made of one document version at one status, staged.

Preparing a version derives its chunks; applying writes them to the index
exactly as staged, so the apply step never needs the sources, sectioning or
strategies. Sections stay canonical (`jsonl`); a chunk set is a staged copy of
what one strategy made of them, and can always be made again.

Stored as JSON Lines: a header line (the document header, the strategy and
`schema_version`), then one line per chunk in document order.
"""

import dataclasses

from app.records import ids, jsonl, models

SCHEMA_VERSION = 1

_RECORD = "record"
_DOCUMENT = "document"
_CHUNK = "chunk"
_SCHEMA_VERSION = "schema_version"
_STRATEGY = "strategy"


class MalformedChunkSetError(ValueError):
    """Records that don't form one version's chunks from one strategy."""


@dataclasses.dataclass(frozen=True)
class ChunkSet:
    header: models.DocumentHeader
    strategy: str
    chunks: tuple[models.ChunkRecord, ...]

    def __post_init__(self) -> None:
        _check(self)

    @property
    def key(self) -> str:
        return key(
            self.strategy,
            self.header.document_id,
            self.header.version_id,
            self.header.status,
        )


def key(
    strategy: str,
    document_id: ids.DocumentId,
    version_id: ids.VersionId,
    status: models.DocumentStatus,
) -> str:
    return f"staged/{strategy}/{document_id}/{version_id}/{status.value}.jsonl"


def dumps(chunk_set: ChunkSet) -> str:
    return jsonl.dump_lines(
        [
            {
                _RECORD: _DOCUMENT,
                _SCHEMA_VERSION: SCHEMA_VERSION,
                _STRATEGY: chunk_set.strategy,
                **chunk_set.header.to_record(),
            },
            *({_RECORD: _CHUNK, **chunk.to_record()} for chunk in chunk_set.chunks),
        ],
    )


def loads(text: str) -> ChunkSet:
    lines = jsonl.parse_lines(text, MalformedChunkSetError)

    if not lines or lines[0].get(_RECORD) != _DOCUMENT:
        msg = "the first line must be the document header"
        raise MalformedChunkSetError(msg)

    first, *rest = lines

    if first.get(_SCHEMA_VERSION) != SCHEMA_VERSION:
        msg = f"unsupported schema version {first.get(_SCHEMA_VERSION)!r}"
        raise MalformedChunkSetError(msg)

    if any(line.get(_RECORD) != _CHUNK for line in rest):
        msg = "every line after the header must be a chunk"
        raise MalformedChunkSetError(msg)

    try:
        return ChunkSet(
            header=models.DocumentHeader.from_record(first),
            strategy=first[_STRATEGY],
            chunks=tuple(models.ChunkRecord.from_record(line) for line in rest),
        )
    except MalformedChunkSetError:
        raise
    except (KeyError, TypeError, ValueError) as e:
        msg = f"invalid record: {e!r}"
        raise MalformedChunkSetError(msg) from e


def _check(chunk_set: ChunkSet) -> None:
    header = chunk_set.header

    if not chunk_set.strategy:
        msg = "a chunk set needs its strategy's name"
        raise MalformedChunkSetError(msg)

    for position, chunk in enumerate(chunk_set.chunks):
        if (
            chunk.document_id,
            chunk.version_id,
            chunk.status,
            chunk.source_type,
        ) != (
            header.document_id,
            header.version_id,
            header.status,
            header.source_type,
        ):
            msg = f"chunk {chunk.chunk_id} does not belong to the header's version"
            raise MalformedChunkSetError(msg)

        if chunk.strategy != chunk_set.strategy:
            msg = (
                f"chunk {chunk.chunk_id} was made by {chunk.strategy!r}, "
                f"not {chunk_set.strategy!r}"
            )
            raise MalformedChunkSetError(msg)

        if chunk.ordinal != position:
            msg = f"expected ordinal {position}, got {chunk.ordinal}"
            raise MalformedChunkSetError(msg)
