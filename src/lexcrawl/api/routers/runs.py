"""### Crawl-run endpoints: execution history, status, and errors."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Security, status

from lexcrawl.api.dependencies import CrawlExecutorDep, PageParamsDep, SessionDep, require_trigger_token
from lexcrawl.api.exceptions import NotFoundError
from lexcrawl.api.openapi import READ_BY_ID, TRIGGER_RESPONSES, VALIDATION_ONLY
from lexcrawl.api.schemas.common import Page, Pagination
from lexcrawl.api.schemas.runs import CrawlAccepted, CrawlErrorOut, CrawlRequest, RunOut
from lexcrawl.api.services import run_queries
from lexcrawl.api.services.crawl_execution import CrawlOptions

router = APIRouter(tags=["Crawl Runs"])


@router.post(
    "/crawl-runs",
    response_model=CrawlAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a crawl",
    description=(
        "Validate the request, reserve concurrency with a PostgreSQL advisory lock, create a "
        "crawl run in `running` status, and execute it in a background worker. Returns the run "
        "id; poll `GET /crawl-runs/{id}` for status. In-process execution is not durable across "
        "application restarts and is intended for a single application worker."
    ),
    responses=TRIGGER_RESPONSES,
    dependencies=[Security(require_trigger_token)],
)
def trigger_crawl_run(payload: CrawlRequest, executor: CrawlExecutorDep) -> CrawlAccepted:
    """### Accept a crawl request and start it in the background."""
    run_uuid = executor.start(CrawlOptions(source=payload.source, max_pages=payload.max_pages))
    return CrawlAccepted(id=run_uuid, status="running", trigger_type="api", message="Crawl accepted.")


@router.get(
    "/crawl-runs",
    response_model=Page[RunOut],
    summary="List crawl runs",
    description=(
        "Return a paginated list of crawl runs with their per-source results. "
        "Ordered by start time descending, then id descending."
    ),
    responses=VALIDATION_ONLY,
)
def list_crawl_runs(
    session: SessionDep,
    page: PageParamsDep,
    source: Annotated[str | None, Query(description="Only runs that include this source code")] = None,
    status: Annotated[
        str | None, Query(description="Filter by run status: running, success, partial_failure, failed")
    ] = None,
) -> Page[RunOut]:
    """### Return a paginated list of crawl runs with their per-source results."""
    runs, total = run_queries.list_runs(
        session, source_code=source, status=status, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[RunOut.from_model(run) for run in runs],
        pagination=Pagination(limit=page.limit, offset=page.offset, total=total),
    )


@router.get(
    "/crawl-runs/{run_id}",
    response_model=RunOut,
    summary="Get a crawl run",
    description="Return one crawl run by its UUID, including per-source results and counters.",
    responses=READ_BY_ID,
)
def get_crawl_run(run_id: UUID, session: SessionDep) -> RunOut:
    """### Return one crawl run by its identifier."""
    run = run_queries.get_run(session, run_id)
    if run is None:
        raise NotFoundError(f"crawl run {run_id} was not found")
    return RunOut.from_model(run)


@router.get(
    "/crawl-runs/{run_id}/errors",
    response_model=Page[CrawlErrorOut],
    summary="List crawl run errors",
    description="Return the structured, classified errors recorded for one crawl run.",
    responses=READ_BY_ID,
)
def list_crawl_run_errors(
    run_id: UUID,
    session: SessionDep,
    page: PageParamsDep,
    stage: Annotated[str | None, Query(description="Filter by error stage")] = None,
    error_type: Annotated[str | None, Query(description="Filter by error type")] = None,
) -> Page[CrawlErrorOut]:
    """### Return the structured errors recorded for one crawl run."""
    if run_queries.get_run(session, run_id) is None:
        raise NotFoundError(f"crawl run {run_id} was not found")
    errors, total = run_queries.list_errors(
        session, run_id, stage=stage, error_type=error_type, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[CrawlErrorOut.from_model(error) for error in errors],
        pagination=Pagination(limit=page.limit, offset=page.offset, total=total),
    )
