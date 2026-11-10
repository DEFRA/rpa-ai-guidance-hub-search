"""What each document is indexed at, so a stale job never overwrites a newer one."""

import dataclasses
from typing import Protocol

from app.records import ids, models


@dataclasses.dataclass(frozen=True)
class IndexedDocument:
    document_id: ids.DocumentId
    version_id: ids.VersionId
    status: models.DocumentStatus
    section_count: int


class IndexedDocuments(Protocol):
    async def get(self, document_id: ids.DocumentId) -> IndexedDocument | None: ...

    async def put(self, indexed: IndexedDocument) -> None: ...
