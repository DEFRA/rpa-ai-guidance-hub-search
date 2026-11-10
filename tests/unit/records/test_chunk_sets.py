"""A staged chunk set: one strategy's chunks of one version at one status."""

import dataclasses
import json

import pytest

from app.records import chunk_sets, models
from tests.support import chunks as fixtures

HEADER, CHUNKS = fixtures.document(
    [
        fixtures.Section("1 Overview", 1, ("Read this first.",)),
        fixtures.Section("1.1 Éligibilité", 2, ("Première partie.", "Seconde.")),
    ],
)
CHUNK_SET = chunk_sets.ChunkSet(HEADER, fixtures.FIXTURE_STRATEGY, tuple(CHUNKS))


class TestTheChunkSetKey:
    def test_is_one_object_per_strategy_version_and_status(self):
        assert CHUNK_SET.key == (
            "staged/fixture/0199b8a2-0000-7000-8000-000000000001/"
            "0199b8a2-0000-7000-8000-000000000002/published.jsonl"
        )

    def test_differs_by_status(self):
        draft = chunk_sets.key(
            fixtures.FIXTURE_STRATEGY,
            fixtures.DOCUMENT_ID,
            fixtures.VERSION_ID,
            models.DocumentStatus.READY_FOR_PUBLISH,
        )

        assert draft != CHUNK_SET.key


class TestAChunkSet:
    def test_reads_back_as_it_was_written(self):
        assert chunk_sets.loads(chunk_sets.dumps(CHUNK_SET)) == CHUNK_SET

    def test_is_the_header_then_one_line_per_chunk(self):
        lines = [json.loads(line) for line in chunk_sets.dumps(CHUNK_SET).splitlines()]

        assert len(lines) == 1 + len(CHUNKS)
        assert (lines[0]["record"], lines[0]["strategy"]) == ("document", "fixture")
        assert lines[0]["schema_version"] == chunk_sets.SCHEMA_VERSION
        assert {line["record"] for line in lines[1:]} == {"chunk"}

    def test_keeps_non_ascii_text_readable(self):
        assert "Éligibilité" in chunk_sets.dumps(CHUNK_SET)

    def test_may_hold_no_chunks(self):
        empty = chunk_sets.ChunkSet(HEADER, fixtures.FIXTURE_STRATEGY, ())

        assert chunk_sets.loads(chunk_sets.dumps(empty)) == empty


def _lines() -> list[dict]:
    return [json.loads(line) for line in chunk_sets.dumps(CHUNK_SET).splitlines()]


def _text(lines: list[dict]) -> str:
    return "".join(f"{json.dumps(line)}\n" for line in lines)


class TestAMalformedChunkSet:
    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("", id="empty"),
            pytest.param("not json\n", id="not-json"),
            pytest.param(_text(_lines()[1:]), id="no-header"),
            pytest.param(
                _text([{**_lines()[0], "schema_version": 2}, *_lines()[1:]]),
                id="unknown-schema-version",
            ),
            pytest.param(
                _text([{**_lines()[0], "strategy": "other"}, *_lines()[1:]]),
                id="another-strategy",
            ),
            pytest.param(
                _text([_lines()[0], {**_lines()[1], "status": "ready_for_publish"}]),
                id="another-status",
            ),
            pytest.param(_text([_lines()[0], _lines()[2]]), id="ordinal-gap"),
            pytest.param(
                _text(
                    [
                        _lines()[0],
                        {k: v for k, v in _lines()[1].items() if k != "text"},
                    ],
                ),
                id="missing-field",
            ),
        ],
    )
    def test_is_refused(self, text):
        with pytest.raises(chunk_sets.MalformedChunkSetError):
            chunk_sets.loads(text)

    @pytest.mark.parametrize(
        ("line", "field", "value", "message"),
        [
            pytest.param(0, "source", [], "source must be an object", id="source"),
            pytest.param(1, "text", 5, "text must be a string", id="text"),
            pytest.param(1, "part", "0", "part must be an integer", id="part"),
            pytest.param(1, "topics", "ab", "topics must be an array", id="topics"),
            pytest.param(1, "meta", [], "meta must be an object", id="meta"),
        ],
    )
    def test_refuses_a_field_of_the_wrong_shape(self, line, field, value, message):
        lines = _lines()
        lines[line] = {**lines[line], field: value}

        with pytest.raises(chunk_sets.MalformedChunkSetError, match=message):
            chunk_sets.loads(_text(lines))

    def test_is_never_made(self):
        out_of_order = (dataclasses.replace(CHUNKS[0], ordinal=5), *CHUNKS[1:])

        with pytest.raises(chunk_sets.MalformedChunkSetError, match="ordinal"):
            chunk_sets.ChunkSet(HEADER, fixtures.FIXTURE_STRATEGY, out_of_order)
