"""The command line only turns its arguments into a job, then prepares and applies it."""

import pytest

from app.common import mongo
from app.records import models
from scripts import index_document
from tests.support import guides

URL = guides.job().content_url


def _args(status="published"):
    return [
        "--source-type",
        "guide",
        "--document-id",
        str(guides.DOCUMENT_ID),
        "--version-id",
        str(guides.VERSION_ID),
        "--status",
        status,
        "--content-url",
        URL,
    ]


@pytest.fixture
def preparer(mocker):
    built = mocker.MagicMock()
    built.prepare = mocker.AsyncMock(return_value=mocker.sentinel.apply_job)
    mocker.patch(
        "app.indexing.prepare.document_preparer.from_config",
        return_value=built,
    )
    return built


@pytest.fixture
def publish(mocker):
    return mocker.patch(
        "app.indexing.apply.document_applier.publish_from_config",
        mocker.AsyncMock(),
    )


@pytest.fixture
def mongo_client(mocker, monkeypatch):
    client = mocker.MagicMock()
    client.close = mocker.AsyncMock()
    monkeypatch.setattr(mongo, "client", client)
    return client


class TestIndexDocument:
    @pytest.mark.usefixtures("mongo_client")
    @pytest.mark.parametrize("status", list(models.DocumentStatus))
    def test_prepares_one_job_then_applies_it(self, preparer, publish, status, mocker):
        index_document.main(_args(status.value))

        (job,), _ = preparer.prepare.await_args

        assert (job.document_id, job.version_id, job.status, job.content_url) == (
            guides.DOCUMENT_ID,
            guides.VERSION_ID,
            status,
            URL,
        )
        assert job.occurred_at.utcoffset() is not None
        assert job.source_type == models.SourceType.GUIDE
        publish.assert_awaited_once_with(mocker.sentinel.apply_job)

    @pytest.mark.usefixtures("mongo_client")
    def test_applies_nothing_when_preparing_fails(self, preparer, publish):
        preparer.prepare.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            index_document.main(_args())

        publish.assert_not_awaited()

    @pytest.mark.usefixtures("preparer")
    def test_closes_the_mongo_client_even_when_applying_fails(
        self,
        publish,
        mongo_client,
    ):
        publish.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            index_document.main(_args())

        mongo_client.close.assert_awaited_once()

    @pytest.mark.usefixtures("preparer")
    def test_runs_without_a_mongo_client(self, publish, monkeypatch):
        monkeypatch.setattr(mongo, "client", None)

        index_document.main(_args())

        publish.assert_awaited_once()

    @pytest.mark.usefixtures("preparer", "publish")
    @pytest.mark.parametrize(
        ("flag", "value"),
        [
            pytest.param("--status", "draft", id="unknown-status"),
            pytest.param("--source-type", "pdf", id="unknown-source-type"),
            pytest.param("--document-id", "not-a-uuid", id="bad-document-id"),
        ],
    )
    def test_refuses_bad_arguments(self, flag, value):
        args = _args()
        args[args.index(flag) + 1] = value

        with pytest.raises(SystemExit):
            index_document.main(args)
