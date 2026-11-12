"""A guide as the CMS API serves it (`GET /guides/{id}`), and its typed metadata.

The models mirror the API's response: the document, its untyped `metadata`
(the keys the authoring journey writes), where it was uploaded from, and every
version. They accept camelCase, ignore fields they don't know (the CMS may add
some), and treat every metadata key as optional, since the API stores metadata
untyped.

`to_metadata` turns a guide and one of its versions into the canonical
`GuideMetadata` the header carries; `version_metadata` does the same from the
details and version number the CMS sends when it pushes a version. Nothing
calls the CMS API itself. A GOV.UK source gets a sibling module of Content API
models.
"""

import datetime as dt
import uuid
from typing import Annotated

import pydantic
from pydantic import alias_generators

from app.records import ids, source_metadata


def _uuid7(value: uuid.UUID) -> uuid.UUID:
    if not ids.is_uuid7(value):
        msg = f"{value} is not a CMS id (UUIDv7)"
        raise ValueError(msg)

    return value


# The CMS mints every guide and version id as a UUIDv7.
CmsId = Annotated[uuid.UUID, pydantic.AfterValidator(_uuid7)]


class _CmsModel(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(
        alias_generator=alias_generators.to_camel,
        populate_by_name=True,
        extra="ignore",
        frozen=True,
    )


class CmsSource(_CmsModel):
    """The cdp-uploader upload a guide was converted from."""

    upload_id: str
    file_id: str
    filename: str | None = None


class CmsPerson(_CmsModel):
    """Who was signed in when a version was made."""

    id: str
    display_name: str | None = None


class CmsGuideMetadata(_CmsModel):
    """What the authoring journey collected. `guidance_type` may be one or many."""

    guide_title: str | None = None
    schemes: tuple[str, ...] = ()
    scheme_year: int | None = None
    owner: str | None = None
    goal: str | None = None
    requirements: str | None = None
    systems: tuple[str, ...] = ()
    audience: tuple[str, ...] = ()
    guidance_type: str | tuple[str, ...] | None = None


class CmsVersion(_CmsModel):
    id: CmsId
    content_url: str
    title: str | None = None
    version_number: str | None = None
    created_by: CmsPerson | None = None
    created_at: dt.datetime


class CmsGuide(_CmsModel):
    id: CmsId
    metadata: CmsGuideMetadata
    source: CmsSource
    created_at: dt.datetime
    versions: tuple[CmsVersion, ...]

    def version(self, version_id: ids.VersionId) -> CmsVersion:
        """Raises `LookupError` if the guide has no such version."""
        for version in self.versions:
            if version.id == version_id:
                return version

        msg = f"guide {self.id} has no version {version_id}"
        raise LookupError(msg)


def to_metadata(guide: CmsGuide, version: CmsVersion) -> source_metadata.GuideMetadata:
    """The canonical metadata of one version of `guide`."""
    return version_metadata(guide.metadata, version.version_number)


def version_metadata(
    details: CmsGuideMetadata,
    version_number: str | None,
) -> source_metadata.GuideMetadata:
    """The canonical metadata of a version with these details and number.

    The authoring notes (`goal`, `requirements`) are not search data and are
    left out.
    """
    guidance_type = details.guidance_type

    return source_metadata.GuideMetadata(
        version_number=version_number,
        schemes=details.schemes,
        scheme_year=details.scheme_year,
        systems=details.systems,
        audience=details.audience,
        guidance_types=(
            (guidance_type,) if isinstance(guidance_type, str) else guidance_type or ()
        ),
        owner=details.owner,
    )
