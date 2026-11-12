"""Applying a job writes its staged chunks, and nothing else, to the index."""

import dataclasses
import uuid

import pytest
import tantivy

from app import config as app_config
from app.index import (
    builds,
    chunk_index,
    index_schema,
    tantivy_index,
    tantivy_schema,
)
from app.indexing import jobs
from app.indexing.apply import document_applier
from app.records import chunk_set_store, chunk_sets, ids, models
from tests.fakes import builds as fake_builds
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


SECOND_DOCUMENT_ID = ids.DocumentId(uuid.UUID("0199b8a2-0000-7000-8000-00000000f0bb"))


@pytest.fixture
def index_builds():
    return builds.IndexBuildService(
        fake_builds.InMemoryBuildStore(),
        fake_builds.InMemoryActiveBuild(),
    )


@pytest.fixture
def applier_for(staged):
    def build(directory):
        return document_applier.DocumentApplier(
            staged,
            tantivy_index.TantivyChunkIndex(directory),
            STRATEGY,
        )

    return build


def _documents_in(directory):
    index = tantivy.Index.open(str(directory))
    tantivy_schema.register_tokenizers(index)
    searcher = index.searcher()
    hits = searcher.search(tantivy.Query.all_query(), 100).hits
    return sorted({searcher.doc(address)["document_id"][0] for _, address in hits})


async def _stage(staged, text, document_id=fixtures.DOCUMENT_ID):
    header, chunks = fixtures.document(
        [fixtures.Section("1 Overview", 1, (text,))],
        fixtures.header(document_id=document_id),
    )
    chunk_set = chunk_sets.ChunkSet(header, STRATEGY, tuple(chunks))
    await staged.put(chunk_set)
    return _job(chunk_set)


class TestPublish:
    async def test_publishes_the_first_build(
        self,
        index_builds,
        applier_for,
        staged,
        tmp_path,
    ):
        job = await _stage(staged, "Hold a holding number.")

        build_id = await document_applier.publish(job, index_builds, applier_for)

        assert await index_builds.active_build() == build_id
        assert await index_builds.load(tmp_path / "check") == build_id
        assert _documents_in(tmp_path / "check") == [str(fixtures.DOCUMENT_ID)]

    async def test_builds_on_the_active_build(
        self,
        index_builds,
        applier_for,
        staged,
        tmp_path,
    ):
        first_job = await _stage(staged, "Hold a holding number.")
        second_job = await _stage(staged, "Licences.", SECOND_DOCUMENT_ID)
        first = await document_applier.publish(first_job, index_builds, applier_for)

        second = await document_applier.publish(second_job, index_builds, applier_for)

        assert second != first
        await index_builds.load(tmp_path / "check")
        assert _documents_in(tmp_path / "check") == sorted(
            [str(fixtures.DOCUMENT_ID), str(SECOND_DOCUMENT_ID)],
        )

    async def test_publishes_nothing_when_applying_fails(
        self,
        index_builds,
        applier_for,
    ):
        job = _job(_staged("Never staged."))

        with pytest.raises(document_applier.ChunkSetNotFoundError):
            await document_applier.publish(
                job,
                index_builds,
                applier_for,
            )

        assert await index_builds.active_build() is None

    async def test_publishes_from_config(self, mocker):
        database = mocker.sentinel.database
        mocker.patch("app.common.mongo.get_mongo_client", mocker.AsyncMock())
        mocker.patch("app.common.mongo.get_db", mocker.AsyncMock(return_value=database))
        from_config = mocker.patch("app.index.builds.get_index_build_service")
        publish = mocker.patch(
            "app.indexing.apply.document_applier.publish",
            mocker.AsyncMock(return_value="build"),
        )
        job = _job(_staged("Anything."))

        assert await document_applier.publish_from_config(job) == "build"

        from_config.assert_called_once_with(database)
        publish.assert_awaited_once_with(
            job,
            from_config.return_value,
            document_applier.from_config,
        )


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
