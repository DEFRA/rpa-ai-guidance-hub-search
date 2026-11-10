"""Where a document version's sections are kept; the source for every rebuild.

Shared: indexing writes sections, and enrichment (and later the RAG agent)
reads them back.
"""

from collections.abc import Sequence
from typing import Protocol

from app.records import ids, models


class SectionStore(Protocol):
    async def put(
        self,
        header: models.DocumentHeader,
        sections: Sequence[models.SectionRecord],
    ) -> None: ...

    async def get(
        self,
        document_id: ids.DocumentId,
        version_id: ids.VersionId,
    ) -> tuple[models.DocumentHeader, tuple[models.SectionRecord, ...]] | None:
        """`None` when the version was never stored."""
        ...
