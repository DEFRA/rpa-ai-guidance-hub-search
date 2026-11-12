"""Chunk and section ids: deterministic, and stable when a document is renumbered."""

import uuid

import pytest

from app.records import ids
from tests.support import chunks as fixtures

Section = fixtures.Section

ORIGINAL = [
    Section("1 Overview", 1),
    Section("2 Claims", 1),
    Section("2.1 Eligibility", 2, ("Who can claim.", "Who cannot claim.")),
    Section("2.1.1 Concurrent claims", 3),
    Section("Annex A", 1),
    Section("A.1 Payment windows", 2),
]

# A section inserted at the top: every number and anchor after it shifts.
RENUMBERED = [
    Section("1 Before you start", 1),
    Section("2 Overview", 1),
    Section("3 Claims", 1),
    Section("3.1 Eligibility", 2, ("Who can claim.", "Who cannot claim.")),
    Section("3.1.1 Concurrent claims", 3),
    Section("Annex A", 1),
    Section("A.1 Introduction", 2),
    Section("A.2 Payment windows", 2),
]


class TestSplittingASectionNumber:
    @pytest.mark.parametrize(
        ("rendered", "expected"),
        [
            pytest.param("1 Overview", ("1", "Overview"), id="top-level"),
            pytest.param("4.2.1 Evidence", ("4.2.1", "Evidence"), id="nested"),
            pytest.param("A.1 Detail", ("A.1", "Detail"), id="appendix-child"),
            pytest.param("AA.3 Detail", ("AA.3", "Detail"), id="double-letter"),
            pytest.param("Annex A", (None, "Annex A"), id="appendix-unnumbered"),
            pytest.param(
                "3 2026 changes",
                ("3", "2026 changes"),
                id="only-the-first-token",
            ),
            pytest.param("1", (None, "1"), id="number-without-text"),
            pytest.param("A Guide", (None, "A Guide"), id="leading-capital-word"),
        ],
    )
    def test_takes_only_a_derived_number(self, rendered, expected):
        assert ids.split_section_number(rendered) == expected


class TestSectionIds:
    def test_are_deterministic(self):
        assert ids.section_id("guide", fixtures.DOCUMENT_ID, ["Claims"]) == (
            ids.section_id("guide", fixtures.DOCUMENT_ID, ["Claims"])
        )

    def test_are_pinned_to_this_algorithm(self):
        assert ids.section_id(
            "guide",
            fixtures.DOCUMENT_ID,
            ["Claims", "Eligibility"],
        ) == ("2614837324de0045bf3c5786156a625a811ebbd7ef371981c357e4a8719ae1b9")

    @pytest.mark.parametrize(
        "other",
        [
            pytest.param(
                ("guide", ["Claims", "Eligibility"], 1),
                id="another-occurrence",
            ),
            pytest.param(("guide", ["Claims", "Payments"], 0), id="another-heading"),
            pytest.param(("guide", ["Eligibility"], 0), id="another-parent"),
            pytest.param(("web_page", ["Claims", "Eligibility"], 0), id="other-source"),
        ],
    )
    def test_differ_when_any_input_differs(self, other):
        source_type, path, occurrence = other

        assert ids.section_id(
            source_type,
            fixtures.DOCUMENT_ID,
            path,
            occurrence,
        ) != ids.section_id("guide", fixtures.DOCUMENT_ID, ["Claims", "Eligibility"])

    def test_differ_between_documents(self):
        other_document = ids.DocumentId(
            uuid.UUID("0199b8a2-0000-7000-8000-0000000000ff")
        )

        assert ids.section_id("guide", other_document, ["Claims"]) != ids.section_id(
            "guide",
            fixtures.DOCUMENT_ID,
            ["Claims"],
        )

    @pytest.mark.parametrize(
        "heading",
        [
            pytest.param("concurrent claims", id="case"),
            pytest.param("  Concurrent   claims ", id="whitespace"),
            pytest.param("Concurrent claims", id="non-breaking-space"),
        ],
    )
    def test_ignore_case_and_spacing(self, heading):
        assert ids.section_id(
            "guide", fixtures.DOCUMENT_ID, [heading]
        ) == ids.section_id(
            "guide",
            fixtures.DOCUMENT_ID,
            ["Concurrent claims"],
        )

    def test_give_duplicate_sibling_headings_distinct_ids(self):
        first, second = ids.section_ids(
            "guide",
            fixtures.DOCUMENT_ID,
            [("Claims", "Overview"), ("Claims", "Overview")],
        )

        assert first != second

    def test_refuse_a_negative_occurrence(self):
        with pytest.raises(ValueError, match="occurrence"):
            ids.section_id("guide", fixtures.DOCUMENT_ID, ["Claims"], -1)


class TestChunkIds:
    def test_differ_by_part(self):
        section = ids.section_id("guide", fixtures.DOCUMENT_ID, ["Claims"])

        assert ids.chunk_id("packed", section, 0) != ids.chunk_id("packed", section, 1)

    def test_differ_by_strategy(self):
        section = ids.section_id("guide", fixtures.DOCUMENT_ID, ["Claims"])

        assert ids.chunk_id("packed", section, 0) != ids.chunk_id(
            "windowed", section, 0
        )

    def test_refuse_a_negative_part(self):
        section = ids.section_id("guide", fixtures.DOCUMENT_ID, ["Claims"])

        with pytest.raises(ValueError, match="part"):
            ids.chunk_id("packed", section, -1)

    def test_refuse_an_unnamed_strategy(self):
        section = ids.section_id("guide", fixtures.DOCUMENT_ID, ["Claims"])

        with pytest.raises(ValueError, match="strategy"):
            ids.chunk_id("", section, 0)


