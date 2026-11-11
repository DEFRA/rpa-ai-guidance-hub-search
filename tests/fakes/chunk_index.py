"""In-memory `ChunkIndex`: `word_match.find` plus edit distance for near matches."""

import re
from collections.abc import Sequence

from app.index import chunk_index, index_schema
from app.matching import word_match
from app.records import ids, models

_WORD = re.compile(r"\w+")
_SHORT = 2
_WHOLE, _START, _TYPO = 1.0, 0.75, 0.5


class InMemoryChunkIndex:
    def __init__(self) -> None:
        self._documents: dict[
            ids.DocumentId,
            tuple[models.DocumentHeader, tuple[models.ChunkRecord, ...]],
        ] = {}
        self._latest: dict[ids.DocumentId, ids.VersionId] = {}

    async def replace_document(
        self,
        header: models.DocumentHeader,
        chunks: Sequence[models.ChunkRecord],
    ) -> None:
        for chunk in chunks:
            index_schema.to_index_document(header, chunk)

        if chunks:
            self._documents[header.document_id] = (header, tuple(chunks))
        else:
            self._documents.pop(header.document_id, None)

        self._latest[header.document_id] = header.version_id

    async def remove_document(self, document_id: ids.DocumentId) -> None:
        self._documents.pop(document_id, None)
        self._latest.pop(document_id, None)

    async def remove_version(
        self,
        document_id: ids.DocumentId,
        version_id: ids.VersionId,
    ) -> None:
        if await self.indexed_version(document_id) == version_id:
            del self._documents[document_id]

        latest = self._latest.get(document_id)

        if latest is None or not ids.is_older(version_id, latest):
            self._latest[document_id] = version_id

    async def indexed_version(
        self,
        document_id: ids.DocumentId,
    ) -> ids.VersionId | None:
        entry = self._documents.get(document_id)
        return entry[0].version_id if entry else None

    async def latest_version(
        self,
        document_id: ids.DocumentId,
    ) -> ids.VersionId | None:
        return self._latest.get(document_id)

    async def search(self, query: chunk_index.ChunkQuery) -> chunk_index.SectionHits:
        best: dict[ids.SectionId, chunk_index.SectionHit] = {}

        for header, chunks in self._visible(query.statuses):
            if query.tag is not None and query.tag not in {
                header.document_id,
                header.version_id,
            }:
                continue

            for chunk in chunks:
                hit = _match(query.text, header, chunk)
                kept = best.get(chunk.section_id)

                if hit and (kept is None or _rank(hit) > _rank(kept)):
                    best[chunk.section_id] = hit

        ranked = sorted(best.values(), key=_rank, reverse=True)
        page = ranked[query.offset : query.offset + query.limit]

        return chunk_index.SectionHits(hits=tuple(page), total=len(ranked))

    async def search_titles(
        self,
        query: chunk_index.TitleQuery,
    ) -> list[chunk_index.TitleHit]:
        hits = [
            chunk_index.TitleHit(
                document_id=header.document_id,
                version_id=header.version_id,
                title=header.title,
                status=header.status,
                url=header.url,
                score=score,
            )
            for header, _ in self._visible(query.statuses)
            if (score := _title_score(query.text, header.title))
        ]
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[: query.limit]

    def _visible(
        self,
        statuses: frozenset[models.DocumentStatus],
    ) -> list[tuple[models.DocumentHeader, tuple[models.ChunkRecord, ...]]]:
        return [
            entry for entry in self._documents.values() if entry[0].status in statuses
        ]


def _rank(hit: chunk_index.SectionHit) -> tuple[bool, float]:
    return hit.exact, hit.score


def _match(
    text: str,
    header: models.DocumentHeader,
    chunk: models.ChunkRecord,
) -> chunk_index.SectionHit | None:
    weights = index_schema.weights()
    heading_weight = weights[index_schema.HEADING_PATH]
    body_weight = weights[index_schema.BODY]
    headings = "\n".join(chunk.heading_path)
    in_number = int(text.strip() == chunk.section_number)
    in_headings = len(word_match.find(text, headings))
    in_body = len(word_match.find(text, chunk.plain_text))
    exact = heading_weight * (in_number + in_headings) + body_weight * in_body

    if exact:
        matches = in_number + in_headings + in_body
        return _hit(header, chunk, exact, exact=True, matches=matches)

    near_headings = _near_matches(text, headings)
    near_body = _near_matches(text, chunk.plain_text)
    near = heading_weight * near_headings + body_weight * near_body

    if not near:
        return None

    return _hit(header, chunk, near, exact=False, matches=near_headings + near_body)


def _near_matches(query: str, text: str) -> int:
    """Runs of whole words in one block, each within the query word's budget."""
    words = [word.casefold() for word in _WORD.findall(query)]

    if not words:
        return 0

    count = 0

    for block in text.split("\n"):
        candidates = [word.casefold() for word in _WORD.findall(block)]

        for start in range(len(candidates) - len(words) + 1):
            run = candidates[start : start + len(words)]
            count += all(
                _distance(word, candidate) <= chunk_index.typo_budget(word)
                for word, candidate in zip(words, run, strict=True)
            )

    return count


def _title_score(query: str, title: str) -> float:
    """The mean of each query word's best match; 0 unless every word matches."""
    words = [word.casefold() for word in _WORD.findall(query)]
    title_words = [word.casefold() for word in _WORD.findall(title)]

    if not words:
        return 0

    scores = [
        max(
            (_title_word_score(word, title_word) for title_word in title_words),
            default=0,
        )
        for word in words
    ]

    return sum(scores) / len(scores) if all(scores) else 0


def _title_word_score(word: str, candidate: str) -> float:
    if word == candidate:
        return _WHOLE

    if len(word) <= _SHORT:
        return 0

    if candidate.startswith(word):
        return _START

    budget = chunk_index.typo_budget(word)
    near = min(_distance(word, candidate), _distance(word, candidate[: len(word)]))

    return _TYPO if near <= budget else 0


def _distance(left: str, right: str) -> int:
    """Edits between two words, counting a swap of neighbours as one."""
    previous: list[int] = []
    row = list(range(len(right) + 1))

    for i, left_char in enumerate(left, start=1):
        before, previous, row = previous, row, [i]

        for j, right_char in enumerate(right, start=1):
            cost = int(left_char != right_char)
            row.append(min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + cost))

            if (
                i > 1
                and j > 1
                and left_char == right[j - 2]
                and left[i - 2] == right_char
            ):
                row[j] = min(row[j], before[j - 2] + 1)

    return row[-1]


def _hit(
    header: models.DocumentHeader,
    chunk: models.ChunkRecord,
    score: float,
    *,
    exact: bool,
    matches: int,
) -> chunk_index.SectionHit:
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
        score=score,
        exact=exact,
        matches=matches,
    )
