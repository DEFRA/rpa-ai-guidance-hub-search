"""Applying a job writes its staged chunks, and nothing else, to the index."""

import dataclasses
import uuid

import pytest
import tantivy

from app import config as app_config
from app.index import chunk_index, index_schema, tantivy_schema
from app.indexing import jobs
from app.indexing.apply import document_applier
from app.records import chunk_set_store, chunk_sets, ids, models
from tests.fakes import chunk_index as fake_chunk_index
from tests.fakes import chunk_set_store as fake_chunk_set_store
from tests.support import chunks as fixtures

NEXT_VERSION_ID = ids.VersionId(uuid.UUID("0199b8a2-0000-7000-8000-00000000f003"))
EVERYONE = frozenset(models.DocumentStatus)
STRATEGY = fixtures.FIXTURE_STRATEGY


def _staged(text, version_id=fixtures.VERSION_ID):
    header, chunks = fixtures.document(
        [fixtures.Section("1 Overview", 1, (text,))],
        fixtures.header(version_id=version_id),
    )

    return chunk_sets.ChunkSet(header, STRATEGY, tuple(chunks))


def _job(chunk_set):
    header = chunk_set.header
    return jobs.ApplyJob(
        document_id=header.document_id,
        version_id=header.version_id,
        status=header.status,
        occurred_at=header.occurred_at,
        strategy=chunk_set.strategy,
        chunk_set_key=chunk_set.key,
    )


@pytest.fixture
def staged():
    return fake_chunk_set_store.InMemoryChunkSetStore()


@pytest.fixture
def index():
    return fake_chunk_index.InMemoryChunkIndex()


@pytest.fixture
def applier(staged, index):
    return document_applier.DocumentApplier(staged, index, STRATEGY)


async def _found(index, text):
    query = chunk_index.ChunkQuery(text=text, statuses=EVERYONE)
    return [hit.version_id for hit in (await index.search(query)).hits]


class TestApply:
    async def test_writes_the_staged_chunks(self, applier, staged, index):
        key = await staged.put(_staged("Hold a holding number."))

        await applier.apply(_job(staged.sets[key]))

        assert await _found(index, "holding number") == [fixtures.VERSION_ID]

    async def test_a_newer_version_replaces_the_old_one(self, applier, staged, index):
        old = _staged("Hold a holding number.")
        new = _staged("Rewritten for orchards.", NEXT_VERSION_ID)
        await staged.put(old)
        await staged.put(new)
        await applier.apply(_job(old))

        await applier.apply(_job(new))

        assert await _found(index, "holding") == []
        assert await _found(index, "orchards") == [NEXT_VERSION_ID]

    async def test_applying_again_changes_nothing(self, applier, staged, index):
        chunk_set = _staged("Hold a holding number.")
        await staged.put(chunk_set)
        await applier.apply(_job(chunk_set))

        await applier.apply(_job(chunk_set))

        assert await _found(index, "holding") == [fixtures.VERSION_ID]

    async def test_refuses_another_strategys_chunks(self, staged, index):
        chunk_set = _staged("Hold a holding number.")
        await staged.put(chunk_set)
        applier = document_applier.DocumentApplier(staged, index, "packed-400")

        job = _job(chunk_set)

        with pytest.raises(document_applier.ChunkSetMismatchError, match="packed-400"):
            await applier.apply(job)

        assert await _found(index, "holding") == []

    async def test_refuses_a_chunk_set_that_is_not_the_jobs(
        self,
        applier,
        staged,
        index,
    ):
        chunk_set = _staged("Hold a holding number.")
        await staged.put(chunk_set)
        job = dataclasses.replace(_job(chunk_set), version_id=NEXT_VERSION_ID)

        with pytest.raises(document_applier.ChunkSetMismatchError):
            await applier.apply(job)

        assert await _found(index, "holding") == []

    async def test_raises_when_nothing_is_staged(self, applier):
        job = _job(_staged("Never staged."))

        with pytest.raises(document_applier.ChunkSetNotFoundError):
            await applier.apply(job)


class TestFromConfig:
    async def test_applies_a_chunk_set_staged_in_the_configured_bucket(
        self,
        mocker,
        moto_s3,
        tmp_path,
    ):
        mocker.patch("app.common.s3.get_s3_client", return_value=moto_s3)
        bucket = app_config.get_config().search_store_s3_bucket
        moto_s3.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": "eu-west-2"},
        )
        fixture_set = _staged("Hold a holding number.")
        chunk_set = dataclasses.replace(
            fixture_set,
            strategy=index_schema.CHUNK_STRATEGY,
            chunks=tuple(
                dataclasses.replace(chunk, strategy=index_schema.CHUNK_STRATEGY)
                for chunk in fixture_set.chunks
            ),
        )
        await chunk_set_store.S3ChunkSetStore(moto_s3, bucket).put(chunk_set)

        await document_applier.from_config(tmp_path).apply(_job(chunk_set))

        index = tantivy.Index.open(str(tmp_path))
        tantivy_schema.register_tokenizers(index)
        assert index.searcher().num_docs == len(chunk_set.chunks) + 1
