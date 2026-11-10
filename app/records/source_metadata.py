"""What a document's source says about it, typed per source type.

The canonical header carries one of these (`DocumentHeader.source`), so every
stored version keeps what its source knew beyond the title and content. Each
type names the `SourceType` value it belongs to in `SOURCE_TYPE`; the header
refuses a mismatch.

Only guides are a source today, as the CMS API defines them. A GOV.UK content
item (from the Content API) adds its own type here, joins `SourceMetadata` and
registers in `_TYPES`.

Stored keys are snake_case. Readers ignore unknown keys, so changes must be additive.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any, ClassVar


@dataclasses.dataclass(frozen=True)
class GuideMetadata:
    """A guide's details as the CMS API records them; every field may be absent.

    `version_number` is the author's (`major.minor`, as SharePoint numbers the
    Word document). The rest is what the authoring journey collected about who
    and what the guide is for.
    """

    SOURCE_TYPE: ClassVar[str] = "guide"

    version_number: str | None = None
    schemes: tuple[str, ...] = ()
    scheme_year: int | None = None
    systems: tuple[str, ...] = ()
    audience: tuple[str, ...] = ()
    guidance_types: tuple[str, ...] = ()
    owner: str | None = None

    @classmethod
    def empty(cls) -> GuideMetadata:
        return cls()

    def to_record(self) -> dict[str, Any]:
        return {
            "version_number": self.version_number,
            "schemes": list(self.schemes),
            "scheme_year": self.scheme_year,
            "systems": list(self.systems),
            "audience": list(self.audience),
            "guidance_types": list(self.guidance_types),
            "owner": self.owner,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> GuideMetadata:
        return cls(
            version_number=record.get("version_number"),
            schemes=tuple(record.get("schemes", ())),
            scheme_year=record.get("scheme_year"),
            systems=tuple(record.get("systems", ())),
            audience=tuple(record.get("audience", ())),
            guidance_types=tuple(record.get("guidance_types", ())),
            owner=record.get("owner"),
        )


type SourceMetadata = GuideMetadata

_TYPES: dict[str, type[GuideMetadata]] = {
    GuideMetadata.SOURCE_TYPE: GuideMetadata,
}


def empty(source_type: str) -> SourceMetadata:
    return _type(source_type).empty()


def of(source_type: str, record: Mapping[str, Any] | None) -> SourceMetadata:
    """The stored metadata for `source_type`; no record reads as empty."""
    kind = _type(source_type)
    return kind.empty() if record is None else kind.from_record(record)


def _type(source_type: str) -> type[GuideMetadata]:
    try:
        return _TYPES[source_type]
    except KeyError:
        msg = f"no metadata type for source type {source_type!r}"
        raise ValueError(msg) from None
