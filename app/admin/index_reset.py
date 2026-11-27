"""Wipes the search index completely, so it can be rebuilt from scratch.

Clears, in this order:

1. every `index_state` record, for all schema versions. Done first, so that
   from now on every replica's poll sees "no active build" and serves empty,
   even if the bucket wipe fails part way;
2. the whole search-store bucket (builds, sections and staged chunk sets). Search
   owns the bucket outright, so a prefix added later is covered too;
3. this replica's served index, swapped to the empty one at once. Other replicas
   pick that up on their next poll (`INDEX_REFRESH_INTERVAL_SECONDS`).

The reset is idempotent: after a failure, run it again.

**Concurrency**

- *This process:* the wipe holds the writer's turn, so no batch is mid-publish
  here while it runs. It also holds the preparation gate, taken first: it waits
  for the preparations under way to be dispatched, and later ones wait for it,
  so none has staged files before the wipe and dispatched after it.
- *Cancellation:* the wipe runs as a task of its own, so a cancelled request
  stops waiting but not the wipe, which keeps the turn until its S3 calls (in a
  thread, which can't be cancelled) have finished.
- *A publish that loses the race:* a batch that started before the reset has its
  `swap(expected=old)` match nothing, so `publish` deletes its upload and raises
  `LostSwapError`. The SQS message is retried, prepared again from the managed
  bucket, and applied onto the empty index.
- *Messages already on the indexing queue* are applied after the reset; the
  applier's newer-version guard keeps an old version from overwriting a newer
  one that is re-sent.
- *Several writer replicas* were already unsafe (see `BatchingDispatcher`), and
  the reset doesn't make that worse.
"""

import asyncio
import dataclasses
import logging
import pathlib
from typing import TYPE_CHECKING

import pymongo

from app.common import tracing
from app.index import active_index, builds
from app.indexing import dispatch

if TYPE_CHECKING:
    import types_boto3_s3
    from types_boto3_s3 import type_defs as s3_type_defs

logger = logging.getLogger(__name__)

# `delete_objects` takes at most 1,000 keys a call.
_DELETE_PAGE = 1000


class BucketWipeError(Exception):
    """S3 refused to delete some objects."""


@dataclasses.dataclass(frozen=True)
class ResetResult:
    deleted_objects: int
    deleted_state_records: int


# asyncio keeps tasks only weakly; a wipe must outlive its request.
_running: set[asyncio.Task[ResetResult]] = set()


async def reset(
    database: pymongo.asynchronous.database.AsyncDatabase,
    s3_client: types_boto3_s3.S3Client,
    bucket: str,
    pending: dispatch.PendingWrites,
    directory: pathlib.Path,
    index_builds: builds.IndexBuildService,
) -> ResetResult:
    """Wipes the index's state and storage; raises if any of it could not be.

    Once called it runs to the end, even if the caller is cancelled.
    """
    task = asyncio.create_task(
        _reset(database, s3_client, bucket, pending, directory, index_builds)
    )
    _running.add(task)
    task.add_done_callback(_running.discard)

    return await asyncio.shield(task)


async def _reset(
    database: pymongo.asynchronous.database.AsyncDatabase,
    s3_client: types_boto3_s3.S3Client,
    bucket: str,
    pending: dispatch.PendingWrites,
    directory: pathlib.Path,
    index_builds: builds.IndexBuildService,
) -> ResetResult:
    # The gate first: preparations under way need the turn to finish.
    async with pending.gate.exclusive(), pending.turn:
        deleted_state_records = 0

        for name in builds.INDEX_STATE_COLLECTIONS:
            result = await database[name].delete_many({})
            deleted_state_records += result.deleted_count

        deleted_objects = await asyncio.to_thread(_empty_bucket, s3_client, bucket)

        await active_index.refresh(directory, index_builds)

    logger.warning(
        "Reset the search index: deleted %d objects and %d state records (trace %s)",
        deleted_objects,
        deleted_state_records,
        tracing.ctx_trace_id.get(None),
    )

    return ResetResult(deleted_objects, deleted_state_records)


def _empty_bucket(client: types_boto3_s3.S3Client, bucket: str) -> int:
    """Deletes every object in the bucket; raises if any was refused."""
    deleted = 0
    paginator = client.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket):
        keys: list[s3_type_defs.ObjectIdentifierTypeDef] = [
            {"Key": item["Key"]} for item in page.get("Contents", [])
        ]

        for start in range(0, len(keys), _DELETE_PAGE):
            response = client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": keys[start : start + _DELETE_PAGE], "Quiet": True},
            )
            errors = response.get("Errors", [])

            if errors:
                msg = f"could not delete {len(errors)} objects, such as {errors[0]}"
                raise BucketWipeError(msg)

            deleted += len(keys[start : start + _DELETE_PAGE])

    return deleted
