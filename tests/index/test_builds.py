"""Index builds in S3, and the active-build pointer in MongoDB."""

import asyncio
import uuid

import pytest
import tantivy

from app.index import builds, index_schema, tantivy_index, tantivy_schema
from tests.fakes import builds as fake_builds
from tests.support import chunks as fixtures

BUCKET = "search-store"
VERSION = 1
CLAIMS = fixtures.document([fixtures.Section("1 Overview", 1, ("Holding numbers.",))])


async def _written_index(directory):
    await tantivy_index.TantivyChunkIndex(directory).replace_document(*CLAIMS)
    return directory


def _entry_count(directory):
    index = tantivy.Index.open(str(directory))
    tantivy_schema.register_tokenizers(index)
    return index.searcher().num_docs


@pytest.fixture
def store():
    return fake_builds.InMemoryBuildStore()


@pytest.fixture
def active():
    return fake_builds.InMemoryActiveBuild()


@pytest.fixture
def index_builds(store, active):
    return builds.IndexBuildService(store, active)


class TestIndexBuildService:
    async def test_loads_nothing_into_an_empty_directory_before_any_build(
        self,
        index_builds,
        tmp_path,
    ):
        directory = tmp_path / "work"

        assert await index_builds.load(directory) is None
        assert list(directory.iterdir()) == []

    async def test_publishes_a_build_and_makes_it_active(
        self,
        index_builds,
        active,
        store,
        tmp_path,
    ):
        directory = await _written_index(tmp_path / "work")

        build_id = await index_builds.publish(directory, None)

        assert active.build_id == build_id
        assert set(store.builds) == {build_id}

    async def test_loads_the_active_build(self, index_builds, tmp_path):
        published = await index_builds.publish(
            await _written_index(tmp_path / "first"),
            None,
        )

        loaded = await index_builds.load(tmp_path / "second")

        assert loaded == published
        assert _entry_count(tmp_path / "second") == 3

    async def test_discards_a_build_made_from_a_build_no_longer_active(
        self,
        index_builds,
        active,
        store,
        tmp_path,
    ):
        directory = await _written_index(tmp_path / "work")
        current = await index_builds.publish(directory, None)

        with pytest.raises(builds.LostSwapError):
            await index_builds.publish(directory, None)

        assert active.build_id == current
        assert set(store.builds) == {current}


@pytest.fixture
def s3_store(moto_s3):
    moto_s3.create_bucket(
        Bucket=BUCKET,
        CreateBucketConfiguration={"LocationConstraint": "eu-west-2"},
    )
    return builds.S3BuildStore(moto_s3, BUCKET, VERSION)


def _keys(client, bucket):
    listing = client.list_objects_v2(Bucket=bucket)
    return sorted(item["Key"] for item in listing.get("Contents", []))


class TestS3BuildStore:
    async def test_uploads_an_index_under_its_own_prefix(
        self,
        s3_store,
        moto_s3,
        tmp_path,
    ):
        directory = await _written_index(tmp_path / "index")
        (directory / ".tantivy-writer.lock").write_text("")

        build_id = await s3_store.upload(directory)

        uploaded = _keys(moto_s3, BUCKET)
        assert uploaded == sorted(
            f"index/v1/{build_id}/{path.name}"
            for path in directory.iterdir()
            if not path.name.endswith(".lock")
        )
        assert "meta.json" in {key.rpartition("/")[2] for key in uploaded}

    async def test_build_ids_sort_by_creation(self, s3_store, tmp_path):
        directory = await _written_index(tmp_path / "index")

        first = await s3_store.upload(directory)
        second = await s3_store.upload(directory)

        assert uuid.UUID(first) < uuid.UUID(second)

    async def test_downloads_a_build_that_opens_as_the_same_index(
        self,
        s3_store,
        tmp_path,
    ):
        build_id = await s3_store.upload(await _written_index(tmp_path / "index"))
        target = tmp_path / "downloaded"
        target.mkdir()

        await s3_store.download(build_id, target)

        assert _entry_count(target) == 3

    async def test_refuses_to_download_a_build_with_no_files(self, s3_store, tmp_path):
        with pytest.raises(FileNotFoundError, match="no files"):
            await s3_store.download(builds.BuildId("absent"), tmp_path)

    async def test_deletes_only_that_build(self, s3_store, moto_s3, tmp_path):
        directory = await _written_index(tmp_path / "index")
        kept, deleted = await asyncio.gather(
            s3_store.upload(directory),
            s3_store.upload(directory),
        )

        await s3_store.delete(deleted)

        assert {key.split("/")[2] for key in _keys(moto_s3, BUCKET)} == {kept}

    async def test_removes_what_was_uploaded_when_a_transfer_fails(
        self,
        s3_store,
        moto_s3,
        tmp_path,
        mocker,
    ):
        directory = await _written_index(tmp_path / "index")
        real_upload = moto_s3.upload_file
        calls = []

        def flaky_upload(filename, bucket, key):
            calls.append(key)
            if len(calls) == 1:
                msg = "boom"
                raise RuntimeError(msg)
            real_upload(filename, bucket, key)

        mocker.patch.object(moto_s3, "upload_file", side_effect=flaky_upload)

        with pytest.raises(RuntimeError, match="boom"):
            await s3_store.upload(directory)

        assert _keys(moto_s3, BUCKET) == []

    async def test_deleting_an_absent_build_is_fine(self, s3_store):
        await s3_store.delete(builds.BuildId("absent"))


