"""Immutable index builds and the active-build pointer.

Tantivy lifecycle only; not part of `ChunkIndex`, so a managed backend drops it.

This is the Guide CMS index. Another source, such as GOV.UK content, ranks and
publishes on its own lifecycle, so it gets its own prefix, state record and
active index rather than a source filter on this one.

Each build is a whole index directory, uploaded once to
`guide-cms/index/v{schemaVersion}/{buildId}/` in the search-store bucket and never
changed. Build IDs are UUIDv7s, so they sort by creation time. The active build
is named by one record per schema version in search's MongoDB, swapped
conditionally, so of two writers working from the same build only the first
wins.

Everything is keyed by `index_schema.SCHEMA_VERSION`. Code only ever loads or
replaces builds of its own version, so during a deploy that changes the index
schema the old and new versions each keep their own active build.

**Transfers are bounded.** A build is one file per Tantivy segment component, and
uploading or downloading one moves every file. They go through one executor of
`TRANSFER_CONCURRENCY` threads, of its own so that search's `to_thread` calls
never wait behind them, and each file goes in a single thread (s3transfer would
otherwise start up to 10 more per file), so at most `TRANSFER_CONCURRENCY` of the
S3 client's pooled connections carry builds at once.

Not here yet: deleting old builds, retrying a lost swap, and reloading a newer
build into a running service.
"""

import asyncio
import datetime as dt
import functools
import logging
import pathlib
import uuid
from collections.abc import Callable
from concurrent import futures
from typing import TYPE_CHECKING, Annotated, NewType, Protocol

import fastapi
import pymongo
from boto3.s3 import transfer
from pymongo import errors as pymongo_errors

from app import config as app_config
from app.common import mongo, s3
from app.index import index_schema

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

logger = logging.getLogger(__name__)

BuildId = NewType("BuildId", str)

INDEX_PREFIX = "guide-cms/index"
INDEX_STATE = "index_state"

# Tantivy's lock files belong to a running writer, not to the index.
_LOCK_SUFFIX = ".lock"

TRANSFER_CONCURRENCY = 8
_TRANSFERS = futures.ThreadPoolExecutor(
    max_workers=TRANSFER_CONCURRENCY,
    thread_name_prefix="index-build-transfer",
)
_ONE_THREAD_PER_FILE = transfer.TransferConfig(use_threads=False)


class LostSwapError(Exception):
    """Another writer made a newer build active first; this one was discarded."""


class BuildStore(Protocol):
    async def upload(self, directory: pathlib.Path) -> BuildId: ...

    async def download(self, build_id: BuildId, directory: pathlib.Path) -> None: ...

    async def delete(self, build_id: BuildId) -> None: ...


class ActiveBuild(Protocol):
    async def get(self) -> BuildId | None: ...

    async def swap(self, expected: BuildId | None, new: BuildId) -> bool:
        """Conditional; False means another task swapped first."""
        ...


class IndexBuildService:
    """Loads the active build into a directory, and publishes one as active."""

    def __init__(self, store: BuildStore, active: ActiveBuild) -> None:
        self._store = store
        self._active = active

    async def active_build(self) -> BuildId | None:
        return await self._active.get()

    async def download(
        self,
        build_id: BuildId | None,
        directory: pathlib.Path,
    ) -> None:
        """Puts `build_id` in `directory`, or leaves it empty for `None`."""
        directory.mkdir(parents=True, exist_ok=True)

        if build_id is not None:
            await self._store.download(build_id, directory)

        logger.info("Downloaded index build %s", build_id or "(none)")

    async def load(self, directory: pathlib.Path) -> BuildId | None:
        """Downloads the active build into `directory`; None when there is none yet."""
        build_id = await self.active_build()
        await self.download(build_id, directory)
        return build_id

    async def publish(
        self,
        directory: pathlib.Path,
        base: BuildId | None,
    ) -> BuildId:
        """Uploads `directory` as a new build and makes it active.

        `base` is the build the directory was made from. If another build was
        made active since, the upload is deleted and `LostSwapError` raised.
        """
        build_id = await self._store.upload(directory)

        if not await self._active.swap(base, build_id):
            await self._store.delete(build_id)
            msg = f"build {base} is no longer active; discarded build {build_id}"
            raise LostSwapError(msg)

        logger.info("Published index build %s (from %s)", build_id, base or "(none)")
        return build_id


