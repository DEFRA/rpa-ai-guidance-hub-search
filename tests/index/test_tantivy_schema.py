"""The chunk index schema, translated for Tantivy, behaves as declared."""

import pytest
import tantivy

from app.index import index_schema, tantivy_schema

SECTION_TEXT = "Applicants claiming for livestock must hold a CPH-1234 number."


@pytest.fixture
def index() -> tantivy.Index:
    built = tantivy.Index(tantivy_schema.build_schema())
    tantivy_schema.register_tokenizers(built)
    writer = built.writer()
    document = tantivy.Document()
    document.add_text("chunk_id", "c1")
    document.add_text("document_id", "g1")
    document.add_text("status", "published")
    document.add_text("title", "Claims Guidance")
    document.add_text("heading_path", "Claims")
    document.add_text("heading_path", "Eligibility")
    document.add_text("section_number", "4.2.1")
    document.add_text("body", SECTION_TEXT)
    document.add_text("body_exact", SECTION_TEXT)
    document.add_text("body_ngram", SECTION_TEXT)
    document.add_text("title_ngram", "Claims Guidance")
    document.add_facet("topics", tantivy.Facet.from_string("/claims"))
    document.add_integer("ordinal", 7)
    document.add_json("meta", {"source_page": 12})
    writer.add_document(document)
    writer.commit()
    writer.wait_merging_threads()
    built.reload()
    return built


def _hits(index: tantivy.Index, query: tantivy.Query) -> int:
    return len(index.searcher().search(query, 10).hits)


def _term(index: tantivy.Index, field: str, value: str) -> tantivy.Query:
    return tantivy.Query.term_query(index.schema, field, value)


class TestTheTantivySchema:
    def test_opens_as_an_index(self):
        assert tantivy.Index(tantivy_schema.build_schema()).searcher().num_docs == 0

    def test_boosts_fields_by_their_weights(self):
        assert tantivy_schema.field_boosts() == {
            "title": 2,
            "heading_path": 3,
            "body": 1,
            "body_exact": 1,
        }

    def test_searches_the_weighted_fields(self):
        assert tantivy_schema.searched_fields() == [
            "title",
            "heading_path",
            "body",
            "body_exact",
        ]

    def test_parses_a_query_with_the_boosts(self, index):
        query = index.parse_query(
            "eligibility",
            tantivy_schema.searched_fields(),
            field_boosts=tantivy_schema.field_boosts(),
        )

        assert _hits(index, query) == 1

    def test_refuses_a_kind_it_cannot_map(self):
        field = index_schema.IndexField("x", index_schema.FieldKind.TEXT)
        object.__setattr__(field, "kind", "vector")  # a kind added without a mapping

        with pytest.raises(ValueError, match="no Tantivy mapping"):
            tantivy_schema.build_schema([field])


class TestTheTantivyFieldKinds:
    def test_stem_the_body(self, index):
        assert _hits(index, index.parse_query("claims", ["body"])) == 1

    def test_do_not_stem_the_exact_body(self, index):
        assert _hits(index, index.parse_query("claims", ["body_exact"])) == 0
        assert _hits(index, index.parse_query("claiming", ["body_exact"])) == 1

    def test_match_keywords_only_whole(self, index):
        assert _hits(index, _term(index, "section_number", "4.2.1")) == 1
        assert _hits(index, _term(index, "section_number", "4.2")) == 0
        assert _hits(index, _term(index, "status", "Published")) == 0

    def test_find_runs_of_three_characters_inside_words(self, index):
        assert _hits(index, _term(index, "body_ngram", "ive")) == 1
        assert _hits(index, _term(index, "body_ngram", "cph")) == 1
        assert _hits(index, _term(index, "title_ngram", "uid")) == 1
        assert _hits(index, _term(index, "body_ngram", "xyz")) == 0

    def test_keep_every_heading_in_the_path(self, index):
        assert _hits(index, index.parse_query("claims", ["heading_path"])) == 1
        assert _hits(index, index.parse_query("eligibility", ["heading_path"])) == 1

    def test_store_what_a_hit_shows_but_not_the_exact_body(self, index):
        searcher = index.searcher()
        (_, address) = searcher.search(_term(index, "chunk_id", "c1"), 1).hits[0]

        stored = searcher.doc(address).to_dict()

        assert stored["heading_path"] == ["Claims", "Eligibility"]
        assert stored["body"] == [SECTION_TEXT]
        assert stored["ordinal"] == [7]
        assert stored["meta"] == [{"source_page": 12}]
        assert "body_exact" not in stored
        assert "body_ngram" not in stored


class TestTheNgramAnalyzer:
    def test_splits_a_word_into_lower_cased_runs_of_three(self):
        assert tantivy_schema.ngram_analyzer().analyze("RePay") == [
            "rep",
            "epa",
            "pay",
        ]

    def test_must_be_registered_on_every_index_opened(self):
        writer = tantivy.Index(tantivy_schema.build_schema()).writer()
        writer.add_document(tantivy.Document(body_ngram="Repayment"))

        with pytest.raises(ValueError, match="tokenizer"):
            writer.commit()
