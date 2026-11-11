"""Contract for every `ChunkIndex`. Subclass it beside a module-level
`chunk_index` fixture that returns an empty index.

Asserts relative order only; scores differ between engines.
"""

import dataclasses
import uuid
from typing import Any

import pytest

from app.index import chunk_index as chunk_index_module
from app.records import ids, models
from tests.support import chunks as fixtures

Section = fixtures.Section
PUBLISHED = frozenset({models.DocumentStatus.PUBLISHED})
EVERY_STATUS = frozenset(models.DocumentStatus)

_HOLDING = "Hold a holding number on the boundaries."

OTHER_DOCUMENT_ID = ids.DocumentId(uuid.UUID("0199b8a2-0000-7000-8000-0000000000aa"))
OTHER_VERSION_ID = ids.VersionId(uuid.UUID("0199b8a2-0000-7000-8000-0000000000ab"))
BOTH = {fixtures.DOCUMENT_ID, OTHER_DOCUMENT_ID}
NEXT_VERSION_ID = ids.VersionId(uuid.UUID("0199b8a2-0000-7000-8000-0000000000ac"))

CLAIMS = fixtures.document(
    [
        Section("1 Overview", 1, ("Read this before making a claim for livestock.",)),
        Section("2 Eligibility", 1, ("Applicants must hold a holding number.",)),
        Section("2.1 Hedgerows", 2, ("Payments for maintaining boundaries.",)),
    ],
)

DRAFT = fixtures.document(
    [Section("1 Woodland", 1, ("Woodland grants and felling licences.",))],
    fixtures.header(
        document_id=OTHER_DOCUMENT_ID,
        version_id=OTHER_VERSION_ID,
        status=models.DocumentStatus.READY_FOR_PUBLISH,
        title="Woodland Guidance",
    ),
)


def _query(
    text: str,
    statuses: frozenset[models.DocumentStatus] = EVERY_STATUS,
    **page: Any,
) -> chunk_index_module.ChunkQuery:
    return chunk_index_module.ChunkQuery(text=text, statuses=statuses, **page)


def _titles(
    text: str,
    statuses: frozenset[models.DocumentStatus] = EVERY_STATUS,
    limit: int = 10,
) -> chunk_index_module.TitleQuery:
    return chunk_index_module.TitleQuery(text=text, statuses=statuses, limit=limit)


async def _found(
    index: chunk_index_module.ChunkIndex,
    query: chunk_index_module.ChunkQuery,
) -> list[tuple[str, ...]]:
    return [hit.heading_path for hit in (await index.search(query)).hits]


async def _titled(
    index: chunk_index_module.ChunkIndex,
    query: chunk_index_module.TitleQuery,
) -> set[ids.DocumentId]:
    return {hit.document_id for hit in await index.search_titles(query)}


