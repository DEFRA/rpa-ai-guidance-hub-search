"""Chunk index fields as a Tantivy schema; weights become field boosts unchanged."""

from collections.abc import Sequence

import tantivy

from app.index import index_schema

NGRAM_TOKENIZER = "ngram3"

_TOKENIZERS = {
    index_schema.FieldKind.KEYWORD: "raw",
    index_schema.FieldKind.TEXT: "en_stem",
    index_schema.FieldKind.TEXT_EXACT: "default",
    index_schema.FieldKind.TEXT_PARTIAL: NGRAM_TOKENIZER,
}


def build_schema(
    fields: Sequence[index_schema.IndexField] = index_schema.CHUNK_INDEX,
) -> tantivy.Schema:
    builder = tantivy.SchemaBuilder()

    for field in fields:
        _add(builder, field)

    return builder.build()


def register_tokenizers(index: tantivy.Index) -> None:
    """Tantivy doesn't persist custom tokenizers; call on every index opened."""
    index.register_tokenizer(NGRAM_TOKENIZER, ngram_analyzer())


def ngram_analyzer() -> tantivy.TextAnalyzer:
    """Also turns each query word into the runs it must share with a match."""
    return (
        tantivy.TextAnalyzerBuilder(tantivy.Tokenizer.ngram(3, 3, prefix_only=False))
        .filter(tantivy.Filter.lowercase())
        .build()
    )


def field_boosts(
    fields: Sequence[index_schema.IndexField] = index_schema.CHUNK_INDEX,
) -> dict[str, float]:
    return index_schema.weights(fields)


def searched_fields(
    fields: Sequence[index_schema.IndexField] = index_schema.CHUNK_INDEX,
) -> list[str]:
    return list(index_schema.weights(fields))


def _add(builder: tantivy.SchemaBuilder, field: index_schema.IndexField) -> None:
    match field.kind:
        case (
            index_schema.FieldKind.KEYWORD
            | index_schema.FieldKind.TEXT
            | index_schema.FieldKind.TEXT_EXACT
            | index_schema.FieldKind.TEXT_PARTIAL
        ):
            builder.add_text_field(
                field.name,
                stored=field.stored,
                tokenizer_name=_TOKENIZERS[field.kind],
            )
        case index_schema.FieldKind.INTEGER:
            builder.add_integer_field(
                field.name,
                stored=field.stored,
                indexed=True,
                fast=True,
            )
        case index_schema.FieldKind.TAGS:
            builder.add_facet_field(field.name)
        case index_schema.FieldKind.OBJECT:
            builder.add_json_field(field.name, stored=field.stored)
        case _:
            msg = f"{field.name}: no Tantivy mapping for field kind {field.kind}"
            raise ValueError(msg)
