"""An index job names one version at one status."""

import dataclasses
import datetime as dt

import pytest

from app.indexing import jobs
from app.records import models
from tests.support import guides


class TestAnIndexJob:
    def test_refuses_a_time_without_a_timezone(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            dataclasses.replace(guides.job(), occurred_at=dt.datetime(2026, 11, 3))  # noqa: DTZ001


def _apply_job(occurred_at):
    return jobs.ApplyJob(
        document_id=guides.DOCUMENT_ID,
        version_id=guides.VERSION_ID,
        status=models.DocumentStatus.PUBLISHED,
        occurred_at=occurred_at,
        strategy="packed-800",
        chunk_set_key="key",
    )


class TestAnApplyJob:
    def test_accepts_a_time_with_a_timezone(self):
        occurred_at = dt.datetime(2026, 11, 3, tzinfo=dt.UTC)

        assert _apply_job(occurred_at).occurred_at == occurred_at

    def test_refuses_a_time_without_a_timezone(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            _apply_job(dt.datetime(2026, 11, 3))  # noqa: DTZ001
