"""The index sync queue's events: CloudEvents naming the staged chunk set."""

import json
import uuid

import pydantic
import pytest

from app.common import cloudevents
from app.indexing import models as indexing_models
from tests.support import events, guides

BUCKET = "search-store"


def _written(job, trace_id=None):
    return json.loads(
        cloudevents.dumps(indexing_models.from_job(job, BUCKET, trace_id))
    )


class TestFromJob:
    def test_a_prepared_version_names_its_chunk_set_by_bucket_and_key(self):
        job = events.apply_job("staged/packed-800-v2/d/v/published.jsonl")

        written = _written(job, trace_id="t-1")

        assert written["type"] == indexing_models.GUIDE_VERSION_PREPARED
        assert written["dataschema"] == indexing_models.GUIDE_VERSION_PREPARED_V1
        assert written["source"] == indexing_models.PREPARATION_SOURCE
        assert written["time"] == "2026-11-12T10:00:00Z"
        assert written["cdprequestid"] == "t-1"
        assert written["data"] == {
            "documentId": str(guides.DOCUMENT_ID),
            "versionId": str(guides.VERSION_ID),
            "status": "published",
            "strategy": "packed-800-v2",
            "chunkSet": {
                "bucket": BUCKET,
                "key": "staged/packed-800-v2/d/v/published.jsonl",
            },
        }

    def test_a_removal_carries_only_the_version(self):
        written = _written(events.remove_job())

        assert written["type"] == indexing_models.GUIDE_VERSION_REMOVAL_REQUESTED
        assert written["data"] == {
            "documentId": str(guides.DOCUMENT_ID),
            "versionId": str(guides.VERSION_ID),
        }

    @pytest.mark.parametrize("job", [events.apply_job(), events.remove_job()])
    def test_round_trips_to_the_same_job(self, job):
        body = cloudevents.dumps(indexing_models.from_job(job, BUCKET))

        assert indexing_models.loads(body).to_job(BUCKET) == job

    @pytest.mark.parametrize("job", [events.apply_job(), events.remove_job()])
    def test_names_its_schema_by_a_search_owned_urn(self, job):
        dataschema = _written(job)["dataschema"]

        assert dataschema.startswith("urn:defra:rpa-ai-guidance-hub-search:events:")
        assert dataschema.endswith(":v1")


class TestEventIds:
    def test_are_the_same_for_the_same_job(self):
        """So a resent job is a duplicate the queue keeps once."""
        first = indexing_models.from_job(events.apply_job(), BUCKET, "trace-1")
        second = indexing_models.from_job(events.apply_job(), BUCKET, "trace-2")

        assert first.id == second.id
        assert uuid.UUID(first.id).version == 5

    def test_differ_between_jobs(self):
        apply = indexing_models.from_job(events.apply_job(), BUCKET)
        remove = indexing_models.from_job(events.remove_job(), BUCKET)
        other = indexing_models.from_job(events.apply_job("another/key"), BUCKET)

        assert len({apply.id, remove.id, other.id}) == 3

    def test_fit_sqs_s_deduplication_id_limit(self):
        assert len(indexing_models.from_job(events.apply_job(), BUCKET).id) <= 128


class TestToJob:
    def test_refuses_a_chunk_set_in_another_bucket(self):
        body = cloudevents.dumps(indexing_models.from_job(events.apply_job(), BUCKET))
        event = indexing_models.loads(body)

        with pytest.raises(indexing_models.ChunkSetBucketNotAllowedError):
            event.to_job("someone-elses")


class TestLoads:
    @pytest.mark.parametrize(
        "change",
        [
            pytest.param({"type": "uk.gov.defra.rpa.other"}, id="unknown-type"),
            pytest.param({"specversion": "0.3"}, id="other-spec-version"),
            pytest.param(
                {"dataschema": indexing_models.GUIDE_VERSION_PREPARED_V1[:-1] + "2"},
                id="other-schema-version",
            ),
            pytest.param({"data": {}}, id="no-data"),
        ],
    )
    def test_refuses_what_is_not_a_writer_event(self, change):
        """The writer is this queue's only reader, so an unknown type is poison."""
        written = {**_written(events.apply_job()), **change}

        with pytest.raises(pydantic.ValidationError):
            indexing_models.loads(json.dumps(written))