class S3BuildStore:
    """Builds under `guide-cms/index/v{schemaVersion}/{buildId}/` in the bucket."""

    def __init__(self, client: S3Client, bucket: str, schema_version: int) -> None:
        self._client = client
        self._bucket = bucket
        self._schema_version = schema_version

    async def upload(self, directory: pathlib.Path) -> BuildId:
        build_id = BuildId(str(uuid.uuid7()))
        files = [
            path
            for path in sorted(directory.iterdir())
            if path.is_file() and not path.name.endswith(_LOCK_SUFFIX)
        ]

        results = await asyncio.gather(
            *(
                _transfer(
                    self._client.upload_file,
                    str(path),
                    self._bucket,
                    self._key(build_id, path.name),
                )
                for path in files
            ),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, BaseException)]

        if errors:
            # Every transfer has finished, so nothing can re-create the prefix.
            await self.delete(build_id)
            raise errors[0]

        return build_id

    async def download(self, build_id: BuildId, directory: pathlib.Path) -> None:
        keys = await asyncio.to_thread(self._keys, build_id)

        if not keys:
            msg = f"build {build_id} has no files"
            raise FileNotFoundError(msg)

        await asyncio.gather(
            *(
                _transfer(
                    self._client.download_file,
                    self._bucket,
                    key,
                    str(directory / key.rpartition("/")[2]),
                )
                for key in keys
            ),
        )

    async def delete(self, build_id: BuildId) -> None:
        keys = await asyncio.to_thread(self._keys, build_id)

        # delete_objects takes at most 1,000 keys a call.
        for start in range(0, len(keys), 1000):
            await asyncio.to_thread(
                self._client.delete_objects,
                Bucket=self._bucket,
                Delete={
                    "Objects": [{"Key": key} for key in keys[start : start + 1000]]
                },
            )

    def _keys(self, build_id: BuildId) -> list[str]:
        paginator = self._client.get_paginator("list_objects_v2")
        pages = paginator.paginate(
            Bucket=self._bucket,
            Prefix=self._key(build_id, ""),
        )
        return [item["Key"] for page in pages for item in page.get("Contents", [])]

    def _key(self, build_id: BuildId, name: str) -> str:
        return build_key(self._schema_version, build_id, name)


class MongoActiveBuild:
    """One `index_state` record per schema version naming its active build."""

    def __init__(
        self,
        database: pymongo.asynchronous.database.AsyncDatabase,
        schema_version: int,
    ) -> None:
        self._collection = database[INDEX_STATE]
        self._state_id = state_id(schema_version)
        self._schema_version = schema_version

    async def get(self) -> BuildId | None:
        record = await self._collection.find_one({"_id": self._state_id})
        return BuildId(record["active_build_id"]) if record else None

    async def swap(self, expected: BuildId | None, new: BuildId) -> bool:
        state = {
            "active_build_id": new,
            "schema_version": self._schema_version,
            "updated_at": dt.datetime.now(dt.UTC),
        }

        if expected is None:
            try:
                await self._collection.insert_one({"_id": self._state_id, **state})
            except pymongo_errors.DuplicateKeyError:
                return False
            return True

        result = await self._collection.update_one(
            {"_id": self._state_id, "active_build_id": expected},
            {"$set": state},
        )
        return bool(result.matched_count)


def get_index_build_service(
    database: Annotated[
        pymongo.asynchronous.database.AsyncDatabase, fastapi.Depends(mongo.get_db)
    ],
    config: app_config.Config,
    client: s3.Client,
) -> IndexBuildService:
    """FastAPI dependency; also called directly with a database outside requests."""
    version = index_schema.SCHEMA_VERSION

    return IndexBuildService(
        store=S3BuildStore(
            client,
            config.search_store_s3_bucket,
            version,
        ),
        active=MongoActiveBuild(database, version),
    )


async def _transfer(move: Callable[..., None], *args: str) -> None:
    """Runs one file's `upload_file` or `download_file` on the transfer executor."""
    await asyncio.get_running_loop().run_in_executor(
        _TRANSFERS,
        functools.partial(move, *args, Config=_ONE_THREAD_PER_FILE),
    )


def build_key(schema_version: int, build_id: BuildId, name: str) -> str:
    return f"{INDEX_PREFIX}/v{schema_version}/{build_id}/{name}"


def state_id(schema_version: int) -> str:
    """The `index_state` record naming the active build of this schema version."""
    return f"guide-cms-chunks-v{schema_version}"