class TestIdsAcrossVersionsOfADocument:
    def test_survive_renumbering(self):
        _, before = fixtures.document(ORIGINAL)
        _, after = fixtures.document(RENUMBERED)
        before_ids = {chunk.heading_path: chunk for chunk in before}
        after_ids = {chunk.heading_path: chunk for chunk in after}

        for path in [
            ("Overview",),
            ("Claims",),
            ("Claims", "Eligibility"),
            ("Claims", "Eligibility", "Concurrent claims"),
            ("Annex A",),
            ("Annex A", "Payment windows"),
        ]:
            assert after_ids[path].chunk_id == before_ids[path].chunk_id, path
            assert after_ids[path].section_id == before_ids[path].section_id, path

    def test_survive_renumbering_while_numbers_and_anchors_change(self):
        _, before = fixtures.document(ORIGINAL)
        _, after = fixtures.document(RENUMBERED)
        path = ("Claims", "Eligibility", "Concurrent claims")
        old = next(chunk for chunk in before if chunk.heading_path == path)
        new = next(chunk for chunk in after if chunk.heading_path == path)

        assert (old.section_number, old.anchor) == ("2.1.1", "211-concurrent-claims")
        assert (new.section_number, new.anchor) == ("3.1.1", "311-concurrent-claims")
        assert new.chunk_id == old.chunk_id

    def test_keep_every_part_of_a_split_section(self):
        _, before = fixtures.document(ORIGINAL)
        _, after = fixtures.document(RENUMBERED)
        path = ("Claims", "Eligibility")

        assert [c.chunk_id for c in after if c.heading_path == path] == [
            c.chunk_id for c in before if c.heading_path == path
        ]

    def test_survive_new_versions_of_the_document(self):
        later = fixtures.header(
            version_id=ids.VersionId(
                uuid.UUID("0199b8a2-0000-7000-8000-000000000099"),
            ),
        )
        _, before = fixtures.document(ORIGINAL)
        _, after = fixtures.document(ORIGINAL, later)

        assert [chunk.chunk_id for chunk in after] == [
            chunk.chunk_id for chunk in before
        ]

    def test_survive_a_body_edit_whose_content_hash_changes(self):
        edited = [
            *ORIGINAL[:3],
            Section("2.1.1 Concurrent claims", 3, ("Reworded body.",)),
            *ORIGINAL[4:],
        ]
        _, before = fixtures.document(ORIGINAL)
        _, after = fixtures.document(edited)

        assert [c.chunk_id for c in after] == [c.chunk_id for c in before]
        assert after[4].content_hash != before[4].content_hash

    def test_change_for_a_renamed_heading_and_everything_beneath_it(self):
        renamed = [
            ORIGINAL[0],
            ORIGINAL[1],
            Section("2.1 Who can claim", 2, ORIGINAL[2].parts),
            ORIGINAL[3],
            *ORIGINAL[4:],
        ]
        before = fixtures.by_heading(fixtures.document(ORIGINAL)[1])
        after = fixtures.by_heading(fixtures.document(renamed)[1])

        assert ("Claims", "Eligibility") not in after
        assert ("Claims", "Eligibility", "Concurrent claims") not in after
        assert after[("Claims", "Who can claim")] not in before.values()
        assert after[("Claims", "Who can claim", "Concurrent claims")] not in (
            before.values()
        )

        for unchanged in [("Overview",), ("Claims",), ("Annex A", "Payment windows")]:
            assert after[unchanged] == before[unchanged]


NAMESPACE = uuid.UUID("6f2c1a8e-3b4d-4c5e-9f60-718293a4b5c6")


class TestCmsIds:
    def test_are_uuid7(self):
        assert ids.is_uuid7(uuid.UUID("0199b8a2-0000-7000-8000-000000000001"))

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(uuid.uuid4(), id="random"),
            pytest.param(uuid.uuid5(NAMESPACE, "x"), id="minted"),
        ],
    )
    def test_are_told_from_other_uuids(self, value):
        assert not ids.is_uuid7(value)


class TestMintedIds:
    def test_are_deterministic(self):
        assert ids.minted_id(NAMESPACE, "content", "en") == ids.minted_id(
            NAMESPACE,
            "content",
            "en",
        )

    def test_are_uuid5(self):
        assert ids.minted_id(NAMESPACE, "content", "en").version == 5  # noqa: PLR2004

    @pytest.mark.parametrize(
        "other",
        [
            pytest.param((NAMESPACE, "content", "cy"), id="another-part"),
            pytest.param((NAMESPACE, "content:en"), id="joined-parts"),
            pytest.param(
                (uuid.UUID("0c9e7d6f-1a2b-4c3d-8e4f-5a6b7c8d9e0f"), "content", "en"),
                id="another-namespace",
            ),
        ],
    )
    def test_differ_by_namespace_and_every_part(self, other):
        assert ids.minted_id(*other) != ids.minted_id(NAMESPACE, "content", "en")

    def test_need_a_part(self):
        with pytest.raises(ValueError, match="part"):
            ids.minted_id(NAMESPACE)
