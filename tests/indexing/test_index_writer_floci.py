"""A guide version is indexed on PUT and removed on DELETE, against floci."""

import asyncio
import dataclasses
import uuid

import fastapi.testclient
import pytest

from app import config as app_config
from app.entrypoints import fastapi as entrypoint
from app.entrypoints import queue_consumers
from app.index import builds, index_schema, tantivy_index
from app.ingestion import event_queue
from app.records import chunk_sets, jsonl, models
from tests.fakes import builds as fake_builds
from tests.support import events, guides
from tests.support import sqs as sqs_support

PATH = f"/documents/{guides.DOCUMENT_ID}/versions/{guides.VERSION_ID}"
WRITER_POLL = dataclasses.replace(
    queue_consumers.INDEX_SYNC_RECEIVE, wait_time_seconds=1
)


@pytest.fixture
def stack(floci_s3, floci_sqs, floci_queues):
    s3_client, bucket = floci_s3
    indexing_url, index_sync_url = floci_queues
    index_builds = builds.IndexBuildService(
        builds.S3BuildStore(s3_client, bucket, index_schema.SCHEMA_VERSION),
        fake_builds.InMemoryActiveBuild(),
    )
    config = app_config.get_config().model_copy(
        update={
            "search_indexing_sqs_queue_url": indexing_url,
            "search_index_sync_sqs_queue_url": index_sync_url,
            "managed_docs_s3_bucket": bucket,
            "search_store_s3_bucket": bucket,
        },
    )
    return config, s3_client, floci_sqs, bucket, index_builds


async def _indexed_version(index_builds, tmp_path):
    directory = tmp_path / str(uuid.uuid4())
    await index_builds.load(directory)
    return await tantivy_index.TantivyChunkIndex(directory).indexed_version(
        guides.DOCUMENT_ID
    )


async def _wait_until_indexed(index_builds, tmp_path, expected):
    deadline = asyncio.get_running_loop().time() + 30

    while await _indexed_version(index_builds, tmp_path) != expected:
        if asyncio.get_running_loop().time() > deadline:
            msg = f"the index never held {expected}"
            raise AssertionError(msg)

        await asyncio.sleep(0.2)


def _staged_keys(s3_client, bucket):
    listing = s3_client.list_objects_v2(Bucket=bucket).get("Contents", [])
    staged = {
        chunk_sets.key(
            index_schema.CHUNK_STRATEGY, guides.DOCUMENT_ID, guides.VERSION_ID, status
        )
        for status in models.DocumentStatus
    }
    sections = {jsonl.key(guides.DOCUMENT_ID, guides.VERSION_ID)}
    return {item["Key"] for item in listing} & (staged | sections)


async def test_put_indexes_and_delete_removes_the_version(stack, tmp_path):
    config, s3_client, sqs_client, bucket, index_builds = stack
    app = entrypoint.create_app(app_config.IngestionMode.HTTP, config)
    app.dependency_overrides[app_config.get_config] = lambda: config
    app.dependency_overrides[event_queue.get_indexing_queue] = lambda: (
        event_queue.SqsIndexingQueue(sqs_client, config.search_indexing_sqs_queue_url)
    )
    client = fastapi.testclient.TestClient(app)
    s3_client.put_object(
        Bucket=bucket, Key=guides.content_key(), Body=guides.GUIDE.read_bytes()
    )
    preparer = queue_consumers.indexing_queue(
        config, sqs_client, s3_client, receive=sqs_support.QUICK_POLL
    )
    writer = queue_consumers.index_sync_queue(
        config, sqs_client, s3_client, index_builds, WRITER_POLL
    )

    async with preparer, writer:
        put = client.put(
            PATH,
            json={
                "sourceType": "guide",
                "status": "published",
                "occurredAt": "2026-11-12T10:00:00Z",
                "contentUrl": f"s3://{bucket}/{guides.content_key()}",
                "versionNumber": "1.0",
                "metadata": {"guideTitle": events.TITLE},
            },
        )
        await _wait_until_indexed(index_builds, tmp_path, guides.VERSION_ID)
        indexed_keys = _staged_keys(s3_client, bucket)

        delete = client.delete(PATH)
        await _wait_until_indexed(index_builds, tmp_path, None)
        await sqs_support.wait_for(lambda: not _staged_keys(s3_client, bucket))

    assert (put.status_code, delete.status_code) == (202, 202)
    assert indexed_keys
