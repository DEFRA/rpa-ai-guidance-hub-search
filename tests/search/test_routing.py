import pytest

from app.records import models
from app.search import routing


class TestStatusesForARole:
    @pytest.mark.parametrize(
        ("role", "statuses"),
        [
            pytest.param(
                routing.Role.VIEWER,
                {models.DocumentStatus.PUBLISHED},
                id="viewer",
            ),
            pytest.param(
                routing.Role.DESIGNER,
                {
                    models.DocumentStatus.PUBLISHED,
                    models.DocumentStatus.READY_FOR_PUBLISH,
                },
                id="designer",
            ),
        ],
    )
    def test_are_as_designed(self, role, statuses):
        assert routing.statuses_for(role) == statuses
