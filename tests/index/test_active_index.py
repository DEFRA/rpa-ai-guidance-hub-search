"""The process loads the active build at startup and swaps in newer ones."""

import asyncio
import logging

import pytest

from app.index import active_index, builds, tantivy_index
from tests.fakes import builds as fake_builds
from tests.support import chunks as fixtures

CLAIMS = fixtures.document([fixtures.Section("1 Overview", 1, ("Holding numbers.",))])


@pytest.fixture(autouse=True)
def unloaded(monkeypatch):
    monkeypatch.setattr(active_index, "_index", None)
    monkeypatch.setattr(active_index, "_build_id", None)
    monkeypatch.setattr(active_index, "_refreshing", asyncio.Lock())


async def _published(tmp_path):
    index_builds = builds.IndexBuildService(
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
        index_builds = builds.IndexBuildService(
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


async def _publish_another(index_builds, tmp_path, name):
    work = tmp_path / name
    await index_builds.download(await index_builds.active_build(), work)
    await tantivy_index.TantivyChunkIndex(work).replace_document(*CLAIMS)

    return await index_builds.publish(work, await index_builds.active_build())


class TestRefresh:
    async def test_leaves_the_served_build_alone_when_it_is_still_active(
        self, tmp_path, mocker
    ):
        index_builds, _ = await _published(tmp_path)
        await active_index.load(tmp_path / "serve", index_builds)

        served = active_index.get_chunk_index()
        download = mocker.spy(index_builds, "download")

        await active_index.refresh(tmp_path / "serve", index_builds)

        assert active_index.get_chunk_index() is served
        download.assert_not_called()

    async def test_serves_a_newly_active_build(self, tmp_path):
        index_builds, first = await _published(tmp_path)
        await active_index.load(tmp_path / "serve", index_builds)
        served = active_index.get_chunk_index()

        second = await _publish_another(index_builds, tmp_path, "second")

        assert await active_index.refresh(tmp_path / "serve", index_builds) == second

        assert active_index.loaded_build_id() == second
        assert active_index.get_chunk_index() is not served
        assert (tmp_path / "serve" / second / "meta.json").exists()

        # Kept for searches still running on it.
        assert (tmp_path / "serve" / first).is_dir()

    async def test_removes_builds_older_than_the_one_replaced(self, tmp_path):
        index_builds, _ = await _published(tmp_path)
        await active_index.load(tmp_path / "serve", index_builds)

        second = await _publish_another(index_builds, tmp_path, "second")
        await active_index.refresh(tmp_path / "serve", index_builds)

        third = await _publish_another(index_builds, tmp_path, "third")

        await active_index.refresh(tmp_path / "serve", index_builds)

        assert sorted(path.name for path in (tmp_path / "serve").iterdir()) == sorted(
            [second, third]
        )

    async def test_replaces_the_empty_index_with_the_first_build(self, tmp_path):
        index_builds = builds.IndexBuildService(
            fake_builds.InMemoryBuildStore(),
            fake_builds.InMemoryActiveBuild(),
        )
        await active_index.load(tmp_path / "serve", index_builds)

        work = tmp_path / "work"
        await tantivy_index.TantivyChunkIndex(work).replace_document(*CLAIMS)
        build_id = await index_builds.publish(work, None)

        await active_index.refresh(tmp_path / "serve", index_builds)

        assert active_index.loaded_build_id() == build_id
        assert (tmp_path / "serve" / "empty").is_dir()

    async def test_startup_clears_builds_left_by_an_earlier_run(self, tmp_path):
        index_builds, build_id = await _published(tmp_path)
        (tmp_path / "serve" / "old-build").mkdir(parents=True)

        await active_index.load(tmp_path / "serve", index_builds)

        assert [path.name for path in (tmp_path / "serve").iterdir()] == [build_id]


class TestPoll:
    async def test_picks_up_a_newly_active_build(self, tmp_path):
        index_builds, _ = await _published(tmp_path)
        await active_index.load(tmp_path / "serve", index_builds)

        second = await _publish_another(index_builds, tmp_path, "second")

        polling = asyncio.create_task(
            active_index.poll(tmp_path / "serve", index_builds, 0.01)
        )

        try:
            async with asyncio.timeout(5):
                while active_index.loaded_build_id() != second:
                    await asyncio.sleep(0.01)
        finally:
            polling.cancel()

    async def test_keeps_serving_and_polling_after_a_failure(
        self, tmp_path, mocker, caplog
    ):
        index_builds, first = await _published(tmp_path)
        await active_index.load(tmp_path / "serve", index_builds)

        refresh = mocker.patch.object(
            active_index,
            "refresh",
            side_effect=[RuntimeError("mongo down"), RuntimeError("still down"), None],
        )

        polling = asyncio.create_task(
            active_index.poll(tmp_path / "serve", index_builds, 0.01)
        )

        try:
            async with asyncio.timeout(5):
                while refresh.await_count < 3:
                    await asyncio.sleep(0.01)
        finally:
            polling.cancel()

        assert active_index.loaded_build_id() == first
        assert [
            record.levelno
            for record in caplog.records
            if "Failed to refresh the index" in record.getMessage()
        ] == [logging.ERROR, logging.ERROR]


class TestGetChunkIndex:
    def test_refuses_before_loading(self):
        with pytest.raises(RuntimeError, match="not been loaded"):
            active_index.get_chunk_index()
