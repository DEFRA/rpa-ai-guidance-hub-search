"""The reset route replies with the counts, or 503 when the reset fails."""

import fastapi.testclient
import pytest

from app import config as app_config
from app.admin import index_reset, pending_writes
from app.common import mongo, s3
from app.entrypoints import fastapi as entrypoint
from app.index import builds

PATH = "/admin/index/reset"


@pytest.fixture
def reset(mocker):
    return mocker.patch(
        "app.admin.index_reset.reset",
        return_value=index_reset.ResetResult(
            deleted_objects=7, deleted_state_records=1
        ),
    )


def _client(mode):
    app = entrypoint.create_app(mode)
    app.dependency_overrides[mongo.get_db] = lambda: None
    app.dependency_overrides[s3.get_s3_client] = lambda: None
    app.dependency_overrides[builds.get_index_build_service] = lambda: None
    return fastapi.testclient.TestClient(app)


@pytest.mark.parametrize("mode", list(app_config.IngestionMode))
@pytest.mark.usefixtures("reset")
def test_replies_with_the_counts_in_camel_case(mode):
    response = _client(mode).post(PATH)

    assert response.status_code == 200
    assert response.json() == {"deletedObjects": 7, "deletedStateRecords": 1}


def test_holds_the_writers_turn_the_indexing_queue_shares(reset):
    _client(app_config.IngestionMode.S3).post(PATH)

    assert reset.call_args.args[3] is pending_writes.get_pending_writes().turn


def test_replies_503_without_saying_why_when_the_reset_fails(reset):
    reset.side_effect = RuntimeError("secret detail")

    response = _client(app_config.IngestionMode.HTTP).post(PATH)

    assert response.status_code == 503
    assert "secret detail" not in response.text
