"""In-memory `SectionStore`."""

from collections.abc import Sequence

from app.records import ids, models


class InMemorySectionStore:
    def __init__(self) -> None:
        self.files: dict[
            tuple[ids.DocumentId, ids.VersionId],
            tuple[models.DocumentHeader, tuple[models.SectionRecord, ...]],
        ] = {}

    async def put(
        self,
        header: models.DocumentHeader,
        sections: Sequence[models.SectionRecord],
    ) -> None:
        self.files[header.document_id, header.version_id] = (header, tuple(sections))

    async def get(
        self,
        document_id: ids.DocumentId,
        version_id: ids.VersionId,
    ) -> tuple[models.DocumentHeader, tuple[models.SectionRecord, ...]] | None:
        return self.files.get((document_id, version_id))

    async def delete(
        self,
        document_id: ids.DocumentId,
        version_id: ids.VersionId,
    ) -> None:
        self.files.pop((document_id, version_id), None)
