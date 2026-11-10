"""What a document's source says about it, typed per source type."""

import pytest

from app.records import models, source_metadata

GUIDE = source_metadata.GuideMetadata(
    version_number="2.1",
    schemes=("Sustainable Farming Incentive",),
    scheme_year=2026,
    systems=("Rural Payments service",),
    audience=("Farmers", "Agents"),
    guidance_types=("Process guide",),
    owner="Schemes team",
)


class TestGuideMetadata:
    def test_belongs_to_guides(self):
        assert GUIDE.SOURCE_TYPE == models.SourceType.GUIDE

    def test_reads_back_as_it_was_stored(self):
        assert source_metadata.GuideMetadata.from_record(GUIDE.to_record()) == GUIDE

    def test_is_stored_with_snake_case_keys_and_lists(self):
        assert GUIDE.to_record() == {
            "version_number": "2.1",
            "schemes": ["Sustainable Farming Incentive"],
            "scheme_year": 2026,
            "systems": ["Rural Payments service"],
            "audience": ["Farmers", "Agents"],
            "guidance_types": ["Process guide"],
            "owner": "Schemes team",
        }

    def test_reads_absent_fields_as_empty(self):
        assert source_metadata.GuideMetadata.from_record({}) == (
            source_metadata.GuideMetadata.empty()
        )

    def test_ignores_fields_it_does_not_know(self):
        stored = {**GUIDE.to_record(), "goal": "Not kept."}

        assert source_metadata.GuideMetadata.from_record(stored) == GUIDE


class TestAGuideMetadataRecordOfTheWrongShape:
    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            pytest.param("schemes", "ab", "schemes must be an array", id="string"),
            pytest.param("systems", [1], "systems item must be a string", id="item"),
            pytest.param("owner", 3, "owner must be a string", id="owner"),
            pytest.param("scheme_year", "2025", "scheme_year must be", id="year"),
        ],
    )
    def test_is_refused(self, field, value, message):
        with pytest.raises(TypeError, match=message):
            source_metadata.GuideMetadata.from_record({field: value})

    def test_is_refused_when_it_is_not_an_object(self):
        with pytest.raises(TypeError, match="source must be an object"):
            source_metadata.of("guide", [])


class TestOf:
    def test_reads_a_source_types_record(self):
        assert source_metadata.of(models.SourceType.GUIDE, GUIDE.to_record()) == GUIDE

    def test_reads_no_record_as_empty(self):
        assert source_metadata.of(models.SourceType.GUIDE, None) == (
            source_metadata.GuideMetadata.empty()
        )

    def test_refuses_a_source_type_with_no_metadata(self):
        with pytest.raises(ValueError, match="govuk_page"):
            source_metadata.of("govuk_page", {})

    def test_has_a_type_for_every_source_type(self):
        for source_type in models.SourceType:
            assert source_type == source_metadata.empty(source_type).SOURCE_TYPE
