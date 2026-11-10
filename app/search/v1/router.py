"""`GET /v1/search`: word search across documents or inside one, and title search.

Every caller is a Viewer for now, so only published documents are found. Service
tokens and the role header come later.
"""

import uuid
from typing import Annotated

import fastapi

from app.index import active_index, chunk_index
from app.search import keyword_search, routing, service
from app.search.v1 import schemas

router = fastapi.APIRouter(prefix="/v1", tags=["search"])

_MAX_QUERY_LENGTH = 500
_ROLE = routing.Role.VIEWER


def get_search_service(
    index: Annotated[
        chunk_index.ChunkIndex, fastapi.Depends(active_index.get_chunk_index)
    ],
) -> service.SearchService:
    return keyword_search.KeywordSearch(index)


@router.get(
    "/search",
    response_model=schemas.WordSearchResponse | schemas.TitleSearchResponse,
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
    limit: Annotated[int, fastapi.Query(ge=1, le=chunk_index.MAX_LIMIT)] = 10,
    offset: Annotated[int, fastapi.Query(ge=0)] = 0,
) -> schemas.WordSearchResponse | schemas.TitleSearchResponse:
    """Give exactly one of `q` (word search) or `title` (title search)."""
    if (q is None) == (title is None):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Give exactly one of q or title",
        )

    if title is not None:
        titles = await search_service.search_titles(
            service.TitleSearch(text=title, role=_ROLE, limit=limit),
        )
        return schemas.TitleSearchResponse.from_model(titles)

    try:
        words = await search_service.search_words(
            service.WordSearch(
                text=q or "",
                role=_ROLE,
                tag=tag,
                limit=limit,
                offset=offset,
            ),
        )
    except service.TooManyMatchesError as e:
        return schemas.WordSearchResponse.too_many(e)

    return schemas.WordSearchResponse.from_model(words)
