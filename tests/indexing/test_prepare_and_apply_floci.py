"""The fixture guide prepared and applied end to end against floci's S3 and a real index."""

import pytest
import tantivy

from app.index import tantivy_index, tantivy_schema
from app.indexing.apply import document_applier
from app.indexing.prepare import (
    document_preparer,
    guide_content,
    guide_markdown,
    guide_source,
    packed_chunking,
)
from app.records import chunk_set_store, chunk_sets, jsonl, models, section_store
from tests.support import guides


@pytest.fixture
def run(floci_s3, tmp_path):
    client, bucket = floci_s3

    client.put_object(
        Bucket=bucket,
        Key=guides.content_key(),
        Body=guides.GUIDE.read_bytes(),
    )

    source = guide_content.S3ContentSource(client)
    staged = chunk_set_store.S3ChunkSetStore(client, bucket)
    preparer = document_preparer.DocumentPreparer(
        readers={
            models.SourceType.GUIDE: guide_source.GuideSource(
                source,
                source,
                guide_markdown.GuideMarkdownParser(),
            ),
        },
        sections=section_store.S3SectionStore(client, bucket),
        strategy=packed_chunking.PackedChunking(),
        chunk_sets=staged,
    )

    applier = document_applier.DocumentApplier(
        staged,
        tantivy_index.TantivyChunkIndex(tmp_path),
    )

    async def index():
        await applier.apply(await preparer.prepare(guides.job(bucket=bucket)))

    return client, bucket, index


def _read(client, bucket, key):
    return client.get_object(Bucket=bucket, Key=key)["Body"].read().decode()


def _stored(client, bucket):
    return _read(client, bucket, jsonl.key(guides.DOCUMENT_ID, guides.VERSION_ID))


def _staged(client, bucket):
    job = guides.job()
    key = chunk_sets.key("packed-800", job.document_id, job.version_id, job.status)

    return _read(client, bucket, key)


def _entry_count(directory):
    index = tantivy.Index.open(str(directory))
    tantivy_schema.register_tokenizers(index)

    return index.searcher().num_docs


class TestIndexingTheFixtureGuide:
    async def test_writes_the_golden_sections_and_chunks_and_indexes_them(
        self,
        run,
        tmp_path,
    ):
        client, bucket, index = run

        await index()

        _, golden_sections = jsonl.loads(guides.GOLDEN_SECTIONS.read_text())
        golden_chunks = guides.GOLDEN_CHUNKS.read_text().splitlines()
        header, sections = jsonl.loads(_stored(client, bucket))

        assert sections == golden_sections
        assert header.content_url == guides.job(bucket=bucket).content_url
        assert len(chunk_sets.loads(_staged(client, bucket)).chunks) == len(
            golden_chunks,
        )
        assert _entry_count(tmp_path) == len(golden_chunks) + 1

    async def test_re_running_changes_nothing(self, run, tmp_path):
        client, bucket, index = run

        await index()

        before = (
            _stored(client, bucket),
            _staged(client, bucket),
            _entry_count(tmp_path),
        )

        await index()

        assert (
            _stored(client, bucket),
            _staged(client, bucket),
            _entry_count(tmp_path),
        ) == before
