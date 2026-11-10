"""The statuses each role may see."""

import enum

from app.records import models


class Role(enum.StrEnum):
    VIEWER = "viewer"
    DESIGNER = "designer"


_STATUSES = {
    Role.VIEWER: frozenset({models.DocumentStatus.PUBLISHED}),
    Role.DESIGNER: frozenset(models.DocumentStatus),
}


def statuses_for(role: Role) -> frozenset[models.DocumentStatus]:
    return _STATUSES[role]
