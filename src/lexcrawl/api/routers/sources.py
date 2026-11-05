"""### Source endpoints: configured crawling sources."""

from __future__ import annotations

from fastapi import APIRouter

from lexcrawl.api.dependencies import SessionDep
from lexcrawl.api.exceptions import NotFoundError
from lexcrawl.api.openapi import NOT_FOUND_RESPONSE
from lexcrawl.api.schemas.sources import SourceOut
from lexcrawl.api.services import source_queries

router = APIRouter(tags=["Sources"])


@router.get("/sources", response_model=list[SourceOut], summary="List sources")
def list_sources(session: SessionDep) -> list[SourceOut]:
    """### Return every configured source."""
    return [SourceOut.from_model(source) for source in source_queries.list_sources(session)]


@router.get(
    "/sources/{source_code}",
    response_model=SourceOut,
    summary="Get a source",
    responses={404: NOT_FOUND_RESPONSE},
)
def get_source(source_code: str, session: SessionDep) -> SourceOut:
    """### Return one source by its code."""
    source = source_queries.get_source(session, source_code)
    if source is None:
        raise NotFoundError(f"source {source_code!r} was not found")
    return SourceOut.from_model(source)
