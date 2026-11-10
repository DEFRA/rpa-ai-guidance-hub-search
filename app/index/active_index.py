"""The chunk index this process serves, loaded from the active build at startup.

The build is downloaded into its own subdirectory of `search_index_directory`.
With no active build yet, the process serves an empty index. Picking up a newer
build without a restart comes later.
"""

import logging
import pathlib
import shutil

from app.index import builds, chunk_index, index_schema, tantivy_index

logger = logging.getLogger(__name__)

_EMPTY = "empty"

_index: chunk_index.ChunkIndex | None = None
_build_id: builds.BuildId | None = None


async def load(
    directory: pathlib.Path,
    index_builds: builds.IndexBuilds,
) -> builds.BuildId | None:
    global _index, _build_id

    build_id = await index_builds.active_build()
    target = directory / (build_id or _EMPTY)

    # Only ever the subdirectory named here, never `directory` itself.
    if target.exists():
        shutil.rmtree(target)

    await index_builds.download(build_id, target)
    _index = tantivy_index.TantivyChunkIndex(target)
    _build_id = build_id
    logger.info(
        "Serving index schema v%d build %s from %s",
        index_schema.SCHEMA_VERSION,
        build_id or "(none)",
        target,
    )
    return build_id


def get_chunk_index() -> chunk_index.ChunkIndex:
    """FastAPI dependency: the loaded index."""
    if _index is None:
        msg = "the chunk index has not been loaded"
        raise RuntimeError(msg)

    return _index


def loaded_build_id() -> builds.BuildId | None:
    return _build_id
