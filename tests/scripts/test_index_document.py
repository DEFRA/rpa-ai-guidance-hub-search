"""The command line only turns its arguments into a job, then prepares and applies it."""

import pytest

from app.records import models
from scripts import index_document
from tests.support import guides

URL = guides.job().content_url


def _args(tmp_path, status="published"):
    return [
        "--index-dir",
        str(tmp_path),
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
def stages(mocker):
    preparer = mocker.MagicMock()
    preparer.prepare = mocker.AsyncMock(return_value=mocker.sentinel.apply_job)
    applier = mocker.MagicMock()
    applier.apply = mocker.AsyncMock()

    mocker.patch(
        "app.indexing.prepare.document_preparer.from_config",
        return_value=preparer,
    )
    applier_from_config = mocker.patch(
        "app.indexing.apply.document_applier.from_config",
        return_value=applier,
    )

    return preparer, applier, applier_from_config


class TestIndexDocument:
    @pytest.mark.parametrize("status", list(models.DocumentStatus))
    def test_prepares_one_job_then_applies_it(self, stages, tmp_path, status, mocker):
        preparer, applier, applier_from_config = stages

        index_document.main(_args(tmp_path, status.value))

        applier_from_config.assert_called_once_with(tmp_path)
        applier.apply.assert_awaited_once_with(mocker.sentinel.apply_job)

        (job,), _ = preparer.prepare.await_args

        assert (job.document_id, job.version_id, job.status, job.content_url) == (
            guides.DOCUMENT_ID,
            guides.VERSION_ID,
            status,
            URL,
        )
        assert job.occurred_at.utcoffset() is not None
        assert job.source_type == models.SourceType.GUIDE

    @pytest.mark.usefixtures("stages")
    @pytest.mark.parametrize(
        ("flag", "value"),
        [
            pytest.param("--status", "draft", id="unknown-status"),
            pytest.param("--source-type", "pdf", id="unknown-source-type"),
            pytest.param("--document-id", "not-a-uuid", id="bad-document-id"),
        ],
    )
    def test_refuses_bad_arguments(self, tmp_path, flag, value):
        args = _args(tmp_path)
        args[args.index(flag) + 1] = value

        with pytest.raises(SystemExit):
            index_document.main(args)
