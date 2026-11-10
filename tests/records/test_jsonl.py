"""The JSON Lines file a document version's sections are stored as."""

import dataclasses
import json

import pytest

from app.records import jsonl
from tests.support import chunks as fixtures

HEADER, SECTIONS = fixtures.sections(
    [
        fixtures.Section("1 Overview", 1),
        fixtures.Section("1.1 Éligibilité", 2, ("Première partie.", "Seconde.")),
    ],
)


class TestTheSectionFileKey:
    def test_is_one_file_per_document_version(self):
        assert jsonl.key(fixtures.DOCUMENT_ID, fixtures.VERSION_ID) == (
            "sections/0199b8a2-0000-7000-8000-000000000001/"
            "0199b8a2-0000-7000-8000-000000000002.jsonl"
        )


class TestASectionFile:
    def test_reads_back_as_it_was_written(self):
        assert jsonl.loads(jsonl.dumps(HEADER, SECTIONS)) == (HEADER, tuple(SECTIONS))

    def test_is_the_header_then_one_line_per_section(self):
        lines = jsonl.dumps(HEADER, SECTIONS).splitlines()

        assert len(lines) == 1 + len(SECTIONS)
        assert json.loads(lines[0])["record"] == "document"
        assert json.loads(lines[0])["schema_version"] == jsonl.SCHEMA_VERSION
        assert {json.loads(line)["record"] for line in lines[1:]} == {"section"}

    def test_ends_with_a_newline(self):
        assert jsonl.dumps(HEADER, SECTIONS).endswith("}\n")

    def test_keeps_non_ascii_text_readable(self):
        assert "Éligibilité" in jsonl.dumps(HEADER, SECTIONS)

    @pytest.mark.parametrize(
        "separator", ["\u0085", "\u2028", "\u2029", "\x0b", "\x0c"]
    )
    def test_keeps_unicode_line_separators_in_text(self, separator):
        header, sections = fixtures.sections(
            [fixtures.Section(f"1 A{separator}B", 1, (f"x{separator}y",))],
        )

        assert jsonl.loads(jsonl.dumps(header, sections)) == (header, tuple(sections))

    def test_may_hold_no_sections(self):
        assert jsonl.loads(jsonl.dumps(HEADER, [])) == (HEADER, ())


def _lines() -> list[dict]:
    return [json.loads(line) for line in jsonl.dumps(HEADER, SECTIONS).splitlines()]


def _text(lines: list[dict]) -> str:
    return "".join(f"{json.dumps(line)}\n" for line in lines)


class TestAMalformedSectionFile:
    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("", id="empty"),
            pytest.param("not json\n", id="not-json"),
            pytest.param("[1, 2]\n", id="not-an-object"),
            pytest.param(_text(_lines()[1:]), id="no-header"),
            pytest.param(_text([*_lines()[1:], _lines()[0]]), id="header-last"),
            pytest.param(
                _text([{**_lines()[0], "schema_version": 2}, *_lines()[1:]]),
                id="unknown-schema-version",
            ),
            pytest.param(
                _text([_lines()[0], {**_lines()[1], "version_id": "x"}]),
                id="invalid-id",
            ),
            pytest.param(
                _text(
                    [
                        _lines()[0],
                        {
                            **_lines()[1],
                            "version_id": "0199b8a2-0000-7000-8000-0000000000ff",
                        },
                    ],
                ),
                id="another-version",
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
                        {k: v for k, v in _lines()[1].items() if k != "blocks"},
                    ]
                ),
                id="missing-field",
            ),
        ],
    )
    def test_is_refused(self, text):
        with pytest.raises(jsonl.MalformedSectionFileError):
            jsonl.loads(text)

    def test_is_never_written(self):
        out_of_order = [dataclasses.replace(SECTIONS[0], ordinal=5), *SECTIONS[1:]]

        with pytest.raises(jsonl.MalformedSectionFileError, match="ordinal"):
            jsonl.dumps(HEADER, out_of_order)
