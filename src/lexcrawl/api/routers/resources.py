"""### Resource endpoints: linked files and attachments."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from lexcrawl.api.dependencies import PageParamsDep, SessionDep
from lexcrawl.api.exceptions import NotFoundError
from lexcrawl.api.openapi import NOT_FOUND_RESPONSE, VALIDATION_ONLY
from lexcrawl.api.schemas.common import Page, Pagination
from lexcrawl.api.schemas.resources import ResourceOut, ResourceParent
from lexcrawl.api.services import resource_queries

router = APIRouter(tags=["Resources"])


@router.get(
    "/resources",
    response_model=Page[ResourceOut],
    summary="List resources",
    description=(
        "Return a paginated list of resources (PDFs and other linked files) with a reference to "
        "their parent content. Ordered by last update descending, then id descending."
    ),
    responses=VALIDATION_ONLY,
)
def list_resources(
    session: SessionDep,
    page: PageParamsDep,
    document_id: Annotated[int | None, Query(description="Filter by owning content id")] = None,
    resource_type: Annotated[
        str | None, Query(description="Filter by resource type: pdf, excel, word, csv, other")
    ] = None,
    extraction_status: Annotated[
        str | None,
        Query(description="Filter by extraction status: pending, complete, access_blocked, failed, url_unavailable"),
    ] = None,
) -> Page[ResourceOut]:
    """### Return a paginated, filterable list of resources."""
    result = resource_queries.list_resources(
        session,
        document_id=document_id,
        resource_type=resource_type,
        extraction_status=extraction_status,
        limit=page.limit,
        offset=page.offset,
    )
    items = [
        ResourceOut.from_model(
            resource,
            parent=ResourceParent.from_models(document, source, kind, localization.title),
        )
        for resource, document, source, localization, kind in result.rows
    ]
    return Page(items=items, pagination=Pagination(limit=page.limit, offset=page.offset, total=result.total))


@router.get(
    "/resources/{resource_id}",
    response_model=ResourceOut,
    summary="Get a resource",
    description="Return one resource with its parent content reference and extraction status.",
    responses={404: NOT_FOUND_RESPONSE},
)
def get_resource(resource_id: int, session: SessionDep) -> ResourceOut:
    """### Return one resource with its parent content reference."""
    found = resource_queries.get_resource(session, resource_id)
    if found is None:
        raise NotFoundError(f"resource {resource_id} was not found")
    resource, document, source, localization, kind = found
    return ResourceOut.from_model(
        resource,
        parent=ResourceParent.from_models(document, source, kind, localization.title),
    )
