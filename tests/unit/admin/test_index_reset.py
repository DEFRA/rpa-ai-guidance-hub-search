"""The reset empties the index's state and storage, in order, under the writer's turn.

Neither a preparation under way nor a cancelled request may see it half done.
"""

import ast
import asyncio
import pathlib
import threading

import pytest

from app.admin import index_reset
from app.index import active_index, builds, tantivy_index
from app.indexing import dispatch
from tests.fakes import builds as fake_builds
from tests.support import chunks as fixtures

BUCKET = "search-store"
CLAIMS = fixtures.document([fixtures.Section("1 Overview", 1, ("Holding numbers.",))])


@pytest.fixture(autouse=True)
def unloaded(monkeypatch):
    monkeypatch.setattr(active_index, "_index", None)
    monkeypatch.setattr(active_index, "_build_id", None)
    monkeypatch.setattr(active_index, "_refreshing", asyncio.Lock())


@pytest.fixture
def s3(moto_s3):
    moto_s3.create_bucket(
        Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-west-2"}
    )
    return moto_s3


@pytest.fixture
def index_builds():
    return builds.IndexBuildService(
        fake_builds.InMemoryBuildStore(),
        fake_builds.InMemoryActiveBuild(),
    )


@pytest.fixture
async def state(mongo_database):
    collection = mongo_database[builds.INDEX_STATE]
    await collection.delete_many({})
    await collection.insert_many(
        [
            {"_id": builds.state_id(1), "active_build_id": "a"},
            {"_id": builds.state_id(2), "active_build_id": "b"},
        ]
    )
    return collection


async def _reset(database, s3_client, index_builds, tmp_path, pending=None):
    return await index_reset.reset(
        database,
        s3_client,
        BUCKET,
        pending or dispatch.PendingWrites(),
        tmp_path / "serve",
        index_builds,
    )


class TestReset:
    @pytest.mark.usefixtures("state")
    async def test_deletes_every_object_in_the_bucket(
        self, mongo_database, s3, index_builds, tmp_path
    ):
        keys = [
            "guide-cms/index/v1/build/meta.json",
            "sections/doc/version.jsonl",
            "staged/packed-800-v2/doc/version/published.jsonl",
            "unrelated/key",
        ]

        for key in keys:
            s3.put_object(Bucket=BUCKET, Key=key, Body=b"x")

        result = await _reset(mongo_database, s3, index_builds, tmp_path)

        assert result.deleted_objects == len(keys)
        assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET)

    @pytest.mark.usefixtures("state")
    async def test_deletes_more_than_one_page_of_objects(
        self, mongo_database, s3, index_builds, tmp_path
    ):
        for number in range(1001):
            s3.put_object(Bucket=BUCKET, Key=f"sections/{number}", Body=b"x")

        result = await _reset(mongo_database, s3, index_builds, tmp_path)

        assert result.deleted_objects == 1001
        assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET)

    async def test_deletes_the_state_of_every_schema_version(
        self, mongo_database, s3, state, index_builds, tmp_path
    ):
        result = await _reset(mongo_database, s3, index_builds, tmp_path)

        assert result.deleted_state_records == 2
        assert await state.count_documents({}) == 0

    @pytest.mark.usefixtures("state")
    async def test_is_idempotent(self, mongo_database, s3, index_builds, tmp_path):
        await _reset(mongo_database, s3, index_builds, tmp_path)

        again = await _reset(mongo_database, s3, index_builds, tmp_path)

        assert again == index_reset.ResetResult(0, 0)

    async def test_clears_the_state_before_the_bucket(
        self, mongo_database, s3, state, index_builds, tmp_path, mocker
    ):
        s3.put_object(Bucket=BUCKET, Key="sections/a", Body=b"x")
        mocker.patch.object(s3, "delete_objects", side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            await _reset(mongo_database, s3, index_builds, tmp_path)

        assert await state.count_documents({}) == 0

    @pytest.mark.usefixtures("state")
    async def test_raises_when_s3_refuses_an_object(
        self, mongo_database, s3, index_builds, tmp_path, mocker
    ):
        s3.put_object(Bucket=BUCKET, Key="sections/a", Body=b"x")
        mocker.patch.object(
            s3,
            "delete_objects",
            return_value={"Errors": [{"Key": "sections/a", "Code": "AccessDenied"}]},
        )

        with pytest.raises(index_reset.BucketWipeError):
            await _reset(mongo_database, s3, index_builds, tmp_path)

    @pytest.mark.usefixtures("state")
    async def test_serves_an_empty_index_afterwards(self, mongo_database, s3, tmp_path):
        store = fake_builds.InMemoryBuildStore()
        active = fake_builds.InMemoryActiveBuild()
        index_builds = builds.IndexBuildService(store, active)
        work = tmp_path / "work"
        await tantivy_index.TantivyChunkIndex(work).replace_document(*CLAIMS)
        await active_index.load(
            tmp_path / "serve", index_builds
        )  # empty: nothing is active yet
        active.build_id = await index_builds.publish(work, None)
        await active_index.refresh(tmp_path / "serve", index_builds)
        assert active_index.loaded_build_id() is not None
        active.build_id = None

        await _reset(mongo_database, s3, index_builds, tmp_path)

        assert active_index.loaded_build_id() is None

    async def test_waits_for_the_writers_turn(
        self, mongo_database, s3, state, index_builds, tmp_path
    ):
        pending = dispatch.PendingWrites()
        await pending.turn.acquire()

        task = asyncio.create_task(
            _reset(mongo_database, s3, index_builds, tmp_path, pending)
        )
        await asyncio.sleep(0.05)

        assert not task.done()
        assert await state.count_documents({}) == 2

        pending.turn.release()
        await task

        assert await state.count_documents({}) == 0


class TestWithPreparations:
    async def test_waits_for_a_preparation_under_way(
        self, mongo_database, s3, state, index_builds, tmp_path
    ):
        pending = dispatch.PendingWrites()
        staged = asyncio.Event()
        finish = asyncio.Event()

        async def prepare():
            async with pending.gate.preparing():
                s3.put_object(Bucket=BUCKET, Key="sections/doc", Body=b"x")
                staged.set()
                await finish.wait()

        preparation = asyncio.create_task(prepare())
        await staged.wait()
        task = asyncio.create_task(
            _reset(mongo_database, s3, index_builds, tmp_path, pending)
        )
        await asyncio.sleep(0.05)

        assert not task.done()
        assert await state.count_documents({}) == 2

        finish.set()
        await preparation
        result = await task

        assert result.deleted_objects == 1
        assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET)

    async def test_makes_a_preparation_that_arrives_meanwhile_wait(
        self, mongo_database, s3, state, index_builds, tmp_path
    ):
        pending = dispatch.PendingWrites()
        await pending.turn.acquire()  # holds the reset where it is
        task = asyncio.create_task(
            _reset(mongo_database, s3, index_builds, tmp_path, pending)
        )
        await asyncio.sleep(0.05)
        started = asyncio.Event()

        async def prepare():
            async with pending.gate.preparing():
                started.set()
                s3.put_object(Bucket=BUCKET, Key="sections/late", Body=b"x")

        preparation = asyncio.create_task(prepare())
        await asyncio.sleep(0.05)

        assert not started.is_set()

        pending.turn.release()
        await task
        await preparation

        assert started.is_set()
        assert await state.count_documents({}) == 0

    @pytest.mark.usefixtures("state")
    async def test_lets_a_preparation_that_waited_for_the_turn_finish_first(
        self, mongo_database, s3, index_builds, tmp_path
    ):
        pending = dispatch.PendingWrites()
        await pending.turn.acquire()  # a publish under way
        dispatched = asyncio.Event()

        async def prepare():
            async with pending.gate.preparing():
                s3.put_object(Bucket=BUCKET, Key="sections/doc", Body=b"x")

                async with pending.turn:  # dispatching needs the writer's turn
                    dispatched.set()

        preparation = asyncio.create_task(prepare())
        await asyncio.sleep(0.05)
        task = asyncio.create_task(
            _reset(mongo_database, s3, index_builds, tmp_path, pending)
        )
        await asyncio.sleep(0.05)

        assert not task.done()

        pending.turn.release()
        await asyncio.gather(preparation, task)

        assert dispatched.is_set()
        assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET)


