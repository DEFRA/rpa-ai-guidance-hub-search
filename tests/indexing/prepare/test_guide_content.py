"""Content and section IDs read from the managed-docs bucket."""

import json

import pytest

from app.indexing.prepare import guide_content
from app.records import ids
from tests.support import guides

URL = guides.job().content_url
SECTIONS_KEY = f"{guides.DOCUMENT_ID}/{guides.VERSION_ID}/sections.json"


@pytest.fixture
def bucket(moto_s3):
    moto_s3.create_bucket(
        Bucket=guides.BUCKET,
        CreateBucketConfiguration={"LocationConstraint": "eu-west-2"},
    )
    moto_s3.put_object(
        Bucket=guides.BUCKET,
        Key=guides.content_key(),
        Body="# Title\n\n## 1 Ünïcode\n".encode(),
    )

    return moto_s3


class TestParseS3Url:
    def test_splits_bucket_and_key(self):
        assert guide_content.parse_s3_url("s3://bucket/a/b.md") == ("bucket", "a/b.md")

    @pytest.mark.parametrize(
        "url",
        ["https://bucket/a", "s3://bucket", "s3://bucket/", "s3:///a"],
    )
    def test_refuses_anything_else(self, url):
        with pytest.raises(ValueError, match="s3://bucket/key"):
            guide_content.parse_s3_url(url)


class TestS3ContentSource:
    async def test_reads_content_as_utf8(self, bucket):
        assert await guide_content.S3ContentSource(bucket).read(URL) == (
            "# Title\n\n## 1 Ünïcode\n"
        )

    async def test_has_no_section_ids_without_a_sections_file(self, bucket):
        assert await guide_content.S3ContentSource(bucket).read_section_ids(URL) is None

    async def test_reads_section_ids_from_beside_the_content(self, bucket):
        bucket.put_object(
            Bucket=guides.BUCKET,
            Key=SECTIONS_KEY,
            Body=json.dumps({"1-overview": "0199b8a2-0000-7000-8000-0000000000e1"}),
        )

        assert await guide_content.S3ContentSource(bucket).read_section_ids(URL) == {
            "1-overview": "0199b8a2-0000-7000-8000-0000000000e1",
        }

    @pytest.mark.parametrize("body", ['["a"]', '{"1-overview": 7}', '{"a": ""}'])
    async def test_refuses_a_sections_file_that_is_not_a_map(self, bucket, body):
        bucket.put_object(Bucket=guides.BUCKET, Key=SECTIONS_KEY, Body=body)

        source = guide_content.S3ContentSource(bucket)

        with pytest.raises(ValueError, match="sections.json"):
            await source.read_section_ids(URL)

    async def test_raises_when_the_content_is_missing(self, bucket):
        missing = URL.replace("content.md", "other.md")

        source = guide_content.S3ContentSource(bucket)

        with pytest.raises(bucket.exceptions.NoSuchKey):
            await source.read(missing)

    async def test_raises_other_errors_reading_section_ids(self, moto_s3):
        source = guide_content.S3ContentSource(moto_s3)

        with pytest.raises(moto_s3.exceptions.NoSuchBucket):
            await source.read_section_ids(URL)


class TestInlineContent:
    async def test_hands_back_what_it_was_sent(self):
        section_ids = {"1-overview": ids.SectionId("section-1")}

        source = guide_content.InlineContent("# Title\n", section_ids)

        assert await source.read(URL) == "# Title\n"
        assert await source.read_section_ids(URL) == section_ids

    async def test_has_no_section_ids_unless_sent_some(self):
        source = guide_content.InlineContent("# Title\n")

        assert await source.read_section_ids(URL) is None
