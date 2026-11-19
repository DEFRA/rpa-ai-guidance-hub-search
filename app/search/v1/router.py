"""`GET /v1/search`: word search across documents or inside one, by section or
grouped by document, and title search. `GET /v1/documents/{documentId}/matches`:
every place the words are in one document.

Only published documents are found for now.
"""

import enum
import uuid
from typing import Annotated

import fastapi

from app import config as app_config
from app.common import s3
from app.index import active_index, chunk_index
from app.records import ids, models, section_store
from app.search import document_matches, keyword_search, service
from app.search.v1 import schemas

router = fastapi.APIRouter(prefix="/v1", tags=["search"])

_MAX_QUERY_LENGTH = 500
_MAX_SECTIONS_PER_DOCUMENT = 20
_STATUSES = frozenset({models.DocumentStatus.PUBLISHED})


class Grouping(enum.StrEnum):
    DOCUMENT = "document"


def get_search_service(
    index: Annotated[
        chunk_index.ChunkIndex, fastapi.Depends(active_index.get_chunk_index)
    ],
) -> service.SearchService:
    return keyword_search.KeywordSearch(index)


@router.get(
    "/search",
    summary="Search documents by words, or by title",
)
async def search(
    search_service: Annotated[
        service.SearchService, fastapi.Depends(get_search_service)
    ],
    q: Annotated[
        str | None,
        fastapi.Query(
            max_length=_MAX_QUERY_LENGTH,
            description="Words to find, like Word's Find: any case, anywhere in a "
            "word, several words as one phrase",
        ),
    ] = None,
    title: Annotated[
        str | None,
        fastapi.Query(max_length=_MAX_QUERY_LENGTH, description="A title to look up"),
    ] = None,
    tag: Annotated[
        uuid.UUID | None,
        fastapi.Query(description="A document or version ID to search inside"),
    ] = None,
    group: Annotated[
        Grouping | None,
        fastapi.Query(
            description="`document` answers word search with a page of documents "
            "whose sections hold the words, each with its best sections, and the "
            "documents whose title the words cover; `limit` and `offset` then "
            "count documents",
        ),
    ] = None,
    sections: Annotated[
        int,
        fastapi.Query(
            ge=1,
            le=_MAX_SECTIONS_PER_DOCUMENT,
            description="With `group=document`: how many of each document's "
            "sections to give, best first",
        ),
    ] = 3,
    limit: Annotated[int, fastapi.Query(ge=1, le=chunk_index.MAX_LIMIT)] = 10,
    offset: Annotated[int, fastapi.Query(ge=0)] = 0,
) -> (
    schemas.WordSearchResponse
    | schemas.DocumentSearchResponse
    | schemas.TitleSearchResponse
):
    """Give exactly one of `q` (word search) or `title` (title search)."""
    if (q is None) == (title is None):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Give exactly one of q or title",
        )

    if group is not None and title is not None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="group is for word search (q) only",
        )

    if title is not None:
        titles = await search_service.search_titles(
            service.TitleSearch(text=title, statuses=_STATUSES, limit=limit),
        )
        return schemas.TitleSearchResponse.from_model(titles)

    if group is Grouping.DOCUMENT:
        return await _search_documents(
            search_service,
            service.DocumentSearch(
                text=q or "",
                statuses=_STATUSES,
                tag=tag,
                limit=limit,
                offset=offset,
                sections=sections,
            ),
        )

    try:
        words = await search_service.search_words(
            service.WordSearch(
                text=q or "",
                statuses=_STATUSES,
                tag=tag,
                limit=limit,
                offset=offset,
            ),
        )
    except service.TooManyMatchesError as e:
        return schemas.WordSearchResponse.too_many(e)

    return schemas.WordSearchResponse.from_model(words)


def get_match_finder(
    index: Annotated[
        chunk_index.ChunkIndex, fastapi.Depends(active_index.get_chunk_index)
    ],
    config: app_config.Config,
    client: s3.Client,
) -> document_matches.DocumentMatchFinder:
    return document_matches.DocumentMatchFinder(
        index,
        section_store.S3SectionStore(client, config.search_store_s3_bucket),
    )


@router.get(
    "/documents/{documentId}/matches",
    summary="Where words are in one document",
)
async def matches(
    finder: Annotated[
        document_matches.DocumentMatchFinder, fastapi.Depends(get_match_finder)
    ],
    document_id: Annotated[uuid.UUID, fastapi.Path(alias="documentId")],
    q: Annotated[
        str,
        fastapi.Query(
            max_length=_MAX_QUERY_LENGTH,
            description="Words to find, like Word's Find: any case, anywhere in a "
            "word, several words as one phrase; exact matches only",
        ),
    ],
) -> schemas.DocumentMatchesResponse:
    """Every match in the document's indexed version, for a page to mark.

    404 when no published version of the document is indexed.
    """
    found = await finder.matches_in(ids.DocumentId(document_id), q, _STATUSES)

    if found is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="No document indexed with this ID",
        )

    return schemas.DocumentMatchesResponse.from_model(found)


async def _search_documents(
    search_service: service.SearchService,
    request: service.DocumentSearch,
) -> schemas.DocumentSearchResponse:
    try:
        documents = await search_service.search_documents(request)
    except service.TooManyMatchesError as e:
        return schemas.DocumentSearchResponse.too_many(e)

    return schemas.DocumentSearchResponse.from_model(documents)
