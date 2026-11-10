"""The excerpt a hit is shown with, by the milestone 1 spike's rule."""

import dataclasses

import pytest

from app.search import excerpts, service
from tests.support import chunks as fixtures

Section = fixtures.Section


def _hit(text, heading="1 Land checks", level=1, *, exact=True):
    header, (chunk,) = fixtures.document([Section(heading, level, (text,))])
    return _section_hit(header, chunk, exact=exact)


def _section_hit(header, chunk, *, exact=True):
    from app.index import chunk_index

    return chunk_index.SectionHit(
        section_id=chunk.section_id,
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        version_id=chunk.version_id,
        title=header.title,
        status=chunk.status,
        heading_path=chunk.heading_path,
        section_number=chunk.section_number,
        anchor=chunk.anchor,
        url=header.url,
        plain_text=chunk.plain_text,
        score=1.0,
        exact=exact,
    )


def _shown(excerpt):
    return [excerpt.text[start:end] for start, end in excerpt.matches]


class TestAnExcerpt:
    def test_is_the_first_sentence_holding_a_match(self):
        excerpt = excerpts.of_hit(
            "cover",
            _hit("Check the land use. Then the land cover. Then the rest."),
        )

        assert excerpt == service.Excerpt(
            text="Then the land cover.", matches=((14, 19),)
        )

    def test_looks_in_the_text_before_the_heading(self):
        excerpt = excerpts.of_hit("land", _hit("Send a letter about the land."))

        assert excerpt.text == "Send a letter about the land."

    def test_shows_the_heading_with_its_number_when_only_it_matches(self):
        excerpt = excerpts.of_hit("scope", _hit("Who it is for.", "1.1 Scope", 2))

        assert (excerpt.text, _shown(excerpt)) == ("1.1 Scope", ["Scope"])

    def test_shows_the_document_title_for_text_before_any_section(self):
        header, (chunk,) = fixtures.document([Section("1 Overview", 1, ("Body.",))])
        preamble = dataclasses.replace(chunk, heading_path=(), section_number=None)

        excerpt = excerpts.of_hit("claims", _section_hit(header, preamble))

        assert (excerpt.text, _shown(excerpt)) == ("Claims Guidance", ["Claims"])

    def test_keeps_each_block_apart(self):
        excerpt = excerpts.of_hit("post", _hit("Send a letter.\nIt goes by post"))

        assert excerpt.text == "It goes by post"

    def test_marks_every_match_in_the_sentence(self):
        excerpt = excerpts.of_hit("land", _hit("Land and more land."))

        assert _shown(excerpt) == ["Land", "land"]

    def test_marks_near_matches_for_a_near_hit(self):
        excerpt = excerpts.of_hit("livestok", _hit("Claim for livestock.", exact=False))

        assert _shown(excerpt) == ["livestock"]

    def test_marks_the_number_for_a_hit_on_its_number(self):
        excerpt = excerpts.of_hit("4.2.1", _hit("Text.", "4.2.1 Evidence", 3))

        assert excerpt == service.Excerpt(text="4.2.1 Evidence", matches=((0, 5),))

    def test_shows_the_heading_unmarked_when_nothing_matches(self):
        excerpt = excerpts.of_hit("bovine", _hit("Text."))

        assert excerpt == service.Excerpt(text="1 Land checks", matches=())


class TestCuttingALongSentence:
    def test_shows_a_sentence_of_up_to_300_characters_whole(self):
        sentence = "word " * 59 + "end"

        assert excerpts.around(sentence, 200) == sentence

    def test_cuts_a_longer_one_at_words_around_its_first_match(self):
        sentence = " ".join(f"w{n:03d}" for n in range(100))
        first = sentence.index("w050")

        shown = excerpts.around(sentence, first)

        assert shown.startswith("…w027 ")
        assert shown.endswith(" w073…")
        assert "w050" in shown

    def test_adds_no_leading_ellipsis_near_the_start(self):
        sentence = "start " + "word " * 80

        assert excerpts.around(sentence, 0).startswith("start ")


class TestSentences:
    @pytest.mark.parametrize(
        ("text", "found"),
        [
            pytest.param("One. Two? Three!", ["One.", "Two?", "Three!"], id="ends"),
            pytest.param(
                "Pay 5.50 now. Then", ["Pay 5.50 now.", "Then"], id="decimals"
            ),
            pytest.param("", [], id="empty"),
        ],
    )
    def test_split_at_sentence_ends(self, text, found):
        assert excerpts.sentences(text) == found
