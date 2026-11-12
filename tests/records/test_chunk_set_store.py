"""Staged chunk sets in the search-store bucket."""

import uuid

import pytest

from app.records import chunk_set_store, chunk_sets, ids, models
from tests.support import chunks as fixtures

BUCKET = "search-store"
SECTIONS = [fixtures.Section("1 Overview", 1, ("Read me.",))]
HEADER, CHUNKS = fixtures.document(SECTIONS)
CHUNK_SET = chunk_sets.ChunkSet(HEADER, fixtures.FIXTURE_STRATEGY, tuple(CHUNKS))
OTHER_VERSION_ID = ids.VersionId(uuid.UUID("0199b8a2-0000-7000-8000-000000000003"))


def chunk_set(**header_fields):
    header, chunks = fixtures.document(
        SECTIONS, document_header=fixtures.header(**header_fields)
    )
    return chunk_sets.ChunkSet(header, fixtures.FIXTURE_STRATEGY, tuple(chunks))


@pytest.fixture
def store(moto_s3):
    moto_s3.create_bucket(
        Bucket=BUCKET,
        CreateBucketConfiguration={"LocationConstraint": "eu-west-2"},
    )
    return chunk_set_store.S3ChunkSetStore(moto_s3, BUCKET)


class TestS3ChunkSetStore:
    async def test_writes_the_set_as_json_lines_at_its_key(self, store, moto_s3):
        key = await store.put(CHUNK_SET)

        stored = moto_s3.get_object(Bucket=BUCKET, Key=key)
        assert key == CHUNK_SET.key
        assert stored["ContentType"] == "application/x-ndjson"
        assert stored["Body"].read().decode() == chunk_sets.dumps(CHUNK_SET)

    async def test_reads_back_what_it_wrote(self, store):
        key = await store.put(CHUNK_SET)

        assert await store.get(key) == CHUNK_SET

    async def test_has_nothing_for_a_key_never_written(self, store):
        assert await store.get(CHUNK_SET.key) is None

    async def test_raises_other_errors(self, moto_s3):
        absent = chunk_set_store.S3ChunkSetStore(moto_s3, "absent")

        with pytest.raises(moto_s3.exceptions.NoSuchBucket):
            await absent.get(CHUNK_SET.key)

    async def test_deletes_the_version_at_every_status(self, store):
        published = chunk_set(status=models.DocumentStatus.PUBLISHED)
        ready = chunk_set(status=models.DocumentStatus.READY_FOR_PUBLISH)
        await store.put(published)
        await store.put(ready)

        await store.delete(
            fixtures.FIXTURE_STRATEGY, fixtures.DOCUMENT_ID, fixtures.VERSION_ID
        )

        assert await store.get(published.key) is None
        assert await store.get(ready.key) is None

    async def test_delete_leaves_other_versions_and_strategies(self, store):
        other_version = chunk_set(version_id=OTHER_VERSION_ID)
        await store.put(CHUNK_SET)
        await store.put(other_version)

        await store.delete("other", fixtures.DOCUMENT_ID, fixtures.VERSION_ID)
        await store.delete(
            fixtures.FIXTURE_STRATEGY, fixtures.DOCUMENT_ID, OTHER_VERSION_ID
        )

        assert await store.get(CHUNK_SET.key) == CHUNK_SET
        assert await store.get(other_version.key) is None

    async def test_deleting_a_version_never_staged_is_a_no_op(self, store):
        await store.delete(
            fixtures.FIXTURE_STRATEGY, fixtures.DOCUMENT_ID, fixtures.VERSION_ID
        )

    async def test_raises_other_errors_deleting(self, moto_s3):
        absent = chunk_set_store.S3ChunkSetStore(moto_s3, "absent")

        with pytest.raises(moto_s3.exceptions.NoSuchBucket):
            await absent.delete(
                fixtures.FIXTURE_STRATEGY, fixtures.DOCUMENT_ID, fixtures.VERSION_ID
            )
