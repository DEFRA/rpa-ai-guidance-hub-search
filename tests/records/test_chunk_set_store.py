"""Staged chunk sets in the search-index bucket."""

import pytest

from app.records import chunk_set_store, chunk_sets
from tests.support import chunks as fixtures

BUCKET = "search-index"
HEADER, CHUNKS = fixtures.document([fixtures.Section("1 Overview", 1, ("Read me.",))])
CHUNK_SET = chunk_sets.ChunkSet(HEADER, fixtures.FIXTURE_STRATEGY, tuple(CHUNKS))


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
        with pytest.raises(moto_s3.exceptions.NoSuchBucket):
            await chunk_set_store.S3ChunkSetStore(moto_s3, "absent").get(CHUNK_SET.key)
