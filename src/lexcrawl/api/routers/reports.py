"""### Report endpoints: reports and publications."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from lexcrawl.api.dependencies import PageParamsDep, SessionDep
from lexcrawl.api.exceptions import NotFoundError
from lexcrawl.api.openapi import READ_BY_ID, VALIDATION_ONLY
from lexcrawl.api.schemas.common import Page, Pagination
from lexcrawl.api.schemas.reports import ReportDetail, ReportSummary
from lexcrawl.api.schemas.resources import ResourceOut, ResourceParent
from lexcrawl.api.services import content_queries

router = APIRouter(tags=["Reports"])


@router.get(
    "/reports",
    response_model=Page[ReportSummary],
    summary="List reports",
    description=(
        "Return a paginated list of reports and publications. "
        "Ordered by publication date descending (NULLs last), then id descending. "
        "Extracted bodies are not included; use the detail endpoint for content."
    ),
    responses=VALIDATION_ONLY,
)
def list_reports(
    session: SessionDep,
    page: PageParamsDep,
    source: Annotated[str | None, Query(description="Filter by source code, e.g. commerce_reports")] = None,
    published_from: Annotated[
        date | None, Query(description="Earliest publication date (inclusive, YYYY-MM-DD)")
    ] = None,
    published_to: Annotated[date | None, Query(description="Latest publication date (inclusive, YYYY-MM-DD)")] = None,
) -> Page[ReportSummary]:
    """### Return a paginated, filterable list of reports."""
    result = content_queries.list_content(
        session,
        "report",
        source_code=source,
        published_from=published_from,
        published_to=published_to,
        limit=page.limit,
        offset=page.offset,
    )
    items = [
        ReportSummary.from_row(document, source_model, localization)
        for document, source_model, localization in result.rows
    ]
    return Page(items=items, pagination=Pagination(limit=page.limit, offset=page.offset, total=result.total))


@router.get(
    "/reports/{report_id}",
    response_model=ReportDetail,
    summary="Get a report",
    description="Return one report with its marked extracted body, localizations, and resources.",
    responses=READ_BY_ID,
)
def get_report(report_id: int, session: SessionDep) -> ReportDetail:
    """### Return one report with its full extracted content and metadata."""
    found = content_queries.get_content(session, report_id, "report")
    if found is None:
        raise NotFoundError(f"report {report_id} was not found")
    document, source, localizations, resources = found
    return ReportDetail.from_rows(document, source, localizations, resources)


@router.get(
    "/reports/{report_id}/resources",
    response_model=list[ResourceOut],
    summary="List report resources",
    description="Return the linked resources (attachments) belonging to one report.",
    responses=READ_BY_ID,
)
def list_report_resources(report_id: int, session: SessionDep) -> list[ResourceOut]:
    """### Return the resources attached to one report."""
    found = content_queries.get_content(session, report_id, "report")
    if found is None:
        raise NotFoundError(f"report {report_id} was not found")
    document, source, localizations, resources = found
    primary_title = sorted(localizations, key=lambda item: item.id)[0].title if localizations else None
    parent = ResourceParent.from_models(document, source, "report", primary_title)
    return [ResourceOut.from_model(resource, parent=parent) for resource in sorted(resources, key=lambda item: item.id)]
