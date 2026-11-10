"""The process loads the active build at startup and serves it."""

import pytest

from app.index import active_index, builds, tantivy_index
from tests.fakes import builds as fake_builds
from tests.support import chunks as fixtures

CLAIMS = fixtures.document([fixtures.Section("1 Overview", 1, ("Holding numbers.",))])


@pytest.fixture(autouse=True)
def unloaded(monkeypatch):
    monkeypatch.setattr(active_index, "_index", None)
    monkeypatch.setattr(active_index, "_build_id", None)


async def _published(tmp_path):
    index_builds = builds.IndexBuilds(
        fake_builds.InMemoryBuildStore(),
        fake_builds.InMemoryActiveBuild(),
    )
    work = tmp_path / "work"
    await tantivy_index.TantivyChunkIndex(work).replace_document(*CLAIMS)
    return index_builds, await index_builds.publish(work, None)


class TestLoad:
    async def test_serves_the_active_build_from_its_own_directory(self, tmp_path):
        index_builds, build_id = await _published(tmp_path)

        assert await active_index.load(tmp_path / "serve", index_builds) == build_id

        assert active_index.loaded_build_id() == build_id
        assert (tmp_path / "serve" / build_id / "meta.json").exists()
        assert isinstance(
            active_index.get_chunk_index(), tantivy_index.TantivyChunkIndex
        )

    async def test_serves_an_empty_index_before_any_build(self, tmp_path):
        index_builds = builds.IndexBuilds(
            fake_builds.InMemoryBuildStore(),
            fake_builds.InMemoryActiveBuild(),
        )

        assert await active_index.load(tmp_path / "serve", index_builds) is None

        assert (tmp_path / "serve" / "empty").is_dir()
        assert active_index.get_chunk_index() is not None

    async def test_replaces_a_stale_copy_of_the_build(self, tmp_path):
        index_builds, build_id = await _published(tmp_path)
        stale = tmp_path / "serve" / build_id
        stale.mkdir(parents=True)
        (stale / "leftover").write_text("x")

        await active_index.load(tmp_path / "serve", index_builds)

        assert not (stale / "leftover").exists()


class TestGetChunkIndex:
    def test_refuses_before_loading(self):
        with pytest.raises(RuntimeError, match="not been loaded"):
            active_index.get_chunk_index()
