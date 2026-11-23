"""The indexing queue's guide events: their v1 shape is pinned."""

import datetime as dt
import json
import pathlib
import uuid

import pydantic
import pytest

from app.common import cloudevents
from app.indexing import jobs
from app.indexing.prepare import guide_content, guide_source
from app.ingestion import models as ingestion_models
from app.records import models, source_metadata
from tests.support import events, guides

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
PUBLISHED = FIXTURES / "guide-version-published.v1.json"
DELETED = FIXTURES / "guide-version-deleted.v1.json"


def _golden(path):
    return json.loads(path.read_text())


def _with(document, **changes):
    return json.dumps({**document, **changes})


class TestTheGoldenEvents:
    @pytest.mark.parametrize(
        ("path", "model"),
        [
            pytest.param(PUBLISHED, ingestion_models.GuideVersionPublished, id="pub"),
            pytest.param(DELETED, ingestion_models.GuideVersionDeleted, id="del"),
        ],
    )
    def test_parse_and_write_back_unchanged(self, path, model):
        event = ingestion_models.loads(path.read_text())

        assert isinstance(event, model)
        assert json.loads(cloudevents.dumps(event)) == _golden(path)

    def test_published_becomes_an_index_job_and_a_reader(self, mocker):
        event = ingestion_models.loads(PUBLISHED.read_text())

        assert event.to_job() == jobs.IndexJob(
            source_type=models.SourceType.GUIDE,
            document_id=guides.DOCUMENT_ID,
            version_id=guides.VERSION_ID,
            status=models.DocumentStatus.PUBLISHED,
            content_url=event.data.content_url,
            occurred_at=dt.datetime(2026, 11, 12, 10, tzinfo=dt.UTC),
        )
        reader = event.to_reader(mocker.sentinel.s3)
        assert isinstance(reader, guide_source.GuideSource)
        assert isinstance(reader._content_source, guide_content.S3ContentSource)
        assert reader._title == "Field Visit Guide"
        assert reader._metadata == source_metadata.GuideMetadata(
            version_number="1.0",
            schemes=("SFI",),
            guidance_types=("Process guide",),
            owner="Schemes team",
        )

    def test_deleted_becomes_a_remove_job(self):
        event = ingestion_models.loads(DELETED.read_text())

        assert event.to_job() == jobs.RemoveJob(
            document_id=guides.DOCUMENT_ID,
            version_id=guides.VERSION_ID,
            occurred_at=dt.datetime(2026, 11, 13, 8, 30, tzinfo=dt.UTC),
        )


class TestRouting:
    def test_mints_a_new_id_per_event(self):
        assert events.published().id != events.published().id

    def test_reads_an_unknown_type_as_unknown(self):
        body = _with(_golden(DELETED), type="uk.gov.defra.rpa.guidancehub.other")

        event = ingestion_models.loads(body)

        assert isinstance(event, cloudevents.UnknownEvent)
        assert event.type == "uk.gov.defra.rpa.guidancehub.other"

    def test_refuses_a_schema_version_it_does_not_read(self):
        document = _golden(PUBLISHED)
        body = _with(document, dataschema=document["dataschema"].replace(":v1", ":v2"))

        with pytest.raises(pydantic.ValidationError):
            ingestion_models.loads(body)

    @pytest.mark.parametrize(
        "data",
        [
            pytest.param({"contentUrl": "/a/b"}, id="not-s3"),
            pytest.param({"documentId": str(uuid.uuid4())}, id="not-a-cms-id"),
            pytest.param({"status": "ready_for_publish"}, id="not-published"),
            pytest.param({"metadata": {"owner": "Team"}}, id="no-title"),
        ],
    )
    def test_refuses_a_known_type_with_invalid_data(self, data):
        document = _golden(PUBLISHED)
        body = _with(document, data={**document["data"], **data})

        with pytest.raises(pydantic.ValidationError):
            ingestion_models.loads(body)


class TestMessageAttributes:
    def test_copy_the_type_document_and_source_type(self):
        event = events.published()

        assert ingestion_models.message_attributes(event) == {
            "type": ingestion_models.GUIDE_VERSION_PUBLISHED,
            "documentId": str(guides.DOCUMENT_ID),
            "sourceType": "guide",
        }

    def test_leave_out_the_source_type_of_a_deletion(self):
        assert ingestion_models.message_attributes(events.deleted()) == {
            "type": ingestion_models.GUIDE_VERSION_DELETED,
            "documentId": str(guides.DOCUMENT_ID),
        }
