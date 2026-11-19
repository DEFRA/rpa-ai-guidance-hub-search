"""The chunk index this process serves: the active build, kept up to date.

The active build is loaded at startup, then `poll` checks the active-build
pointer every `INDEX_REFRESH_INTERVAL_SECONDS` and swaps in a newer build, so
every replica picks up a publish, whichever replica made it.

Each build is downloaded into its own subdirectory of `search_index_directory`.
With no active build yet, the process serves an empty index. A swap only rebinds
the served index, so a search that already has the old one finishes on it; the
replaced build's directory is kept until the next swap for the same reason.
"""

import asyncio
import logging
import pathlib
import shutil

from app.index import builds, chunk_index, index_schema, tantivy_index

logger = logging.getLogger(__name__)

_EMPTY = "empty"

_index: chunk_index.ChunkIndex | None = None
_build_id: builds.BuildId | None = None
_refreshing = asyncio.Lock()


async def load(
    directory: pathlib.Path,
    index_builds: builds.IndexBuildService,
) -> builds.BuildId | None:
    """Loads the active build at startup."""
    return await refresh(directory, index_builds)


async def refresh(
    directory: pathlib.Path,
    index_builds: builds.IndexBuildService,
) -> builds.BuildId | None:
    """Serves the active build, if it isn't already the one served."""
    global _index, _build_id

    async with _refreshing:
        build_id = await index_builds.active_build()

        if _index is not None and build_id == _build_id:
            return build_id

        target = directory / (build_id or _EMPTY)

        # Only ever the subdirectory named here, never `directory` itself.
        if target.exists():
            shutil.rmtree(target)

        await index_builds.download(build_id, target)
        index = tantivy_index.TantivyChunkIndex(target)

        # At startup nothing is replaced, so earlier runs' builds all go.
        keep = {target.name}

        if _index is not None:
            keep.add(_build_id or _EMPTY)

        logger.info(
            "Serving index schema v%d build %s (was %s)",
            index_schema.SCHEMA_VERSION,
            build_id or "(none)",
            _build_id or "(none)",
        )

        _index, _build_id = index, build_id
        _remove_old_builds(directory, keep)

        return build_id


async def poll(
    directory: pathlib.Path,
    index_builds: builds.IndexBuildService,
    interval: float,
) -> None:
    """Refreshes every `interval` seconds until cancelled.

    A failed refresh is logged, and the current build stays served.
    """
    while True:
        await asyncio.sleep(interval)

        try:
            await refresh(directory, index_builds)
        except Exception:
            logger.exception("Failed to refresh the index; still serving %s", _build_id)


def get_chunk_index() -> chunk_index.ChunkIndex:
    """FastAPI dependency: the loaded index."""
    if _index is None:
        msg = "the chunk index has not been loaded"
        raise RuntimeError(msg)

    return _index


def loaded_build_id() -> builds.BuildId | None:
    return _build_id


def _remove_old_builds(directory: pathlib.Path, keep: set[str]) -> None:
    for child in directory.iterdir():
        if child.is_dir() and child.name not in keep:
            shutil.rmtree(child, ignore_errors=True)
