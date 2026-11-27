"""Index a guide, reset, and index it again: against floci's S3, MongoDB and a real index."""

import asyncio

import pytest
import tantivy

from app.admin import index_reset
from app.index import active_index, builds, index_schema, tantivy_index, tantivy_schema
from app.indexing import dispatch
from app.indexing.apply import document_applier
from app.indexing.prepare import (
    document_preparer,
    guide_content,
    guide_markdown,
    guide_source,
    packed_chunking,
)
from app.records import chunk_set_store, models, section_store
from tests.support import guides


@pytest.fixture(autouse=True)
def unloaded(monkeypatch):
    monkeypatch.setattr(active_index, "_index", None)
    monkeypatch.setattr(active_index, "_build_id", None)
    monkeypatch.setattr(active_index, "_refreshing", asyncio.Lock())


def _entry_count(directory):
    index = tantivy.Index.open(str(directory))
    tantivy_schema.register_tokenizers(index)

    return index.searcher().num_docs


class TestResetAgainstFloci:
    async def test_search_is_empty_after_a_reset_and_finds_the_guide_again_after_indexing(
        self, floci_s3, mongo_database, tmp_path
    ):
        client, bucket = floci_s3

        def put_content():
            # Production reads content from the managed-docs bucket, which the reset
            # leaves alone; here one bucket does both jobs, so it is put back.
            client.put_object(
                Bucket=bucket, Key=guides.content_key(), Body=guides.GUIDE.read_bytes()
            )

        put_content()
        await mongo_database[builds.INDEX_STATE].delete_many({})
        version = index_schema.SCHEMA_VERSION
        index_builds = builds.IndexBuildService(
            builds.S3BuildStore(client, bucket, version),
            builds.MongoActiveBuild(mongo_database, version),
        )
        staged = chunk_set_store.S3ChunkSetStore(client, bucket)
        preparer = document_preparer.DocumentPreparer(
            readers={
                models.SourceType.GUIDE: guide_source.GuideSource(
                    guide_content.S3ContentSource(client),
                    guide_markdown.GuideMarkdownParser(),
                ),
            },
            sections=section_store.S3SectionStore(client, bucket),
            strategy=packed_chunking.PackedChunking(),
            chunk_sets=staged,
        )
        serve = tmp_path / "serve"

        async def index() -> builds.BuildId:
            work = tmp_path / "work"
            base = await index_builds.load(work)
            applier = document_applier.DocumentApplier(
                staged, tantivy_index.TantivyChunkIndex(work)
            )
            await applier.apply(await preparer.prepare(guides.job(bucket=bucket)))
            build_id = await index_builds.publish(work, base)
            await active_index.refresh(serve, index_builds)

            return build_id

        build_id = await index()

        assert _entry_count(serve / build_id) > 0

        await index_reset.reset(
            mongo_database,
            client,
            bucket,
            dispatch.PendingWrites(),
            serve,
            index_builds,
        )

        assert active_index.loaded_build_id() is None
        assert client.list_objects_v2(Bucket=bucket).get("Contents") is None

        put_content()

        again = await index()

        assert again != build_id
        assert _entry_count(serve / again) > 0
