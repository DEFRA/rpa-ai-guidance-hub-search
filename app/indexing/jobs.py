"""The two jobs of indexing one document version, whatever asked for it.

`IndexJob` asks for a version to be prepared: read, sectioned and chunked.
Preparing it produces an `ApplyJob`, which asks for the staged chunks to be
written to the index. The two run as separate steps so that, later, any number
of workers can prepare while a single writer applies.
"""

import dataclasses
import datetime as dt

from app.records import ids, models


@dataclasses.dataclass(frozen=True)
class IndexJob:
    """Index this version of the document at this status.

    `source_type` picks the reader. `content_url` is where that reader finds
    this exact version, e.g. `s3://<bucket>/<documentId>/<versionId>/content.md`
    for a guide. `occurred_at` is when the version reached `status`.

    `version_id` is None when the source has no version ids of its own: its
    reader mints one from what it reads (`ids.minted_id`), so it can't be known
    before reading. A guide's always comes from the CMS. The prepared
    `ApplyJob` always carries the version's id.
    """

    source_type: models.SourceType
    document_id: ids.DocumentId
    version_id: ids.VersionId | None
    status: models.DocumentStatus
    content_url: str
    occurred_at: dt.datetime

    def __post_init__(self) -> None:
        if self.occurred_at.utcoffset() is None:
            msg = "occurred_at must be timezone-aware"
            raise ValueError(msg)


@dataclasses.dataclass(frozen=True)
class ApplyJob:
    """Write this version's staged chunk set to the index.

    Small on purpose: it names the chunk set (`chunk_set_key` in the
    `ChunkSetStore`) rather than carrying the chunks. `strategy` is the
    strategy that made them, so the apply step can refuse chunks its index
    wasn't built for. `occurred_at` is copied from the `IndexJob`.
    """

    document_id: ids.DocumentId
    version_id: ids.VersionId
    status: models.DocumentStatus
    occurred_at: dt.datetime
    strategy: str
    chunk_set_key: str

    def __post_init__(self) -> None:
        if self.occurred_at.utcoffset() is None:
            msg = "occurred_at must be timezone-aware"
            raise ValueError(msg)
