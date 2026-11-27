"""The index writer publishes each batch together, and reports each job's outcome."""

import pytest

from app.indexing.apply import document_applier, index_writer
from tests.fakes import chunk_set_store as fake_chunk_set_store
from tests.fakes import section_store as fake_section_store
from tests.support import events


class FakePublish:
    """Stands in for `publish_all`: records each batch, answers with `errors`."""

    def __init__(self, errors=None, raises=None):
        self.batches = []
        self._errors = errors
        self._raises = raises

    async def __call__(self, batch):
        self.batches.append(list(batch))

        if self._raises is not None:
            raise self._raises

        errors = self._errors or [None] * len(batch)
        return document_applier.Published(build_id=None, errors=tuple(errors))


class FailingDelete(fake_chunk_set_store.InMemoryChunkSetStore):
    async def delete(self, *_args):
        msg = "S3 refused the delete"
        raise RuntimeError(msg)


def _writer(publish, chunk_sets=None, sections=None):
    return index_writer.IndexWriter(
        publish_all=publish,
        sections=sections or fake_section_store.InMemorySectionStore(),
        chunk_sets=chunk_sets or fake_chunk_set_store.InMemoryChunkSetStore(),
    )


class TestPublishing:
    async def test_publishes_the_whole_batch_together_in_order(self):
        publish = FakePublish()
        batch = [events.apply_job("k/1"), events.remove_job(), events.apply_job("k/2")]

        outcomes = await _writer(publish).write(batch)

        assert publish.batches == [batch]
        assert outcomes == [None, None, None]

    async def test_reports_what_each_job_raised(self):
        missing = document_applier.ChunkSetNotFoundError("nothing staged")
        publish = FakePublish(errors=[None, missing])

        outcomes = await _writer(publish).write(
            [events.apply_job("k/1"), events.apply_job("k/2")]
        )

        assert outcomes == [None, missing]

    async def test_raises_what_failed_the_whole_publish(self):
        error = document_applier.IndexWriteError("write failed")

        with pytest.raises(document_applier.IndexWriteError):
            await _writer(FakePublish(raises=error)).write([events.apply_job()])


class TestRemoval:
    async def test_deletes_the_removed_version_s_staged_files(self, mocker):
        sections = fake_section_store.InMemorySectionStore()
        chunk_sets = fake_chunk_set_store.InMemoryChunkSetStore()
        delete_sections = mocker.spy(sections, "delete")
        delete_chunks = mocker.spy(chunk_sets, "delete")
        job = events.remove_job()

        await _writer(FakePublish(), chunk_sets, sections).write([job])

        delete_sections.assert_awaited_once_with(job.document_id, job.version_id)
        delete_chunks.assert_awaited_once_with(
            mocker.ANY, job.document_id, job.version_id
        )

    async def test_keeps_the_files_of_a_removal_that_failed(self, mocker):
        chunk_sets = fake_chunk_set_store.InMemoryChunkSetStore()
        delete = mocker.spy(chunk_sets, "delete")
        error = RuntimeError("write failed")

        outcomes = await _writer(FakePublish(errors=[error]), chunk_sets).write(
            [events.remove_job()]
        )

        assert outcomes == [error]
        delete.assert_not_awaited()

    async def test_fails_only_the_removal_whose_cleanup_failed(self):
        outcomes = await _writer(FakePublish(), FailingDelete()).write(
            [events.remove_job(), events.apply_job()]
        )

        assert isinstance(outcomes[0], RuntimeError)
        assert outcomes[1] is None
