"""Any source's document becomes canonical sections the same way."""

import pytest

from app.indexing.prepare import sectioning, sources
from app.records import ids, models
from tests.support import chunks as fixtures

HEADER = fixtures.header()


def _section(heading, level, *, anchor="", section_id=None, text="Body."):
    return sources.SourceSection(
        heading=heading,
        section_number=None,
        level=level,
        anchor=anchor,
        blocks=(fixtures.paragraph(text),),
        section_id=section_id,
    )


def _build(*sections):
    return sectioning.build(sources.SourceDocument(header=HEADER, sections=sections))


class TestBuild:
    def test_nests_heading_paths_by_level(self):
        built = _build(
            _section(None, 0),
            _section("Claims", 1),
            _section("Eligibility", 2),
            _section("Payments", 1),
        )

        assert [section.heading_path for section in built] == [
            (),
            ("Claims",),
            ("Claims", "Eligibility"),
            ("Payments",),
        ]

    def test_a_heading_after_a_skipped_level_is_a_sibling(self):
        built = _build(
            _section("Claims", 2),
            _section("Photographs", 4),
            _section("Maps", 4),
        )

        assert [section.heading_path for section in built] == [
            ("Claims",),
            ("Claims", "Photographs"),
            ("Claims", "Maps"),
        ]

    def test_keeps_a_section_id_the_source_gave(self):
        given = ids.SectionId("given")

        built = _build(_section("Claims", 1, section_id=given), _section("Sub", 2))

        assert built[0].section_id == given
        assert built[1].parent_section_id == given

    def test_hashes_an_id_the_source_did_not_give(self):
        built = _build(_section("Claims", 1))

        assert built[0].section_id == ids.section_id(
            models.SourceType.GUIDE,
            HEADER.document_id,
            ("Claims",),
        )

    def test_copies_the_headers_identity(self):
        (built,) = _build(_section("Claims", 1))

        assert (built.document_id, built.version_id, built.status) == (
            HEADER.document_id,
            HEADER.version_id,
            HEADER.status,
        )

    def test_resolves_internal_links_by_anchor(self):
        built = _build(
            _section("Claims", 1, anchor="claims", text="See [pay](#pay)."),
            _section("Pay", 1, anchor="pay"),
        )

        assert built[0].references[0].section_id == built[1].section_id


class TestNormaliseUrl:
    @pytest.mark.parametrize(
        ("url", "normalised"),
        [
            pytest.param("HTTP://A.COM:80/P", "http://a.com/P", id="default-port"),
            pytest.param(
                "https://a.com:8443/p", "https://a.com:8443/p", id="other-port"
            ),
            pytest.param("mailto:Team@A.com", "mailto:Team@A.com", id="no-host"),
            pytest.param("https://a.com:x/", "https://a.com:x/", id="bad-port"),
            pytest.param("file:///a b.pdf", "file:///a b.pdf", id="file"),
            pytest.param("HTTP://[::1]:80/p", "http://[::1]/p", id="ipv6-host"),
        ],
    )
    def test_normalises(self, url, normalised):
        assert sectioning.normalise_url(url) == normalised