class TestS3BuildStoreAgainstFloci:
    async def test_round_trips_an_index(self, floci_s3, tmp_path):
        client, bucket = floci_s3
        store = builds.S3BuildStore(client, bucket, VERSION)
        build_id = await store.upload(await _written_index(tmp_path / "index"))
        target = tmp_path / "downloaded"
        target.mkdir()

        await store.download(build_id, target)

        assert _entry_count(target) == 3


@pytest.fixture
async def pointer(mongo_database):
    await mongo_database[builds.INDEX_STATE].delete_many({})
    yield builds.MongoActiveBuild(mongo_database, VERSION)
    await mongo_database[builds.INDEX_STATE].delete_many({})


FIRST = builds.BuildId("0199b8a2-0000-7000-8000-0000000000b1")
SECOND = builds.BuildId("0199b8a2-0000-7000-8000-0000000000b2")
THIRD = builds.BuildId("0199b8a2-0000-7000-8000-0000000000b3")


class TestMongoActiveBuild:
    async def test_has_no_active_build_at_first(self, pointer):
        assert await pointer.get() is None

    async def test_makes_the_first_build_active(self, pointer):
        assert await pointer.swap(None, FIRST) is True
        assert await pointer.get() == FIRST

    async def test_swaps_from_the_build_it_expects(self, pointer):
        await pointer.swap(None, FIRST)

        assert await pointer.swap(FIRST, SECOND) is True
        assert await pointer.get() == SECOND

    @pytest.mark.parametrize(
        "expected",
        [pytest.param(None, id="none"), pytest.param(THIRD, id="stale")],
    )
    async def test_refuses_to_swap_from_a_build_that_is_not_active(
        self,
        pointer,
        expected,
    ):
        await pointer.swap(None, FIRST)

        assert await pointer.swap(expected, SECOND) is False
        assert await pointer.get() == FIRST

    async def test_records_when_it_swapped(self, pointer, mongo_database):
        await pointer.swap(None, FIRST)

        record = await mongo_database[builds.INDEX_STATE].find_one(
            {"_id": "chunks-v1"},
        )
        assert record["updated_at"] is not None
        assert record["schema_version"] == VERSION

    async def test_keeps_each_schema_versions_build_apart(
        self,
        pointer,
        mongo_database,
    ):
        next_version = builds.MongoActiveBuild(mongo_database, VERSION + 1)
        await pointer.swap(None, FIRST)

        assert await next_version.get() is None
        assert await next_version.swap(None, SECOND) is True
        assert await pointer.get() == FIRST
        assert await next_version.get() == SECOND


class TestFromConfig:
    def test_wires_s3_and_mongo(self, mocker):
        mocker.patch("app.common.s3.get_s3_client", return_value=mocker.sentinel.s3)

        built = builds.get_index_build_service(mocker.MagicMock())

        assert built._store._client is mocker.sentinel.s3
        assert built._store._bucket == "rpa-ai-guidance-hub-search-store"

    def test_keys_builds_by_the_index_schema_version(self, mocker):
        mocker.patch("app.common.s3.get_s3_client")

        built = builds.get_index_build_service(mocker.MagicMock())

        assert built._store._schema_version == index_schema.SCHEMA_VERSION
        assert built._active._state_id == f"chunks-v{index_schema.SCHEMA_VERSION}"
