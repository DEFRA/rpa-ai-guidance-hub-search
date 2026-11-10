"""Section files in the search-index bucket."""

import pytest

from app.records import jsonl, section_store
from tests.support import chunks as fixtures

BUCKET = "search-index"


@pytest.fixture
def store(moto_s3):
    moto_s3.create_bucket(
        Bucket=BUCKET,
        CreateBucketConfiguration={"LocationConstraint": "eu-west-2"},
    )
    return section_store.S3SectionStore(moto_s3, BUCKET)


DOCUMENT = fixtures.sections([fixtures.Section("1 Overview", 1)])


class TestS3SectionStore:
    async def test_writes_the_version_as_json_lines(self, store, moto_s3):
        header, sections = DOCUMENT

        await store.put(header, sections)

        stored = moto_s3.get_object(
            Bucket=BUCKET,
            Key=f"sections/{header.document_id}/{header.version_id}.jsonl",
        )
        assert stored["ContentType"] == "application/x-ndjson"
        assert stored["Body"].read().decode() == jsonl.dumps(header, sections)

    async def test_reads_back_what_it_wrote(self, store):
        header, sections = DOCUMENT
        await store.put(header, sections)

        assert await store.get(header.document_id, header.version_id) == (
            header,
            tuple(sections),
        )

    async def test_has_nothing_for_a_version_never_written(self, store):
        header, _ = DOCUMENT

        assert await store.get(header.document_id, header.version_id) is None

    async def test_raises_other_errors(self, moto_s3):
        header, _ = DOCUMENT

        with pytest.raises(moto_s3.exceptions.NoSuchBucket):
            await section_store.S3SectionStore(moto_s3, "absent").get(
                header.document_id,
                header.version_id,
            )
