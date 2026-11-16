"""Content read from the managed-docs bucket, or sent inline."""

import pytest

from app.indexing.prepare import guide_content
from tests.support import guides

URL = guides.job().content_url


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

    async def test_raises_when_the_content_is_missing(self, bucket):
        missing = URL.replace("content.md", "other.md")

        source = guide_content.S3ContentSource(bucket)

        with pytest.raises(bucket.exceptions.NoSuchKey):
            await source.read(missing)


class TestInlineContent:
    async def test_hands_back_what_it_was_sent(self):
        source = guide_content.InlineContent("# Title\n")

        assert await source.read(URL) == "# Title\n"
