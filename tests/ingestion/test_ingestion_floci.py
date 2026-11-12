"""What the CMS sends, indexed and deleted end to end against floci's S3.

Runs the real service, stores, index and S3 build store. Only the active-build
pointer is in memory, so MongoDB isn't needed.
"""

import uuid

import pytest

from app.index import builds, index_schema, tantivy_index
from app.indexing import dispatch
from app.indexing.apply import document_applier
from app.indexing.prepare import document_preparer, packed_chunking
from app.ingestion import schemas, service
from app.records import chunk_set_store, chunk_sets, ids, jsonl, models, section_store
from tests.fakes import builds as fake_builds
from tests.support import guides

NEXT_VERSION_ID = ids.VersionId(uuid.UUID("0199b8a2-0000-7000-8000-00000000f003"))


@pytest.fixture
def stack(floci_s3, mocker):
    client, bucket = floci_s3
    mocker.patch("app.common.s3.get_s3_client", return_value=client)
    sections = section_store.S3SectionStore(client, bucket)
    staged = chunk_set_store.S3ChunkSetStore(client, bucket)
    index_builds = builds.IndexBuilds(
        builds.S3BuildStore(client, bucket, index_schema.SCHEMA_VERSION),
        fake_builds.InMemoryActiveBuild(),
    )

    def preparer_for(readers):
        return document_preparer.DocumentPreparer(
            readers, sections, packed_chunking.PackedChunking(), staged
        )

    def applier_for(directory):
        return document_applier.DocumentApplier(
            staged, tantivy_index.TantivyChunkIndex(directory)
        )

    async def publish(job):
        return await document_applier.publish(job, index_builds, applier_for)

    ingestion = service.IngestionService(
        dispatcher=dispatch.InProcessDispatcher(publish),
        preparer_for=preparer_for,
        sections=sections,
        chunk_sets=staged,
    )
    return client, bucket, ingestion, index_builds


def _request(**fields):
    return schemas.IndexRequest.model_validate(
        {
            "sourceType": "guide",
            "status": "published",
            "occurredAt": "2026-11-12T10:00:00Z",
            "versionNumber": "1.0",
            "metadata": {"guideTitle": "Field Visit Guide", "owner": "Schemes team"},
            **fields,
        },
    )


def _inline(**fields):
    return _request(content={"markdown": guides.GUIDE.read_text()}, **fields)


async def _index(ingestion, request, version_id=guides.VERSION_ID):
    await ingestion.index(
        request.to_job(guides.DOCUMENT_ID, version_id), request.to_reader()
    )


async def _delete(ingestion, version_id=guides.VERSION_ID):
    await ingestion.delete(schemas.remove_job(guides.DOCUMENT_ID, version_id))


async def _indexed_version(index_builds, tmp_path):
    directory = tmp_path / str(uuid.uuid4())
    await index_builds.load(directory)
    return await tantivy_index.TantivyChunkIndex(directory).indexed_version(
        guides.DOCUMENT_ID
    )


def _keys(client, bucket, version_id=guides.VERSION_ID):
    listing = client.list_objects_v2(Bucket=bucket).get("Contents", [])
    staged = {
        chunk_sets.key(
            index_schema.CHUNK_STRATEGY, guides.DOCUMENT_ID, version_id, status
        )
        for status in models.DocumentStatus
    }
    sections = {jsonl.key(guides.DOCUMENT_ID, version_id)}
    return {item["Key"] for item in listing} & (staged | sections)


class TestIndexing:
    async def test_indexes_inline_content_with_its_title_and_metadata(
        self, stack, tmp_path
    ):
        client, bucket, ingestion, index_builds = stack

        await _index(ingestion, _inline())

        assert await _indexed_version(index_builds, tmp_path) == guides.VERSION_ID
        stored = client.get_object(
            Bucket=bucket, Key=jsonl.key(guides.DOCUMENT_ID, guides.VERSION_ID)
        )
        header, _ = jsonl.loads(stored["Body"].read().decode())
        assert header.title == "Field Visit Guide"
        assert (header.source.version_number, header.source.owner) == (
            "1.0",
            "Schemes team",
        )
        assert header.content_url.startswith("cms://")

    async def test_indexes_content_read_from_its_url(self, stack, tmp_path):
        client, bucket, ingestion, index_builds = stack
        job = guides.job(bucket=bucket)
        client.put_object(
            Bucket=bucket, Key=guides.content_key(), Body=guides.GUIDE.read_bytes()
        )

        await _index(ingestion, _request(contentUrl=job.content_url))

        assert await _indexed_version(index_builds, tmp_path) == guides.VERSION_ID


class TestDeleting:
    async def test_removes_the_indexed_version_and_its_files(self, stack, tmp_path):
        client, bucket, ingestion, index_builds = stack
        await _index(ingestion, _inline())

        await _delete(ingestion)

        assert await _indexed_version(index_builds, tmp_path) is None
        assert _keys(client, bucket) == set()

    async def test_keeps_another_indexed_version(self, stack, tmp_path):
        client, bucket, ingestion, index_builds = stack
        await _index(ingestion, _inline())
        await _index(ingestion, _inline(), NEXT_VERSION_ID)
        active = await index_builds.active_build()

        await _delete(ingestion)

        assert await _indexed_version(index_builds, tmp_path) == NEXT_VERSION_ID
        assert await index_builds.active_build() == active
        assert _keys(client, bucket) == set()
        assert _keys(client, bucket, NEXT_VERSION_ID) != set()
