"""A guide as the CMS API serves it, and the metadata the header keeps."""

import json
import uuid

import pydantic
import pytest

from app.indexing.prepare import guide_cms
from app.records import ids, source_metadata
from tests.support import guides

RESPONSE = json.loads((guides.FIXTURES / "cms_guide.json").read_text())
NEXT_VERSION_ID = ids.VersionId(uuid.UUID("0199b8a2-0000-7000-8000-00000000f003"))


def _guide(**metadata):
    return guide_cms.CmsGuide.model_validate(
        {**RESPONSE, "metadata": {**RESPONSE["metadata"], **metadata}},
    )


class TestACmsGuide:
    def test_reads_the_apis_response(self):
        guide = guide_cms.CmsGuide.model_validate(RESPONSE)

        assert guide.id == guides.DOCUMENT_ID
        assert guide.metadata.guide_title == "Field Visit Guidance"
        assert guide.source.filename == "field-visit.docx"
        assert [version.version_number for version in guide.versions] == [
            "1.0",
            "1.1",
        ]
        assert guide.versions[0].created_by == guide_cms.CmsPerson(
            id="user-1",
            display_name="A Author",
        )

    def test_ignores_fields_it_does_not_know(self):
        guide = guide_cms.CmsGuide.model_validate(
            {**RESPONSE, "pinned": True, "metadata": {"reviewBy": "2027-01-01"}},
        )

        assert guide.metadata == guide_cms.CmsGuideMetadata()

    def test_refuses_a_response_without_versions(self):
        response = {k: v for k, v in RESPONSE.items() if k != "versions"}

        with pytest.raises(pydantic.ValidationError):
            guide_cms.CmsGuide.model_validate(response)

    @pytest.mark.parametrize(
        "response",
        [
            pytest.param(
                {**RESPONSE, "id": "6f2c1a8e-3b4d-4c5e-9f60-718293a4b5c6"},
                id="guide-id",
            ),
            pytest.param(
                {
                    **RESPONSE,
                    "versions": [
                        {**RESPONSE["versions"][0], "id": str(uuid.uuid4())},
                    ],
                },
                id="version-id",
            ),
        ],
    )
    def test_refuses_ids_that_are_not_uuid7(self, response):
        with pytest.raises(pydantic.ValidationError, match="UUIDv7"):
            guide_cms.CmsGuide.model_validate(response)

    def test_finds_one_of_its_versions(self):
        guide = guide_cms.CmsGuide.model_validate(RESPONSE)

        assert guide.version(NEXT_VERSION_ID).version_number == "1.1"

    def test_has_no_version_it_was_not_given(self):
        guide = guide_cms.CmsGuide.model_validate(RESPONSE)
        missing = ids.VersionId(uuid.UUID("0199b8a2-0000-7000-8000-0000000000ff"))

        with pytest.raises(LookupError, match=str(missing)):
            guide.version(missing)


class TestToMetadata:
    def test_keeps_what_search_can_use(self):
        guide = guide_cms.CmsGuide.model_validate(RESPONSE)

        metadata = guide_cms.to_metadata(guide, guide.version(guides.VERSION_ID))

        assert metadata == source_metadata.GuideMetadata(
            version_number="1.0",
            schemes=("Sustainable Farming Incentive",),
            scheme_year=2026,
            systems=("Rural Payments service",),
            audience=("Inspectors",),
            guidance_types=("Process guide",),
            owner="Schemes team",
        )

    def test_takes_the_versions_own_number(self):
        guide = guide_cms.CmsGuide.model_validate(RESPONSE)

        metadata = guide_cms.to_metadata(guide, guide.version(NEXT_VERSION_ID))

        assert metadata.version_number == "1.1"

    def test_keeps_many_guidance_types(self):
        guide = _guide(guidanceType=["Process guide", "Desk instructions"])

        metadata = guide_cms.to_metadata(guide, guide.versions[0])

        assert metadata.guidance_types == ("Process guide", "Desk instructions")

    def test_reads_absent_details_as_empty(self):
        guide = guide_cms.CmsGuide.model_validate({**RESPONSE, "metadata": {}})

        metadata = guide_cms.to_metadata(guide, guide.versions[1])

        assert metadata == source_metadata.GuideMetadata(version_number="1.1")


class TestVersionMetadata:
    def test_combines_the_details_with_the_version_number(self):
        details = guide_cms.CmsGuideMetadata.model_validate(
            {"schemes": ["SFI"], "guidanceType": "Process guide", "goal": "Notes"},
        )

        metadata = guide_cms.version_metadata(details, "2.0")

        assert metadata == source_metadata.GuideMetadata(
            version_number="2.0",
            schemes=("SFI",),
            guidance_types=("Process guide",),
        )
