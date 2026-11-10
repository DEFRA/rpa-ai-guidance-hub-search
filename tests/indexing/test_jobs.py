"""An index job names one version at one status."""

import dataclasses
import datetime as dt

import pytest

from tests.support import guides


class TestAnIndexJob:
    def test_refuses_a_time_without_a_timezone(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            dataclasses.replace(guides.job(), occurred_at=dt.datetime(2026, 11, 3))  # noqa: DTZ001
