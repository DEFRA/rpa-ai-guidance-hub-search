"""The index's single writer: publishes each batch of jobs together."""

from collections.abc import Sequence

from app.index import index_schema
from app.indexing import jobs
from app.indexing.apply import document_applier
from app.records import chunk_set_store, section_store


class IndexWriter:
    def __init__(
        self,
        publish_all: document_applier.PublishAll,
        sections: section_store.SectionStore,
        chunk_sets: chunk_set_store.ChunkSetStore,
        strategy: str = index_schema.CHUNK_STRATEGY,
    ) -> None:
        self._publish_all = publish_all
        self._sections = sections
        self._chunk_sets = chunk_sets
        self._strategy = strategy

    async def write(self, batch: Sequence[jobs.WriteJob]) -> list[Exception | None]:
        """What each job raised, in order, or None; raises if the publish failed."""
        published = await self._publish_all(batch)
        outcomes: list[Exception | None] = []

        for job, raised in zip(batch, published.errors, strict=True):
            outcomes.append(raised or await self._after(job))

        return outcomes

    async def _after(self, job: jobs.WriteJob) -> Exception | None:
        """Deletes a removed version's staged files."""
        if not isinstance(job, jobs.RemoveJob):
            return None

        try:
            await self._sections.delete(job.document_id, job.version_id)
            await self._chunk_sets.delete(
                self._strategy, job.document_id, job.version_id
            )
        except Exception as error:  # fails only this job
            return error

        return None