class TestCancelled:
    @pytest.mark.usefixtures("state")
    async def test_keeps_the_writers_turn_until_the_wipe_has_finished(
        self, mongo_database, s3, index_builds, tmp_path, mocker
    ):
        pending = dispatch.PendingWrites()
        s3.put_object(Bucket=BUCKET, Key="sections/old", Body=b"x")
        in_wipe = threading.Event()
        resume = threading.Event()
        delete_objects = s3.delete_objects

        def slow_delete(**kwargs):
            in_wipe.set()
            resume.wait(timeout=5)

            return delete_objects(**kwargs)

        mocker.patch.object(s3, "delete_objects", side_effect=slow_delete)
        request = asyncio.create_task(
            _reset(mongo_database, s3, index_builds, tmp_path, pending)
        )
        await asyncio.to_thread(in_wipe.wait, 5)
        writer = asyncio.create_task(_write_under(pending.turn, s3, "builds/new"))

        request.cancel()
        await asyncio.sleep(0.05)

        assert request.cancelled()
        assert pending.turn.locked()
        assert not writer.done()

        resume.set()
        await writer
        await asyncio.gather(*index_reset._running)

        keys = [item["Key"] for item in s3.list_objects_v2(Bucket=BUCKET)["Contents"]]

        assert keys == ["builds/new"]


async def _write_under(turn, s3, key):
    async with turn:
        s3.put_object(Bucket=BUCKET, Key=key, Body=b"x")


APP = pathlib.Path(__file__).parent.parent.parent.parent / "app"


def _collections_used(source: str) -> set[str]:
    """Names of collections taken from a database: `database[NAME]`, `db[NAME]`."""
    tree = ast.parse(source)
    constants = {
        node.targets[0].id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
    }
    used = set()

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id in {"database", "db", "_database"}
            and isinstance(node.slice, ast.Constant | ast.Name)
        ):
            key = node.slice
            used.add(key.value if isinstance(key, ast.Constant) else constants[key.id])

    return used


class TestCollections:
    def test_the_reset_clears_every_collection_the_app_uses(self):
        used = {
            name
            for path in APP.rglob("*.py")
            if not path.is_relative_to(APP / "admin")
            for name in _collections_used(path.read_text())
        }

        assert used <= set(builds.INDEX_STATE_COLLECTIONS)

    def test_the_check_finds_a_collection(self):
        assert _collections_used((APP / "index" / "builds.py").read_text()) == {
            builds.INDEX_STATE
        }
