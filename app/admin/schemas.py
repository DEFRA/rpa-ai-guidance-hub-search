"""What the admin routes reply with (camelCase)."""

from typing import Self

import pydantic
from pydantic import alias_generators

from app.admin import index_reset


class _CamelModel(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(
        alias_generator=alias_generators.to_camel,
        populate_by_name=True,
        frozen=True,
    )


class ResetResult(_CamelModel):
    """What the reset removed."""

    deleted_objects: int
    deleted_state_records: int

    @classmethod
    def from_result(cls, result: index_reset.ResetResult) -> Self:
        return cls(
            deleted_objects=result.deleted_objects,
            deleted_state_records=result.deleted_state_records,
        )