class ChunkIndexContract:
    async def test_finds_a_replaced_document(self, chunk_index):
        await chunk_index.replace_document(*CLAIMS)

        assert await _found(chunk_index, _query("holding")) == [("Eligibility",)]

    async def test_returns_what_a_hit_needs_to_be_shown_and_linked(self, chunk_index):
        header, chunks = CLAIMS

        await chunk_index.replace_document(header, chunks)

        (hit,) = (await chunk_index.search(_query("hedgerows"))).hits

        assert (
            hit.section_id,
            hit.chunk_id,
            hit.document_id,
            hit.version_id,
            hit.title,
            hit.status,
            hit.heading_path,
            hit.section_number,
            hit.anchor,
            hit.url,
            hit.plain_text,
            hit.exact,
        ) == (
            chunks[2].section_id,
            chunks[2].chunk_id,
            header.document_id,
            header.version_id,
            "Claims Guidance",
            models.DocumentStatus.PUBLISHED,
            ("Eligibility", "Hedgerows"),
            "2.1",
            "21-hedgerows",
            header.url,
            "Payments for maintaining boundaries.",
            True,
        )

    async def test_keeps_only_the_latest_replacement_of_a_document(self, chunk_index):
        await chunk_index.replace_document(*CLAIMS)
        next_version = fixtures.document(
            [Section("1 Overview", 1, ("Rewritten guidance on orchards.",))],
            fixtures.header(version_id=NEXT_VERSION_ID),
        )

        await chunk_index.replace_document(*next_version)

        assert await _found(chunk_index, _query("holding")) == []
        (hit,) = (await chunk_index.search(_query("orchards"))).hits

        assert hit.version_id == NEXT_VERSION_ID

    async def test_replacing_with_the_same_chunks_changes_nothing(self, chunk_index):
        await chunk_index.replace_document(*CLAIMS)
        once = await chunk_index.search(_query("holding"))

        await chunk_index.replace_document(*CLAIMS)

        assert await chunk_index.search(_query("holding")) == once

    async def test_replacing_with_no_chunks_removes_the_document(self, chunk_index):
        header, _ = CLAIMS

        await chunk_index.replace_document(*CLAIMS)

        await chunk_index.replace_document(header, [])

        assert await _found(chunk_index, _query("holding")) == []
        assert await chunk_index.search_titles(_titles("Claims Guidance")) == []

    async def test_removes_a_document_and_leaves_the_others(self, chunk_index):
        await chunk_index.replace_document(*CLAIMS)
        await chunk_index.replace_document(*DRAFT)

        await chunk_index.remove_document(CLAIMS[0].document_id)

        assert await _found(chunk_index, _query("holding")) == []
        assert await _found(chunk_index, _query("woodland")) == [("Woodland",)]
        titles = await chunk_index.search_titles(_titles("Claims Guidance"))

        assert CLAIMS[0].document_id not in {hit.document_id for hit in titles}

    async def test_removing_an_absent_document_is_fine(self, chunk_index):
        await chunk_index.remove_document(OTHER_DOCUMENT_ID)

        assert await _found(chunk_index, _query("woodland")) == []

    async def test_removes_the_indexed_version(self, chunk_index):
        header, _ = CLAIMS
        await chunk_index.replace_document(*CLAIMS)
        await chunk_index.replace_document(*DRAFT)

        await chunk_index.remove_version(header.document_id, header.version_id)

        assert await chunk_index.indexed_version(header.document_id) is None
        assert await _found(chunk_index, _query("holding")) == []
        assert header.document_id not in await _titled(
            chunk_index, _titles("Claims Guidance")
        )
        assert await _found(chunk_index, _query("woodland")) == [("Woodland",)]

    async def test_removing_a_version_not_indexed_leaves_the_document(
        self, chunk_index
    ):
        header, _ = CLAIMS
        await chunk_index.replace_document(*CLAIMS)

        await chunk_index.remove_version(header.document_id, NEXT_VERSION_ID)

        assert await chunk_index.indexed_version(header.document_id) == (
            header.version_id
        )
        assert await _found(chunk_index, _query("holding")) == [("Eligibility",)]

    async def test_removing_a_version_of_an_absent_document_is_fine(self, chunk_index):
        await chunk_index.remove_version(OTHER_DOCUMENT_ID, OTHER_VERSION_ID)

        assert await chunk_index.indexed_version(OTHER_DOCUMENT_ID) is None

    async def test_knows_the_version_indexed_for_a_document(self, chunk_index):
        header, _ = CLAIMS
        await chunk_index.replace_document(*CLAIMS)
        next_version = fixtures.document(
            [Section("1 Overview", 1, ("Rewritten guidance on orchards.",))],
            fixtures.header(version_id=NEXT_VERSION_ID),
        )

        await chunk_index.replace_document(*next_version)

        assert await chunk_index.indexed_version(header.document_id) == (
            NEXT_VERSION_ID
        )

    async def test_finds_only_the_statuses_asked_for(self, chunk_index):
        await chunk_index.replace_document(*CLAIMS)
        await chunk_index.replace_document(*DRAFT)

        assert await _found(chunk_index, _query("woodland", PUBLISHED)) == []
        assert await _found(chunk_index, _query("woodland")) == [("Woodland",)]

    async def test_pages_by_limit_and_offset_and_counts_every_match(self, chunk_index):
        header, chunks = CLAIMS

        await chunk_index.replace_document(
            header,
            [dataclasses.replace(chunk, plain_text="shared word") for chunk in chunks],
        )

        first = await chunk_index.search(_query("shared", limit=2))
        rest = await chunk_index.search(_query("shared", limit=2, offset=2))

        assert (len(first.hits), first.total) == (2, 3)
        assert (len(rest.hits), rest.total) == (1, 3)
        assert {hit.section_id for hit in first.hits + rest.hits} == {
            chunk.section_id for chunk in chunks
        }

    @pytest.mark.parametrize(
        ("tag", "found"),
        [
            pytest.param(fixtures.DOCUMENT_ID, [("Eligibility",)], id="document"),
            pytest.param(fixtures.VERSION_ID, [("Eligibility",)], id="version"),
            pytest.param(NEXT_VERSION_ID, [], id="not-indexed"),
        ],
    )
    async def test_searches_inside_one_document_or_version(
        self, chunk_index, tag, found
    ):
        await chunk_index.replace_document(*CLAIMS)
        await chunk_index.replace_document(
            *fixtures.document(
                [Section("1 Holding", 1, ("Woodland holding numbers.",))],
                DRAFT[0],
            ),
        )

        assert await _found(chunk_index, _query("holding", tag=tag)) == found

    async def test_returns_one_hit_per_section_from_its_best_chunk(self, chunk_index):
        header, chunks = fixtures.document(
            [Section("1 Overview", 1, ("One hedgerow.", "A hedgerow and a hedgerow."))],
        )

        await chunk_index.replace_document(header, chunks)

        (hit,) = (await chunk_index.search(_query("hedgerow"))).hits

        assert hit.chunk_id == chunks[1].chunk_id

    async def test_ranks_a_heading_match_above_a_body_only_match(self, chunk_index):
        header, chunks = fixtures.document(
            [
                Section("1 Overview", 1, ("Hedgerows may be cut in winter.",)),
                Section("2 Hedgerows", 1, ("Rules for boundaries.",)),
            ],
        )

        await chunk_index.replace_document(header, chunks)

        assert await _found(chunk_index, _query("hedgerows")) == [
            ("Hedgerows",),
            ("Overview",),
        ]

    @pytest.mark.parametrize(
        ("body", "text", "found"),
        [
            pytest.param("Repayment schedules.", "pay", True, id="inside-a-word"),
            pytest.param("REPAYMENT SCHEDULES.", "repay", True, id="any-case"),
            pytest.param("Hold a holding number.", "a holding", True, id="phrase"),
            pytest.param("Holding a number.", "a holding", False, id="word-order"),
            pytest.param("Make a claim\nfor stock.", "claim for", False, id="blocks"),
            pytest.param("Make a claim for stock.", "claim for", True, id="one-block"),
            pytest.param("Rules for an area.", "an", True, id="short-whole-word"),
            pytest.param("Planting rules.", "an", False, id="short-inside-word"),
        ],
    )
    async def test_matches_like_find_in_a_word_processor(
        self,
        chunk_index,
        body,
        text,
        found,
    ):
        await chunk_index.replace_document(
            *fixtures.document([Section("1 Overview", 1, (body,))]),
        )

        assert bool(await _found(chunk_index, _query(text))) is found

    async def test_counts_the_matches_in_a_hit(self, chunk_index):
        await chunk_index.replace_document(
            *fixtures.document(
                [Section("1 Hedgerows", 1, ("A hedgerow and a hedgerow.",))]
            ),
        )

        (hit,) = (await chunk_index.search(_query("hedgerow"))).hits

        assert hit.matches == 3

    async def test_finds_a_near_match_for_a_typo(self, chunk_index):
        await chunk_index.replace_document(*CLAIMS)

        (hit,) = (await chunk_index.search(_query("livestok"))).hits

        assert (hit.heading_path, hit.exact) == (("Overview",), False)

    async def test_ranks_every_near_match_below_every_exact_match(self, chunk_index):
        await chunk_index.replace_document(
            *fixtures.document(
                [
                    Section("1 Overview", 1, ("A hedgerw, a hedgerw, a hedgerw.",)),
                    Section("2 Boundaries", 1, ("One hedgerow.",)),
                ],
            ),
        )

        hits = (await chunk_index.search(_query("hedgerow"))).hits

        assert [(hit.heading_path, hit.exact) for hit in hits] == [
            (("Boundaries",), True),
            (("Overview",), False),
        ]

    @pytest.mark.parametrize(
        ("body", "text"),
        [
            pytest.param("Make a clam\nfor stock.", "claim for", id="across-blocks"),
            pytest.param("Planting on an area.", "in", id="short-word"),
            pytest.param("Make a claimant list.", "clam", id="part-of-a-word"),
            pytest.param("Holding a number.", "hlding number", id="word-order"),
        ],
    )
    async def test_finds_no_near_match_beyond_the_rules(self, chunk_index, body, text):
        await chunk_index.replace_document(
            *fixtures.document([Section("1 Overview", 1, (body,))]),
        )

        assert await _found(chunk_index, _query(text)) == []

    @pytest.mark.parametrize(
        ("text", "found"),
        [
            pytest.param("4.2.1", [("Eligibility",)], id="number"),
            pytest.param(" 4.2.1 ", [("Eligibility",)], id="spaced"),
            pytest.param("4.2.10", [("Woodland",)], id="longer-number"),
            pytest.param("4.2", [], id="parent-number"),
        ],
    )
    async def test_finds_a_section_by_its_number(self, chunk_index, text, found):
        await chunk_index.replace_document(
            *fixtures.document(
                [
                    Section("4.2.1 Eligibility", 1, ("Hold a holding number.",)),
                    Section("4.2.10 Woodland", 1, ("Woodland grants.",)),
                ],
            ),
        )

        assert await _found(chunk_index, _query(text)) == found

    @pytest.mark.parametrize(
        ("text", "exact"),
        [
            pytest.param("holding", [True], id="exact"),
            pytest.param("holdng", [False], id="one-typo"),
            pytest.param("bundaires", [False], id="two-typos-in-a-long-word"),
            pytest.param("bundairs", [], id="three-typos"),
            pytest.param("hldng", [], id="over-the-budget"),
            pytest.param("hold a holdng", [False], id="phrase-with-a-typo"),
            pytest.param("a hold holdng", [], id="near-words-out-of-order"),
            pytest.param("numbr", [False], id="body-word"),
            pytest.param("nmber", [False], id="missing-letter"),
            pytest.param("bed", [], id="short-word-has-no-budget"),
        ],
    )
    async def test_finds_a_near_match_within_the_typo_budget(
        self,
        chunk_index,
        text,
        exact,
    ):
        await chunk_index.replace_document(
            *fixtures.document(
                [Section("4.2.1 Eligibility", 1, (_HOLDING,))],
            ),
        )

        hits = (await chunk_index.search(_query(text))).hits

        assert [(hit.heading_path, hit.exact) for hit in hits] == [
            (("Eligibility",), flag) for flag in exact
        ]

    async def test_ranks_every_exact_match_above_any_near_match(self, chunk_index):
        await chunk_index.replace_document(
            *fixtures.document(
                [
                    Section("1 Boundry rules", 1, ("Boundry, boundry, boundry.",)),
                    Section("2 Overview", 1, ("A boundary.",)),
                ],
            ),
        )

        hits = (await chunk_index.search(_query("boundary"))).hits

        assert [(hit.heading_path, hit.exact) for hit in hits] == [
            (("Overview",), True),
            (("Boundry rules",), False),
        ]

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("", id="empty"),
            pytest.param("   ", id="blank"),
            pytest.param("Can I claim for hedgerows?", id="question"),
            pytest.param('"unbalanced quote', id="quote"),
            pytest.param("title:hedgerows", id="field-prefix"),
            pytest.param("hedgerows AND OR NOT", id="operators"),
            pytest.param("hedge* (rows", id="wildcard-and-bracket"),
            pytest.param("4.2.1:", id="clause-colon"),
        ],
    )
    async def test_never_fails_on_what_a_person_types(self, chunk_index, text):
        await chunk_index.replace_document(*CLAIMS)

        hits = await chunk_index.search(_query(text))

        assert hits.total == len(hits.hits)

    async def test_finds_nothing_for_an_empty_query(self, chunk_index):
        await chunk_index.replace_document(*CLAIMS)

        assert await chunk_index.search(_query("")) == chunk_index_module.SectionHits(
            hits=(),
            total=0,
        )

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("Clams Guidanse", id="typos"),
            pytest.param("Guidance Claims", id="word-order"),
            pytest.param("claims guidance", id="any-case"),
        ],
    )
    async def test_finds_a_title_despite(self, chunk_index, text):
        await chunk_index.replace_document(*CLAIMS)
        await chunk_index.replace_document(*DRAFT)

        hits = await chunk_index.search_titles(_titles(text))

        assert (hits[0].document_id, hits[0].url) == (
            fixtures.DOCUMENT_ID,
            f"/guides/{fixtures.DOCUMENT_ID}",
        )

    async def test_finds_only_titles_of_the_statuses_asked_for(self, chunk_index):
        await chunk_index.replace_document(*DRAFT)

        assert await chunk_index.search_titles(_titles("Woodland", PUBLISHED)) == []

        (hit,) = await chunk_index.search_titles(_titles("Woodland Guidance"))

        assert (hit.document_id, hit.version_id, hit.title, hit.status) == (
            OTHER_DOCUMENT_ID,
            OTHER_VERSION_ID,
            "Woodland Guidance",
            models.DocumentStatus.READY_FOR_PUBLISH,
        )

    @pytest.mark.parametrize(
        ("text", "found"),
        [
            pytest.param("Cla", {fixtures.DOCUMENT_ID}, id="start-of-a-word"),
            pytest.param("Guida", BOTH, id="shared"),
            pytest.param("guida cla", {fixtures.DOCUMENT_ID}, id="reordered-starts"),
            pytest.param("Gudia", BOTH, id="typo"),
            pytest.param("Woo cla", set(), id="starts-of-two-titles"),
        ],
    )
    async def test_finds_a_title_from_half_words(self, chunk_index, text, found):
        await chunk_index.replace_document(*CLAIMS)
        await chunk_index.replace_document(*DRAFT)

        assert await _titled(chunk_index, _titles(text)) == found

    async def test_ranks_a_whole_title_word_above_its_start(self, chunk_index):
        await chunk_index.replace_document(*CLAIMS)
        await chunk_index.replace_document(
            *fixtures.document(
                [Section("1 Overview", 1)],
                fixtures.header(
                    document_id=OTHER_DOCUMENT_ID,
                    version_id=OTHER_VERSION_ID,
                    title="Claim Forms",
                ),
            ),
        )

        hits = await chunk_index.search_titles(_titles("claim"))

        assert [hit.title for hit in hits] == ["Claim Forms", "Claims Guidance"]

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("zzzz qqqq", id="nonsense"),
            pytest.param("Hedge", id="start-of-no-title-word"),
            pytest.param("ms", id="short-inside-a-word"),
            pytest.param("aims", id="end-of-a-word"),
        ],
    )
    async def test_finds_no_title_for_something_unlike_any(self, chunk_index, text):
        await chunk_index.replace_document(*CLAIMS)

        assert await chunk_index.search_titles(_titles(text)) == []

    async def test_returns_at_most_the_title_limit(self, chunk_index):
        await chunk_index.replace_document(*CLAIMS)
        await chunk_index.replace_document(*DRAFT)

        assert await _titled(chunk_index, _titles("Guidance")) == BOTH
        assert len(await chunk_index.search_titles(_titles("Guidance", limit=1))) == 1
